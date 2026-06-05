"""FastAPI 应用入口。

实现需求文档中的 POST /api/v1/contract/ner 接口，包含：
  - Bearer Token 认证
  - 内存限流
  - 文本长度/空值校验
  - 混合 NER 识别
  - 统一错误响应格式
  - 不持久化合同文本（仅内存处理，处理后即丢弃）
"""

from __future__ import annotations

import logging
import time

from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .auth import RateLimiter, extract_bearer_token, verify_api_key
from .config import Settings, get_settings
from .errors import (
    ApiError,
    EmptyTextError,
    InternalError,
    InvalidOptionsError,
    TextTooLongError,
)
from .ner import NerEngine
from .ner.dict_engine import get_dict
from .ner.llm_client import LlmClient
from .ner.spacy_backend import SpacyBackend
from .schemas import (
    Entity,
    Meta,
    NerData,
    NerRequest,
    NerResponse,
    Statistics,
)

logger = logging.getLogger("desenti")

app = FastAPI(
    title="合同敏感信息识别 API",
    version=__version__,
    description="接收中文合同文本，返回识别到的敏感实体列表。",
)

# MCP Streamable HTTP — 挂载到 /mcp（延迟导入，避免循环依赖）
from .mcp_server import mcp_app  # noqa: E402
app.mount("/mcp", mcp_app)

# CORS：在所有情况下允许任意来源跨域（始终 "*"）。
# 本服务用 Bearer 头鉴权（非 Cookie），故 allow_credentials=False，
# 与 "*" 兼容；CORS 仅放开浏览器读取响应，不泄露 API Key。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)


def _build_engine(settings: Settings) -> NerEngine:
    """构建引擎：fast=字典+正则+spaCy；启用 LLM 时叠加 accurate。"""
    # 词典（最高优先级，热更新）
    sensitive_dict = get_dict(settings.dict_file)

    # spaCy 后端（公司/人名/地址兜底，模型不可用时优雅降级）
    spacy_backend = None
    if settings.spacy_enabled:
        spacy_backend = SpacyBackend(model_name=settings.spacy_model)
        logger.info("spaCy 后端已配置: model=%s", settings.spacy_model)

    # LLM 后端（accurate 模式，自托管）
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


_settings = get_settings()
_engine = _build_engine(_settings)
_rate_limiter = RateLimiter(_settings.rate_limit_per_minute)


# ----------------------------------------------------------------------------
# 统一错误处理
# ----------------------------------------------------------------------------

def _error_response(err: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=err.http_status,
        content={
            "success": False,
            "error": {"code": err.code, "message": err.message},
        },
    )


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return _error_response(exc)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # 不向客户端泄露内部细节；仅记录日志（不记录合同文本）
    logger.exception("未处理的内部异常: %s", type(exc).__name__)
    return _error_response(InternalError())


# ----------------------------------------------------------------------------
# 依赖：认证 + 限流
# ----------------------------------------------------------------------------

async def authenticate(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    token = extract_bearer_token(authorization)
    key = verify_api_key(token, settings)
    _rate_limiter.check(key)
    return key


# ----------------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "model_version": _settings.model_version}


@app.post("/api/v1/contract/ner", response_model=NerResponse)
async def contract_ner(
    payload: NerRequest,
    _key: str = Depends(authenticate),
    settings: Settings = Depends(get_settings),
) -> NerResponse:
    started = time.perf_counter()

    text = payload.text
    if text is None or text.strip() == "":
        raise EmptyTextError()

    if len(text.encode("utf-8")) > settings.max_text_bytes:
        raise TextTooLongError()

    opts = payload.options
    if not (0.0 <= opts.min_confidence <= 1.0):
        raise InvalidOptionsError("min_confidence 必须在 0 到 1 之间")
    if opts.context_window < 0:
        raise InvalidOptionsError("context_window 不能为负数")

    try:
        raw_entities, used_mode = _engine.analyze(
            text=text,
            entity_types=opts.entity_types,
            min_confidence=opts.min_confidence,
            context_window=opts.context_window,
            mode=opts.mode,
        )
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("NER 处理失败: %s", type(exc).__name__)
        raise InternalError() from exc

    entities = [Entity(**e) for e in raw_entities]

    by_type: dict[str, int] = {}
    for e in entities:
        by_type[e.type.value] = by_type.get(e.type.value, 0) + 1

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return NerResponse(
        success=True,
        data=NerData(
            entities=entities,
            statistics=Statistics(
                total_entities=len(entities),
                by_type=by_type,
            ),
        ),
        meta=Meta(
            model_version=settings.model_version,
            processing_time_ms=elapsed_ms,
            mode=used_mode,
        ),
    )
