"""基于词典的敏感信息匹配（参考 contract-mask-with-nlp 的 dict_engine）。

加载 sensitive_dict.txt：
  - `[GROUP]` 段头指定该段词条的实体类型（group 即 entity type）；
  - 每行一个词条，精确匹配，英文忽略大小写；
  - `#` 注释与空行忽略；
  - 基于 mtime 的热更新：文件改动后下次访问自动重载。

命中优先级最高，可用于强制匹配已知实体或纠正规则/模型的错误切分
（例如把含「银行」的公司全称作为一个词条，避免被 bank_name 正则切碎）。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

# 词典分组名 -> 本项目实体类型。未列出的分组名按原样作为 type。
GROUP_TO_TYPE = {
    "CN_COMPANY": "company_name",
    "COMPANY": "company_name",
    "PERSON": "person_name",
    "CN_PERSON": "person_name",
    "CN_ADDRESS": "address",
    "ADDRESS": "address",
    "CN_BANK_NAME": "bank_name",
    "BANK_NAME": "bank_name",
    "CN_USCC": "credit_code",
    "CREDIT_CODE": "credit_code",
}


@dataclass
class DictEntry:
    term: str
    group: str
    term_lower: str = field(init=False)

    def __post_init__(self):
        self.term_lower = self.term.lower()


@dataclass
class DictHit:
    start: int
    end: int
    term: str
    entity_type: str


class SensitiveDict:
    def __init__(self, path: str | Path = "sensitive_dict.txt"):
        self._path = Path(path)
        self._entries: list[DictEntry] = []
        self._mtime: float = -1.0
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = []
            self._mtime = -1.0
            return
        mtime = self._path.stat().st_mtime
        if mtime == self._mtime:
            return  # 未变化
        entries: list[DictEntry] = []
        current_group = "company_name"
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"\[(.+?)\]", line)
            if m:
                current_group = m.group(1).strip()
                continue
            entries.append(DictEntry(term=line, group=current_group))
        self._entries = entries
        self._mtime = mtime

    @property
    def entries(self) -> list[DictEntry]:
        with self._lock:
            self._load()  # 访问时自动热更新
            return list(self._entries)

    def find_hits(self, text: str) -> list[DictHit]:
        """返回词典词条在文本中的所有精确匹配（英文忽略大小写）。

        重叠匹配：长词优先，同长按位置靠前。结果按 start 排序。
        """
        text_lower = text.lower()
        raw_hits: list[DictHit] = []
        for entry in self.entries:
            needle = entry.term_lower
            if not needle:
                continue
            etype = GROUP_TO_TYPE.get(entry.group, entry.group)
            start = 0
            while True:
                pos = text_lower.find(needle, start)
                if pos == -1:
                    break
                raw_hits.append(
                    DictHit(
                        start=pos,
                        end=pos + len(entry.term),
                        term=text[pos : pos + len(entry.term)],
                        entity_type=etype,
                    )
                )
                start = pos + 1  # 允许重叠搜索，下方去重
        raw_hits.sort(key=lambda h: (h.start, -(h.end - h.start)))
        merged: list[DictHit] = []
        for hit in raw_hits:
            if merged and hit.start < merged[-1].end:
                continue
            merged.append(hit)
        return merged


_dict_instances: dict[str, SensitiveDict] = {}
_dict_lock = threading.Lock()


def get_dict(path: str | Path = "sensitive_dict.txt") -> SensitiveDict:
    """按 path 缓存的实例。不同路径返回独立实例，相同路径复用。"""
    key = str(Path(path).resolve())
    if key not in _dict_instances:
        with _dict_lock:
            if key not in _dict_instances:
                _dict_instances[key] = SensitiveDict(path)
    return _dict_instances[key]
