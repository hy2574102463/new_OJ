"""验证 Step 1 题目 JSON 持久化、CRUD、权限和错误路径。"""

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.problems import ProblemRepository, ProblemStorageError
from app.schemas.problems import StoredProblem
from tests.conftest import login_admin
from tests.test_auth import register


def valid_problem(problem_id: str = "P1001") -> dict[str, Any]:
    """生成字段完整的最小题目，测试只需覆盖与目标相关的字段。"""

    return {
        "id": problem_id,
        "title": "A+B Problem",
        "description": "计算两个整数的和。",
        "input_description": "输入两个整数。",
        "output_description": "输出它们的和。",
        "samples": [{"input": "1 2", "output": "3"}],
        "constraints": "整数在 32 位范围内。",
        "testcases": [{"input": "-1 1", "output": "0"}],
    }


def login_user(client: TestClient, username: str = "alice") -> str:
    """注册并登录普通用户，返回后续权限断言使用的用户 ID。"""

    user_id = register(client, username).json()["data"]["user_id"]
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "secret1"}
    )
    assert response.status_code == 200
    return user_id


def test_problem_endpoints_require_authentication_before_validation(
    client: TestClient,
) -> None:
    """匿名请求即使正文无效，也应先按错误优先级返回 401。"""

    assert client.get("/api/problems/").status_code == 401
    assert client.get("/api/problems/P1001").status_code == 401
    assert client.post("/api/problems/", json={}).status_code == 401
    assert client.put("/api/problems/P1001", json={}).status_code == 401
    assert client.delete("/api/problems/P1001").status_code == 401


def test_logged_in_user_can_create_list_and_read_problem(client: TestClient) -> None:
    """新增后列表只返回摘要，详情返回完整字段和稳定默认值。"""

    login_user(client)
    response = client.post("/api/problems/", json=valid_problem())
    assert response.status_code == 200
    assert response.json() == {
        "code": 200,
        "msg": "add success",
        "data": {"id": "P1001"},
    }

    assert client.get("/api/problems/").json()["data"] == [
        {"id": "P1001", "title": "A+B Problem"}
    ]
    detail = client.get("/api/problems/P1001").json()["data"]
    assert detail["hint"] == ""
    assert detail["source"] == ""
    assert detail["tags"] == []
    assert detail["author"] == ""
    assert detail["difficulty"] == ""
    assert detail["time_limit"] == 3.0
    assert detail["memory_limit"] == 128
    assert detail["testcases"] == [{"input": "-1 1", "output": "0"}]


@pytest.mark.parametrize(
    "change",
    [
        {"title": ""},
        {"samples": []},
        {"testcases": []},
        {"time_limit": 0},
        {"memory_limit": -1},
        {"samples": [{"input": "missing output"}]},
    ],
)
def test_problem_validation_rejects_empty_or_malformed_required_content(
    client: TestClient, change: dict[str, Any]
) -> None:
    """必填内容为空、测试点结构错误或限制非正数都返回 400。"""

    login_user(client)
    payload = valid_problem()
    payload.update(change)
    assert client.post("/api/problems/", json=payload).status_code == 400


def test_duplicate_and_missing_problem_status_codes(client: TestClient) -> None:
    """重复 ID 是状态冲突 409，不存在的详情和编辑是 404。"""

    login_user(client)
    assert client.post("/api/problems/", json=valid_problem()).status_code == 200
    assert client.post("/api/problems/", json=valid_problem()).status_code == 409
    assert client.get("/api/problems/missing").status_code == 404
    assert client.put(
        "/api/problems/missing", json=valid_problem("missing")
    ).status_code == 404


def test_problem_ids_are_case_sensitive_and_list_is_sorted(client: TestClient) -> None:
    """ID 大小写不同可共存，列表按完整 ID 稳定排序。"""

    login_user(client)
    for problem_id in ("p1001", "P1002", "P1001"):
        payload = valid_problem(problem_id)
        payload["title"] = problem_id
        assert client.post("/api/problems/", json=payload).status_code == 200

    summaries = client.get("/api/problems/").json()["data"]
    assert [item["id"] for item in summaries] == ["P1001", "P1002", "p1001"]


