"""定义用户接口的请求校验和公开响应结构。"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.users import UserRecord, UserRole


class UserCredentials(BaseModel):
    """注册、登录和创建管理员共同使用的用户名密码输入。"""

    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6)

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value: Any) -> Any:
        """校验长度前去除用户名首尾空白；密码有意不做 trim。"""

        return value.strip() if isinstance(value, str) else value


class RoleUpdate(BaseModel):
    """管理员修改角色时允许的请求体。"""

    role: UserRole


class UserPublic(BaseModel):
    """可以返回给客户端的用户字段，不包含密码和 Session。"""

    user_id: str
    username: str
    join_time: str
    role: UserRole
    submit_count: int
    resolve_count: int

    @classmethod
    def from_record(cls, user: UserRecord) -> "UserPublic":
        """从内部记录中显式挑选公开字段，并把 ID 转成 API 要求的字符串。"""

        return cls(
            user_id=str(user.user_id),
            username=user.username,
            join_time=user.join_time[:10],
            role=user.role,
            submit_count=user.submit_count,
            resolve_count=user.resolve_count,
        )


def public_user_data(user: UserRecord) -> dict[str, Any]:
    """生成适合放入统一响应 ``data`` 的公开用户字典。"""

    return UserPublic.from_record(user).model_dump(mode="json")
