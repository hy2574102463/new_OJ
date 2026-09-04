"""用户 HTTP 接口：注册、资料查询、列表、管理员创建和角色修改。"""

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_current_user, require_admin
from app.models.users import UserRecord
from app.schemas.responses import response_body
from app.schemas.users import RoleUpdate, UserCredentials, public_user_data

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/")
async def register(
    credentials: UserCredentials, request: Request
) -> dict[str, object]:
    """注册普通用户，并返回不含密码和 Session 的公开资料。"""

    user = await request.app.state.auth_service.register(
        credentials.username, credentials.password
    )
    return response_body(200, "register success", public_user_data(user))


@router.post("/admin")
async def create_admin(
    credentials: UserCredentials,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """由管理员创建另一个管理员账户。"""

    user = await request.app.state.user_service.create_admin(
        credentials.username, credentials.password
    )
    return response_body(
        200,
        "success",
        {"user_id": str(user.user_id), "username": user.username},
    )


@router.get("/")
async def list_users(
    request: Request,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1),
    _admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """由管理员查询全部用户或指定分页，结果按用户 ID 排序。"""

    total, users = await request.app.state.user_service.list_users(page, page_size)
    return response_body(
        200,
        "success",
        {"total": total, "users": [public_user_data(user) for user in users]},
    )


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """查询本人资料；管理员可以查询任意存在的用户。"""

    user = await request.app.state.user_service.get_user(current_user, user_id)
    return response_body(200, "success", public_user_data(user))


@router.put("/{user_id}/role")
async def change_role(
    user_id: int,
    update: RoleUpdate,
    request: Request,
    admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """由管理员更新其他用户角色，并在数据库写入审计记录。"""

    user = await request.app.state.user_service.change_role(admin, user_id, update.role)
    return response_body(
        200,
        "role updated",
        {"user_id": str(user.user_id), "role": user.role.value},
    )
