"""验证 OpenAI-compatible HTTP 协议、Token 回退和敏感错误边界。"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.services.ai_client import (
    AIProviderError,
    OpenAICompatibleClient,
    model_config_data,
)


def settings() -> Settings:
    """返回不会从本地 .env 继承字段的确定配置。"""

    return Settings(
        _env_file=None,
        ai_provider_url="https://provider.test/v1",
        ai_model="test-model",
        ai_api_key="secret-key",
    )


@pytest.mark.asyncio
async def test_client_uses_config_and_provider_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """请求必须实际使用部署 URL、模型和 Bearer 密钥。"""

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3},
            },
        )

    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    result = await OpenAICompatibleClient(settings()).complete(
        [{"role": "user", "content": "hello"}]
    )
    assert seen == {
        "url": "https://provider.test/v1/chat/completions",
        "authorization": "Bearer secret-key",
        "body": {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
    }
    assert (result.input_tokens, result.output_tokens, result.usage_source) == (
        12,
        3,
        "provider",
    )


@pytest.mark.asyncio
async def test_client_estimates_missing_usage_and_hides_provider_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 usage 时标明估算；失败正文不会进入公开异常文本。"""

    responses = [
        httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]}),
        httpx.Response(500, text="api_key=leaked provider trace"),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    original = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    client = OpenAICompatibleClient(settings())
    result = await client.complete([{"role": "user", "content": "hello"}])
    assert result.usage_source == "estimated"
    assert result.input_tokens > 0 and result.output_tokens > 0
    with pytest.raises(AIProviderError) as caught:
        await client.complete([{"role": "user", "content": "hello"}])
    assert "leaked" not in str(caught.value)
    assert "api_key" not in str(caught.value)


def test_invalid_provider_url_credentials_are_not_echoed() -> None:
    """误写到 URL 的凭据既不可使用，也不能从配置摘要返回。"""

    unsafe = Settings(
        _env_file=None,
        ai_provider_url="https://user:secret@provider.test/v1",
        ai_model="model",
        ai_api_key="another-secret",
    )
    data = model_config_data(unsafe)
    assert data["configured"] is False
    assert data["provider_url"] == ""
    assert "secret" not in json.dumps(data)
