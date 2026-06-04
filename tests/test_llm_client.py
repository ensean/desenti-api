"""LlmClient 解析逻辑测试（不触网）。"""

from __future__ import annotations

import pytest

from app.ner.llm_client import LlmClient, LlmUnavailable


def make_client():
    return LlmClient(
        base_url="http://127.0.0.1:11434/v1",
        api_key="x",
        model="qwen2.5:7b",
        timeout=1.0,
        max_chars_per_chunk=6000,
        default_confidence=0.9,
    )


def test_parse_plain_json():
    c = make_client()
    raw = '{"entities":[{"value":"星河研究院","type":"company_name","role":"party_a","context_field":"签约主体"}]}'
    out = c._parse_entities(raw)
    assert len(out) == 1
    assert out[0]["value"] == "星河研究院"
    assert out[0]["confidence"] == 0.9


def test_parse_fenced_json():
    c = make_client()
    raw = '```json\n{"entities":[{"value":"张三","type":"person_name"}]}\n```'
    out = c._parse_entities(raw)
    assert out[0]["type"] == "person_name"
    assert out[0]["role"] == "unknown"  # 缺省补全


def test_parse_with_surrounding_text():
    c = make_client()
    raw = '好的，结果如下：{"entities":[{"value":"李四","type":"person_name"}]} 完毕'
    out = c._parse_entities(raw)
    assert out[0]["value"] == "李四"


def test_invalid_type_filtered():
    c = make_client()
    raw = '{"entities":[{"value":"x","type":"phone"}]}'  # phone 不归 LLM 管
    out = c._parse_entities(raw)
    assert out == []


def test_bad_role_defaults_unknown():
    c = make_client()
    raw = '{"entities":[{"value":"王五","type":"person_name","role":"party_z"}]}'
    out = c._parse_entities(raw)
    assert out[0]["role"] == "unknown"


def test_non_json_raises():
    c = make_client()
    with pytest.raises(LlmUnavailable):
        c._parse_entities("这根本不是 JSON")


def test_strip_think_block():
    # 思考模式残留的 <think> 块应被剥离后再解析
    c = make_client()
    raw = '<think>让我分析一下这段合同……</think>{"entities":[{"value":"张三","type":"person_name"}]}'
    out = c._parse_entities(raw)
    assert out and out[0]["value"] == "张三"


def test_strip_multiline_think_block():
    c = make_client()
    raw = (
        "<think>\n第一步：找公司\n第二步：找人名\n</think>\n"
        '```json\n{"entities":[{"value":"星河研究院","type":"company_name"}]}\n```'
    )
    out = c._parse_entities(raw)
    assert out and out[0]["type"] == "company_name"


def test_chunking_long_text():
    c = make_client()
    c.max_chars_per_chunk = 20
    text = "\n".join(f"第{i}行内容文本" for i in range(20))
    chunks = c._chunk(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text
