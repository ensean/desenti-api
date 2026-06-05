"""MCP Server 入口。

通过 Streamable HTTP transport 暴露合同 NER 工具。
两种使用方式：
  1. 集成模式：由 app/main.py 挂载到 /mcp 路径，随主服务启动
  2. 独立模式：python -m app.mcp_server，监听 0.0.0.0:8001
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from .config import get_settings
from .main import _build_engine

logger = logging.getLogger("desenti.mcp")

_ENTITY_TYPE_DESC: dict[str, str] = {
    "company_name": "公司名称",
    "person_name": "人名",
    "credit_code": "统一社会信用代码",
    "id_card": "身份证号",
    "phone": "电话号码（手机/座机）",
    "address": "地址",
    "bank_name": "开户行名称",
    "bank_account": "银行账号",
    "email": "电子邮件地址",
    "account_name": "户名（账户名称）",
}

_settings = get_settings()
_engine = _build_engine(_settings)

mcp = FastMCP("desenti-ner")

# ASGI app，供 main.py mount("/mcp", mcp_app) 使用
mcp_app = mcp.streamable_http_app()


@mcp.tool(
    description=(
        "分析中文合同文本，识别敏感实体（公司名、人名、地址、"
        "电话、银行账号、信用代码等），并标注角色与语义字段。"
    )
)
async def contract_ner(
    text: str,
    mode: str = "fast",
    entity_types: list[str] | None = None,
    min_confidence: float = 0.7,
    context_window: int = 100,
) -> str:
    """合同实体识别工具。

    Args:
        text: 合同纯文本内容
        mode: 识别模式，fast=字典+正则+spaCy（默认）；accurate=fast+LLM（需自托管，秒级）
        entity_types: 需要识别的实体类型列表，None 或 ["all"] 表示全部类型
        min_confidence: 最低置信度阈值（0.0–1.0）
        context_window: 角色判定的上下文窗口字符数（0–2000，默认 100）
    """
    if entity_types is None:
        entity_types = ["all"]

    if not text or not text.strip():
        return json.dumps({"success": False, "error": "text 不能为空"}, ensure_ascii=False)

    if len(text.encode("utf-8")) > _settings.max_text_bytes:
        limit_kb = _settings.max_text_bytes // 1024
        return json.dumps(
            {"success": False, "error": f"text 超过大小限制（{limit_kb}KB）"},
            ensure_ascii=False,
        )

    if mode not in ("fast", "accurate"):
        return json.dumps(
            {"success": False, "error": "mode 必须为 fast 或 accurate"},
            ensure_ascii=False,
        )

    if not (0.0 <= min_confidence <= 1.0):
        return json.dumps(
            {"success": False, "error": "min_confidence 必须在 0 到 1 之间"},
            ensure_ascii=False,
        )

    if not (0 <= context_window <= 2000):
        return json.dumps(
            {"success": False, "error": "context_window 必须在 0 到 2000 之间"},
            ensure_ascii=False,
        )

    raw_entities, used_mode = await asyncio.to_thread(
        _engine.analyze,
        text=text,
        entity_types=entity_types,
        min_confidence=min_confidence,
        context_window=context_window,
        mode=mode,
    )

    by_type: dict[str, int] = {}
    for e in raw_entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    result = {
        "success": True,
        "data": {
            "entities": raw_entities,
            "statistics": {
                "total_entities": len(raw_entities),
                "by_type": by_type,
            },
        },
        "meta": {
            "model_version": _settings.model_version,
            "mode": used_mode,
        },
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(description="列出所有支持的实体类型及其中文含义。")
async def list_entity_types() -> str:
    result = {
        "entity_types": [
            {"type": k, "description": v}
            for k, v in _ENTITY_TYPE_DESC.items()
        ]
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def main() -> None:
    """独立运行入口：python -m app.mcp_server"""
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
