"""使用异步 SQLite 保存提交状态及暂不公开的测试点结果。"""

from datetime import datetime

import aiosqlite

from app.judge.models import JudgeResult
from app.models.submissions import SubmissionRecord, SubmissionStatus
from app.repositories.database import Database


class SubmissionRepository:
    """提供提交状态转换所需的原子数据操作。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _to_submission(row: aiosqlite.Row) -> SubmissionRecord:
        """把数据库行转换为有明确生命周期状态的领域对象。"""

        return SubmissionRecord(
            submission_id=int(row["submission_id"]),
            user_id=int(row["user_id"]),
            problem_id=str(row["problem_id"]),
            language_name=str(row["language_name"]),
            code=str(row["code"]),
            status=SubmissionStatus(row["status"]),
            score=int(row["score"]) if row["score"] is not None else None,
            counts=int(row["counts"]) if row["counts"] is not None else None,
            compile_result=(
                str(row["compile_result"])
                if row["compile_result"] is not None
                else None
            ),
            compile_message=(
                str(row["compile_message"])
                if row["compile_message"] is not None
                else None
            ),
            run_result=str(row["run_result"]) if row["run_result"] is not None else None,
            run_message=str(row["run_message"]) if row["run_message"] is not None else None,
            error_info=str(row["error_info"]) if row["error_info"] is not None else None,
            created_at=str(row["created_at"]),
            finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
        )

    async def count_recent(self, user_id: int, since: datetime) -> int:
        """统计用户在限流窗口内已经接受的提交。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT COUNT(*) AS count FROM submissions "
                "WHERE user_id = ? AND created_at >= ?",
                (user_id, since.isoformat()),
            )
            row = await cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    async def create_pending(
        self,
        user_id: int,
        problem_id: str,
        language_name: str,
        code: str,
        created_at: datetime,
    ) -> SubmissionRecord:
        """创建 pending 任务并同步增加用户提交次数。"""

        async with self.database.transaction() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO submissions(
                    user_id, problem_id, language_name, code, status, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, problem_id, language_name, code, created_at.isoformat()),
            )
            submission_id = cursor.lastrowid
            assert submission_id is not None
            await connection.execute(
                "UPDATE users SET submit_count = submit_count + 1 WHERE user_id = ?",
                (user_id,),
            )
            row = await self._fetch_by_id(connection, int(submission_id))
            assert row is not None
            return self._to_submission(row)

    async def get(self, submission_id: int) -> SubmissionRecord | None:
        """按 ID 查询提交，不存在返回 None。"""

        async with self.database.connection() as connection:
            row = await self._fetch_by_id(connection, submission_id)
        return self._to_submission(row) if row is not None else None

    async def list_submissions(
        self,
        user_id: int | None,
        problem_id: str | None,
        status: SubmissionStatus | None,
        page: int | None,
        page_size: int | None,
    ) -> tuple[int, list[SubmissionRecord]]:
        """按组合条件筛选，返回分页前总数和按 ID 排序的当前页。"""

        clauses: list[str] = []
        parameters: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(user_id)
        if problem_id is not None:
            clauses.append("problem_id = ?")
            parameters.append(problem_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.database.connection() as connection:
            cursor = await connection.execute(
                f"SELECT COUNT(*) AS count FROM submissions{where}", parameters
            )
            count_row = await cursor.fetchone()
            total = int(count_row["count"]) if count_row is not None else 0

            sql = f"SELECT * FROM submissions{where} ORDER BY submission_id"
            list_parameters = list(parameters)
            if page_size is not None:
                resolved_page = page or 1
                sql += " LIMIT ? OFFSET ?"
                list_parameters.extend(
                    (page_size, (resolved_page - 1) * page_size)
                )
            cursor = await connection.execute(sql, list_parameters)
            rows = await cursor.fetchall()
        return total, [self._to_submission(row) for row in rows]

    async def reset_for_rejudge(self, submission_id: int) -> SubmissionRecord:
        """清除旧结果并原子切回 pending，同时修正唯一 AC 的解题计数。"""

        async with self.database.transaction() as connection:
            row = await self._fetch_by_id(connection, submission_id)
            if row is None:
                raise LookupError("submission not found")
            submission = self._to_submission(row)
            if submission.status is SubmissionStatus.PENDING:
                raise ValueError("submission is pending")

            if (
                submission.status is SubmissionStatus.SUCCESS
                and submission.counts is not None
                and submission.counts > 0
                and submission.score == submission.counts
            ):
                cursor = await connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM submissions
                    WHERE user_id = ? AND problem_id = ?
                      AND submission_id <> ? AND status = 'success'
                      AND counts > 0 AND score = counts
                    """,
                    (
                        submission.user_id,
                        submission.problem_id,
                        submission.submission_id,
                    ),
                )
                other_accepts = await cursor.fetchone()
                if other_accepts is not None and int(other_accepts["count"]) == 0:
                    await connection.execute(
                        """
                        UPDATE users
                        SET resolve_count = CASE
                            WHEN resolve_count > 0 THEN resolve_count - 1 ELSE 0
                        END
                        WHERE user_id = ?
                        """,
                        (submission.user_id,),
                    )

            # 旧测试点属于上一次运行；pending 期间不能让 Step 5 读到过期明细。
            await connection.execute(
                "DELETE FROM case_results WHERE submission_id = ?", (submission_id,)
            )
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'pending', score = NULL, counts = NULL,
                    compile_result = NULL, compile_message = NULL,
                    run_result = NULL, run_message = NULL,
                    error_info = NULL, finished_at = NULL
                WHERE submission_id = ?
                """,
                (submission_id,),
            )
            updated = await self._fetch_by_id(connection, submission_id)
            assert updated is not None
            return self._to_submission(updated)

    async def finish_success(
        self, submission_id: int, result: JudgeResult, finished_at: datetime
    ) -> None:
        """在同一事务中写入测试点并把 pending 转成 success。"""

        compile_result = result.compile_info.result if result.compile_info else None
        compile_message = result.compile_info.message if result.compile_info else None
        async with self.database.transaction() as connection:
            await connection.execute(
                "DELETE FROM case_results WHERE submission_id = ?", (submission_id,)
            )
            await connection.executemany(
                """
                INSERT INTO case_results(
                    submission_id, case_index, result, time_seconds, memory_mb
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        submission_id, item.case_index, item.result.value,
                        item.time_seconds, item.memory_mb,
                    )
                    for item in result.cases
                ),
            )
            if result.counts > 0 and result.score == result.counts:
                # resolve_count 按题目计数；同一用户重复 AC 不应重复增加。
                cursor = await connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM submissions AS current
                    WHERE current.submission_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM submissions AS previous
                          WHERE previous.user_id = current.user_id
                            AND previous.problem_id = current.problem_id
                            AND previous.submission_id <> current.submission_id
                            AND previous.status = 'success'
                            AND previous.score = previous.counts
                      )
                    """,
                    (submission_id,),
                )
                first_accept = await cursor.fetchone()
                if first_accept is not None and int(first_accept["count"]) == 1:
                    await connection.execute(
                        """
                        UPDATE users SET resolve_count = resolve_count + 1
                        WHERE user_id = (
                            SELECT user_id FROM submissions WHERE submission_id = ?
                        )
                        """,
                        (submission_id,),
                    )
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'success', score = ?, counts = ?,
                    compile_result = ?, compile_message = ?,
                    run_result = ?, run_message = ?, error_info = '',
                    finished_at = ?
                WHERE submission_id = ? AND status = 'pending'
                """,
                (
                    result.score, result.counts, compile_result, compile_message,
                    result.run_result, result.run_message, finished_at.isoformat(),
                    submission_id,
                ),
            )

    async def finish_error(
        self, submission_id: int, message: str, finished_at: datetime
    ) -> None:
        """把基础设施失败写成可轮询的 error，消息必须由调用方预先脱敏。"""

        async with self.database.transaction() as connection:
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'error', error_info = ?, finished_at = ?
                WHERE submission_id = ? AND status = 'pending'
                """,
                (message, finished_at.isoformat(), submission_id),
            )

    async def fail_stale_pending(self, finished_at: datetime) -> None:
        """应用重启后原任务已丢失，将遗留 pending 转为明确错误。"""

        async with self.database.transaction() as connection:
            await connection.execute(
                """
                UPDATE submissions
                SET status = 'error', error_info = 'evaluation interrupted',
                    finished_at = ?
                WHERE status = 'pending'
                """,
                (finished_at.isoformat(),),
            )

    @staticmethod
    async def _fetch_by_id(
        connection: aiosqlite.Connection, submission_id: int
    ) -> aiosqlite.Row | None:
        """复用连接查询提交，供事务内创建后立即读取。"""

        cursor = await connection.execute(
            "SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)
        )
        return await cursor.fetchone()
