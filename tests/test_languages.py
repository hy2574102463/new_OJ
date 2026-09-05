"""验证 Step 2 语言默认配置、注册权限和模板安全约束。"""

import pytest
from fastapi.testclient import TestClient

from tests.test_problems import login_user


def test_default_languages_are_available_without_login(client: TestClient) -> None:
    """应用启动时应幂等提供排序后的 Python/C++ 名称。"""

    assert client.get("/api/languages/").json() == {
        "code": 200, "msg": "success", "data": {"name": ["cpp", "python"]}
    }


def test_registration_requires_login_before_body_validation(client: TestClient) -> None:
    """匿名无效请求先返回 401，保持全局错误优先级。"""

    assert client.post("/api/languages/", json={}).status_code == 401


def test_logged_in_user_can_register_interpreted_language(client: TestClient) -> None:
    """普通用户可注册安全的解释型命令，并在列表中看到结果。"""

    login_user(client)
    response = client.post(
        "/api/languages/",
        json={"name": "ruby", "file_ext": ".rb", "run_cmd": "ruby {src}"},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"name": "ruby"}
    assert client.get("/api/languages/").json()["data"]["name"] == [
        "cpp", "python", "ruby"
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"file_ext": "../../py"},
        {"run_cmd": "python3 program.py"},
        {"run_cmd": "/bin/python3 {src}"},
        {"run_cmd": "python3 {unknown}"},
        {"memory_limit": 1.5},
        {"time_limit": "1"},
    ],
)
def test_registration_rejects_unsafe_or_malformed_config(
    client: TestClient, change: dict[str, object]
) -> None:
    """路径、未知占位符和错误资源类型不能进入持久化层。"""

    login_user(client)
    payload: dict[str, object] = {
        "name": "custom", "file_ext": ".py", "run_cmd": "python3 {src}"
    }
    payload.update(change)
    assert client.post("/api/languages/", json=payload).status_code == 400


def test_language_names_are_case_insensitively_unique(client: TestClient) -> None:
    """不同大小写不能覆盖已有默认语言配置。"""

    login_user(client)
    response = client.post(
        "/api/languages/",
        json={"name": "Python", "file_ext": ".py", "run_cmd": "python3 {src}"},
    )
    assert response.status_code == 409
