"""定义可复用于所有受保护路由的认证与管理员依赖。"""

from fastapi import Depends, Request

from app.core.exceptions import AppError
from app.models.users import UserRecord, UserRole
from app.services.auth import AuthService


def get_auth_service(request: Request) -> AuthService:
    """从应用状态取得认证服务，避免每个请求重新构造依赖图。"""

    return request.app.state.auth_service


async def get_current_user(request: Request) -> UserRecord:
    """读取 Session Cookie 并恢复身份；失败时传播 401。"""

    token = request.cookies.get(request.app.state.settings.session_cookie_name)
    return await get_auth_service(request).authenticate(token)


async def require_admin(
    current_user: UserRecord = Depends(get_current_user),
) -> UserRecord:
    """在已登录基础上要求 admin 角色，否则返回 403。"""

    if current_user.role is not UserRole.ADMIN:
        raise AppError(403, "permission denied")
    return current_user
