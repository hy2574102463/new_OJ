"""实现注册、密码验证、Session 生命周期和初始管理员创建。"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    hash_password,
    new_session_token,
    session_token_hash,
    verify_password,
)
from app.models.users import UserRecord, UserRole
from app.repositories.users import DuplicateUsernameError, UserRepository


@dataclass(frozen=True)
class LoginResult:
    """登录成功后的内部结果，包含用户和仅返回给 Cookie 的原始令牌。"""

    user: UserRecord
    token: str


class AuthService:
    """集中认证规则，避免路由直接处理哈希、时间或数据库异常。"""

    def __init__(self, settings: Settings, users: UserRepository) -> None:
        """注入配置与用户仓库；Session 期限来自配置。"""

        self.settings = settings
        self.users = users

    @staticmethod
    def normalize_username(username: str) -> tuple[str, str]:
        """返回保留大小写的显示名和用于唯一查询的 casefold 键。"""

        display_name = username.strip()
        return display_name, display_name.casefold()

    async def register(
        self, username: str, password: str, role: UserRole = UserRole.USER
    ) -> UserRecord:
        """哈希密码并创建用户；大小写重复用户名转换为公开 400。"""

        display_name, username_key = self.normalize_username(username)
        password_hash = await hash_password(password)
        try:
            return await self.users.create_user(
                username=display_name,
                username_key=username_key,
                password_hash=password_hash,
                role=role,
                join_time=datetime.now(timezone.utc).isoformat(),
            )
        except DuplicateUsernameError as exc:
            raise AppError(400, "username already exists") from exc

    async def ensure_initial_admin(self) -> UserRecord:
        """幂等创建课程指定管理员，不覆盖已存在账号的密码或角色。"""

        existing = await self.users.get_by_username_key("admin")
        if existing is not None:
            return existing
        try:
            return await self.register(
                "admin", "admintestpassword", role=UserRole.ADMIN
            )
        except AppError:
            # 多实例同时启动时，唯一约束可能让其中一个创建失败；重新查询即可。
            existing = await self.users.get_by_username_key("admin")
            if existing is None:
                raise
            return existing

    async def login(self, username: str, password: str) -> LoginResult:
        """验证凭据、拒绝禁用用户，并创建 24 小时服务端 Session。"""

        _, username_key = self.normalize_username(username)
        user = await self.users.get_by_username_key(username_key)
        if user is None or not await verify_password(password, user.password_hash):
            # 用户不存在与密码错误使用相同消息，避免帮助攻击者枚举账号。
            raise AppError(401, "invalid username or password")
        if user.role is UserRole.BANNED:
            raise AppError(403, "user is banned")

        token = new_session_token()
        created_at = datetime.now(timezone.utc)
        expires_at = created_at + timedelta(seconds=self.settings.session_ttl_seconds)
        await self.users.create_session(
            session_token_hash(token),
            user.user_id,
            created_at.isoformat(),
            expires_at.isoformat(),
        )
        return LoginResult(user=user, token=token)

    async def authenticate(self, token: str | None) -> UserRecord:
        """由 Cookie 恢复当前用户；无效、过期或已封禁统一视为未登录。"""

        if not token:
            raise AppError(401, "authentication required")
        token_hash = session_token_hash(token)
        user = await self.users.get_by_session(token_hash, datetime.now(timezone.utc))
        if user is None:
            raise AppError(401, "authentication required")
        if user.role is UserRole.BANNED:
            await self.users.delete_session(token_hash)
            raise AppError(401, "authentication required")
        return user

    async def logout(self, token: str) -> None:
        """删除当前 Session；调用前已由认证依赖确认令牌有效。"""

        await self.users.delete_session(session_token_hash(token))
