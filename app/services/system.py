"""系统级业务规则，负责健康检查和受保护的测试重置。"""

from app.core.config import Settings
from app.core.exceptions import AppError
from app.repositories.database import Database
from app.repositories.problems import ProblemRepository
from app.services.auth import AuthService


class SystemService:
    """协调系统配置与数据库，不依赖 FastAPI 的 Request/Response。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        auth: AuthService,
        problems: ProblemRepository,
    ) -> None:
        """注入配置、数据库、认证服务和题库，统一执行测试重置。"""

        self.settings = settings
        self.database = database
        self.auth = auth
        self.problems = problems

    async def health(self) -> dict[str, str]:
        """确认数据库可查询，并返回供健康接口展示的稳定数据。"""

        await self.database.ping()
        return {"status": "healthy", "database": "ok"}

    async def reset(self) -> None:
        """只允许显式启用的测试环境重置，否则以 404 隐藏接口。"""

        # 两个条件同时成立才能执行破坏性操作，防止配置失误清空真实数据。
        if self.settings.environment != "test" or not self.settings.test_reset_enabled:
            raise AppError(status_code=404, message="not found")
        await self.problems.reset()
        await self.database.reset()
        # reset 删除全部业务表数据，因此必须恢复课程指定的初始管理员。
        await self.auth.ensure_initial_admin()
