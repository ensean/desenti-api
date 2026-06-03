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

_settings = get_settings()
_engine = NerEngine(model_version=_settings.model_version)
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
        raw_entities = _engine.analyze(
            text=text,
            entity_types=opts.entity_types,
            min_confidence=opts.min_confidence,
            context_window=opts.context_window,
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
        ),
    )
