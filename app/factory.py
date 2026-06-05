"""引擎工厂。

从 main.py 中拆出，避免 main.py <-> mcp_server.py 循环导入。
只依赖 config.py 和 ner/ 下的模块，不导入 main.py。
"""

from __future__ import annotations

import logging

from .config import Settings
from .ner import NerEngine
from .ner.dict_engine import get_dict
from .ner.llm_client import LlmClient
from .ner.spacy_backend import SpacyBackend

logger = logging.getLogger("desenti")


def _build_engine(settings: Settings) -> NerEngine:
    """构建引擎：fast=字典+正则+spaCy；启用 LLM 时叠加 accurate。"""
    sensitive_dict = get_dict(settings.dict_file)

    spacy_backend = None
    if settings.spacy_enabled:
        spacy_backend = SpacyBackend(model_name=settings.spacy_model)
        logger.info("spaCy 后端已配置: model=%s", settings.spacy_model)

    llm_client = None
    if settings.llm_enabled:
        llm_client = LlmClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_timeout_seconds,
            max_chars_per_chunk=settings.llm_max_chars_per_chunk,
            default_confidence=settings.llm_default_confidence,
            disable_thinking=settings.llm_disable_thinking,
            use_json_format=settings.llm_use_json_format,
            api_style=settings.llm_api_style,
        )
        logger.info("LLM 后端已启用: model=%s base=%s",
                    settings.llm_model, settings.llm_base_url)

    return NerEngine(
        model_version=settings.model_version,
        llm_client=llm_client,
        spacy_backend=spacy_backend,
        sensitive_dict=sensitive_dict,
        spacy_confidence=settings.spacy_confidence,
    )
