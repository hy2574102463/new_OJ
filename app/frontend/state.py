"""管理 Streamlit 会话中的前端身份；不负责后端 Session 的有效性判断。"""

import os
from collections.abc import MutableMapping
from typing import Any

import requests

try:
    from .client import ApiClient, ApiError
except ImportError:  # Streamlit 入口以脚本方式运行，不具有 Python 包上下文。
    from client import ApiClient, ApiError


AUTH_KEY = "current_user"
CLIENT_KEY = "api_client"
PAGE_STATE_KEYS = (
    "selected_problem_id",
    "active_submission_id",
    "active_submission_pending",
    "selected_submission_id",
)


def clear_login_state(state: MutableMapping[str, Any]) -> None:
    """删除前端身份和私有页面选择；调用者仍需清理客户端 Cookie。"""

    state.pop(AUTH_KEY, None)
    for key in PAGE_STATE_KEYS:
        state.pop(key, None)


def get_api_client(state: MutableMapping[str, Any]) -> ApiClient:
    """为当前浏览器会话创建或复用唯一客户端及 Cookie 容器。"""

    existing = state.get(CLIENT_KEY)
    if isinstance(existing, ApiClient):
        return existing

    def handle_unauthorized() -> None:
        clear_login_state(state)

    client = ApiClient(
        os.getenv("OJ_API_BASE_URL", "http://127.0.0.1:8000"),
        session=requests.Session(),
        on_unauthorized=handle_unauthorized,
    )
    state[CLIENT_KEY] = client
    return client


def login(state: MutableMapping[str, Any], username: str, password: str) -> dict[str, Any]:
    """调用登录接口并保存其公开身份字段；校验错误直接向页面传播。"""

    user = get_api_client(state).post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    if not isinstance(user, dict):
        raise ApiError(200, "登录响应缺少用户资料。")
    state[AUTH_KEY] = user
    return user


def logout(state: MutableMapping[str, Any]) -> bool:
    """尝试撤销后端 Session，并始终清除本地 Cookie 与页面状态。

    返回 ``False`` 表示网络失败或后端未确认登出，此时服务器 Session 可能持续到过期。
    """

    client = get_api_client(state)
    confirmed = True
    try:
        client.post("/api/auth/logout")
    except ApiError:
        confirmed = False
    finally:
        # 即使后端不可达也不能让共享屏幕继续显示上一位用户的数据。
        client.session.cookies.clear()
        clear_login_state(state)
    return confirmed
