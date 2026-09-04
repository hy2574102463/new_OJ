"""系统级业务规则，负责健康检查和受保护的测试重置。"""

from app.core.config import Settings
from app.core.exceptions import AppError
from app.repositories.database import Database


class SystemService:
    """协调系统配置与数据库，不依赖 FastAPI 的 Request/Response。"""

    def __init__(self, settings: Settings, database: Database) -> None:
        """注入运行配置和数据库，便于测试替换为临时实例。"""

        self.settings = settings
        self.database = database

    async def health(self) -> dict[str, str]:
        """确认数据库可查询，并返回供健康接口展示的稳定数据。"""

        await self.database.ping()
        return {"status": "healthy", "database": "ok"}

    async def reset(self) -> None:
        """只允许显式启用的测试环境重置，否则以 404 隐藏接口。"""

        # 两个条件同时成立才能执行破坏性操作，防止配置失误清空真实数据。
        if self.settings.environment != "test" or not self.settings.test_reset_enabled:
            raise AppError(status_code=404, message="not found")
        await self.database.reset()
