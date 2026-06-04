"""词典引擎与词典优先级测试（不依赖 spaCy）。"""

from __future__ import annotations

from pathlib import Path

from app.ner import NerEngine
from app.ner.dict_engine import SensitiveDict


def _write_dict(tmp_path: Path, content: str) -> SensitiveDict:
    f = tmp_path / "dict.txt"
    f.write_text(content, encoding="utf-8")
    return SensitiveDict(path=f)


def _find(entities, type_):
    return [e for e in entities if e["type"] == type_]


def test_dict_basic_match(tmp_path):
    d = _write_dict(tmp_path, "[company_name]\n星河数据研究院\n")
    hits = d.find_hits("本协议由星河数据研究院提供服务。")
    assert len(hits) == 1
    assert hits[0].term == "星河数据研究院"
    assert hits[0].entity_type == "company_name"


def test_dict_group_alias(tmp_path):
    # 参考项目别名 CN_COMPANY 应映射到 company_name
    d = _write_dict(tmp_path, "[CN_COMPANY]\n云图科技\n")
    hits = d.find_hits("云图科技与对方签约")
    assert hits[0].entity_type == "company_name"


def test_dict_english_case_insensitive(tmp_path):
    d = _write_dict(tmp_path, "[company_name]\nAcme Corp\n")
    hits = d.find_hits("the counterparty is ACME CORP today")
    assert len(hits) == 1
    assert hits[0].term == "ACME CORP"  # 保留原文大小写


def test_dict_longer_term_wins(tmp_path):
    d = _write_dict(tmp_path, "[company_name]\n微众\n深圳前海微众银行股份有限公司\n")
    hits = d.find_hits("深圳前海微众银行股份有限公司为存管方")
    # 长词优先，不应同时返回“微众”
    assert len(hits) == 1
    assert hits[0].term == "深圳前海微众银行股份有限公司"


def test_dict_hot_reload(tmp_path):
    f = tmp_path / "dict.txt"
    f.write_text("[company_name]\n甲公司\n", encoding="utf-8")
    d = SensitiveDict(path=f)
    assert len(d.find_hits("甲公司")) == 1
    # 改写文件后应自动重载
    import os, time
    time.sleep(0.01)
    f.write_text("[company_name]\n乙公司\n", encoding="utf-8")
    os.utime(f, None)
    assert len(d.find_hits("甲公司")) == 0
    assert len(d.find_hits("乙公司")) == 1


def test_dict_overrides_bank_name_split(tmp_path):
    """核心场景：词典把含「银行」的公司全称作为整体，纠正 bank_name 切分。"""
    d = _write_dict(tmp_path, "[company_name]\n深圳前海微众银行股份有限公司\n")
    eng = NerEngine(sensitive_dict=d)
    ents, _ = eng.analyze(
        "深圳前海微众银行股份有限公司（以下简称微众银行）为本协议的资金存管方。",
        ["all"], 0.7, 100, mode="fast",
    )
    comps = _find(ents, "company_name")
    # 全称作为单一公司名出现
    assert any(e["value"] == "深圳前海微众银行股份有限公司" for e in comps)
    # 不应再有把全称切碎的 bank_name 片段与之重叠
    for e in ents:
        if e["type"] == "bank_name":
            assert not (e["start"] < 14 and e["end"] > 0)


def test_dict_missing_file_graceful(tmp_path):
    d = SensitiveDict(path=tmp_path / "nonexistent.txt")
    assert d.find_hits("任意文本") == []
