"""编排 AI 多轮命题、权限、后台生命周期、结果校验和费用统计。"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import AppError
from app.models.ai import AIRoundMode, AIRoundRecord, AITaskRecord, AITaskStatus
from app.models.users import UserRecord, UserRole
from app.repositories.ai import AIRepository
from app.repositories.problems import ProblemRepository
from app.schemas.ai import AIRoundCreate, AITaskCreate
from app.schemas.problems import ProblemPayload
from app.services.ai_client import (
    AIConfigurationError,
    AIModelResponse,
    AIProviderError,
    OpenAICompatibleClient,
    is_model_configured,
    model_config_data,
)
from app.services.ai_prompts import SYSTEM_PROMPT, round_instruction


logger = logging.getLogger("oj.ai")


class AIRoundResultError(ValueError):
    """表示模型结果违反本轮明确规则，code 可安全反馈给修正请求。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AIService:
    """持有后台任务强引用，并把每轮活动最终归入一个终态。"""

    def __init__(self, settings: Settings, repository: AIRepository,
                 problems: ProblemRepository,
                 model_client: OpenAICompatibleClient | None = None) -> None:
        self.settings = settings
        self.repository = repository
        self.problems = problems
        self.model_client = model_client or OpenAICompatibleClient(settings)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """启动时将无法恢复的旧进程活动任务标为失败。"""

        await self.repository.fail_stale(datetime.now(timezone.utc))

    async def shutdown(self) -> None:
        """取消并等待模型调用，让 HTTP 连接先清理再关闭应用或重置数据库。"""

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def config(self) -> dict[str, object]:
        """返回环境模型配置的脱敏摘要。"""

        return model_config_data(self.settings)

    async def create(self, payload: AITaskCreate,
                     current_user: UserRecord) -> AITaskRecord:
        """校验部署配置和参考题目，持久化 pending 后立即调度首轮。"""

        if not is_model_configured(self.settings):
            raise AppError(503, "AI model is not configured")
        problem_id = payload.problem_id
        if problem_id is not None and await self.problems.get(problem_id) is None:
            raise AppError(404, "problem not found")
        task = await self.repository.create(
            uuid4().hex, current_user.user_id, problem_id, payload.requirement,
            datetime.now(timezone.utc),
        )
        self._schedule(task.task_id, task.current_round)
        return task

    async def get(self, task_id: str, current_user: UserRecord) -> dict[str, Any]:
        """按所有者/管理员权限返回状态、历史、结果及用量。"""

        task = await self._authorized_task(task_id, current_user)
        rounds = await self.repository.rounds(task.task_id)
        data = self._task_data(task, rounds)
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("id"), str):
            # 保存动作取决于当前结果是否已存在，不依赖会话最初从何处开始。
            data["result_action"] = (
                "update" if await self.problems.get(result["id"]) is not None else "create"
            )
        else:
            data["result_action"] = None
        return data

    async def continue_task(self, task_id: str, payload: AIRoundCreate,
                            current_user: UserRecord) -> AITaskRecord:
        """只允许终态 completed 追加追问，避免并发轮次覆盖结果。"""

        task = await self._authorized_task(task_id, current_user)
        if not is_model_configured(self.settings):
            raise AppError(503, "AI model is not configured")
        if task.status is not AITaskStatus.COMPLETED:
            raise AppError(409, "AI task is not completed")
        updated = await self.repository.append_round(
            task.task_id, payload.requirement, payload.mode, datetime.now(timezone.utc)
        )
        self._schedule(updated.task_id, updated.current_round)
        return updated

    async def cancel(self, task_id: str,
                     current_user: UserRecord) -> AITaskRecord:
        """先持久化取消，再传播 asyncio 取消，确保迟到响应不能完成任务。"""

        task = await self._authorized_task(task_id, current_user)
        if task.status not in {AITaskStatus.PENDING, AITaskStatus.RUNNING}:
            raise AppError(409, "AI task has already finished")
        if not await self.repository.cancel(task.task_id, datetime.now(timezone.utc)):
            raise AppError(409, "AI task has already finished")
        running = self._tasks.get(task.task_id)
        if running is not None:
            running.cancel()
        cancelled = await self.repository.get(task.task_id)
        assert cancelled is not None
        return cancelled

    async def _authorized_task(self, task_id: str,
                               current_user: UserRecord) -> AITaskRecord:
        """普通用户先按归属拒绝探测，管理员才得到明确 404。"""

        task = await self.repository.get(task_id)
        if current_user.role is not UserRole.ADMIN and (
            task is None or task.user_id != current_user.user_id
        ):
            raise AppError(403, "permission denied")
        if task is None:
            raise AppError(404, "AI task not found")
        return task

    def _schedule(self, task_id: str, round_index: int) -> None:
        """创建后台任务并以 task_id 保存强引用，结束后只删除自身引用。"""

        task = asyncio.create_task(
            self._run_round(task_id, round_index), name=f"ai-{task_id}-{round_index}"
        )
        self._tasks[task_id] = task

        def discard(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(task_id) is completed:
                self._tasks.pop(task_id, None)

        task.add_done_callback(discard)

    async def _run_round(self, task_id: str, round_index: int) -> None:
        """执行一轮最多两次模型调用，并保证异常转换成脱敏终态。"""

        try:
            if not await self.repository.mark_running(
                task_id, round_index, datetime.now(timezone.utc)
            ):
                return
            messages = await self._messages(task_id, round_index)
            await self._progress(task_id, round_index, 25, "正在请求模型生成题目")
            first = await self._complete_with_heartbeat(
                task_id, round_index, messages, start_percent=25
            )
            responses = [first]
            try:
                result = self._parse_problem(first.content)
                await self._validate_round_result(task_id, round_index, result)
            except (ValueError, ValidationError) as validation_error:
                await self._progress(task_id, round_index, 72, "正在修正题目结构")
                repair_messages = messages + [
                    {"role": "assistant", "content": first.content},
                    {"role": "user", "content": self._repair_prompt(validation_error)},
                ]
                second = await self._complete_with_heartbeat(
                    task_id, round_index, repair_messages, start_percent=72
                )
                responses.append(second)
                result = self._parse_problem(second.content)
                await self._validate_round_result(task_id, round_index, result)

            task = await self.repository.get(task_id)
            if task is None:
                return
            input_tokens = sum(item.input_tokens for item in responses)
            output_tokens = sum(item.output_tokens for item in responses)
            usage_source = self._combined_usage(responses)
            cost = self._cost(input_tokens, output_tokens)
            total_cost = Decimal(task.cost) + cost
            await self.repository.complete(
                task_id, round_index, result.model_dump_json(), input_tokens,
                output_tokens, str(cost), str(total_cost), usage_source,
                datetime.now(timezone.utc),
            )
        except asyncio.CancelledError:
            # 用户取消已先写 cancelled；shutdown 则在此补写稳定失败状态。
            await self.repository.fail(
                task_id, round_index, "AI task interrupted", datetime.now(timezone.utc)
            )
            raise
        except (AIConfigurationError, AIProviderError):
            await self.repository.fail(
                task_id, round_index, "AI provider request failed",
                datetime.now(timezone.utc),
            )
        except (ValidationError, ValueError, json.JSONDecodeError):
            await self.repository.fail(
                task_id, round_index, "AI result did not match problem schema",
                datetime.now(timezone.utc),
            )
        except Exception as exc:
            logger.error("ai_task_failed id=%s type=%s", task_id, type(exc).__name__)
            await self.repository.fail(
                task_id, round_index, "AI task failed", datetime.now(timezone.utc)
            )

    async def _messages(self, task_id: str,
                        round_index: int) -> list[dict[str, str]]:
        """构造首轮或追问上下文，不包含密钥和未验证模型文本。"""

        task = await self.repository.get(task_id)
        rounds = await self.repository.rounds(task_id)
        assert task is not None
        current = rounds[round_index - 1]
        current_problem: dict[str, Any] | None = None
        if round_index == 1 and task.problem_id is not None:
            problem = await self.problems.get(task.problem_id)
            if problem is None:
                raise ValueError("referenced problem disappeared")
            current_problem = problem.model_dump(exclude={"public_cases"})
        elif round_index > 1:
            previous = rounds[round_index - 2]
            if previous.result_json is None:
                raise ValueError("previous round has no result")
            current_problem = json.loads(previous.result_json)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": round_instruction(
                    current.mode, current_problem, current.requirement
                ),
            },
        ]

    async def _validate_round_result(
        self, task_id: str, round_index: int, result: ProblemPayload
    ) -> None:
        """按显式轮次意图校验 ID，避免模型文字理解偏差影响题库操作。"""

        rounds = await self.repository.rounds(task_id)
        current = rounds[round_index - 1]
        previous_id: str | None = None
        if round_index > 1:
            previous_json = rounds[round_index - 2].result_json
            if previous_json is not None:
                previous_id = str(json.loads(previous_json)["id"])
        else:
            task = await self.repository.get(task_id)
            if task is not None:
                previous_id = task.problem_id

        if current.mode is AIRoundMode.REVISE:
            if previous_id is None or result.id != previous_id:
                raise AIRoundResultError("revision_id_changed")
            return
        if previous_id is not None and result.id == previous_id:
            raise AIRoundResultError("new_problem_reused_previous_id")
        if await self.problems.get(result.id) is not None:
            raise AIRoundResultError("new_problem_id_already_exists")

    def _byte_progress(self, task_id: str,
                       round_index: int, floor: int = 34):
        """把响应下载活动转换成有限频率的可观察阶段进度。"""

        last_percent = floor

        async def update(byte_count: int) -> None:
            nonlocal last_percent
            percent = min(90, floor + byte_count // 8192)
            if percent > last_percent:
                last_percent = percent
                await self._progress(task_id, round_index, percent, "正在接收模型结果")

        return update

    async def _complete_with_heartbeat(
        self,
        task_id: str,
        round_index: int,
        messages: list[dict[str, str]],
        *,
        start_percent: int,
    ) -> AIModelResponse:
        """等待非流式模型时持续写入存活进度，并正确传播取消。

        心跳只在等待区间缓慢前进且不达到完成值；外层被取消时，内部 HTTP
        任务也会取消并等待其连接清理，避免遗留网络请求。
        """

        request = asyncio.create_task(
            self.model_client.complete(
                messages, self._byte_progress(task_id, round_index, start_percent)
            )
        )
        percent = start_percent
        try:
            while True:
                done, _pending = await asyncio.wait({request}, timeout=1.0)
                if request in done:
                    return await request
                percent = min(88, percent + 1)
                await self._progress(
                    task_id, round_index, percent, "正在等待模型生成结果"
                )
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    async def _progress(self, task_id: str, round_index: int,
                        percent: int, message: str) -> None:
        """把阶段信息同时写入任务摘要和当前轮次。"""

        await self.repository.update_progress(
            task_id, round_index, percent, message, datetime.now(timezone.utc)
        )

    @staticmethod
    def _parse_problem(content: str) -> ProblemPayload:
        """兼容可选 Markdown JSON 围栏，然后按正式题目 schema 校验。"""

        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            first_newline = stripped.find("\n")
            if first_newline < 0:
                raise ValueError("invalid JSON fence")
            stripped = stripped[first_newline + 1:-3].strip()
        return ProblemPayload.model_validate(json.loads(stripped))

    @staticmethod
    def _repair_prompt(error: Exception) -> str:
        """只发送字段位置和错误类型，不回传服务端路径或异常原文。"""

        if isinstance(error, ValidationError):
            issues = [
                {"field": ".".join(str(part) for part in item["loc"]),
                 "type": item["type"]}
                for item in error.errors(include_url=False)[:20]
            ]
        elif isinstance(error, AIRoundResultError):
            issues = [{"field": "id", "type": error.code}]
        else:
            issues = [{"field": "$", "type": "invalid_json"}]
        return "上一输出无效。请仅返回修正后的完整 JSON。校验摘要：" + json.dumps(
            issues, ensure_ascii=False
        )

    @staticmethod
    def _combined_usage(responses: list[AIModelResponse]) -> str:
        """多次调用只要统计来源不同，任务轮次就标记 mixed。"""

        sources = {item.usage_source for item in responses}
        return next(iter(sources)) if len(sources) == 1 else "mixed"

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        """使用 Decimal 避免二进制浮点给费用展示带来不可解释尾差。"""

        unit = Decimal(self.settings.ai_price_unit)
        value = (
            Decimal(input_tokens) * Decimal(str(self.settings.ai_input_price)) / unit
            + Decimal(output_tokens) * Decimal(str(self.settings.ai_output_price)) / unit
        )
        return value.quantize(Decimal("0.00000001"))

    def _task_data(self, task: AITaskRecord,
                   rounds: list[AIRoundRecord]) -> dict[str, Any]:
        """构造不会暴露原始模型失败正文的公开任务视图。"""

        result = json.loads(task.result_json) if task.result_json else None
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "progress": {
                "percent": task.progress_percent,
                "message": task.progress_message,
            },
            "problem_id": task.problem_id,
            "current_round": task.current_round,
            "result": result,
            "error_info": task.error_info,
            "usage": self._usage_data(
                task.input_tokens, task.output_tokens, task.cost, task.usage_source
            ),
            "rounds": [
                {
                    "round": item.round_index,
                    "requirement": item.requirement,
                    "mode": item.mode.value,
                    "status": item.status.value,
                    "progress": {"percent": item.progress_percent,
                                 "message": item.progress_message},
                    "usage": self._usage_data(
                        item.input_tokens, item.output_tokens, item.cost,
                        item.usage_source,
                    ),
                }
                for item in rounds
            ],
        }

    def _usage_data(self, input_tokens: int, output_tokens: int,
                    cost: str, source: str) -> dict[str, Any]:
        """保持每轮和累计用量响应使用同一种结构。"""

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": float(Decimal(cost)),
            "currency": self.settings.ai_currency,
            "source": source,
            "price_unit": self.settings.ai_price_unit,
        }
