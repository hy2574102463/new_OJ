"""使用异步 SQLite 持久化可扩展的评测语言配置。"""

import aiosqlite

from app.models.languages import LanguageRecord
from app.repositories.database import Database


class DuplicateLanguageError(Exception):
    """表示大小写无关的语言名称已经存在。"""


class LanguageRepository:
    """读写语言配置，不负责 HTTP 权限或命令执行。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _to_language(row: aiosqlite.Row) -> LanguageRecord:
        """把数据库行转换为内部不可变对象。"""

        return LanguageRecord(
            name=str(row["name"]),
            name_key=str(row["name_key"]),
            file_ext=str(row["file_ext"]),
            compile_cmd=str(row["compile_cmd"]) if row["compile_cmd"] is not None else None,
            run_cmd=str(row["run_cmd"]),
            time_limit=float(row["time_limit"]),
            memory_limit=int(row["memory_limit"]),
            created_by=int(row["created_by"]) if row["created_by"] is not None else None,
        )

    async def ensure_defaults(self) -> None:
        """幂等创建课程要求的 Python 与 C++ 默认配置。"""

        defaults = (
            ("python", "python", ".py", None, "python3 {src}", 1.0, 128),
            ("cpp", "cpp", ".cpp", "g++ {src} -std=c++14 -O2 -o {exe}", "{exe}", 1.0, 128),
        )
        async with self.database.transaction() as connection:
            await connection.executemany(
                """
                INSERT OR IGNORE INTO languages(
                    name, name_key, file_ext, compile_cmd, run_cmd,
                    time_limit, memory_limit, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                defaults,
            )

    async def create(self, language: LanguageRecord) -> None:
        """新增语言；数据库唯一约束负责处理并发重复注册。"""

        try:
            async with self.database.transaction() as connection:
                await connection.execute(
                    """
                    INSERT INTO languages(
                        name, name_key, file_ext, compile_cmd, run_cmd,
                        time_limit, memory_limit, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        language.name, language.name_key, language.file_ext,
                        language.compile_cmd, language.run_cmd, language.time_limit,
                        language.memory_limit, language.created_by,
                    ),
                )
        except aiosqlite.IntegrityError as exc:
            raise DuplicateLanguageError from exc

    async def get(self, name: str) -> LanguageRecord | None:
        """按大小写无关名称查询一种语言。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM languages WHERE name_key = ?", (name.strip().casefold(),)
            )
            row = await cursor.fetchone()
        return self._to_language(row) if row is not None else None

    async def list_all(self) -> list[LanguageRecord]:
        """返回按规范化名称排序的全部语言。"""

        async with self.database.connection() as connection:
            cursor = await connection.execute("SELECT * FROM languages ORDER BY name_key")
            rows = await cursor.fetchall()
        return [self._to_language(row) for row in rows]
