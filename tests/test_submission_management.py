"""验证 Step 3 提交列表、筛选、分页、权限和原 ID 重判。"""

import asyncio

from fastapi.testclient import TestClient

from tests.conftest import login_admin
from tests.test_problems import login_user, valid_problem
from tests.test_submissions import wait_for_result


AC_CODE = "a,b=map(int,input().split());print(a+b)"
WA_CODE = "print(0)"


def create_problem(client: TestClient, problem_id: str = "P1001") -> None:
    """建立一个测试点的题目，使列表分数和重判结果容易观察。"""

    payload = valid_problem(problem_id)
    payload["testcases"] = [{"input": "1 2", "output": "3"}]
    assert client.post("/api/problems/", json=payload).status_code == 200


def submit_and_wait(client: TestClient, problem_id: str, code: str) -> str:
    """创建 Python 提交并等待完成，返回后续筛选使用的 ID。"""

    response = client.post(
        "/api/submissions/",
        json={"problem_id": problem_id, "language": "python", "code": code},
    )
    assert response.status_code == 200
    submission_id = response.json()["data"]["submission_id"]
    wait_for_result(client, submission_id)
    return submission_id


def test_submission_list_requires_authentication_before_parameters(
    client: TestClient,
) -> None:
    """匿名非法查询仍先返回 401，符合全局错误优先级。"""

    response = client.get(
        "/api/submissions/", params={"page": 0, "status": "unknown"}
    )
    assert response.status_code == 401


def test_pagination_parameter_combinations(client: TestClient) -> None:
    """练习答案：全空查全部、仅 size 第一页、仅 page 错误、两者正常。"""

    login_user(client)
    create_problem(client)
    first_id = submit_and_wait(client, "P1001", AC_CODE)
    second_id = submit_and_wait(client, "P1001", WA_CODE)

    all_items = client.get(
        "/api/submissions/", params={"problem_id": "P1001"}
    ).json()["data"]
    first_page = client.get(
        "/api/submissions/", params={"problem_id": "P1001", "page_size": 1}
    ).json()["data"]
    second_page = client.get(
        "/api/submissions/",
        params={"problem_id": "P1001", "page": 2, "page_size": 1},
    ).json()["data"]

    assert all_items["total"] == 2
    assert [item["submission_id"] for item in all_items["submissions"]] == [
        first_id,
        second_id,
    ]
    assert first_page == {"total": 2, "submissions": [all_items["submissions"][0]]}
    assert second_page == {"total": 2, "submissions": [all_items["submissions"][1]]}
    assert client.get(
        "/api/submissions/", params={"problem_id": "P1001", "page": 1}
    ).status_code == 400
    assert client.get(
        "/api/submissions/", params={"problem_id": "P1001", "page_size": 0}
    ).status_code == 400


def test_list_requires_primary_filter_and_valid_status(client: TestClient) -> None:
    """二级筛选不能脱离 user/problem，状态只接受任务状态枚举。"""

    login_user(client)
    assert client.get("/api/submissions/").status_code == 400
    assert client.get(
        "/api/submissions/", params={"status": "success"}
    ).status_code == 400
    assert client.get(
        "/api/submissions/", params={"problem_id": "P1001", "status": "AC"}
    ).status_code == 400
    assert client.get(
        "/api/submissions/", params={"problem_id": "   "}
    ).status_code == 400


