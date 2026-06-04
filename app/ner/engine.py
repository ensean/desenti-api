"""混合 NER 引擎。

两种模式：
  - fast（默认）：字典 + 正则 + spaCy NER（参考 contract-mask-with-nlp）。
  - accurate：fast + 自托管 LLM（EC2 host）。

fast 模式识别优先级（高 -> 低，高优先级覆盖重叠的低优先级）：
  词典 > 字段锚定正则 > 强格式/通用正则 > spaCy NER

职责划分：
  - 字典：已知实体强制匹配，可纠正规则/模型的错误切分。
  - 强格式实体（信用代码/身份证/手机/邮箱/银行账号）：正则 + 校验，偏移精确。
  - 公司名/人名/地址：正则锚点 + spaCy NER 兜底；accurate 模式再叠加 LLM。
  - spaCy 模型不可用时 fast 自动降级为「字典 + 正则」。
  - LLM 不可用时 accurate 自动降级为 fast。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import patterns as P
from .dict_engine import SensitiveDict
from .llm_client import LlmClient, LlmUnavailable
from .spacy_backend import SpacyBackend
from .validators import validate_credit_code, validate_id_card

logger = logging.getLogger("desenti.engine")

# 各实体类型在重叠裁决中的优先级（数值越大越优先保留）
_PRIORITY = {
    "credit_code": 100,
    "id_card": 95,
    "email": 90,
    "phone": 85,
    "bank_account": 80,
    "account_name": 70,
    "bank_name": 65,
    "company_name": 60,
    "address": 55,
    "person_name": 50,
}

# 来源优先级（数值越大越优先保留）。重叠裁决先比来源，再比类型/跨度/置信度。
#   dict          词典强制匹配，最高（可纠正错误切分，如含「银行」的公司全称）
#   anchor        字段锚定（“法定代表人：”等），精确
#   regex_strong  强格式正则（信用代码/身份证/手机/邮箱/银行账号/开户行），权威
#   llm           LLM 语义实体（accurate）
#   spacy         spaCy NER 语义实体
#   regex_fallback 公司/地址的正则兜底，最弱（易过度捕获，让位于 spaCy/LLM 的干净跨度）
_SOURCE_PRIORITY = {
    "dict": 5,
    "anchor": 4,
    "regex_strong": 3,
    "llm": 2,
    "spacy": 1,
    "regex_fallback": 0,
}

_PUNCT = "：:（）()【】[]　 \t\u3000-、，,。;；"


@dataclass
class Candidate:
    value: str
    type: str
    start: int
    end: int
    confidence: float
    # 来源：dict / anchor / regex_strong / regex_fallback / spacy / llm
    source: str = "regex_strong"
    # LLM 提供的角色/字段，作为基于位置的规则判定失败时的回退
    llm_role: str | None = None
    llm_field: str | None = None


class NerEngine:
    def __init__(
        self,
        model_version: str = "1.0.0",
        llm_client: LlmClient | None = None,
        spacy_backend: SpacyBackend | None = None,
        sensitive_dict: SensitiveDict | None = None,
        spacy_confidence: float = 0.75,
    ):
        self.model_version = model_version
        self.llm_client = llm_client
        self.spacy_backend = spacy_backend
        self.sensitive_dict = sensitive_dict
        self.spacy_confidence = spacy_confidence

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def analyze(
        self,
        text: str,
        entity_types: list[str],
        min_confidence: float,
        context_window: int,
        mode: str = "fast",
    ) -> tuple[list[dict], str]:
        """识别实体。

        返回 (entities, used_mode)。used_mode 表示实际使用的模式：
        请求 accurate 但 LLM 不可用时会降级为 "fast"。
        """
        wanted = self._normalize_wanted(entity_types)

        candidates: list[Candidate] = []
        # 1) 词典命中（最高优先级）
        candidates += self._extract_dict_entities(text)
        # 2) 字段锚定 + 强格式正则
        candidates += self._extract_regex_entities(text)
        candidates += self._extract_anchored_entities(text)
        # 3) spaCy NER 兜底（公司/人名/地址）
        candidates += self._extract_spacy_entities(text)

        used_mode = "fast"
        if mode == "accurate" and self.llm_client is not None:
            try:
                candidates += self._extract_llm_entities(text)
                used_mode = "accurate"
            except LlmUnavailable as exc:
                logger.warning("LLM 不可用，降级为规则模式: %s", exc)
                used_mode = "fast"

        # 重叠裁决
        resolved = self._resolve_overlaps(candidates)

        results: list[dict] = []
        for c in resolved:
            if c.confidence < min_confidence:
                continue
            if wanted is not None and c.type not in wanted:
                continue
            role = self._detect_role(text, c.start, context_window)
            if role == "unknown" and c.llm_role:
                role = c.llm_role
            context_field = self._detect_context_field(text, c, context_window)
            if context_field == "其他" and c.llm_field:
                context_field = c.llm_field
            results.append(
                {
                    "value": c.value,
                    "type": c.type,
                    "role": role,
                    "start": c.start,
                    "end": c.end,
                    "confidence": round(c.confidence, 2),
                    "context_field": context_field,
                }
            )

        results.sort(key=lambda e: e["start"])
        return results, used_mode

    # ------------------------------------------------------------------
    # 实体类型过滤
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_wanted(entity_types: list[str]) -> set[str] | None:
        if not entity_types or "all" in entity_types:
            return None
        return set(entity_types)

    # ------------------------------------------------------------------
    # 正则实体抽取
    # ------------------------------------------------------------------
    def _extract_regex_entities(self, text: str) -> list[Candidate]:
        out: list[Candidate] = []

        # 统一社会信用代码：校验通过给高分；仅结构匹配（含脱敏/OCR 误差）
        # 仍以较高置信度上报，以满足脱敏场景的召回要求。
        for m in P.CREDIT_CODE_RE.finditer(text):
            val = m.group()
            conf = 0.99 if validate_credit_code(val) else 0.8
            out.append(Candidate(val, "credit_code", m.start(), m.end(), conf))

        # 身份证号
        for m in P.ID_CARD_RE.finditer(text):
            val = m.group()
            conf = 0.97 if validate_id_card(val) else 0.8
            out.append(Candidate(val, "id_card", m.start(), m.end(), conf))

        # 手机号
        for m in P.MOBILE_RE.finditer(text):
            out.append(Candidate(m.group(), "phone", m.start(), m.end(), 0.95))

        # 座机号
        for m in P.LANDLINE_RE.finditer(text):
            out.append(Candidate(m.group(), "phone", m.start(), m.end(), 0.85))

        # 邮箱
        for m in P.EMAIL_RE.finditer(text):
            out.append(Candidate(m.group(), "email", m.start(), m.end(), 0.97))

        # 银行账号
        for m in P.BANK_ACCOUNT_RE.finditer(text):
            raw = m.group()
            digits = re.sub(r"\s", "", raw)
            if not (12 <= len(digits) <= 19):
                continue
            out.append(Candidate(raw.strip(), "bank_account",
                                  m.start(), m.start() + len(raw.rstrip()), 0.85))

        # 公司名称（正则兜底，最弱：易过度捕获，让位于 spaCy/LLM 的干净跨度）
        for m in P.COMPANY_RE.finditer(text):
            out.append(Candidate(m.group(), "company_name", m.start(), m.end(),
                                 0.9, source="regex_fallback"))

        # 开户行/银行名称（强格式，较可靠）
        for m in P.BANK_NAME_RE.finditer(text):
            out.append(Candidate(m.group(), "bank_name", m.start(), m.end(), 0.9))

        # 地址（正则兜底，易过度捕获）
        for m in P.ADDRESS_RE.finditer(text):
            val = m.group()
            if len(val) < 6:
                continue
            out.append(Candidate(val, "address", m.start(), m.end(),
                                 0.78, source="regex_fallback"))

        return out

    # ------------------------------------------------------------------
    # 锚点实体抽取（人名 / 户名）
    # ------------------------------------------------------------------
    def _extract_anchored_entities(self, text: str) -> list[Candidate]:
        out: list[Candidate] = []

        # 人名：在“法定代表人/联系人/经办人/法人”等关键词后抽取 2-4 个汉字
        person_anchors = ["法定代表人", "法定代表", "法人代表", "法人",
                          "联系人", "经办人", "负责人", "委托代理人", "代理人"]
        for anchor in person_anchors:
            for m in re.finditer(re.escape(anchor), text):
                name = self._capture_name_after(text, m.end())
                if name:
                    start, val = name
                    out.append(Candidate(val, "person_name", start,
                                         start + len(val), 0.88, source="anchor"))

        # 户名：在“户名/账户名称/开户名”后抽取（可能是公司名或人名）
        account_anchors = ["户名", "账户名称", "开户名", "开户名称"]
        for anchor in account_anchors:
            for m in re.finditer(re.escape(anchor), text):
                val = self._capture_account_name_after(text, m.end())
                if val:
                    start, name = val
                    out.append(Candidate(name, "account_name", start,
                                         start + len(name), 0.86, source="anchor"))

        return out

    @staticmethod
    def _capture_name_after(text: str, pos: int) -> tuple[int, str] | None:
        """跳过分隔符后抽取 2-4 个连续汉字作为人名。"""
        i = pos
        n = len(text)
        while i < n and text[i] in _PUNCT:
            i += 1
        start = i
        chars: list[str] = []
        while i < n and len(chars) < 4 and "\u4e00" <= text[i] <= "\u9fa5":
            chars.append(text[i])
            i += 1
        if len(chars) >= 2:
            return start, "".join(chars)
        return None

    @staticmethod
    def _capture_account_name_after(text: str, pos: int) -> tuple[int, str] | None:
        """抽取户名：分隔符后到行尾/标点前的非空内容（公司名或人名）。"""
        i = pos
        n = len(text)
        while i < n and text[i] in _PUNCT:
            i += 1
        start = i
        stop = "\n\r，,。;；、 \t\u3000"
        chars: list[str] = []
        while i < n and text[i] not in stop and len(chars) < 40:
            chars.append(text[i])
            i += 1
        val = "".join(chars).strip()
        if len(val) >= 2:
            return start, val
        return None

    # ------------------------------------------------------------------
    # 词典实体抽取（最高优先级）
    # ------------------------------------------------------------------
    def _extract_dict_entities(self, text: str) -> list[Candidate]:
        if self.sensitive_dict is None:
            return []
        out: list[Candidate] = []
        for hit in self.sensitive_dict.find_hits(text):
            out.append(
                Candidate(
                    value=hit.term,
                    type=hit.entity_type,
                    start=hit.start,
                    end=hit.end,
                    confidence=0.99,
                    source="dict",
                )
            )
        return out

    # ------------------------------------------------------------------
    # spaCy NER 兜底（公司/人名/地址）
    # ------------------------------------------------------------------
    def _extract_spacy_entities(self, text: str) -> list[Candidate]:
        if self.spacy_backend is None:
            return []
        out: list[Candidate] = []
        for sp in self.spacy_backend.extract(text):
            out.append(
                Candidate(
                    value=sp.value,
                    type=sp.entity_type,
                    start=sp.start,
                    end=sp.end,
                    confidence=self.spacy_confidence,
                    source="spacy",
                )
            )
        return out

    # ------------------------------------------------------------------
    # LLM 实体抽取（accurate 模式）+ 偏移回填
    # ------------------------------------------------------------------
    def _extract_llm_entities(self, text: str) -> list[Candidate]:
        """调用 LLM 取语义实体值，再在原文中定位字符偏移。

        LLM 不给偏移，本方法负责把每个 value 回填到原文位置：
        - 同一 value 出现多次时，依次占用尚未被使用的位置；
        - value 不在原文中（模型幻觉/改写）则丢弃，保证 start/end 精确。
        """
        llm_entities = self.llm_client.extract(text)

        # 记录每个 value 已消费到的搜索起点，处理重复出现
        search_from: dict[str, int] = {}
        out: list[Candidate] = []
        for ent in llm_entities:
            value = ent["value"]
            if not value:
                continue
            start = text.find(value, search_from.get(value, 0))
            if start == -1:
                # 该值未出现在原文（可能是模型改写），尝试从头再找一次
                start = text.find(value)
                if start == -1:
                    logger.debug("丢弃未定位到原文的 LLM 实体: %r", value)
                    continue
            end = start + len(value)
            search_from[value] = end
            out.append(
                Candidate(
                    value=value,
                    type=ent["type"],
                    start=start,
                    end=end,
                    confidence=ent.get("confidence", 0.9),
                    source="llm",
                    llm_role=ent.get("role") or None,
                    llm_field=ent.get("context_field") or None,
                )
            )
        return out

    # ------------------------------------------------------------------
    # 重叠裁决
    # ------------------------------------------------------------------
    def _resolve_overlaps(self, candidates: list[Candidate]) -> list[Candidate]:
        # 去重完全相同的 (start, end, type)
        unique: dict[tuple[int, int, str], Candidate] = {}
        for c in candidates:
            key = (c.start, c.end, c.type)
            if key not in unique or c.confidence > unique[key].confidence:
                unique[key] = c
        items = list(unique.values())

        # 排序：来源优先级 -> 类型优先级 -> 跨度长度 -> 置信度。
        # 来源优先于类型，使词典能纠正含「银行」的公司全称切分（词典 company_name
        # 覆盖 regex bank_name），并让 spaCy/LLM 的干净语义跨度覆盖正则兜底的过度捕获。
        items.sort(
            key=lambda c: (
                _SOURCE_PRIORITY.get(c.source, 0),
                _PRIORITY.get(c.type, 0),
                c.end - c.start,
                c.confidence,
            ),
            reverse=True,
        )

        accepted: list[Candidate] = []
        for c in items:
            if any(self._overlaps(c, a) for a in accepted):
                continue
            accepted.append(c)
        return accepted

    @staticmethod
    def _overlaps(a: Candidate, b: Candidate) -> bool:
        return a.start < b.end and b.start < a.end

    # ------------------------------------------------------------------
    # 角色判定（第 4 节）：向前 context_window 字符匹配最近角色关键词
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_role(text: str, start: int, context_window: int) -> str:
        win_start = max(0, start - context_window)
        window = text[win_start:start]
        best_role = "unknown"
        best_pos = -1
        for role, keywords in P.ROLE_KEYWORDS.items():
            for kw in keywords:
                idx = window.rfind(kw)
                if idx > best_pos:
                    best_pos = idx
                    best_role = role
        return best_role

    # ------------------------------------------------------------------
    # context_field 判定（第 5 节）
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_context_field(text: str, c: Candidate, context_window: int) -> str:
        win_start = max(0, c.start - context_window)
        window = text[win_start:c.start]

        # 收集窗口内所有命中的字段锚点关键词：(start, end, field)
        matches: list[tuple[int, int, str]] = []
        for field, keywords, types in P.CONTEXT_FIELD_ANCHORS:
            if c.type not in types:
                continue
            for kw in keywords:
                idx = window.rfind(kw)
                if idx >= 0:
                    matches.append((idx, idx + len(kw), field))

        if not matches:
            return "其他"

        # 过滤被更长关键词覆盖的命中（如“地址”被“注册地址”覆盖），
        # 保留更具体的字段。
        def covered(m: tuple[int, int, str]) -> bool:
            return any(
                o is not m and o[0] <= m[0] and o[1] >= m[1] and (o[1] - o[0]) > (m[1] - m[0])
                for o in matches
            )

        filtered = [m for m in matches if not covered(m)]
        # 选择离实体最近的命中（start 最大）
        best = max(filtered, key=lambda m: m[0])
        return best[2]
