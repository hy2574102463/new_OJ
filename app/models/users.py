"""定义服务层和持久化层之间传递的用户领域对象。"""

from dataclasses import dataclass
from enum import Enum


class UserRole(str, Enum):
    """系统允许的三种角色；继承 str 便于写入数据库和 JSON。"""

    USER = "user"
    ADMIN = "admin"
    BANNED = "banned"


@dataclass(frozen=True)
class UserRecord:
    """数据库中的完整用户记录，只能在后端内部流转。

    ``password_hash`` 不能直接交给响应层；API 必须通过用户响应 schema
    挑选可公开字段。
    """

    user_id: int
    username: str
    username_key: str
    password_hash: str
    role: UserRole
    join_time: str
    submit_count: int
    resolve_count: int