def test_list_permission_filters_and_summary_shape(client: TestClient) -> None:
    """普通用户不能跨用户查询，管理员可组合筛选且摘要不泄露源码。"""

    alice_id = login_user(client, "alice")
    create_problem(client)
    alice_submission = submit_and_wait(client, "P1001", AC_CODE)

    bob_id = login_user(client, "bob")
    bob_submission = submit_and_wait(client, "P1001", WA_CODE)
    own = client.get(
        "/api/submissions/", params={"problem_id": "P1001"}
    ).json()["data"]
    assert [item["submission_id"] for item in own["submissions"]] == [bob_submission]
    assert client.get(
        "/api/submissions/", params={"user_id": alice_id}
    ).status_code == 403
    assert client.get(
        "/api/submissions/",
        params={"user_id": alice_id, "page_size": 0},
    ).status_code == 403

    login_admin(client)
    all_problem = client.get(
        "/api/submissions/", params={"problem_id": "P1001"}
    ).json()["data"]
    alice_success = client.get(
        "/api/submissions/",
        params={"user_id": alice_id, "problem_id": "P1001", "status": "success"},
    ).json()["data"]
    missing_user = client.get(
        "/api/submissions/", params={"user_id": 99999}
    ).json()["data"]

    assert [item["submission_id"] for item in all_problem["submissions"]] == [
        alice_submission,
        bob_submission,
    ]
    assert alice_success["total"] == 1
    assert alice_success["submissions"][0] == {
        "submission_id": alice_submission,
        "status": "success",
        "score": 10,
        "counts": 10,
    }
    assert "code" not in alice_success["submissions"][0]
    assert "details" not in alice_success["submissions"][0]
    assert missing_user == {"total": 0, "submissions": []}
    assert bob_id != alice_id


def test_pending_and_error_list_items_are_minimal(client: TestClient) -> None:
    """pending/error 摘要只包含 ID 和状态，尚未产生的字段不占列表空间。"""

    user_id = login_user(client)
    create_problem(client)

    async def seed_states() -> tuple[str, str]:
        repository = client.app.state.submission_repository
        from datetime import datetime, timezone

        pending = await repository.create_pending(
            int(user_id), "P1001", "python", "pass", datetime.now(timezone.utc)
        )
        error = await repository.create_pending(
            int(user_id), "P1001", "python", "pass", datetime.now(timezone.utc)
        )
        await repository.finish_error(
            error.submission_id, "evaluation failed", datetime.now(timezone.utc)
        )
        return str(pending.submission_id), str(error.submission_id)

    pending_id, error_id = client.portal.call(seed_states)
    data = client.get(
        "/api/submissions/", params={"problem_id": "P1001"}
    ).json()["data"]
    assert data["submissions"] == [
        {"submission_id": pending_id, "status": "pending"},
        {"submission_id": error_id, "status": "error"},
    ]


def test_regular_user_cannot_probe_missing_submission(client: TestClient) -> None:
    """普通用户对不属于自己的未知 ID 得到 403，管理员才得到 404。"""

    login_user(client)
    assert client.get("/api/submissions/99999").status_code == 403
    login_admin(client)
    assert client.get("/api/submissions/99999").status_code == 404


def test_rejudge_requires_admin_before_id_validation(client: TestClient) -> None:
    """重判严格遵循 401、403、400 的检查顺序。"""

    assert client.put("/api/submissions/not-an-id/rejudge").status_code == 401
    login_user(client)
    assert client.put("/api/submissions/not-an-id/rejudge").status_code == 403
    login_admin(client)
    assert client.put("/api/submissions/not-an-id/rejudge").status_code == 400


