"""验证 Step 5 测试点日志、公开权限和访问审计。"""

from fastapi.testclient import TestClient

from tests.conftest import login_admin
from tests.test_problems import login_user, valid_problem
from tests.test_submission_management import AC_CODE, create_problem, submit_and_wait


def update_visibility(
    client: TestClient, problem_id: str, public_cases: bool
) -> dict[str, object]:
    """以管理员身份更新日志策略，并返回统一响应中的 data。"""

    response = client.put(
        f"/api/problems/{problem_id}/log_visibility",
        json={"public_cases": public_cases},
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_log_permission_matrix_and_public_detail_boundary(client: TestClient) -> None:
    """本人、他人和管理员在私有/公开状态下遵循独立权限边界。"""

    alice_id = login_user(client, "alice")
    create_problem(client)
    submission_id = submit_and_wait(client, "P1001", AC_CODE)

    owner_log = client.get(f"/api/submissions/{submission_id}/log")
    assert owner_log.status_code == 200
    owner_data = owner_log.json()["data"]
    assert owner_data["score"] == 10
    assert owner_data["counts"] == 10
    assert owner_data["details"][0]["id"] == 1
    assert owner_data["details"][0]["result"] == "AC"
    # 资源指标随机器和调度变化，只断言稳定的数值类型与合法范围。
    assert owner_data["details"][0]["time"] >= 0
    assert owner_data["details"][0]["memory"] >= 0

    bob_id = login_user(client, "bob")
    assert client.get(f"/api/submissions/{submission_id}/log").status_code == 403

    login_admin(client)
    admin_log = client.get(f"/api/submissions/{submission_id}/log")
    assert admin_log.status_code == 200
    assert update_visibility(client, "P1001", True) == {
        "problem_id": "P1001",
        "public_cases": True,
    }

    # 公开测试点不改变 Step 3 汇总详情的 owner/admin 权限。
    login_user(client, "charlie")
    assert client.get(f"/api/submissions/{submission_id}/log").status_code == 200
    assert client.get(f"/api/submissions/{submission_id}").status_code == 403
    assert alice_id != bob_id


def test_log_response_excludes_sensitive_fields(client: TestClient) -> None:
    """日志只公开判定指标，不泄露源码、输入输出、路径或内部错误。"""

    login_user(client)
    create_problem(client)
    submission_id = submit_and_wait(client, "P1001", AC_CODE)

    data = client.get(f"/api/submissions/{submission_id}/log").json()["data"]
    assert set(data) == {"details", "score", "counts"}
    assert set(data["details"][0]) == {"id", "result", "time", "memory"}
    serialized = str(data)
    for secret in (AC_CODE, "input", "output", "actual", "/tmp", "traceback"):
        assert secret not in serialized.lower()


def test_log_requests_validate_authentication_id_and_missing_resources(
    client: TestClient,
) -> None:
    """日志路由保持 401 > 400，已登录后不存在资源返回 404。"""

    assert client.get("/api/submissions/not-an-id/log").status_code == 401
    login_user(client)
    assert client.get("/api/submissions/not-an-id/log").status_code == 400
    assert client.get("/api/submissions/99999/log").status_code == 404


def test_visibility_requires_admin_and_strict_payload(client: TestClient) -> None:
    """可见性由管理员管理，正文只接受布尔值并支持缺省 false。"""

    assert client.put(
        "/api/problems/P1001/log_visibility", json={"public_cases": True}
    ).status_code == 401
    login_user(client)
    assert client.put(
        "/api/problems/P1001/log_visibility", json={"public_cases": True}
    ).status_code == 403

    login_admin(client)
    assert client.post("/api/problems/", json=valid_problem()).status_code == 200
    assert client.put(
        "/api/problems/P1001/log_visibility", json={"public_cases": "true"}
    ).status_code == 400
    assert client.put(
        "/api/problems/P1001/log_visibility", json={"unknown": True}
    ).status_code == 400
    assert update_visibility(client, "P1001", True)["public_cases"] is True
    assert client.put(
        "/api/problems/P1001/log_visibility", json={}
    ).json()["data"]["public_cases"] is False
    assert client.put(
        "/api/problems/missing/log_visibility", json={"public_cases": True}
    ).status_code == 404


def test_access_audits_record_success_and_denial_but_not_excluded_requests(
    client: TestClient,
) -> None:
    """只记录既有提交的 200/403 日志读取，不记录匿名、非法或不存在请求。"""

    assert client.get("/api/submissions/1/log").status_code == 401
    owner_id = login_user(client, "owner")
    create_problem(client)
    submission_id = submit_and_wait(client, "P1001", AC_CODE)
    assert client.get(f"/api/submissions/{submission_id}/log").status_code == 200
    assert client.get("/api/submissions/bad/log").status_code == 400
    assert client.get("/api/submissions/99999/log").status_code == 404

    denied_id = login_user(client, "denied")
    assert client.get(f"/api/submissions/{submission_id}/log").status_code == 403

    login_admin(client)
    response = client.get("/api/logs/access/")
    assert response.status_code == 200
    audits = response.json()["data"]
    assert [(item["user_id"], item["status"]) for item in audits] == [
        (owner_id, "200"),
        (denied_id, "403"),
    ]
    assert all(item["problem_id"] == "P1001" for item in audits)
    assert all(item["action"] == "view_logs" for item in audits)
    assert all(item["time"].endswith("+00:00") for item in audits)

    # 读取审计列表本身不生成 view_logs 记录。
    assert client.get("/api/logs/access/").json()["data"] == audits


def test_access_audit_filters_pagination_and_permissions(client: TestClient) -> None:
    """管理员可查全部或组合筛选；分页规则与提交列表保持一致。"""

    first_user = login_user(client, "first")
    create_problem(client, "P1001")
    create_problem(client, "P1002")
    first_submission = submit_and_wait(client, "P1001", AC_CODE)
    second_submission = submit_and_wait(client, "P1002", AC_CODE)
    assert client.get(f"/api/submissions/{first_submission}/log").status_code == 200
    assert client.get(f"/api/submissions/{second_submission}/log").status_code == 200

    assert client.get("/api/logs/access/").status_code == 403
    login_admin(client)
    all_rows = client.get("/api/logs/access/").json()["data"]
    by_user = client.get(
        "/api/logs/access/", params={"user_id": first_user}
    ).json()["data"]
    by_problem = client.get(
        "/api/logs/access/", params={"problem_id": "P1002"}
    ).json()["data"]
    first_page = client.get(
        "/api/logs/access/", params={"page_size": 1}
    ).json()["data"]
    second_page = client.get(
        "/api/logs/access/", params={"page": 2, "page_size": 1}
    ).json()["data"]

    assert len(all_rows) == 2
    assert by_user == all_rows
    assert by_problem == [all_rows[1]]
    assert first_page == [all_rows[0]]
    assert second_page == [all_rows[1]]
    assert client.get("/api/logs/access/", params={"page": 1}).status_code == 400
    assert client.get(
        "/api/logs/access/", params={"page_size": 0}
    ).status_code == 400
    assert client.get(
        "/api/logs/access/", params={"user_id": "bad"}
    ).status_code == 400
    assert client.get(
        "/api/logs/access/", params={"problem_id": "   "}
    ).status_code == 400


def test_pending_log_has_no_stale_case_details(client: TestClient) -> None:
    """pending 尚无有效评测结论，因此返回空明细和空汇总字段。"""

    user_id = login_user(client)
    create_problem(client)

    async def create_pending() -> str:
        """直接建立未调度任务，以便稳定观察 pending 的日志表现。"""

        from datetime import datetime, timezone

        submission = await client.app.state.submission_repository.create_pending(
            int(user_id), "P1001", "python", "pass", datetime.now(timezone.utc)
        )
        return str(submission.submission_id)

    submission_id = client.portal.call(create_pending)
    response = client.get(f"/api/submissions/{submission_id}/log")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "details": [],
        "score": None,
        "counts": None,
    }


def test_reset_clears_log_access_audits(client: TestClient) -> None:
    """测试 reset 重建迁移表，旧访问记录不会跨验收场景残留。"""

    login_user(client)
    create_problem(client)
    submission_id = submit_and_wait(client, "P1001", AC_CODE)
    assert client.get(f"/api/submissions/{submission_id}/log").status_code == 200

    response = client.post("/api/reset/")
    assert response.status_code == 200
    login_admin(client)
    assert client.get("/api/logs/access/").json()["data"] == []
