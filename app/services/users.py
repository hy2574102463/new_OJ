"""实现用户资料读取、管理员列表和角色管理规则。"""

from datetime import datetime, timezone

from app.core.exceptions import AppError
from app.models.users import UserRecord, UserRole
from app.repositories.users import UserRepository
from app.services.auth import AuthService


class UserService:
    """协调认证服务和用户仓库，并执行资源级权限判断。"""

    def __init__(self, auth: AuthService, users: UserRepository) -> None:
        """注入认证服务以复用安全的用户创建流程。"""

        self.auth = auth
        self.users = users

    async def create_admin(self, username: str, password: str) -> UserRecord:
        """创建 admin 角色用户；调用者身份由路由依赖提前验证。"""

        return await self.auth.register(username, password, role=UserRole.ADMIN)

    async def get_user(self, actor: UserRecord, target_user_id: int) -> UserRecord:
        """只允许本人或管理员查询，并在权限通过后判断资源是否存在。"""

        if actor.role is not UserRole.ADMIN and actor.user_id != target_user_id:
            raise AppError(403, "permission denied")
        user = await self.users.get_by_id(target_user_id)
        if user is None:
            raise AppError(404, "user not found")
        return user

    async def list_users(
        self, page: int | None, page_size: int | None
    ) -> tuple[int, list[UserRecord]]:
        """校验课程分页组合，并从仓库取得稳定排序结果。"""

        if page is not None and page_size is None:
            raise AppError(400, "page_size is required when page is provided")
        return await self.users.list_users(page, page_size)

    async def change_role(
        self, actor: UserRecord, target_user_id: int, new_role: UserRole
    ) -> UserRecord:
        """禁止管理员修改自己，并原子更新目标角色与审计记录。"""

        if actor.user_id == target_user_id:
            raise AppError(409, "cannot change your own role")
        updated = await self.users.change_role(
            actor.user_id,
            target_user_id,
            new_role,
            datetime.now(timezone.utc).isoformat(),
        )
        if updated is None:
            raise AppError(404, "user not found")
        if new_role is UserRole.BANNED:
            # 清除全部 Session，让封禁对已经登录的浏览器也立即生效。
            await self.users.delete_user_sessions(target_user_id)
        return updated
