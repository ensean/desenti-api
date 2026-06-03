"""演示脚本：调用本地 NER API 并打印格式化结果。

用法：
    DESENTI_API_KEYS=demo-key uvicorn app.main:app --port 8077
    python scripts/demo_request.py
"""

from __future__ import annotations

import json
import sys
import urllib.request

API = "http://127.0.0.1:8077/api/v1/contract/ner"
KEY = "demo-key"


def main() -> int:
    with open("tests/sample_contract.txt", encoding="utf-8") as f:
        text = f.read()

    body = json.dumps(
        {"text": text, "options": {"min_confidence": 0.7, "context_window": 100}}
    ).encode("utf-8")

    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.load(resp)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
