"""spaCy NER 后端（参考 contract-mask-with-nlp 的 spaCy 处理）。

为 fast 模式提供语义实体兜底：
  - ORG  -> company_name（含 GPE+ORG 合并、公司后缀过滤、非公司前缀剔除）
  - PERSON -> person_name（中文 2-8 字、剔除常见误判词）
  - FAC/LOC -> address（建筑/地点补充）

设计：
  - 模型按名称缓存为进程级单例（trf 是 ~400MB BERT，重复加载会拖垮性能）；
  - 推理按模型加锁串行（spaCy nlp() 对同一管线并发不保证线程安全）；
  - 模型未安装 / 加载失败 / 推理异常一律优雅降级（返回空），
    由引擎回退到「字典 + 正则」，服务不中断。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger("desenti.spacy")

COMPANY_SUFFIXES = frozenset([
    "公司", "企业", "集团", "控股", "股份", "有限", "合伙",
    "事务所", "基金", "银行", "保险", "证券", "投资", "实业",
    "科技", "传媒", "文化", "网络", "信息", "技术", "工程",
    "建设", "贸易", "咨询", "服务", "发展", "电子", "医疗",
    "教育", "金融", "商贸", "物流", "能源", "地产", "置业",
    "研究院", "研究所", "中心",
])

NON_COMPANY_PREFIXES = frozenset([
    "甲方", "乙方", "双方", "第三方", "对方", "一方", "任何",
    "收款", "付款", "开户", "账户",
])

_NON_PERSON_WORDS = frozenset([
    "张贴", "送达", "签收", "披露", "接收", "甲方", "乙方", "双方",
    "第三方", "当事人", "代理人", "委托", "授权", "法院", "仲裁",
    "通知", "文书", "留置", "邮寄", "退回",
])

_NON_ADDRESS = frozenset([
    "中华人民共和国", "中国", "全国", "境内", "境外", "海外",
])

MAX_MERGE_GAP = 10  # GPE 与后续 ORG 合并的最大间隔字符数


@dataclass
class SpacySpan:
    start: int
    end: int
    entity_type: str
    value: str


class SpacyBackend:
    """封装一个 spaCy 中文管线，提供实体抽取与优雅降级。"""

    def __init__(self, model_name: str = "zh_core_web_trf"):
        self.model_name = model_name
        self._nlp = None
        self._load_attempted = False
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._ensure_loaded() is not None

    def _ensure_loaded(self):
        if self._nlp is not None:
            return self._nlp
        if self._load_attempted:
            return None
        with self._load_lock:
            if self._nlp is not None:
                return self._nlp
            if self._load_attempted:
                return None
            self._load_attempted = True
            try:
                import spacy  # 延迟导入：未装 spaCy 也不影响其他功能
                self._nlp = spacy.load(self.model_name)
                logger.info("spaCy 模型已加载: %s", self.model_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "spaCy 模型不可用，fast 模式降级为字典+正则: %s", exc
                )
                self._nlp = None
            return self._nlp

    def _analyze(self, text: str):
        nlp = self._ensure_loaded()
        if nlp is None:
            return None
        try:
            with self._infer_lock:  # 串行化推理，保证线程安全
                return nlp(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("spaCy 推理失败: %s", exc)
            return None

    # ------------------------------------------------------------------
    def extract(self, text: str) -> list[SpacySpan]:
        """返回 company_name / person_name / address 实体。模型不可用返回空。"""
        doc = self._analyze(text)
        if doc is None:
            return []
        spans: list[SpacySpan] = []
        spans += self._company_spans(doc, text)
        spans += self._person_spans(doc, text)
        spans += self._address_spans(doc)
        return spans

    # ------------------------------------------------------------------
    def _company_spans(self, doc, text: str) -> list[SpacySpan]:
        ents = list(doc.ents)
        out: list[SpacySpan] = []
        skip_next = False
        for i, ent in enumerate(ents):
            if skip_next:
                skip_next = False
                continue
            # 合并 GPE + (间隔≤MAX_MERGE_GAP) + ORG，如「北京」+「星辰科技有限公司」
            if ent.label_ == "GPE" and i + 1 < len(ents):
                nxt = ents[i + 1]
                gap = nxt.start_char - ent.end_char
                if nxt.label_ in ("ORG", "COMPANY") and 0 <= gap <= MAX_MERGE_GAP:
                    merged = text[ent.start_char : nxt.end_char]
                    if (any(kw in merged for kw in COMPANY_SUFFIXES)
                            and not any(merged.startswith(p) for p in NON_COMPANY_PREFIXES)):
                        out.append(SpacySpan(ent.start_char, nxt.end_char,
                                             "company_name", merged))
                        skip_next = True
                        continue
            if ent.label_ in ("ORG", "COMPANY"):
                val = ent.text
                if (any(kw in val for kw in COMPANY_SUFFIXES)
                        and not any(val.startswith(p) for p in NON_COMPANY_PREFIXES)):
                    out.append(SpacySpan(ent.start_char, ent.end_char,
                                         "company_name", val))
        return out

    def _person_spans(self, doc, text: str) -> list[SpacySpan]:
        out: list[SpacySpan] = []
        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            val = ent.text
            if any(w in val for w in _NON_PERSON_WORDS):
                continue
            cn = sum(1 for c in val if "\u4e00" <= c <= "\u9fa5")
            if cn > 0 and not (2 <= cn <= 8):
                continue
            out.append(SpacySpan(ent.start_char, ent.end_char, "person_name", val))
        return out

    def _address_spans(self, doc) -> list[SpacySpan]:
        out: list[SpacySpan] = []
        for ent in doc.ents:
            if ent.label_ not in ("FAC", "LOC"):
                continue
            val = ent.text.strip()
            if len(val) < 4 or val in _NON_ADDRESS:
                continue
            out.append(SpacySpan(ent.start_char, ent.end_char, "address", val))
        return out