def test_edit_requires_matching_id_and_preserves_public_cases(
    client: TestClient,
) -> None:
    """普通完整编辑不能改 ID，也不能覆盖 Step 5 管理的内部可见性。"""

    login_user(client)
    client.post("/api/problems/", json=valid_problem())
    repository = client.app.state.problem_repository

    async def make_cases_public() -> None:
        stored = await repository.get("P1001")
        assert stored is not None
        await repository.update(stored.model_copy(update={"public_cases": True}))

    client.portal.call(make_cases_public)
    mismatch = valid_problem("OTHER")
    assert client.put("/api/problems/P1001", json=mismatch).status_code == 400

    updated = valid_problem()
    updated["title"] = "Updated title"
    assert client.put("/api/problems/P1001", json=updated).status_code == 200
    assert client.get("/api/problems/P1001").json()["data"]["title"] == "Updated title"

    async def read_visibility() -> bool:
        stored = await repository.get("P1001")
        assert stored is not None
        return stored.public_cases

    assert client.portal.call(read_visibility) is True


def test_only_admin_can_delete_problem(client: TestClient) -> None:
    """普通用户删除返回 403；管理员删除成功，重复删除返回 404。"""

    login_user(client)
    client.post("/api/problems/", json=valid_problem())
    assert client.delete("/api/problems/P1001").status_code == 403

    login_admin(client)
    assert client.delete("/api/problems/P1001").status_code == 200
    assert client.delete("/api/problems/P1001").status_code == 404


def test_hashed_filename_prevents_problem_id_path_traversal(
    client: TestClient, test_settings
) -> None:
    """危险 ID 只能影响 JSON 内容，不能决定磁盘目录或文件名。"""

    login_user(client)
    dangerous_id = "../../outside"
    assert client.post(
        "/api/problems/", json=valid_problem(dangerous_id)
    ).status_code == 200

    expected_name = hashlib.sha256(dangerous_id.encode("utf-8")).hexdigest() + ".json"
    files = list(test_settings.problems_path.iterdir())
    assert [path.name for path in files] == [expected_name]
    assert files[0].parent == test_settings.problems_path


def test_problem_persists_across_application_restart(test_settings) -> None:
    """题目来自 JSON 文件，因此创建新应用实例后仍能读取。"""

    with TestClient(create_app(test_settings)) as first_client:
        login_admin(first_client)
        assert first_client.post(
            "/api/problems/", json=valid_problem()
        ).status_code == 200

    with TestClient(create_app(test_settings)) as second_client:
        login_admin(second_client)
        assert second_client.get("/api/problems/P1001").status_code == 200


def test_reset_removes_problems_and_recreates_admin(client: TestClient) -> None:
    """测试 reset 同时清空 JSON 题库、Session 和用户，再恢复管理员。"""

    login_admin(client)
    client.post("/api/problems/", json=valid_problem())
    assert client.post("/api/reset/").status_code == 200
    assert client.get("/api/languages/").json()["data"] == {
        "name": ["cpp", "python"]
    }
    login_admin(client)
    assert client.get("/api/problems/").json()["data"] == []


@pytest.mark.asyncio
async def test_atomic_update_failure_keeps_old_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原子替换失败时旧文件仍完整，且临时文件不会遗留。"""

    repository = ProblemRepository(tmp_path / "problems")
    await repository.initialize()
    original = StoredProblem.model_validate(valid_problem())
    assert await repository.create(original) is True

    def fail_replace(_source: os.PathLike[str], _target: os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("app.repositories.problems.os.replace", fail_replace)
    changed = original.model_copy(update={"title": "should not persist"})
    with pytest.raises(ProblemStorageError):
        await repository.update(changed)

    stored = await repository.get("P1001")
    assert stored is not None
    assert stored.title == "A+B Problem"
    assert list((tmp_path / "problems").glob("*.tmp")) == []


def test_corrupt_json_prevents_application_startup(test_settings) -> None:
    """损坏配置必须在启动阶段暴露，不能静默跳过部分题库。"""

    test_settings.problems_path.mkdir(parents=True)
    (test_settings.problems_path / ("0" * 64 + ".json")).write_text("not json")

    with pytest.raises(ProblemStorageError):
        with TestClient(create_app(test_settings)):
            pass
