from pathlib import Path

import pytest

from app.repositories.database import MIGRATIONS, Database


@pytest.mark.asyncio
async def test_initialize_applies_each_migration_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "nested" / "oj.db")

    await database.initialize()
    await database.initialize()

    async with database.connection() as connection:
        cursor = await connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        )
        rows = await cursor.fetchall()

    assert [(row["version"], row["name"]) for row in rows] == [
        (migration.version, migration.name) for migration in MIGRATIONS
    ]


@pytest.mark.asyncio
async def test_existing_p0_database_upgrades_to_user_schema(tmp_path: Path) -> None:
    """已有 migration 1 的数据库启动时应只补做新的用户迁移。"""

    database = Database(tmp_path / "oj.db")
    async with database.connection() as connection:
        await connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        await connection.execute(
            "INSERT INTO schema_migrations VALUES (1, 'bootstrap', '2026-09-04')"
        )
        await connection.commit()

    await database.initialize()

    async with database.connection() as connection:
        cursor = await connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        versions = [row["version"] for row in await cursor.fetchall()]
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        )
        users_table = await cursor.fetchone()

    assert versions == [1, 2]
    assert users_table is not None


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    database = Database(tmp_path / "oj.db")
    await database.initialize()
    async with database.connection() as connection:
        await connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        await connection.commit()

    with pytest.raises(RuntimeError, match="stop transaction"):
        async with database.transaction() as connection:
            await connection.execute("INSERT INTO sample(value) VALUES ('kept?')")
            raise RuntimeError("stop transaction")

    async with database.connection() as connection:
        cursor = await connection.execute("SELECT COUNT(*) AS count FROM sample")
        row = await cursor.fetchone()

    assert row is not None
    assert row["count"] == 0


@pytest.mark.asyncio
async def test_reset_removes_application_tables_and_reapplies_migrations(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "oj.db")
    await database.initialize()
    async with database.connection() as connection:
        await connection.execute("CREATE TABLE temporary_business_data (id INTEGER)")
        await connection.commit()

    await database.reset()

    async with database.connection() as connection:
        cursor = await connection.execute(
            # sqlite_sequence 是 AUTOINCREMENT 自动维护的内部表，不属于应用结构。
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        names = [row["name"] for row in await cursor.fetchall()]

    assert names == [
        "schema_migrations",
        "sessions",
        "user_role_audits",
        "users",
    ]
