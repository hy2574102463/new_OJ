"""协调提交校验、持久化、后台评测和详情权限。"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.exceptions import AppError
from app.judge.runner import JudgeInfrastructureError, JudgeRunner
from app.models.languages import LanguageRecord
from app.models.submissions import SubmissionRecord
from app.models.users import UserRecord, UserRole
from app.repositories.languages import LanguageRepository
from app.repositories.problems import ProblemRepository
from app.repositories.submissions import SubmissionRepository
from app.schemas.problems import StoredProblem
from app.schemas.submissions import SubmissionPayload


logger = logging.getLogger("oj.submissions")


class SubmissionService:
    """创建可轮询任务，并持有后台任务直到完成或应用关闭。"""

    def __init__(
        self,
        submissions: SubmissionRepository,
        problems: ProblemRepository,
        languages: LanguageRepository,
        runner: JudgeRunner,
    ) -> None:
        self.submissions = submissions
        self.problems = problems
        self.languages = languages
        self.runner = runner
        self._tasks: set[asyncio.Task[None]] = set()
        self._submit_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """恢复策略是终止遗留 pending，因为进程重启无法恢复原子进程。"""

        await self.submissions.fail_stale_pending(datetime.now(timezone.utc))

    async def shutdown(self) -> None:
        """取消并等待全部后台任务，让 Judge 有机会清理用户进程。"""

        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(
        self, payload: SubmissionPayload, current_user: UserRecord
    ) -> SubmissionRecord:
        """校验限流和资源后创建 pending，并把实际评测交给后台任务。"""

        async with self._submit_lock:
            now = datetime.now(timezone.utc)
            recent = await self.submissions.count_recent(
                current_user.user_id, now - timedelta(minutes=1)
            )
            if recent >= 3:
                raise AppError(429, "submission rate limit exceeded")

            problem = await self.problems.get(payload.problem_id)
            language = await self.languages.get(payload.language)
            if problem is None:
                raise AppError(404, "problem not found")
            if language is None:
                raise AppError(404, "language not found")

            submission = await self.submissions.create_pending(
                current_user.user_id,
                problem.id,
                language.name,
                payload.code,
                now,
            )
        # create_task 立即让路由返回 pending；集合强引用保证任务不会中途被回收。
        task = asyncio.create_task(
            self._evaluate(submission, problem, language),
            name=f"submission-{submission.submission_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return submission

    async def get_detail(
        self, submission_id: int, current_user: UserRecord
    ) -> SubmissionRecord:
        """仅提交者或管理员可查看汇总，源码始终不返回。"""

        submission = await self.submissions.get(submission_id)
        if submission is None:
            raise AppError(404, "submission not found")
        if (
            current_user.role is not UserRole.ADMIN
            and submission.user_id != current_user.user_id
        ):
            raise AppError(403, "permission denied")
        return submission

    async def _evaluate(
        self,
        submission: SubmissionRecord,
        problem: StoredProblem,
        language: LanguageRecord,
    ) -> None:
        """执行后台评测并保证每个 pending 最终落入 success 或 error。"""

        try:
            result = await self.runner.judge(
                submission.code,
                language,
                problem.testcases,
                problem.time_limit or language.time_limit,
                problem.memory_limit or language.memory_limit,
            )
            await self.submissions.finish_success(
                submission.submission_id, result, datetime.now(timezone.utc)
            )
        except asyncio.CancelledError:
            # shutdown 会取消任务；写入明确状态后继续传播取消信号。
            await self.submissions.finish_error(
                submission.submission_id,
                "evaluation interrupted",
                datetime.now(timezone.utc),
            )
            raise
        except JudgeInfrastructureError:
            await self.submissions.finish_error(
                submission.submission_id,
                "evaluation infrastructure error",
                datetime.now(timezone.utc),
            )
        except Exception as exc:
            # 不记录异常文本、源码或路径，只保留异常类型供服务端定位。
            logger.error(
                "submission_failed id=%s type=%s",
                submission.submission_id,
                type(exc).__name__,
            )
            await self.submissions.finish_error(
                submission.submission_id,
                "evaluation failed",
                datetime.now(timezone.utc),
            )
