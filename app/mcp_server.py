"""MCP Server 入口。

通过 stdio transport 暴露合同 NER 工具，供 Claude Desktop 等 MCP 客户端调用。
运行方式：python -m app.mcp_server
"""

from __future__ import annotations

import asyncio
import json
import logging

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .config import get_settings
from .main import _build_engine

logger = logging.getLogger("desenti.mcp")

# 所有支持的实体类型及中文含义
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

# 延迟初始化引擎（进程启动时构建一次）
_settings = get_settings()
_engine = _build_engine(_settings)

# 创建 MCP Server 实例
server = Server("desenti-ner")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """向客户端声明本服务提供的工具列表。"""
    return [
        types.Tool(
            name="contract_ner",
            description=(
                "分析中文合同文本，识别敏感实体（公司名、人名、地址、"
                "电话、银行账号、信用代码等），并标注角色与语义字段。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "合同纯文本内容",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fast", "accurate"],
                        "default": "fast",
                        "description": (
                            "识别模式：fast=字典+正则+spaCy（默认，毫秒级）；"
                            "accurate=fast+LLM（更准，需自托管 LLM，秒级）"
                        ),
                    },
                    "entity_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["all"],
                        "description": (
                            "需要识别的实体类型列表，[\"all\"] 表示全部类型。"
                            "可选值：company_name, person_name, credit_code, "
                            "id_card, phone, address, bank_name, bank_account, "
                            "email, account_name"
                        ),
                    },
                    "min_confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 0.7,
                        "description": "最低置信度阈值，低于此值的实体将被过滤",
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="list_entity_types",
            description="列出所有支持的实体类型及其中文含义。",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    """路由工具调用到对应处理函数。"""
    if name == "contract_ner":
        return await _handle_contract_ner(arguments)
    if name == "list_entity_types":
        return _handle_list_entity_types()
    raise ValueError(f"未知工具: {name}")


async def _handle_contract_ner(
    arguments: dict,
) -> list[types.TextContent]:
    """执行合同 NER 识别，返回 JSON 格式结果。"""
    text: str = arguments.get("text", "")
    mode: str = arguments.get("mode", "fast")
    entity_types: list[str] = arguments.get("entity_types", ["all"])
    min_confidence: float = float(arguments.get("min_confidence", 0.7))

    if not text or not text.strip():
        result = {"success": False, "error": "text 不能为空"}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if mode not in ("fast", "accurate"):
        result = {"success": False, "error": "mode 必须为 fast 或 accurate"}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    if not (0.0 <= min_confidence <= 1.0):
        result = {"success": False, "error": "min_confidence 必须在 0 到 1 之间"}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    # NER 分析（同步调用，用 asyncio.to_thread 避免阻塞事件循环）
    raw_entities, used_mode = await asyncio.to_thread(
        _engine.analyze,
        text=text,
        entity_types=entity_types,
        min_confidence=min_confidence,
        context_window=_settings.default_context_window,
        mode=mode,
    )

    # 统计各类型数量
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
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


def _handle_list_entity_types() -> list[types.TextContent]:
    """返回所有支持的实体类型列表。"""
    result = {
        "entity_types": [
            {"type": k, "description": v}
            for k, v in _ENTITY_TYPE_DESC.items()
        ]
    }
    return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def _main() -> None:
    """启动 MCP stdio server。"""
    logging.basicConfig(level=logging.WARNING)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
