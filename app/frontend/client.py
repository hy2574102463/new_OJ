"""同步 FastAPI 客户端：保存 Cookie、验证响应信封并隐藏网络细节。

本模块不依赖 Streamlit，因此可以独立测试。页面负责展示错误，客户端只把各种
HTTP/网络失败归一成 :class:`ApiError`，绝不把响应正文、Cookie 或请求体写入日志。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_TIMEOUT = (3.0, 15.0)


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    """页面可安全展示的 API 错误，包含状态码和中文提示。"""

    status_code: int | None
    message: str

    def __str__(self) -> str:
        """只返回整理后的提示，不暴露原始异常或响应正文。"""

        return self.message


ERROR_MESSAGES = {
    400: "请求参数不正确，请检查表单内容。",
    401: "登录已失效，请重新登录。",
    403: "当前账户没有执行此操作的权限。",
    404: "请求的资源不存在。",
    409: "操作与当前数据状态冲突，请刷新后重试。",
    429: "操作过于频繁，请稍后重试。",
    500: "服务暂时不可用，请稍后重试。",
    503: "AI 模型尚未配置或服务暂时不可用。",
}


class ApiClient:
    """使用一个 ``requests.Session`` 调用 OJ API。

    ``session`` 应由一个 Streamlit 浏览器会话独占，这样后端设置的 HttpOnly Cookie
    会自动随之后请求发送。``on_unauthorized`` 在 401 时清理页面登录状态。
    """

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        on_unauthorized: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout
        self.on_unauthorized = on_unauthorized

    @staticmethod
    def path_segment(value: str) -> str:
        """把用户控制的 ID 编码成单个 URL 路径段，避免斜杠改变路由。"""

        return quote(value, safe="")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """发送请求并返回信封中的 ``data``；失败时抛出安全的 ``ApiError``。"""

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
                timeout=self.timeout,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ApiError(None, "无法连接后端服务，请确认 FastAPI 已启动。") from exc
        except requests.RequestException as exc:
            raise ApiError(None, "请求后端服务失败，请稍后重试。") from exc

        try:
            body = response.json()
        except (requests.JSONDecodeError, ValueError) as exc:
            raise ApiError(response.status_code, "后端返回了无法识别的响应。") from exc

        if not isinstance(body, dict) or not {"code", "msg", "data"} <= body.keys():
            raise ApiError(response.status_code, "后端响应格式不符合约定。")
        if not isinstance(body["code"], int) or body["code"] != response.status_code:
            raise ApiError(response.status_code, "后端响应状态不一致。")

        if response.status_code == 401:
            # Cookie 和页面身份必须一起失效，否则界面会呈现一个已无权限的幽灵会话。
            self.session.cookies.clear()
            if self.on_unauthorized is not None:
                self.on_unauthorized()
        if not 200 <= response.status_code < 300:
            message = ERROR_MESSAGES.get(
                response.status_code, "后端拒绝了本次操作，请稍后重试。"
            )
            raise ApiError(response.status_code, message)
        return body["data"]

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """发送 GET 请求。"""

        return self.request("GET", path, params=params)

    def post(self, path: str, *, json: Any = None) -> Any:
        """发送 POST 请求。"""

        return self.request("POST", path, json=json)

    def put(self, path: str, *, json: Any = None) -> Any:
        """发送 PUT 请求。"""

        return self.request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        """发送 DELETE 请求。"""

        return self.request("DELETE", path)
