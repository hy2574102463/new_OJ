"""验证 Step 2 提交鉴权、异步状态、计分和限流。"""

import time

from fastapi.testclient import TestClient

from tests.conftest import login_admin
from tests.test_problems import login_user, valid_problem


def create_problem(client: TestClient, problem_id: str = "P1001") -> None:
    """创建包含两个测试点的题目，便于验证总分计算。"""

    payload = valid_problem(problem_id)
    payload["testcases"] = [
        {"input": "1 2", "output": "3"},
        {"input": "-4 1", "output": "-3"},
    ]
    assert client.post("/api/problems/", json=payload).status_code == 200


def wait_for_result(
    client: TestClient, submission_id: str, timeout: float = 5.0
) -> dict[str, object]:
    """像前端一样轮询，直到任务离开 pending 或达到测试超时。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/submissions/{submission_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        if data["status"] != "pending":
            return data
        time.sleep(0.02)
    raise AssertionError("submission did not finish")


def test_submission_endpoints_require_authentication_before_validation(
    client: TestClient,
) -> None:
    """匿名请求无论正文或 ID 是否有效，都先返回 401。"""

    assert client.post("/api/submissions/", json={}).status_code == 401
    assert client.get("/api/submissions/not-an-id").status_code == 401


def test_python_submission_returns_pending_then_success(client: TestClient) -> None:
    """提交响应立即为 pending，轮询后返回分数且不泄露源码或测例。"""

    user_id = login_user(client)
    create_problem(client)
    response = client.post(
        "/api/submissions/",
        json={
            "problem_id": "P1001",
            "language": "python",
            "code": "a,b=map(int,input().split());print(a+b)",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"

    result = wait_for_result(client, response.json()["data"]["submission_id"])
    assert result == {
        "submission_id": response.json()["data"]["submission_id"],
        "status": "success",
        "score": 20,
        "counts": 20,
        "compile_info": None,
        "run_info": {"result": "finished", "message": "2 test cases finished"},
        "error_info": "",
    }
    assert "code" not in result and "details" not in result
    profile = client.get(f"/api/users/{user_id}").json()["data"]
    assert profile["submit_count"] == 1
    assert profile["resolve_count"] == 1

    repeated = client.post(
        "/api/submissions/",
        json={
            "problem_id": "P1001",
            "language": "python",
            "code": "a,b=map(int,input().split());print(a+b)",
        },
    )
    wait_for_result(client, repeated.json()["data"]["submission_id"])
    profile = client.get(f"/api/users/{user_id}").json()["data"]
    assert profile["submit_count"] == 2
    assert profile["resolve_count"] == 1


def test_cpp_compile_error_is_successful_evaluation_with_zero_score(
    client: TestClient,
) -> None:
    """CE 是正常完成的判题结论，不应误写成任务 error。"""

    login_user(client)
    create_problem(client)
    response = client.post(
        "/api/submissions/",
        json={"problem_id": "P1001", "language": "cpp", "code": "int main( {"},
    )
    result = wait_for_result(client, response.json()["data"]["submission_id"])
    assert result["status"] == "success"
    assert result["score"] == 0
    assert result["compile_info"]["result"] == "error"
    assert "/tmp/" not in result["compile_info"]["message"]


def test_submission_validates_resources_and_detail_permissions(client: TestClient) -> None:
    """不存在资源返回 404，其他普通用户不能读取已有提交。"""

    login_user(client, "alice")
    assert client.post(
        "/api/submissions/",
        json={"problem_id": "missing", "language": "python", "code": "pass"},
    ).status_code == 404
    create_problem(client)
    assert client.post(
        "/api/submissions/",
        json={"problem_id": "P1001", "language": "missing", "code": "pass"},
    ).status_code == 404
    response = client.post(
        "/api/submissions/",
        json={"problem_id": "P1001", "language": "python", "code": "print(0)"},
    )
    submission_id = response.json()["data"]["submission_id"]

    login_user(client, "bob")
    assert client.get(f"/api/submissions/{submission_id}").status_code == 403
    login_admin(client)
    assert client.get(f"/api/submissions/{submission_id}").status_code == 200


def test_fourth_submission_within_a_minute_is_rate_limited(client: TestClient) -> None:
    """前三个有效提交被接受，第四个在资源查询前返回 429。"""

    login_user(client)
    create_problem(client)
    for _ in range(3):
        assert client.post(
            "/api/submissions/",
            json={"problem_id": "P1001", "language": "python", "code": "print(0)"},
        ).status_code == 200
    response = client.post(
        "/api/submissions/",
        json={"problem_id": "missing", "language": "missing", "code": "pass"},
    )
    assert response.status_code == 429
