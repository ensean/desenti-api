"""混合 NER 引擎。

策略（见需求第 9 节）：
  - 强格式实体（信用代码/身份证/手机/邮箱/银行账号）用正则 + 校验，高精度；
  - 公司名/开户行/地址用正则关键词锚点抽取；
  - 人名/户名用上下文关键词锚点抽取（避免泛汉字误匹配）；
  - 角色判定：向前 context_window 字符匹配最近角色关键词（第 4 节）；
  - context_field：向前匹配最近的字段锚点关键词（第 5 节）。

该引擎为纯 CPU、无外部模型依赖，可独立运行；如需接入 spaCy /
BERT / LAC，可在 _extract_model_entities 中扩展并合并结果。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import patterns as P
from .validators import validate_credit_code, validate_id_card

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

_PUNCT = "：:（）()【】[]　 \t\u3000-、，,。;；"


@dataclass
class Candidate:
    value: str
    type: str
    start: int
    end: int
    confidence: float


class NerEngine:
    def __init__(self, model_version: str = "1.0.0"):
        self.model_version = model_version

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------
    def analyze(
        self,
        text: str,
        entity_types: list[str],
        min_confidence: float,
        context_window: int,
    ) -> list[dict]:
        wanted = self._normalize_wanted(entity_types)

        candidates: list[Candidate] = []
        candidates += self._extract_regex_entities(text)
        candidates += self._extract_anchored_entities(text)

        # 重叠裁决
        resolved = self._resolve_overlaps(candidates)

        results: list[dict] = []
        for c in resolved:
            if c.confidence < min_confidence:
                continue
            if wanted is not None and c.type not in wanted:
                continue
            role = self._detect_role(text, c.start, context_window)
            context_field = self._detect_context_field(text, c, context_window)
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
        return results

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

        # 公司名称
        for m in P.COMPANY_RE.finditer(text):
            out.append(Candidate(m.group(), "company_name", m.start(), m.end(), 0.9))

        # 开户行/银行名称
        for m in P.BANK_NAME_RE.finditer(text):
            out.append(Candidate(m.group(), "bank_name", m.start(), m.end(), 0.9))

        # 地址
        for m in P.ADDRESS_RE.finditer(text):
            val = m.group()
            if len(val) < 6:
                continue
            out.append(Candidate(val, "address", m.start(), m.end(), 0.78))

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
                                         start + len(val), 0.88))

        # 户名：在“户名/账户名称/开户名”后抽取（可能是公司名或人名）
        account_anchors = ["户名", "账户名称", "开户名", "开户名称"]
        for anchor in account_anchors:
            for m in re.finditer(re.escape(anchor), text):
                val = self._capture_account_name_after(text, m.end())
                if val:
                    start, name = val
                    out.append(Candidate(name, "account_name", start,
                                         start + len(name), 0.86))

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

        # 排序：优先级 -> 跨度长度 -> 置信度
        items.sort(
            key=lambda c: (
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
