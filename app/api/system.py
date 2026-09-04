"""系统级 HTTP 接口，包括健康检查和测试环境重置。"""

from fastapi import APIRouter, Request, Response

from app.schemas.responses import response_body
from app.services.system import SystemService

# 路由对象只声明 HTTP 契约，具体规则交给 SystemService。
router = APIRouter()


def get_system_service(request: Request) -> SystemService:
    """从应用状态中取得启动时创建的系统服务实例。"""

    return request.app.state.system_service


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    """检查数据库可用性，并返回统一格式的健康状态。"""

    data = await get_system_service(request).health()
    return response_body(200, "success", data)


@router.post("/api/reset/")
async def reset(request: Request, response: Response) -> dict[str, object]:
    """重置测试数据库并清除当前 Cookie；环境不允许时抛出 404。"""

    await get_system_service(request).reset()
    settings = request.app.state.settings
    # 数据库中的 Session 已被删除，同时清 Cookie 避免浏览器继续发送旧令牌。
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/",
    )
    return response_body(200, "system reset successfully")
