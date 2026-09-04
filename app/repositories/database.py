"""提供异步 SQLite 连接、事务、迁移和测试重置能力。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class Migration:
    """描述一个只应执行一次的数据库结构版本。

    ``version`` 决定执行顺序，``name`` 便于审计，``statements`` 保存该
    版本的 SQL。冻结 dataclass 可以防止运行时意外修改迁移定义。
    """

    version: int
    name: str
    statements: tuple[str, ...] = ()


# 已发布迁移只允许追加，不能改写，否则旧数据库和新数据库会得到不同结构。
MIGRATIONS = (
    Migration(version=1, name="bootstrap"),
    Migration(
        version=2,
        name="add_users_and_sessions",
        statements=(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin', 'banned')),
                join_time TEXT NOT NULL,
                submit_count INTEGER NOT NULL DEFAULT 0 CHECK (submit_count >= 0),
                resolve_count INTEGER NOT NULL DEFAULT 0 CHECK (resolve_count >= 0)
            )
            """,
            """
            CREATE TABLE sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """,
            "CREATE INDEX sessions_user_id_idx ON sessions(user_id)",
            "CREATE INDEX sessions_expires_at_idx ON sessions(expires_at)",
            """
            CREATE TABLE user_role_audits (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER NOT NULL,
                target_user_id INTEGER NOT NULL,
                old_role TEXT NOT NULL,
                new_role TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY (actor_user_id) REFERENCES users(user_id),
                FOREIGN KEY (target_user_id) REFERENCES users(user_id)
            )
            """,
        ),
    ),
)


class Database:
    """管理指定 SQLite 文件的连接生命周期和结构版本。"""

    def __init__(self, path: Path) -> None:
        """保存数据库路径；真正连接推迟到异步方法中创建。"""

        self.path = path

    async def initialize(self) -> None:
        """创建数据库目录，并以幂等方式应用尚未执行的迁移。"""

        # Path.mkdir 是同步文件操作，因此交给工作线程，避免阻塞事件循环。
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        async with self.connection() as connection:
            await self._apply_migrations(connection)
            await connection.commit()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """打开已配置的连接，并保证离开上下文时总能关闭它。

        调用方使用 ``async with`` 获取连接；SQL 错误会继续向上传播，但
        ``finally`` 仍会释放文件描述符和 aiosqlite 工作线程。
        """

        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        # 外键保证引用完整性；busy_timeout 让短暂写锁等待而非立即失败。
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        # WAL 允许读取与单个写入更好地并行，适合 Web 请求模式。
        await connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            await connection.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """提供自动提交或回滚的异步事务上下文。

        上下文内代码正常结束就提交；任何异常或任务取消都会回滚并继续
        向上抛出，避免业务层误以为部分写入已经成功。
        """

        async with self.connection() as connection:
            await connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    async def ping(self) -> None:
        """执行最小查询；连接或查询失败时让异常交给 HTTP 层处理。"""

        async with self.connection() as connection:
            cursor = await connection.execute("SELECT 1")
            await cursor.fetchone()

    async def reset(self) -> None:
        """删除所有应用表并重新应用迁移，整个过程保持原子性。

        此方法本身不知道当前环境；是否允许重置由 ``SystemService`` 在
        调用前判断。失败时回滚，防止数据库只删除了一部分表。
        """

        async with self.connection() as connection:
            # 删除有关联的表前暂时关闭外键检查，提交后立即恢复。
            await connection.execute("PRAGMA foreign_keys = OFF")
            await connection.execute("BEGIN")
            try:
                cursor = await connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
                tables = await cursor.fetchall()
                for row in tables:
                    # 表名来自 sqlite_master，但仍转义双引号以安全构造标识符。
                    table_name = str(row["name"]).replace('"', '""')
                    await connection.execute(f'DROP TABLE "{table_name}"')
                await self._apply_migrations(connection)
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()
            finally:
                await connection.execute("PRAGMA foreign_keys = ON")

    async def _apply_migrations(self, connection: aiosqlite.Connection) -> None:
        """创建迁移记录表，并按版本执行尚未记录的 SQL。"""

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        cursor = await connection.execute("SELECT version FROM schema_migrations")
        applied = {int(row["version"]) for row in await cursor.fetchall()}

        # 跳过已记录版本使 initialize 可以在每次启动时安全调用。
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            for statement in migration.statements:
                await connection.execute(statement)
            await connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
