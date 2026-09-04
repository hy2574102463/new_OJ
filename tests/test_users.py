"""验证用户资料权限、管理员操作、分页规则和角色审计。"""

from fastapi.testclient import TestClient

from tests.conftest import login_admin
from tests.test_auth import register


def test_user_can_view_self_but_not_another_user(client: TestClient) -> None:
    """普通用户只能读取本人；权限检查优先于目标是否存在。"""

    user_id = register(client, "alice").json()["data"]["user_id"]
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )

    assert client.get(f"/api/users/{user_id}").status_code == 200
    assert client.get("/api/users/99999").status_code == 403


def test_anonymous_and_non_admin_requests_are_rejected(client: TestClient) -> None:
    """没有身份返回 401，已有普通用户身份但权限不足返回 403。"""

    assert client.get("/api/users/").status_code == 401
    register(client, "alice")
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    assert client.get("/api/users/").status_code == 403
    assert client.post(
        "/api/users/admin", json={"username": "boss", "password": "secret1"}
    ).status_code == 403


def test_authentication_and_authorization_precede_parameter_errors(
    client: TestClient,
) -> None:
    """错误同时存在时必须遵循 401、403、400 的课程优先级。"""

    assert client.get("/api/users/", params={"page_size": 0}).status_code == 401
    assert client.put(
        "/api/users/2/role", json={"role": "owner"}
    ).status_code == 401

    register(client, "alice")
    client.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    )
    assert client.get("/api/users/", params={"page_size": 0}).status_code == 403
    assert client.put(
        "/api/users/2/role", json={"role": "owner"}
    ).status_code == 403


def test_admin_can_create_admin_and_view_any_user(client: TestClient) -> None:
    """管理员创建的账户应直接获得 admin 角色，并可查询普通用户。"""

    user_id = register(client, "alice").json()["data"]["user_id"]
    login_admin(client)
    created = client.post(
        "/api/users/admin", json={"username": "boss", "password": "secret1"}
    )

    assert created.status_code == 200
    assert created.json()["data"]["username"] == "boss"
    assert client.get(f"/api/users/{user_id}").status_code == 200


def test_admin_user_list_pagination_rules(client: TestClient) -> None:
    """列表总数不受分页影响，page_size 单独出现时默认第一页。"""

    register(client, "alice")
    register(client, "bob")
    login_admin(client)

    all_users = client.get("/api/users/").json()["data"]
    first_page = client.get("/api/users/", params={"page_size": 2}).json()["data"]
    beyond_end = client.get(
        "/api/users/", params={"page": 99, "page_size": 2}
    ).json()["data"]

    assert all_users["total"] == 3
    assert [user["user_id"] for user in all_users["users"]] == ["1", "2", "3"]
    assert len(first_page["users"]) == 2
    assert beyond_end == {"total": 3, "users": []}
    assert client.get("/api/users/", params={"page": 1}).status_code == 400
    assert client.get("/api/users/", params={"page_size": 0}).status_code == 400


def test_role_change_is_audited_and_admin_cannot_change_self(
    client: TestClient,
) -> None:
    """角色更新和审计必须在同一事务中完成，自身降权则返回 409。"""

    user_id = register(client, "alice").json()["data"]["user_id"]
    login_admin(client)
    response = client.put(f"/api/users/{user_id}/role", json={"role": "admin"})

    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": user_id, "role": "admin"}
    assert client.put("/api/users/1/role", json={"role": "user"}).status_code == 409
    assert client.put(f"/api/users/{user_id}/role", json={"role": "owner"}).status_code == 400

    async def read_audit() -> tuple[str, str, str]:
        async with client.app.state.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT actor_user_id, old_role, new_role FROM user_role_audits"
            )
            row = await cursor.fetchone()
            assert row is not None
            return str(row["actor_user_id"]), row["old_role"], row["new_role"]

    assert client.portal.call(read_audit) == ("1", "user", "admin")


def test_admin_gets_404_for_missing_user(client: TestClient) -> None:
    """管理员通过权限检查后，查询不存在资源应得到 404。"""

    login_admin(client)
    assert client.get("/api/users/99999").status_code == 404
    assert client.put(
        "/api/users/99999/role", json={"role": "user"}
    ).status_code == 404
