"""FastAPI 应用入口，负责组装配置、数据库、服务和路由。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.system import router as system_router
from app.api.users import router as users_router
from app.core.config import Settings, get_settings
from app.core.http import register_http_conventions
from app.core.logging import configure_logging
from app.repositories.database import Database
from app.repositories.users import UserRepository
from app.services.auth import AuthService
from app.services.system import SystemService
from app.services.users import UserService


def create_app(settings: Settings | None = None) -> FastAPI:
    """根据配置创建一个彼此隔离的 FastAPI 应用实例。

    正常启动使用环境配置；测试传入 ``Settings``，即可把数据库指向临时
    目录。函数只负责依赖组装，不在这里实现具体业务规则。
    """

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    database = Database(resolved_settings.database_path)
    user_repository = UserRepository(database)
    auth_service = AuthService(resolved_settings, user_repository)
    user_service = UserService(auth_service, user_repository)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """在接收请求前初始化数据库，关闭服务时退出上下文。"""

        # await 是异步边界：迁移完成前服务不会开始接收业务请求。
        await database.initialize()
        await auth_service.ensure_initial_admin()
        yield

    application = FastAPI(title="OJ API", version="0.1.0", lifespan=lifespan)
    # 共享对象只创建一次，再通过 app.state 提供给路由依赖。
    application.state.settings = resolved_settings
    application.state.database = database
    application.state.auth_service = auth_service
    application.state.user_service = user_service
    application.state.system_service = SystemService(
        resolved_settings, database, auth_service
    )
    register_http_conventions(application)
    application.include_router(system_router)
    application.include_router(auth_router)
    application.include_router(users_router)
    return application


# Uvicorn 使用 ``app.main:app`` 导入这个默认应用实例。
app = create_app()
