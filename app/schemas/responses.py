"""定义所有接口共同使用的 ``code/msg/data`` 响应信封。"""

from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """课程 API 契约规定的统一 JSON 响应模型。"""

    code: int
    msg: str
    data: Any = None


def response_body(code: int, msg: str, data: Any = None) -> dict[str, Any]:
    """校验响应字段并转换为可由 FastAPI 序列化的字典。"""

    return ApiResponse(code=code, msg=msg, data=data).model_dump()
