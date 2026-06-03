"""应用配置。

通过环境变量覆盖默认值。敏感配置（如 API Key）不应硬编码，
生产环境务必通过环境变量或密钥管理服务注入。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DESENTI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务元信息
    model_version: str = "1.0.0"

    # 认证：逗号分隔的合法 API Key 列表。
    # 默认提供一个开发用 Key，生产环境必须通过 DESENTI_API_KEYS 覆盖。
    api_keys: str = "dev-local-key"

    # 文本限制：100KB（按 UTF-8 字节计）
    max_text_bytes: int = 100 * 1024

    # 限流：每个 API Key 每分钟最大请求数（满足 ≥10 QPS => 600/min）
    rate_limit_per_minute: int = 600

    # NER 默认参数
    default_min_confidence: float = 0.7
    default_context_window: int = 100

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
