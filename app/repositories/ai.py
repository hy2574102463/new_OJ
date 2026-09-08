"""使用异步 SQLite 原子保存 AI 多轮任务、进度、结果和用量。"""

from datetime import datetime

import aiosqlite

from app.models.ai import AIRoundMode, AIRoundRecord, AITaskRecord, AITaskStatus
from app.repositories.database import Database


class AIRepository:
    """封装任务状态转换；条件 UPDATE 防止取消被迟到结果覆盖。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _to_task(row: aiosqlite.Row) -> AITaskRecord:
        """把数据库任务行转换成领域记录。"""

        return AITaskRecord(
            task_id=str(row["task_id"]),
            user_id=int(row["user_id"]),
            problem_id=str(row["problem_id"]) if row["problem_id"] is not None else None,
            status=AITaskStatus(row["status"]),
            progress_percent=int(row["progress_percent"]),
            progress_message=str(row["progress_message"]),
            result_json=str(row["result_json"]) if row["result_json"] is not None else None,
            error_info=str(row["error_info"]) if row["error_info"] is not None else None,
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost=str(row["cost"]),
            usage_source=str(row["usage_source"]),
            current_round=int(row["current_round"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _to_round(row: aiosqlite.Row) -> AIRoundRecord:
        """把数据库轮次行转换成领域记录。"""

        return AIRoundRecord(
            task_id=str(row["task_id"]),
            round_index=int(row["round_index"]),
            requirement=str(row["requirement"]),
            mode=AIRoundMode(row["mode"]),
            status=AITaskStatus(row["status"]),
            progress_percent=int(row["progress_percent"]),
            progress_message=str(row["progress_message"]),
            result_json=str(row["result_json"]) if row["result_json"] is not None else None,
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost=str(row["cost"]),
            usage_source=str(row["usage_source"]),
            created_at=str(row["created_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
        )

    async def create(self, task_id: str, user_id: int, problem_id: str | None,
                     requirement: str, now: datetime) -> AITaskRecord:
        """在同一事务创建任务和首轮，避免出现没有轮次的任务。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            await connection.execute(
                """INSERT INTO ai_tasks(
                    task_id,user_id,problem_id,status,progress_percent,progress_message,
                    created_at,updated_at
                ) VALUES (?, ?, ?, 'pending', 0, '等待执行', ?, ?)""",
                (task_id, user_id, problem_id, timestamp, timestamp),
            )
            await connection.execute(
                """INSERT INTO ai_task_rounds(
                    task_id,round_index,requirement,status,progress_percent,
                    progress_message,created_at,mode
                ) VALUES (?, 1, ?, 'pending', 0, '等待执行', ?, ?)""",
                (
                    task_id,
                    requirement,
                    timestamp,
                    AIRoundMode.REVISE.value if problem_id else AIRoundMode.NEW.value,
                ),
            )
            row = await self._fetch_task(connection, task_id)
            assert row is not None
            return self._to_task(row)

    async def get(self, task_id: str) -> AITaskRecord | None:
        """按不透明任务 ID 查询，不存在返回 None。"""

        async with self.database.connection() as connection:
            row = await self._fetch_task(connection, task_id)
        return self._to_task(row) if row is not None else None

    async def rounds(self, task_id: str) -> list[AIRoundRecord]:
        """按轮次顺序读取完整历史，供构造后续模型上下文。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM ai_task_rounds WHERE task_id = ? ORDER BY round_index",
                (task_id,),
            )
            rows = await cursor.fetchall()
        return [self._to_round(row) for row in rows]

    async def append_round(self, task_id: str, requirement: str, mode: AIRoundMode,
                           now: datetime) -> AITaskRecord:
        """仅允许 completed 任务原子进入下一轮 pending。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            row = await self._fetch_task(connection, task_id)
            if row is None:
                raise LookupError("AI task not found")
            task = self._to_task(row)
            if task.status is not AITaskStatus.COMPLETED:
                raise ValueError("AI task is not completed")
            round_index = task.current_round + 1
            await connection.execute(
                """UPDATE ai_tasks SET status='pending', progress_percent=0,
                    progress_message='等待执行', error_info=NULL, current_round=?, updated_at=?
                    WHERE task_id=? AND status='completed'""",
                (round_index, timestamp, task_id),
            )
            await connection.execute(
                """INSERT INTO ai_task_rounds(
                    task_id,round_index,requirement,status,progress_percent,
                    progress_message,created_at,mode
                ) VALUES (?, ?, ?, 'pending', 0, '等待执行', ?, ?)""",
                (task_id, round_index, requirement, timestamp, mode.value),
            )
            updated = await self._fetch_task(connection, task_id)
            assert updated is not None
            return self._to_task(updated)

    async def mark_running(self, task_id: str, round_index: int,
                           now: datetime) -> bool:
        """把仍为 pending 的指定轮次切换为 running。"""

        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """UPDATE ai_tasks SET status='running', progress_percent=10,
                    progress_message='正在整理命题上下文', updated_at=?
                    WHERE task_id=? AND current_round=? AND status='pending'""",
                (now.isoformat(), task_id, round_index),
            )
            await connection.execute(
                """UPDATE ai_task_rounds SET status='running', progress_percent=10,
                    progress_message='正在整理命题上下文'
                    WHERE task_id=? AND round_index=? AND status='pending'""",
                (task_id, round_index),
            )
            return cursor.rowcount == 1

    async def update_progress(self, task_id: str, round_index: int,
                              percent: int, message: str, now: datetime) -> None:
        """仅更新当前 running 轮次，取消后的进度写入会被忽略。"""

        async with self.database.transaction() as connection:
            await connection.execute(
                """UPDATE ai_tasks SET progress_percent=?, progress_message=?, updated_at=?
                    WHERE task_id=? AND current_round=? AND status='running'""",
                (percent, message, now.isoformat(), task_id, round_index),
            )
            await connection.execute(
                """UPDATE ai_task_rounds SET progress_percent=?, progress_message=?
                    WHERE task_id=? AND round_index=? AND status='running'""",
                (percent, message, task_id, round_index),
            )

    async def complete(self, task_id: str, round_index: int, result_json: str,
                       input_tokens: int, output_tokens: int, cost: str,
                       total_cost: str, usage_source: str, now: datetime) -> bool:
        """原子写入本轮结果并累加任务用量；仅 running 可以完成。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """UPDATE ai_tasks SET status='completed', progress_percent=100,
                    progress_message='命题完成', result_json=?, error_info=NULL,
                    input_tokens=input_tokens+?, output_tokens=output_tokens+?,
                    cost=?,
                    usage_source=CASE
                        WHEN current_round=1 THEN ?
                        WHEN usage_source=? THEN usage_source ELSE 'mixed' END,
                    updated_at=?
                    WHERE task_id=? AND current_round=? AND status='running'""",
                (result_json, input_tokens, output_tokens, total_cost, usage_source,
                 usage_source, timestamp, task_id, round_index),
            )
            if cursor.rowcount != 1:
                return False
            await connection.execute(
                """UPDATE ai_task_rounds SET status='completed', progress_percent=100,
                    progress_message='命题完成', result_json=?, input_tokens=?,
                    output_tokens=?, cost=?, usage_source=?, finished_at=?
                    WHERE task_id=? AND round_index=? AND status='running'""",
                (result_json, input_tokens, output_tokens, cost, usage_source,
                 timestamp, task_id, round_index),
            )
            return True

    async def fail(self, task_id: str, round_index: int, message: str,
                   now: datetime) -> None:
        """将仍活动的任务置为失败，公开消息必须已由 Service 脱敏。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            await connection.execute(
                """UPDATE ai_tasks SET status='failed', progress_message='命题失败',
                    error_info=?, updated_at=? WHERE task_id=? AND current_round=?
                    AND status IN ('pending','running')""",
                (message, timestamp, task_id, round_index),
            )
            await connection.execute(
                """UPDATE ai_task_rounds SET status='failed', progress_message='命题失败',
                    finished_at=? WHERE task_id=? AND round_index=?
                    AND status IN ('pending','running')""",
                (timestamp, task_id, round_index),
            )

    async def cancel(self, task_id: str, now: datetime) -> bool:
        """将活动任务及当前轮次原子标为 cancelled。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """UPDATE ai_tasks SET status='cancelled', progress_message='任务已中断',
                    updated_at=? WHERE task_id=? AND status IN ('pending','running')""",
                (timestamp, task_id),
            )
            if cursor.rowcount != 1:
                return False
            await connection.execute(
                """UPDATE ai_task_rounds SET status='cancelled',
                    progress_message='任务已中断', finished_at=? WHERE task_id=?
                    AND round_index=(SELECT current_round FROM ai_tasks WHERE task_id=?)
                    AND status IN ('pending','running')""",
                (timestamp, task_id, task_id),
            )
            return True

    async def fail_stale(self, now: datetime) -> None:
        """进程重启无法恢复 HTTP 请求，因此明确终止遗留活动任务。"""

        timestamp = now.isoformat()
        async with self.database.transaction() as connection:
            await connection.execute(
                """UPDATE ai_task_rounds SET status='failed',
                    progress_message='服务重启，任务已终止', finished_at=?
                    WHERE status IN ('pending','running')""",
                (timestamp,),
            )
            await connection.execute(
                """UPDATE ai_tasks SET status='failed',
                    progress_message='服务重启，任务已终止',
                    error_info='AI task interrupted', updated_at=?
                    WHERE status IN ('pending','running')""",
                (timestamp,),
            )

    @staticmethod
    async def _fetch_task(connection: aiosqlite.Connection,
                          task_id: str) -> aiosqlite.Row | None:
        """复用已有连接查询任务，供事务方法避免嵌套连接。"""

        cursor = await connection.execute(
            "SELECT * FROM ai_tasks WHERE task_id=?", (task_id,)
        )
        return await cursor.fetchone()
