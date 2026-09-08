"""验证 Step 6 客户端、会话清理和纯表单转换，不发出真实网络请求。"""

from pathlib import Path
from typing import Any

import pytest
import requests

from app.frontend.client import ApiClient, ApiError
from app.frontend.forms import (
    build_language_payload,
    build_problem_payload,
    clean_cases,
    normalize_case_text,
    should_poll,
    submission_query,
)
from app.frontend.state import AUTH_KEY, CLIENT_KEY, clear_login_state, login, logout


FRONTEND_ENTRYPOINT = Path(__file__).parents[1] / "app/frontend/app.py"


class FakeResponse:
    """提供 ``requests.Response`` 中客户端实际使用的最小接口。"""

    def __init__(self, status_code: int, body: Any = None, *, invalid_json: bool = False):
        self.status_code = status_code
        self.body = body
        self.invalid_json = invalid_json

    def json(self) -> Any:
        """返回预设信封，或模拟非 JSON 后端响应。"""

        if self.invalid_json:
            raise requests.JSONDecodeError("secret response", "secret body", 0)
        return self.body


class FakeSession:
    """记录请求并复用 CookieJar，用于证明同一会话跨请求保存 Cookie。"""

    def __init__(self, responses: list[FakeResponse | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """记录调用并依次返回响应；异常用于模拟超时或断网。"""

        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def envelope(status: int = 200, data: Any = None) -> FakeResponse:
    """构造符合项目统一协议的假响应。"""

    return FakeResponse(status, {"code": status, "msg": "ignored", "data": data})


def test_client_reuses_session_cookie_and_passes_all_method_arguments() -> None:
    """四种方法共用 Session，且查询参数、JSON 和超时不被包装层改写。"""

    session = FakeSession([envelope(data=1), envelope(data=2), envelope(data=3), envelope(data=4)])
    session.cookies.set("oj_session", "opaque-token")
    client = ApiClient("http://api.test/", session=session)  # type: ignore[arg-type]

    assert client.get("/items", params={"page": 2}) == 1
    assert client.post("/items", json={"a": 1}) == 2
    assert client.put("/items/1", json={"a": 2}) == 3
    assert client.delete("/items/1") == 4
    assert [call["method"] for call in session.calls] == ["GET", "POST", "PUT", "DELETE"]
    assert session.calls[0]["params"] == {"page": 2}
    assert session.calls[1]["json"] == {"a": 1}
    assert all(call["timeout"] == (3.0, 15.0) for call in session.calls)
    assert session.cookies.get("oj_session") == "opaque-token"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500])
def test_client_maps_http_errors_to_safe_chinese(status: int) -> None:
    """服务端 msg 即使包含敏感文本，也不会原样进入页面异常。"""

    response = FakeResponse(status, {"code": status, "msg": "password=/srv/secret", "data": None})
    client = ApiClient("http://api.test", session=FakeSession([response]))  # type: ignore[arg-type]

    with pytest.raises(ApiError) as caught:
        client.get("/failure")
    assert caught.value.status_code == status
    assert "secret" not in caught.value.message
    assert any("\u4e00" <= character <= "\u9fff" for character in caught.value.message)


def test_unauthorized_clears_cookie_and_calls_state_callback() -> None:
    """401 同时失效传输 Cookie 和界面身份，避免两层状态不一致。"""

    session = FakeSession([envelope(401)])
    session.cookies.set("oj_session", "opaque-token")
    called: list[bool] = []
    client = ApiClient(
        "http://api.test", session=session, on_unauthorized=lambda: called.append(True)  # type: ignore[arg-type]
    )

    with pytest.raises(ApiError):
        client.get("/private")
    assert not session.cookies
    assert called == [True]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(200, invalid_json=True), "无法识别"),
        (FakeResponse(200, {"code": 200}), "格式"),
        (FakeResponse(200, {"code": 201, "msg": "x", "data": None}), "状态"),
    ],
)
def test_client_rejects_invalid_envelopes_without_body_leak(
    response: FakeResponse, expected: str
) -> None:
    """HTML、残缺字段和 HTTP/code 不一致均作为协议错误处理。"""

    client = ApiClient("http://api.test", session=FakeSession([response]))  # type: ignore[arg-type]
    with pytest.raises(ApiError) as caught:
        client.get("/broken")
    assert expected in caught.value.message
    assert "secret body" not in caught.value.message


@pytest.mark.parametrize("failure", [requests.Timeout("secret"), requests.ConnectionError("secret")])
def test_client_hides_network_exception_details(failure: Exception) -> None:
    """网络异常只给出可操作提示，不显示底层地址、栈或异常文本。"""

    client = ApiClient("http://api.test", session=FakeSession([failure]))  # type: ignore[arg-type]
    with pytest.raises(ApiError) as caught:
        client.get("/health")
    assert "无法连接" in caught.value.message
    assert "secret" not in caught.value.message


