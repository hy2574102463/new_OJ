"""调用 OpenAI-compatible Chat Completions；不管理任务或持久化状态。"""

import json
import math
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import Settings


class AIConfigurationError(Exception):
    """表示部署环境没有提供完整、安全的模型配置。"""


class AIProviderError(Exception):
    """表示外部模型响应失败；异常文本不得直接返回给浏览器。"""


@dataclass(frozen=True)
class AIModelResponse:
    """模型正文以及精确或估算后的输入、输出 Token。"""

    content: str
    input_tokens: int
    output_tokens: int
    usage_source: str


ProgressCallback = Callable[[int], Awaitable[None]]


def model_config_data(settings: Settings) -> dict[str, object]:
    """返回不含密钥的部署配置摘要，供页面确认配置是否可用。"""

    try:
        _chat_completions_url(settings.ai_provider_url)
        public_provider_url = settings.ai_provider_url.rstrip("/")
    except AIConfigurationError:
        # 无效 URL 可能误含 user:password，配置页不能把它当普通文本回显。
        public_provider_url = ""
    return {
        "provider_url": public_provider_url,
        "model": settings.ai_model,
        "api_key_configured": bool(settings.ai_api_key.get_secret_value()),
        "input_price": settings.ai_input_price,
        "output_price": settings.ai_output_price,
        "price_unit": settings.ai_price_unit,
        "currency": settings.ai_currency,
        "configured": is_model_configured(settings),
    }


def is_model_configured(settings: Settings) -> bool:
    """仅完整且数值合法的配置才能用于外部请求。"""

    try:
        _chat_completions_url(settings.ai_provider_url)
    except AIConfigurationError:
        return False
    return bool(
        settings.ai_model.strip()
        and settings.ai_api_key.get_secret_value()
        and settings.ai_input_price >= 0
        and settings.ai_output_price >= 0
        and settings.ai_price_unit > 0
        and settings.ai_request_timeout_seconds > 0
    )


def _chat_completions_url(provider_url: str) -> str:
    """校验部署者给出的基地址并追加标准 Chat Completions 路径。"""

    parsed = urlsplit(provider_url.strip().rstrip("/"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AIConfigurationError("invalid AI provider URL")
    path = f"{parsed.path.rstrip('/')}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _estimate_tokens(text: str) -> int:
    """提供商缺少 usage 时采用可解释的近似值：每四个字符一个 Token。"""

    return max(1, math.ceil(len(text) / 4))


class OpenAICompatibleClient:
    """以异步 HTTP 调用模型，使取消信号能中断网络等待。"""

    def __init__(self, settings: Settings, *, max_response_bytes: int = 2_000_000):
        self.settings = settings
        self.max_response_bytes = max_response_bytes

    async def complete(
        self,
        messages: list[dict[str, str]],
        on_bytes: ProgressCallback | None = None,
    ) -> AIModelResponse:
        """发送一次对话请求，限制响应大小并校验最小兼容结构。

        HTTP 状态、JSON 结构或大小异常统一抛出无敏感正文的内部异常；任务
        Service 再将它映射为稳定的失败状态。
        """

        if not is_model_configured(self.settings):
            raise AIConfigurationError("AI model is not configured")
        request_text = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        body = bytearray()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.ai_request_timeout_seconds)
            ) as client:
                # 逐块读取普通 JSON 响应既兼容非流式服务，也能限制内存占用。
                async with client.stream(
                    "POST",
                    _chat_completions_url(self.settings.ai_provider_url),
                    headers=headers,
                    json={"model": self.settings.ai_model, "messages": messages},
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AIProviderError("model provider rejected request")
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_response_bytes:
                            raise AIProviderError("model response is too large")
                        if on_bytes is not None:
                            await on_bytes(len(body))
        except (httpx.HTTPError, UnicodeError) as exc:
            raise AIProviderError("model provider request failed") from exc

        try:
            document = json.loads(body.decode("utf-8"))
            content = document["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise TypeError
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as exc:
            raise AIProviderError("invalid model provider response") from exc

        usage = document.get("usage") if isinstance(document, dict) else None
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        has_prompt = isinstance(prompt_tokens, int) and prompt_tokens >= 0
        has_completion = isinstance(completion_tokens, int) and completion_tokens >= 0
        resolved_input = prompt_tokens if has_prompt else _estimate_tokens(request_text)
        resolved_output = completion_tokens if has_completion else _estimate_tokens(content)
        source = "provider" if has_prompt and has_completion else "estimated"
        if has_prompt != has_completion:
            source = "mixed"
        return AIModelResponse(content, resolved_input, resolved_output, source)
