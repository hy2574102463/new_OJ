"""使用异步 SQLite 持久化并查询日志访问审计。"""

from datetime import datetime

import aiosqlite

from app.models.logs import LogAccessAudit
from app.repositories.database import Database


class LogRepository:
    """提供单条审计写入以及管理员筛选读取。"""

    def __init__(self, database: Database) -> None:
        """注入共享数据库，保持持久化细节不进入 Service。"""

        self.database = database

    @staticmethod
    def _to_audit(row: aiosqlite.Row) -> LogAccessAudit:
        """把 SQLite 行转换成不携带数据库对象的领域记录。"""

        return LogAccessAudit(
            audit_id=int(row["audit_id"]),
            user_id=int(row["user_id"]),
            problem_id=str(row["problem_id"]),
            action=str(row["action"]),
            accessed_at=str(row["accessed_at"]),
            status=str(row["status"]),
        )

    async def create_access_audit(
        self,
        user_id: int,
        problem_id: str,
        accessed_at: datetime,
        status: str,
    ) -> None:
        """原子写入一次 view_logs 访问；数据库约束拒绝未知状态。"""

        # 使用事务提交审计，写入失败时不留下不完整记录。
        async with self.database.transaction() as connection:
            await connection.execute(
                """
                INSERT INTO log_access_audits(
                    user_id, problem_id, action, accessed_at, status
                ) VALUES (?, ?, 'view_logs', ?, ?)
                """,
                (user_id, problem_id, accessed_at.isoformat(), status),
            )

    async def list_access_audits(
        self,
        user_id: int | None,
        problem_id: str | None,
        page: int | None,
        page_size: int | None,
    ) -> list[LogAccessAudit]:
        """按可选用户、题目和分页条件查询，结果按审计 ID 升序。"""

        clauses: list[str] = []
        parameters: list[object] = []
        if user_id is not None:
            clauses.append("user_id = ?")
            parameters.append(user_id)
        if problem_id is not None:
            clauses.append("problem_id = ?")
            parameters.append(problem_id)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM log_access_audits{where} ORDER BY audit_id"
        if page_size is not None:
            resolved_page = page or 1
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((page_size, (resolved_page - 1) * page_size))

        async with self.database.connection() as connection:
            cursor = await connection.execute(sql, parameters)
            rows = await cursor.fetchall()
        return [self._to_audit(row) for row in rows]
