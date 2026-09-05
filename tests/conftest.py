from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


ADMIN_CREDENTIALS = {"username": "admin", "password": "admintestpassword"}


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_path=tmp_path / "oj-test.db",
        problems_path=tmp_path / "problems",
        judge_workspace_path=tmp_path / "judge",
        log_level="WARNING",
        test_reset_enabled=True,
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    """启动使用临时数据库的完整应用，并模拟一个浏览器会话。"""

    with TestClient(create_app(test_settings), raise_server_exceptions=False) as client:
        yield client


def login_admin(client: TestClient) -> None:
    """让测试客户端登录初始管理员，并确认认证前置条件成立。"""

    response = client.post("/api/auth/login", json=ADMIN_CREDENTIALS)
    assert response.status_code == 200
