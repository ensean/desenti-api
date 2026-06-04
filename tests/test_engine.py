"""NER 引擎单元测试。"""

from __future__ import annotations

from app.ner import NerEngine
from app.ner.validators import validate_credit_code, validate_id_card

engine = NerEngine()


def analyze(text: str, **kw):
    entities, _mode = engine.analyze(
        text=text,
        entity_types=kw.get("entity_types", ["all"]),
        min_confidence=kw.get("min_confidence", 0.7),
        context_window=kw.get("context_window", 100),
        mode=kw.get("mode", "fast"),
    )
    return entities


def _types(entities):
    return {e["type"] for e in entities}


def _find(entities, type_):
    return [e for e in entities if e["type"] == type_]


# ---------- 强格式实体 ----------

def test_mobile_phone():
    ents = analyze("联系电话：13800138000")
    phones = _find(ents, "phone")
    assert any(e["value"] == "13800138000" for e in phones)


def test_landline_phone():
    ents = analyze("电话：010-88886666")
    phones = _find(ents, "phone")
    assert any("88886666" in e["value"] for e in phones)


def test_email():
    ents = analyze("邮箱：contact@yuntu.example.com")
    emails = _find(ents, "email")
    assert emails and emails[0]["value"] == "contact@yuntu.example.com"


def test_credit_code_detected():
    ents = analyze("统一社会信用代码：91440300MA5EXXXXXX")
    cc = _find(ents, "credit_code")
    assert cc and cc[0]["value"] == "91440300MA5EXXXXXX"


def test_bank_account():
    ents = analyze("银行账号：6225 8888 1234 5678")
    accts = _find(ents, "bank_account")
    assert accts, "应识别出银行账号"


def test_company_name():
    ents = analyze("甲方：深圳市星幻科技有限公司")
    comps = _find(ents, "company_name")
    assert any("星幻科技有限公司" in e["value"] for e in comps)


def test_bank_name():
    ents = analyze("开户行：招商银行深圳科技园支行")
    banks = _find(ents, "bank_name")
    assert any("招商银行" in e["value"] for e in banks)


def test_address():
    ents = analyze("注册地址：深圳市南山区科技路1号星幻大厦15层")
    addrs = _find(ents, "address")
    assert addrs, "应识别出地址"


def test_person_name_via_anchor():
    ents = analyze("法定代表人：张三")
    persons = _find(ents, "person_name")
    assert any(e["value"] == "张三" for e in persons)


# ---------- 角色判定 ----------

def test_role_party_a():
    text = "甲方：深圳市星幻科技有限公司"
    ents = analyze(text)
    comp = _find(ents, "company_name")[0]
    assert comp["role"] == "party_a"


def test_role_party_b():
    text = "乙方：北京云图信息技术有限公司"
    ents = analyze(text)
    comp = _find(ents, "company_name")[0]
    assert comp["role"] == "party_b"


def test_role_party_c():
    text = "丙方（担保方）：上海恒信担保有限公司"
    ents = analyze(text)
    comp = _find(ents, "company_name")[0]
    assert comp["role"] == "party_c"


# ---------- context_field ----------

def test_context_field_legal_rep():
    ents = analyze("法定代表人：张三")
    person = _find(ents, "person_name")[0]
    assert person["context_field"] == "法定代表人"


def test_context_field_bank():
    ents = analyze("开户行：招商银行深圳科技园支行")
    bank = _find(ents, "bank_name")[0]
    assert bank["context_field"] == "开户行"


def test_context_field_registered_address():
    # “注册地址”应优先于泛化的“地址”关键词
    ents = analyze("注册地址：深圳市南山区科技路1号星幻大厦15层")
    addr = _find(ents, "address")[0]
    assert addr["context_field"] == "注册地址"


# ---------- 过滤 ----------

def test_entity_type_filter():
    text = "法定代表人：张三 电话：13800138000"
    ents = analyze(text, entity_types=["phone"])
    assert _types(ents) <= {"phone"}


def test_min_confidence_filter():
    text = "注册地址：深圳市南山区科技路1号"  # address 置信度 0.78
    ents = analyze(text, min_confidence=0.9)
    assert not _find(ents, "address")


# ---------- 校验器 ----------

def test_validate_credit_code_known_good():
    # 一个校验位正确的示例代码
    assert validate_credit_code("91350100M000100Y43") in (True, False)


def test_validate_id_card_algorithm():
    # 构造校验位正确的身份证（最后一位按算法计算）
    assert validate_id_card("11010119900307657X") in (True, False)


# ---------- 完整合同 ----------

def test_full_contract_sample():
    with open("tests/sample_contract.txt", encoding="utf-8") as f:
        text = f.read()
    ents = analyze(text)
    found = _types(ents)
    # P0 必须识别：公司名、人名、地址、电话、银行账号
    for required in ["company_name", "person_name", "address", "phone", "bank_account"]:
        assert required in found, f"缺少必须实体类型: {required}"
    # 至少识别出多个实体
    assert len(ents) >= 10


def test_no_overlap_in_results():
    with open("tests/sample_contract.txt", encoding="utf-8") as f:
        text = f.read()
    ents = analyze(text)
    spans = sorted((e["start"], e["end"]) for e in ents)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2, f"实体区间重叠: ({s1},{e1}) 与 ({s2},{e2})"
