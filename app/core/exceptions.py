"""定义可安全转换成公开 HTTP 响应的应用异常。"""

from typing import Any


class AppError(Exception):
    """携带 HTTP 状态码、公开消息和可选响应数据的业务异常。

    服务层通过抛出该异常中止处理；HTTP 异常处理器负责将它转换为
    ``{code, msg, data}``，调用方不需要在每个路由重复写 try/except。
    """

    def __init__(self, status_code: int, message: str, data: Any = None) -> None:
        """保存允许返回给客户端的错误信息。"""

        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.data = data
