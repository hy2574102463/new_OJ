"""集中定义应用配置，并从环境变量或 .env 文件加载覆盖值。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """经过 Pydantic 校验的运行配置。

    字段可以由 ``OJ_`` 前缀的环境变量覆盖；路径使用 ``Path``，布尔值
    和运行环境也会在应用启动前完成类型校验。
    """

    environment: Literal["development", "test", "production"] = "development"
    database_path: Path = Path("data/oj.db")
    problems_path: Path = Path("data/problems")
    judge_workspace_path: Path = Path(".judge-tmp")
    log_level: str = "INFO"
    test_reset_enabled: bool = False
    session_cookie_name: str = "oj_session"
    session_ttl_seconds: int = 86400
    # AI 密钥只从部署环境读取，并以 SecretStr 避免调试输出意外回显。
    ai_provider_url: str = ""
    ai_model: str = ""
    ai_api_key: SecretStr = SecretStr("")
    ai_input_price: float = 0.0
    ai_output_price: float = 0.0
    ai_price_unit: int = 1_000_000
    ai_currency: str = "USD"
    ai_request_timeout_seconds: float = 120.0

    model_config = SettingsConfigDict(
        # 未设置环境变量时读取本地 .env；示例文件本身不会被自动读取。
        env_file=".env",
        env_prefix="OJ_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """创建并缓存默认配置，避免每个请求重复读取环境变量。"""

    return Settings()
