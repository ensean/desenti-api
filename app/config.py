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
    # 默认识别模式：fast=字典+正则+spaCy；accurate=fast + LLM
    default_mode: str = "fast"

    # ---------------- fast 模式：字典 + spaCy ----------------
    # 敏感词典文件路径（相对启动目录）。命中优先级最高，可强制匹配/纠正实体。
    dict_file: str = "sensitive_dict.txt"
    # 是否启用 spaCy NER（公司名/人名/地址兜底）。
    # 关闭或模型不可用时，fast 模式自动降级为「字典 + 正则」。
    spacy_enabled: bool = True
    # spaCy 中文模型名。生产推荐 zh_core_web_trf（~400MB BERT，需 torch）；
    # 轻量集成可用 zh_core_web_sm。可用环境变量 DESENTI_SPACY_MODEL 覆盖，
    # 内网可指向本地模型目录路径。
    spacy_model: str = "zh_core_web_trf"
    # spaCy 实体置信度（模型不显式给分，统一赋值）。
    spacy_confidence: float = 0.75

    # ---------------- LLM 后端（accurate 模式） ----------------
    # 是否启用 LLM 后端。关闭时 accurate 模式自动降级为 fast。
    llm_enabled: bool = False
    # OpenAI 兼容的 base url。
    #   Ollama:  http://127.0.0.1:11434/v1
    #   vLLM:    http://127.0.0.1:8000/v1
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    # 部分后端（vLLM/OpenAI 兼容网关）需要 Key；Ollama 可留默认值。
    llm_api_key: str = "not-needed"
    # 模型名（EC2 host 上自托管的模型）。中文合同推荐 qwen3.5:9b
    # （官方 Ollama 库，256K 上下文，中文强）。备选 glm4:9b。
    llm_model: str = "qwen3.5:9b"
    # 单次请求超时（秒）。SLA 已放宽，给模型留足生成时间。
    llm_timeout_seconds: float = 120.0
    # 单个 LLM 分块最大字符数。qwen3.5 为 256K 上下文，正常 100KB 合同
    # （中文约 3.3 万字）可一次喂入，无需切块；此值作超长文本兜底。
    llm_max_chars_per_chunk: int = 40000
    # LLM 实体的默认置信度（模型未显式给出时）。
    llm_default_confidence: float = 0.9
    # API 风格：ollama 用原生 /api/chat（正确支持 think=false，Qwen3.5 必需）；
    # openai 用 /v1/chat/completions（vLLM 等标准 OpenAI 兼容后端）。
    llm_api_style: str = "ollama"
    # 是否禁用思考模式（Qwen3/3.5 thinking）。抽取任务应关闭：
    # 思考会混入 <think> 块、拖慢生成、干扰 JSON 输出。
    llm_disable_thinking: bool = True
    # 是否使用 response_format=json_object 强制 JSON 语法约束。
    # 默认关闭：在 llama.cpp + 混合架构模型（Qwen3.5）上会触发昂贵的
    # 语法编译，单次请求延迟从数秒升到 60 秒以上。依赖提示 + 容错解析即可。
    # 若后端为 vLLM 且模型为标准架构，可开启以提高 JSON 稳定性。
    llm_use_json_format: bool = False

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
