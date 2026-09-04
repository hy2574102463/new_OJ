"""封装用户、Session 和角色审计的 SQLite 数据访问。"""

from datetime import datetime

import aiosqlite

from app.models.users import UserRecord, UserRole
from app.repositories.database import Database


class DuplicateUsernameError(Exception):
    """表示数据库唯一约束拒绝了重复用户名。"""


class UserRepository:
    """通过参数化 SQL 读写用户数据，不处理 HTTP 权限或密码算法。"""

    def __init__(self, database: Database) -> None:
        """保存共享数据库对象，具体连接按操作打开并及时关闭。"""

        self.database = database

    @staticmethod
    def _to_user(row: aiosqlite.Row) -> UserRecord:
        """把 SQLite 行转换成有明确类型的内部用户对象。"""

        return UserRecord(
            user_id=int(row["user_id"]),
            username=str(row["username"]),
            username_key=str(row["username_key"]),
            password_hash=str(row["password_hash"]),
            role=UserRole(row["role"]),
            join_time=str(row["join_time"]),
            submit_count=int(row["submit_count"]),
            resolve_count=int(row["resolve_count"]),
        )

    async def create_user(
        self,
        username: str,
        username_key: str,
        password_hash: str,
        role: UserRole,
        join_time: str,
    ) -> UserRecord:
        """创建用户；并发重复注册由数据库唯一约束最终裁决。"""

        try:
            async with self.database.transaction() as connection:
                cursor = await connection.execute(
                    """
                    INSERT INTO users(username, username_key, password_hash, role, join_time)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (username, username_key, password_hash, role.value, join_time),
                )
                user_id = cursor.lastrowid
                assert user_id is not None
                row = await self._fetch_by_id(connection, int(user_id))
                assert row is not None
                return self._to_user(row)
        except aiosqlite.IntegrityError as exc:
            # Service 把内部冲突转换成 API 文档规定的 400。
            raise DuplicateUsernameError from exc

    async def get_by_id(self, user_id: int) -> UserRecord | None:
        """按数字 ID 查询用户，不存在时返回 None。"""

        async with self.database.connection() as connection:
            row = await self._fetch_by_id(connection, user_id)
        return self._to_user(row) if row is not None else None

    async def get_by_username_key(self, username_key: str) -> UserRecord | None:
        """按规范化用户名查询，使登录和唯一性都不区分大小写。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM users WHERE username_key = ?", (username_key,)
            )
            row = await cursor.fetchone()
        return self._to_user(row) if row is not None else None

    async def create_session(
        self, token_hash: str, user_id: int, created_at: str, expires_at: str
    ) -> None:
        """保存令牌摘要；原始 Cookie 令牌永远不进入数据库。"""

        async with self.database.transaction() as connection:
            # 登录时顺便清理过期记录，避免 Session 表无限增长。
            await connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (created_at,)
            )
            await connection.execute(
                """
                INSERT INTO sessions(token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, created_at, expires_at),
            )

    async def get_by_session(
        self, token_hash: str, now: datetime
    ) -> UserRecord | None:
        """联表查询仍有效的 Session 和对应用户。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.user_id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, now.isoformat()),
            )
            row = await cursor.fetchone()
        return self._to_user(row) if row is not None else None

    async def delete_session(self, token_hash: str) -> None:
        """删除单个 Session，使登出立即在服务端生效。"""

        async with self.database.transaction() as connection:
            await connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
            )

    async def delete_user_sessions(self, user_id: int) -> None:
        """删除一个用户的全部 Session，供封禁操作立即撤销登录。"""

        async with self.database.transaction() as connection:
            await connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    async def list_users(
        self, page: int | None, page_size: int | None
    ) -> tuple[int, list[UserRecord]]:
        """按 ID 稳定排序并返回分页前总数与当前页用户。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute("SELECT COUNT(*) AS count FROM users")
            count_row = await cursor.fetchone()
            total = int(count_row["count"]) if count_row is not None else 0

            sql = "SELECT * FROM users ORDER BY user_id"
            parameters: tuple[int, ...] = ()
            if page_size is not None:
                resolved_page = page or 1
                sql += " LIMIT ? OFFSET ?"
                parameters = (page_size, (resolved_page - 1) * page_size)
            cursor = await connection.execute(sql, parameters)
            rows = await cursor.fetchall()
        return total, [self._to_user(row) for row in rows]

    async def change_role(
        self,
        actor_user_id: int,
        target_user_id: int,
        new_role: UserRole,
        changed_at: str,
    ) -> UserRecord | None:
        """在同一事务中修改角色并写入审计；目标不存在时返回 None。"""

        async with self.database.transaction() as connection:
            row = await self._fetch_by_id(connection, target_user_id)
            if row is None:
                return None
            old_role = UserRole(row["role"])
            await connection.execute(
                "UPDATE users SET role = ? WHERE user_id = ?",
                (new_role.value, target_user_id),
            )
            await connection.execute(
                """
                INSERT INTO user_role_audits(
                    actor_user_id, target_user_id, old_role, new_role, changed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    actor_user_id,
                    target_user_id,
                    old_role.value,
                    new_role.value,
                    changed_at,
                ),
            )
            updated = await self._fetch_by_id(connection, target_user_id)
            assert updated is not None
            return self._to_user(updated)

    @staticmethod
    async def _fetch_by_id(
        connection: aiosqlite.Connection, user_id: int
    ) -> aiosqlite.Row | None:
        """复用已有连接查询用户，避免事务内部重新打开连接。"""

        cursor = await connection.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        return await cursor.fetchone()