def test_admin_rejudge_reuses_id_and_updates_user_counts(client: TestClient) -> None:
    """原 ID 重判不增加提交数，并按新的 AC 结论重算解题数。"""

    user_id = login_user(client)
    create_problem(client)
    submission_id = submit_and_wait(client, "P1001", AC_CODE)
    login_admin(client)

    async def replace_code() -> None:
        async with client.app.state.database.transaction() as connection:
            await connection.execute(
                "UPDATE submissions SET code = ? WHERE submission_id = ?",
                (WA_CODE, int(submission_id)),
            )

    # Step 3 不提供源码编辑接口；测试直接改库以证明重判确实覆盖旧结果。
    client.portal.call(replace_code)
    original_judge = client.app.state.submission_service.runner.judge

    async def delayed_judge(*args, **kwargs):
        """短暂阻塞新评测，让测试能观察 pending 事务的数据库状态。"""

        await asyncio.sleep(0.1)
        return await original_judge(*args, **kwargs)

    client.app.state.submission_service.runner.judge = delayed_judge
    response = client.put(f"/api/submissions/{submission_id}/rejudge")
    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "msg": "rejudge started",
        "data": {"submission_id": submission_id, "status": "pending"},
    }

    async def read_pending_state() -> tuple[str, int]:
        async with client.app.state.database.connection() as connection:
            cursor = await connection.execute(
                "SELECT status FROM submissions WHERE submission_id = ?",
                (int(submission_id),),
            )
            submission = await cursor.fetchone()
            cursor = await connection.execute(
                "SELECT COUNT(*) AS count FROM case_results WHERE submission_id = ?",
                (int(submission_id),),
            )
            cases = await cursor.fetchone()
            assert submission is not None and cases is not None
            return str(submission["status"]), int(cases["count"])

    assert client.portal.call(read_pending_state) == ("pending", 0)
    result = wait_for_result(client, submission_id)
    assert result["score"] == 0

    profile = client.get(f"/api/users/{user_id}").json()["data"]
    assert profile["submit_count"] == 1
    assert profile["resolve_count"] == 0


def test_rejudge_keeps_resolve_count_when_another_accept_exists(
    client: TestClient,
) -> None:
    """同题另一条 AC 仍有效时，重判一条为 WA 不撤销已解决题目。"""

    user_id = login_user(client)
    create_problem(client)
    first_id = submit_and_wait(client, "P1001", AC_CODE)
    submit_and_wait(client, "P1001", AC_CODE)
    login_admin(client)

    async def replace_code() -> None:
        async with client.app.state.database.transaction() as connection:
            await connection.execute(
                "UPDATE submissions SET code = ? WHERE submission_id = ?",
                (WA_CODE, int(first_id)),
            )

    client.portal.call(replace_code)
    assert client.put(f"/api/submissions/{first_id}/rejudge").status_code == 200
    assert wait_for_result(client, first_id)["score"] == 0
    profile = client.get(f"/api/users/{user_id}").json()["data"]
    assert profile["submit_count"] == 2
    assert profile["resolve_count"] == 1


def test_rejudge_rejects_pending_and_preserves_missing_problem_result(
    client: TestClient,
) -> None:
    """pending 冲突为 409；题目丢失返回 404 且旧成功结果不被清空。"""

    user_id = login_user(client)
    create_problem(client)
    completed_id = submit_and_wait(client, "P1001", AC_CODE)

    async def seed_pending() -> str:
        from datetime import datetime, timezone

        record = await client.app.state.submission_repository.create_pending(
            int(user_id), "P1001", "python", "pass", datetime.now(timezone.utc)
        )
        return str(record.submission_id)

    pending_id = client.portal.call(seed_pending)
    login_admin(client)
    assert client.put(f"/api/submissions/{pending_id}/rejudge").status_code == 409
    assert client.delete("/api/problems/P1001").status_code == 200
    assert client.put(f"/api/submissions/{completed_id}/rejudge").status_code == 404
    preserved = client.get(f"/api/submissions/{completed_id}").json()["data"]
    assert preserved["status"] == "success"
    assert preserved["score"] == 10


def test_error_submission_can_be_rejudged(client: TestClient) -> None:
    """任务级 error 也能使用原记录重新评测并覆盖为 success。"""

    user_id = login_user(client)
    create_problem(client)

    async def seed_error() -> str:
        from datetime import datetime, timezone

        repository = client.app.state.submission_repository
        record = await repository.create_pending(
            int(user_id), "P1001", "python", AC_CODE, datetime.now(timezone.utc)
        )
        await repository.finish_error(
            record.submission_id, "evaluation failed", datetime.now(timezone.utc)
        )
        return str(record.submission_id)

    submission_id = client.portal.call(seed_error)
    login_admin(client)
    assert client.put(f"/api/submissions/{submission_id}/rejudge").status_code == 200
    result = wait_for_result(client, submission_id)
    assert result["status"] == "success"
    assert result["score"] == 10
