"""accurate 模式（规则 + LLM）测试。

使用假的 LLM 客户端，不依赖真实模型/网络，验证：
  - LLM 实体的字符偏移回填正确（含重复值）
  - LLM 提供的 role/context_field 在规则判不出时作为回退
  - 强格式实体仍由正则负责
  - LLM 不可用时自动降级为 fast，请求不失败
  - LLM 幻觉（原文不存在的值）被丢弃
"""

from __future__ import annotations

from app.ner import NerEngine
from app.ner.engine import Candidate
from app.ner.llm_client import LlmUnavailable


class FakeLlm:
    """返回预设实体的假 LLM 客户端。"""

    def __init__(self, entities):
        self._entities = entities

    def extract(self, text: str):
        return list(self._entities)


class BrokenLlm:
    def extract(self, text: str):
        raise LlmUnavailable("模拟后端宕机")


def _find(entities, type_):
    return [e for e in entities if e["type"] == type_]


def test_llm_offset_backfill():
    text = "本协议由智算无界（北京）网络科技合伙企业与对方签订。"
    fake = FakeLlm([
        {"value": "智算无界（北京）网络科技合伙企业", "type": "company_name",
         "role": "party_a", "context_field": "签约主体", "confidence": 0.93},
    ])
    eng = NerEngine(llm_client=fake)
    ents, mode = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    assert mode == "accurate"
    comp = _find(ents, "company_name")
    assert comp, "应通过 LLM 识别出公司名"
    e = comp[0]
    # 偏移必须精确指向原文子串
    assert text[e["start"]:e["end"]] == e["value"]


def test_llm_role_field_fallback():
    # 自由文本，没有“甲方:”这类锚点，规则判不出角色，应回退到 LLM
    text = "委托方为云图科技服务集团，负责提供技术支持。"
    fake = FakeLlm([
        {"value": "云图科技服务集团", "type": "company_name",
         "role": "party_a", "context_field": "签约主体", "confidence": 0.9},
    ])
    eng = NerEngine(llm_client=fake)
    ents, _ = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    comp = _find(ents, "company_name")[0]
    # “委托方”是 party_a 关键词，规则其实能判出；这里确认结果为 party_a
    assert comp["role"] == "party_a"


def test_llm_role_pure_fallback():
    # 完全无角色关键词，必须依赖 LLM 的判定
    text = "合作单位星河数据研究院将参与本项目。"
    fake = FakeLlm([
        {"value": "星河数据研究院", "type": "company_name",
         "role": "party_b", "context_field": "签约主体", "confidence": 0.9},
    ])
    eng = NerEngine(llm_client=fake)
    ents, _ = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    comp = _find(ents, "company_name")[0]
    assert comp["role"] == "party_b"  # 来自 LLM 回退


def test_llm_hallucination_dropped():
    text = "本协议由星河数据研究院签订。"
    fake = FakeLlm([
        {"value": "根本不存在的公司XYZ", "type": "company_name",
         "role": "unknown", "context_field": "其他", "confidence": 0.9},
    ])
    eng = NerEngine(llm_client=fake)
    ents, _ = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    # 原文不含该值，应被丢弃
    assert not any(e["value"] == "根本不存在的公司XYZ" for e in ents)


def test_llm_duplicate_values_distinct_offsets():
    text = "星河研究院与星河研究院的关联方签署。"
    fake = FakeLlm([
        {"value": "星河研究院", "type": "company_name", "role": "party_a",
         "context_field": "签约主体", "confidence": 0.9},
        {"value": "星河研究院", "type": "company_name", "role": "party_b",
         "context_field": "签约主体", "confidence": 0.9},
    ])
    eng = NerEngine(llm_client=fake)
    ents, _ = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    comps = _find(ents, "company_name")
    starts = sorted(e["start"] for e in comps)
    # 两次出现应回填到不同位置
    assert len(set(starts)) == len(starts)


def test_strong_format_still_regex_in_accurate():
    text = "联系电话：13800138000，邮箱：a@b.com"
    fake = FakeLlm([])  # LLM 不返回任何东西
    eng = NerEngine(llm_client=fake)
    ents, _ = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    assert _find(ents, "phone"), "强格式电话仍应由正则识别"
    assert _find(ents, "email"), "强格式邮箱仍应由正则识别"


def test_graceful_degrade_on_llm_failure():
    text = "甲方：深圳市星幻科技有限公司"
    eng = NerEngine(llm_client=BrokenLlm())
    ents, mode = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    # LLM 宕机 -> 降级为 fast，但规则结果仍在
    assert mode == "fast"
    assert _find(ents, "company_name")


def test_accurate_without_client_is_fast():
    text = "甲方：深圳市星幻科技有限公司"
    eng = NerEngine(llm_client=None)
    ents, mode = eng.analyze(text, ["all"], 0.7, 100, mode="accurate")
    assert mode == "fast"
    assert _find(ents, "company_name")
