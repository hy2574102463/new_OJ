"""认证 HTTP 接口：登录创建 Cookie，登出撤销服务端 Session。"""

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import get_current_user
from app.models.users import UserRecord
from app.schemas.responses import response_body
from app.schemas.users import UserCredentials

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
async def login(
    credentials: UserCredentials, request: Request, response: Response
) -> dict[str, object]:
    """校验用户名和密码，创建 Session，并把原始令牌放入安全 Cookie。"""

    result = await request.app.state.auth_service.login(
        credentials.username, credentials.password
    )
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return response_body(
        200,
        "login success",
        {
            "user_id": str(result.user.user_id),
            "username": result.user.username,
            "role": result.user.role.value,
        },
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    _current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """撤销当前服务端 Session，并通知浏览器立即删除 Cookie。"""

    settings = request.app.state.settings
    token = request.cookies[settings.session_cookie_name]
    await request.app.state.auth_service.logout(token)
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return response_body(200, "logout success")
