"""本地烟测：fast 模式的 字典 + 正则 + spaCy 级联。

需先安装 spaCy 与中文模型（如 zh_core_web_sm）：
    pip install -r requirements-nlp.txt
    python -m spacy download zh_core_web_sm
运行：
    DESENTI_SPACY_MODEL=zh_core_web_sm python scripts/smoke_fast_cascade.py
"""

from __future__ import annotations

import json
import os

from app.ner import NerEngine
from app.ner.dict_engine import SensitiveDict
from app.ner.spacy_backend import SpacyBackend

MODEL = os.environ.get("DESENTI_SPACY_MODEL", "zh_core_web_sm")

TEXTS = [
    "本协议由智算无界（北京）网络科技合伙企业与乙方签订，经办人为周明远先生。",
    "项目由王建国负责对接，技术问题可直接联系工程师李娜。",
    "深圳前海微众银行股份有限公司（以下简称微众银行）为本协议的资金存管方。",
]


def main() -> int:
    be = SpacyBackend(model_name=MODEL)
    print("spaCy available:", be.available)
    d = SensitiveDict(path="sensitive_dict.txt")
    eng = NerEngine(spacy_backend=be, sensitive_dict=d, spacy_confidence=0.75)
    for t in TEXTS:
        ents, mode = eng.analyze(t, ["all"], 0.7, 100, mode="fast")
        print("\n>", t)
        for e in ents:
            print(f"   {e['value']} | {e['type']} | {e['role']} | "
                  f"{e['context_field']} | {e['start']}-{e['end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
