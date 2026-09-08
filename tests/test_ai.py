"""验证 AI 命题的权限、状态机、修正、取消、用量和结果回填契约。"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.ai_client import AIModelResponse
from tests.test_problems import login_user, valid_problem


class FakeModelClient:
    """按顺序返回预设模型响应，且记录消息供协议断言。"""

    def __init__(self, responses: list[AIModelResponse]):
        self.responses = responses
        self.messages: list[list[dict[str, str]]] = []

    async def complete(self, messages: list[dict[str, str]], on_bytes=None):
        """模拟异步模型调用，不接触网络或真实密钥。"""

        self.messages.append(messages)
        return self.responses.pop(0)


class SlowModelClient:
    """持续等待直到被取消，用于证明取消会传播到模型协程。"""

    def __init__(self) -> None:
        self.cancelled = False
        self.started = False

    async def complete(self, messages: list[dict[str, str]], on_bytes=None):
        """捕获取消后重新抛出，保持 asyncio 的取消语义。"""

        self.started = True
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def configured_settings(tmp_path: Path) -> Settings:
    """构造只使用临时存储和假提供商的完整 AI 测试配置。"""

    return Settings(
        environment="test",
        database_path=tmp_path / "oj.db",
        problems_path=tmp_path / "problems",
        judge_workspace_path=tmp_path / "judge",
        test_reset_enabled=True,
        log_level="WARNING",
        ai_provider_url="https://provider.test/v1",
        ai_model="course-model",
        ai_api_key="test-secret-key",
        ai_input_price=1.0,
        ai_output_price=2.0,
        ai_price_unit=1000,
    )


def model_problem(problem_id: str = "AI1001", title: str = "AI Sum") -> str:
    """复用正式题目工厂，保证假模型结果和 CRUD 契约一致。"""

    payload = valid_problem(problem_id)
    payload["title"] = title
    return json.dumps(payload, ensure_ascii=False)


def wait_for_ai(
    client: TestClient,
    task_id: str,
    terminal: set[str] | None = None,
) -> dict[str, Any]:
    """像 Streamlit 一样轮询，直到任务进入指定状态。"""

    expected = terminal or {"completed", "failed", "cancelled"}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/ai/problem-tasks/{task_id}")
        assert response.status_code == 200
        data = response.json()["data"]
        if data["status"] in expected:
            return data
        time.sleep(0.01)
    raise AssertionError("AI task did not reach expected state")


def test_config_is_authenticated_and_never_returns_key(tmp_path: Path) -> None:
    """配置摘要证明环境值生效，但任何密钥文本都不进入响应。"""

    with TestClient(create_app(configured_settings(tmp_path))) as client:
        assert client.get("/api/ai/model-config").status_code == 401
        login_user(client)
        response = client.get("/api/ai/model-config")
        data = response.json()["data"]
        assert data["provider_url"] == "https://provider.test/v1"
        assert data["model"] == "course-model"
        assert data["configured"] is True
        assert data["api_key_configured"] is True
        assert "test-secret-key" not in response.text
        assert "api_key" not in data


def test_create_complete_continue_and_usage(tmp_path: Path) -> None:
    """首轮结果可回填，追问复用上一版，并累计 Token 和费用。"""

    with TestClient(create_app(configured_settings(tmp_path))) as client:
        login_user(client)
        fake = FakeModelClient(
            [
                AIModelResponse(model_problem(), 100, 50, "provider"),
                AIModelResponse(model_problem(title="Hard Sum"), 200, 80, "provider"),
                # 新题轮次第一次错误复用旧 ID，Service 应要求模型修正。
                AIModelResponse(model_problem(title="Another Sum"), 30, 10, "provider"),
                AIModelResponse(model_problem("AI2002", "Graph Path"), 40, 20, "provider"),
            ]
        )
        client.app.state.ai_service.model_client = fake
        created = client.post(
            "/api/ai/problem-tasks/", json={"requirement": "生成一道加法题"}
        )
        task_id = created.json()["data"]["task_id"]
        first = wait_for_ai(client, task_id)
        assert first["result"]["id"] == "AI1001"
        assert first["result_action"] == "create"
        assert first["usage"] == {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cost": 0.2,
            "currency": "USD",
            "source": "provider",
            "price_unit": 1000,
        }
        # AI 结果与正式题目 payload 同构，无需适配即可进入现有 CRUD。
        assert client.post("/api/problems/", json=first["result"]).status_code == 200
        assert client.get(f"/api/ai/problem-tasks/{task_id}").json()["data"][
            "result_action"
        ] == "update"
        continued = client.post(
            f"/api/ai/problem-tasks/{task_id}/rounds",
            json={"requirement": "提高难度"},
        )
        assert continued.status_code == 200
        second = wait_for_ai(client, task_id)
        assert second["current_round"] == 2
        assert second["result"]["title"] == "Hard Sum"
        assert second["usage"]["total_tokens"] == 430
        assert second["usage"]["cost"] == 0.56
        assert second["rounds"][1]["mode"] == "revise"
        assert "revise_current_problem" in fake.messages[1][1]["content"]

        new_round = client.post(
            f"/api/ai/problem-tasks/{task_id}/rounds",
            json={"requirement": "基于知识点另出一道更难的新题", "mode": "new"},
        )
        assert new_round.status_code == 200
        third = wait_for_ai(client, task_id)
        assert third["result"]["id"] == "AI2002"
        assert third["result_action"] == "create"
        assert third["rounds"][2]["mode"] == "new"
        assert "create_distinct_problem" in fake.messages[2][1]["content"]
        assert "new_problem_reused_previous_id" in fake.messages[3][-1]["content"]
        assert client.post("/api/problems/", json=third["result"]).status_code == 200


def test_invalid_result_is_repaired_once(tmp_path: Path) -> None:
    """首次非 JSON 触发一次结构修正，修正成功后才写 completed。"""

    with TestClient(create_app(configured_settings(tmp_path))) as client:
        login_user(client)
        fake = FakeModelClient(
            [
                AIModelResponse("not json", 10, 3, "estimated"),
                AIModelResponse(model_problem(), 20, 10, "provider"),
            ]
        )
        client.app.state.ai_service.model_client = fake
        response = client.post(
            "/api/ai/problem-tasks/", json={"requirement": "生成题目"}
        )
        result = wait_for_ai(client, response.json()["data"]["task_id"])
        assert result["status"] == "completed"
        assert result["usage"]["source"] == "mixed"
        assert len(fake.messages) == 2
        assert "invalid_json" in fake.messages[1][-1]["content"]


def test_cancel_stops_running_coroutine_and_prevents_result(tmp_path: Path) -> None:
    """取消不仅改变页面状态，也真实取消正在等待的模型协程。"""

    with TestClient(create_app(configured_settings(tmp_path))) as client:
        login_user(client)
        slow = SlowModelClient()
        client.app.state.ai_service.model_client = slow
        response = client.post(
            "/api/ai/problem-tasks/", json={"requirement": "慢任务"}
        )
        task_id = response.json()["data"]["task_id"]
        wait_for_ai(client, task_id, {"running"})
        deadline = time.monotonic() + 1
        while not slow.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert slow.started is True
        cancelled = client.put(f"/api/ai/problem-tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        result = wait_for_ai(client, task_id)
        assert result["status"] == "cancelled"
        assert result["result"] is None
        deadline = time.monotonic() + 1
        while not slow.cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
        assert slow.cancelled is True
        assert client.put(f"/api/ai/problem-tasks/{task_id}/cancel").status_code == 409


def test_task_permissions_and_reference_id_invariant(tmp_path: Path) -> None:
    """其他用户不能探测任务；改编结果改变原 ID 时任务失败。"""

    with TestClient(create_app(configured_settings(tmp_path))) as client:
        login_user(client, "alice")
        assert client.post("/api/problems/", json=valid_problem("P1")).status_code == 200
        fake = FakeModelClient(
            [
                AIModelResponse(model_problem("OTHER"), 1, 1, "provider"),
                AIModelResponse(model_problem("OTHER"), 1, 1, "provider"),
            ]
        )
        client.app.state.ai_service.model_client = fake
        response = client.post(
            "/api/ai/problem-tasks/",
            json={"requirement": "改编", "problem_id": "P1"},
        )
        task_id = response.json()["data"]["task_id"]
        result = wait_for_ai(client, task_id)
        assert result["status"] == "failed"
        assert result["result"] is None
        login_user(client, "bob")
        assert client.get(f"/api/ai/problem-tasks/{task_id}").status_code == 403
        assert client.get("/api/ai/problem-tasks/missing").status_code == 403


def test_missing_configuration_rejects_creation_without_network(
    client: TestClient,
) -> None:
    """未配置模型时仍可查看安全摘要，但创建任务明确返回 503。"""

    login_user(client)
    assert client.get("/api/ai/model-config").json()["data"]["configured"] is False
    assert (
        client.post(
            "/api/ai/problem-tasks/", json={"requirement": "生成题目"}
        ).status_code
        == 503
    )
