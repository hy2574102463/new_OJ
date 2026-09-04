"""验证注册、密码哈希、登录 Session、登出和禁用用户行为。"""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_CREDENTIALS, login_admin


def register(client: TestClient, username: str, password: str = "secret1"):
    """通过公开 API 注册用户，减少各测试的重复请求代码。"""

    return client.post(
        "/api/users/", json={"username": username, "password": password}
    )


def test_startup_creates_initial_admin_and_login_sets_cookie(
    client: TestClient,
) -> None:
    """初始管理员应可登录，响应还应设置受保护的 Session Cookie。"""

    response = client.post("/api/auth/login", json=ADMIN_CREDENTIALS)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "user_id": "1",
        "username": "admin",
        "role": "admin",
    }
    cookie = response.headers["set-cookie"].lower()
    assert "oj_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=86400" in cookie


def test_registration_trims_username_and_hides_password(client: TestClient) -> None:
    """注册响应只公开稳定用户字段，不能包含密码或哈希。"""

    response = register(client, "  Alice  ")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["username"] == "Alice"
    assert data["role"] == "user"
    assert data["submit_count"] == 0
    assert data["resolve_count"] == 0
    assert set(data) == {
        "user_id",
        "username",
        "join_time",
        "role",
        "submit_count",
        "resolve_count",
    }


def test_username_uniqueness_is_case_insensitive(client: TestClient) -> None:
    """显示形式可以保留，但 Alice 与 alice 不能注册为两个账户。"""

    assert register(client, "Alice").status_code == 200
    response = register(client, "alice")

    assert response.status_code == 400
    assert response.json()["msg"] == "username already exists"


def test_registration_validation_returns_400(client: TestClient) -> None:
    """用户名和密码长度错误应遵循项目约定返回 400，而不是 422。"""

    assert register(client, "ab").status_code == 400
    assert register(client, "valid-name", "12345").status_code == 400


def test_password_is_hashed_and_wrong_password_returns_401(
    client: TestClient,
) -> None:
    """数据库不得保存明文，错误凭据也不能暴露账户细节。"""

    register(client, "alice", "correct-password")
    database = client.app.state.database

    async def read_hash() -> str:
        async with database.connection() as connection:
            cursor = await connection.execute(
                "SELECT password_hash FROM users WHERE username_key = ?", ("alice",)
            )
            row = await cursor.fetchone()
            assert row is not None
            return str(row["password_hash"])

    # TestClient 的 portal 在同步测试中安全执行异步数据库检查。
    stored_hash = client.portal.call(read_hash)
    assert stored_hash != "correct-password"
    assert stored_hash.startswith("$2")

    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["msg"] == "invalid username or password"


def test_login_is_case_insensitive_and_logout_revokes_session(
    client: TestClient,
) -> None:
    """登录名忽略大小写；登出后原 Cookie 不能继续访问受保护接口。"""

    user_id = register(client, "Alice").json()["data"]["user_id"]
    response = client.post(
        "/api/auth/login", json={"username": "aLiCe", "password": "secret1"}
    )
    assert response.status_code == 200
    assert client.get(f"/api/users/{user_id}").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert client.get(f"/api/users/{user_id}").status_code == 401


def test_expired_session_is_rejected(client: TestClient) -> None:
    """即使浏览器仍携带 Cookie，超过服务端期限的 Session 也必须失效。"""

    user_id = register(client, "alice").json()["data"]["user_id"]
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    raw_token = client.cookies.get("oj_session")
    assert raw_token is not None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    async def expire_session() -> None:
        async with client.app.state.database.transaction() as connection:
            await connection.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (expired_at, token_hash),
            )

    client.portal.call(expire_session)
    assert client.get(f"/api/users/{user_id}").status_code == 401


def test_banned_user_cannot_login_and_existing_session_stops_working(
    client: TestClient,
) -> None:
    """角色在每次请求时读取，因此封禁能立即让已有 Session 失效。"""

    user_id = register(client, "alice").json()["data"]["user_id"]
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    user_session = client.cookies.get("oj_session")

    # 独立客户端代表管理员浏览器，避免覆盖普通用户的 Cookie。
    with TestClient(client.app, raise_server_exceptions=False) as admin:
        login_admin(admin)
        assert admin.put(
            f"/api/users/{user_id}/role", json={"role": "banned"}
        ).status_code == 200

    client.cookies.set("oj_session", user_session)
    assert client.get(f"/api/users/{user_id}").status_code == 401
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    assert response.status_code == 403


def test_reset_recreates_only_initial_admin(client: TestClient) -> None:
    """测试重置应删除新增用户，同时恢复可登录的唯一初始管理员。"""

    register(client, "alice")
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    response = client.post("/api/reset/")
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.post("/api/auth/login", json=ADMIN_CREDENTIALS).status_code == 200

    async def count_users() -> int:
        async with client.app.state.database.connection() as connection:
            cursor = await connection.execute("SELECT COUNT(*) AS count FROM users")
            row = await cursor.fetchone()
            assert row is not None
            return int(row["count"])

    assert client.portal.call(count_users) == 1
