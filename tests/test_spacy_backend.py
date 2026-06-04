"""spaCy 后端测试：优雅降级 + 实体后处理逻辑（用假 Doc，不依赖真实模型）。"""

from __future__ import annotations

from app.ner import NerEngine
from app.ner.spacy_backend import SpacyBackend, SpacySpan


# ---------------- 优雅降级 ----------------

def test_unavailable_model_degrades():
    # 用一个不存在的模型名，加载必然失败 -> available=False, extract 返回空
    be = SpacyBackend(model_name="__no_such_model__")
    assert be.available is False
    assert be.extract("深圳市星幻科技有限公司与对方签约") == []


def test_engine_fast_works_without_spacy():
    # spacy_backend=None 时 fast 仍由字典+正则工作
    eng = NerEngine(spacy_backend=None)
    ents, mode = eng.analyze("法定代表人：张三 电话：13800138000", ["all"], 0.7, 100)
    assert mode == "fast"
    assert any(e["type"] == "phone" for e in ents)


# ---------------- 后处理逻辑（注入假 Doc） ----------------

class _FakeEnt:
    def __init__(self, label, start, end, text):
        self.label_ = label
        self.start_char = start
        self.end_char = end
        self.text = text


class _FakeDoc:
    def __init__(self, ents):
        self.ents = ents


class _StubBackend(SpacyBackend):
    """绕过真实模型，直接喂预设的 ents 给后处理。"""

    def __init__(self, ents):
        super().__init__(model_name="stub")
        self._ents = ents

    def _analyze(self, text):
        return _FakeDoc(self._ents)


def test_company_suffix_filter():
    text = "北京仲裁委员会与星辰科技有限公司"
    # 仲裁委员会无公司后缀 -> 过滤；星辰科技有限公司保留
    ents = [
        _FakeEnt("ORG", 2, 7, "仲裁委员会"),
        _FakeEnt("ORG", 8, 16, "星辰科技有限公司"),
    ]
    be = _StubBackend(ents)
    out = be.extract(text)
    vals = [s.value for s in out if s.entity_type == "company_name"]
    assert "星辰科技有限公司" in vals
    assert "仲裁委员会" not in vals


def test_gpe_org_merge():
    text = "北京星辰科技有限公司签约"
    # spaCy 常把它切成 GPE(北京)+ORG(科技有限公司)，应合并
    ents = [
        _FakeEnt("GPE", 0, 2, "北京"),
        _FakeEnt("ORG", 4, 11, "科技有限公司"),
    ]
    be = _StubBackend(ents)
    out = be.extract(text)
    comps = [s for s in out if s.entity_type == "company_name"]
    assert comps and comps[0].start == 0 and comps[0].end == 11


def test_person_filtering():
    text = "签约代表张伟，甲方代理人到场"
    ents = [
        _FakeEnt("PERSON", 4, 6, "张伟"),
        _FakeEnt("PERSON", 7, 9, "甲方"),  # 误判词，应过滤
    ]
    be = _StubBackend(ents)
    out = be.extract(text)
    persons = [s.value for s in out if s.entity_type == "person_name"]
    assert "张伟" in persons
    assert "甲方" not in persons


def test_address_fac_loc():
    text = "仓库位于碧波路690号3号楼附近"
    ents = [_FakeEnt("FAC", 4, 14, "碧波路690号3号楼")]
    be = _StubBackend(ents)
    out = be.extract(text)
    addrs = [s for s in out if s.entity_type == "address"]
    assert addrs and addrs[0].value == "碧波路690号3号楼"


def test_spacy_via_engine_overrides_regex_fallback():
    """spaCy 的干净公司跨度应覆盖正则兜底的过度捕获。"""
    text = "本协议由星辰科技有限公司与对方签订。"
    # spaCy 给出干净跨度（不含“本协议由”）
    ents = [_FakeEnt("ORG", 4, 12, "星辰科技有限公司")]
    eng = NerEngine(spacy_backend=_StubBackend(ents))
    out, _ = eng.analyze(text, ["all"], 0.7, 100, mode="fast")
    comps = [e for e in out if e["type"] == "company_name"]
    assert comps
    assert comps[0]["value"] == "星辰科技有限公司"