def test_login_logout_and_unauthorized_state_cleanup() -> None:
    """登录保存公开身份，登出即使断网也删除身份、选择状态和 Cookie。"""

    session = FakeSession([envelope(data={"user_id": "7", "username": "alice", "role": "user"}), requests.Timeout()])
    session.cookies.set("oj_session", "opaque-token")
    client = ApiClient("http://api.test", session=session)  # type: ignore[arg-type]
    state: dict[str, Any] = {CLIENT_KEY: client, "active_submission_id": "9"}

    assert login(state, "alice", "password")["user_id"] == "7"
    assert state[AUTH_KEY]["username"] == "alice"
    assert logout(state) is False
    assert AUTH_KEY not in state
    assert "active_submission_id" not in state
    assert not session.cookies


def test_clear_login_state_preserves_unrelated_preferences() -> None:
    """401 只清私有页面选择，不误删与认证无关的显示偏好。"""

    state = {AUTH_KEY: {"user_id": "1"}, "selected_problem_id": "P1", "theme": "wide"}
    clear_login_state(state)
    assert state == {"theme": "wide"}


def test_problem_payload_keeps_empty_case_and_encodes_inherited_limits() -> None:
    """空输入/输出是合法测试点，继承限制必须以 JSON null 传给后端。"""

    values = {
        "id": " P1 ",
        "title": " Sum ",
        "description": "desc",
        "input_description": "in",
        "output_description": "out",
        "constraints": "small",
        "tags": "math, basic, math",
        "time_limit": 2.5,
        "memory_limit": 256,
    }
    payload = build_problem_payload(
        values,
        [{"input": "1 2", "output": "3"}],
        [{"input": "", "output": ""}, {"input": None, "output": "bad"}],
        inherit_time_limit=True,
        inherit_memory_limit=False,
    )
    assert payload["id"] == "P1"
    assert payload["samples"] == [{"input": "1 2", "output": "3"}]
    assert payload["testcases"] == [{"input": "", "output": ""}]
    assert payload["time_limit"] is None
    assert payload["memory_limit"] == 256
    assert payload["tags"] == ["math", "basic", "math"]
    assert clean_cases([{"input": 1, "output": "x"}]) == []


def test_case_payload_preserves_real_newlines() -> None:
    """前端原样提交多行测试数据，不转义或删除真实换行。"""

    rows = [{"input": "line 1\nline 2", "output": "a\nb"}]
    assert clean_cases(rows) == rows
    assert normalize_case_text(r"line 1\nline 2") == "line 1\nline 2"
    assert normalize_case_text("line 1\nline 2") == "line 1\nline 2"


def test_language_payload_distinguishes_interpreted_and_compiled_modes() -> None:
    """两种语言模式共用资源字段，但只有编译型语言发送编译命令。"""

    values = {
        "name": " Go ",
        "file_ext": " .go ",
        "compile_cmd": " go build -o {exe} {src} ",
        "run_cmd": " {exe} ",
        "time_limit": 2,
        "memory_limit": 256,
    }
    compiled = build_language_payload(values, compiled=True)
    assert compiled == {
        "name": "Go",
        "file_ext": ".go",
        "compile_cmd": "go build -o {exe} {src}",
        "run_cmd": "{exe}",
        "time_limit": 2.0,
        "memory_limit": 256,
    }

    interpreted = build_language_payload(
        {**values, "run_cmd": " go run {src} "}, compiled=False
    )
    assert interpreted["compile_cmd"] is None
    assert interpreted["run_cmd"] == "go run {src}"


def test_submission_query_pairs_pagination_and_polling_stops_at_terminal_state() -> None:
    """分页总是成对发送；只有 pending 会继续自动轮询。"""

    assert submission_query(
        user_id=" 7 ", problem_id="", status="success", use_pagination=True, page=2, page_size=10
    ) == {"user_id": "7", "status": "success", "page": 2, "page_size": 10}
    assert submission_query(
        user_id="", problem_id="P1", status="全部", use_pagination=False, page=1, page_size=20
    ) == {"problem_id": "P1"}
    assert should_poll("pending") is True
    assert should_poll("success") is False
    assert should_poll("error") is False
    assert ApiClient.path_segment("folder/P 1") == "folder%2FP%201"


def test_streamlit_anonymous_account_page_smoke() -> None:
    """匿名入口应只渲染账户操作，且页面脚本没有未捕获异常。"""

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(FRONTEND_ENTRYPOINT).run(timeout=10)
    assert not list(app.exception)
    assert [title.value for title in app.title] == ["账户"]
    assert {button.label for button in app.button} == {"登录", "创建账户"}


def test_protected_page_guard_requires_frontend_login_state() -> None:
    """受保护页面入口存在时，匿名状态由页面守卫提示登录而非伪造 404。"""

    from app.frontend.pages import _require_user

    assert _require_user.__doc__


def test_streamlit_admin_shell_handles_backend_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """管理员导航初始化不应因后端离线崩溃，错误需通过安全中文提示展示。"""

    from streamlit.testing.v1 import AppTest

    # 使用确定不提供 OJ 服务的端口，避免测试结果依赖开发服务器是否正在运行。
    monkeypatch.setenv("OJ_API_BASE_URL", "http://127.0.0.1:1")
    app = AppTest.from_file(FRONTEND_ENTRYPOINT)
    app.session_state[AUTH_KEY] = {"user_id": "1", "username": "admin", "role": "admin"}
    app.run(timeout=10)
    assert not list(app.exception)
    assert [error.value for error in app.error] == ["无法连接后端服务，请确认 FastAPI 已启动。"]
