"""系统级 HTTP 接口，包括健康检查和测试环境重置。"""

from fastapi import APIRouter, Request

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
async def reset(request: Request) -> dict[str, object]:
    """重置测试数据库；环境不允许时由服务层抛出 404。"""

    await get_system_service(request).reset()
    return response_body(200, "system reset successfully")
