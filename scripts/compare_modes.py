"""对比 fast 与 accurate 两种模式的识别效果。

在部署了 API（启用 LLM 后端）的机器上运行：
    DESENTI_API_KEY=<key> python3 scripts/compare_modes.py

输出 Markdown 对比表，可直接贴进 README。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

API = os.environ.get("DESENTI_API", "http://127.0.0.1:8000/api/v1/contract/ner")
KEY = os.environ.get("DESENTI_API_KEY", "demo-key")

# 覆盖不同难度的样本：
#  - 自由文本中紧贴动词/连接词的公司名（规则易过度捕获）
#  - 无关键词锚点的公司名/人名/地址（规则易漏）
#  - 嵌套/复杂上下文的角色判定
#  - 强格式实体（两模式应一致，由正则负责）
SAMPLES = [
    ("自由文本公司名+人名",
     "本协议由智算无界（北京）网络科技合伙企业与乙方签订，经办人为周明远先生。"),
    ("无锚点公司名（受让方在后）",
     "鼎晖创世股权投资基金管理有限公司作为受让方，受让上述全部股权。"),
    ("口语化人名无锚点",
     "项目由王建国负责对接，技术问题可直接联系工程师李娜。"),
    ("地址夹在句中",
     "乙方将货物运送至承运人位于上海市浦东新区张江高科技园区博云路2号的仓库。"),
    ("多方角色嵌套",
     "甲方海创集团有限公司委托乙方中科软件技术股份有限公司开发，丙方天平律师事务所提供法律见证。"),
    ("强格式实体集合",
     "开户行：中国建设银行杭州西湖支行，账号6217 0012 3456 7890 123，信用代码91330100MA2H1234XY。"),
    ("简称与全称混用",
     "深圳前海微众银行股份有限公司（以下简称微众银行）为本协议的资金存管方。"),
    ("人名+职务无标点",
     "签约代表张伟出席，监事会成员陈晓红列席本次会议。"),
]


def call(text: str, mode: str) -> dict:
    body = json.dumps({"text": text, "options": {"mode": mode}}).encode("utf-8")
    req = urllib.request.Request(
        API,
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {KEY}"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.load(resp)
    elapsed = int((time.perf_counter() - t0) * 1000)
    return result, elapsed


def fmt_entities(result: dict) -> str:
    ents = result["data"]["entities"]
    if not ents:
        return "（无）"
    parts = []
    for e in ents:
        parts.append(f"{e['value']}｜{e['type']}｜{e['role']}｜{e['context_field']}")
    return "<br>".join(parts)


def main() -> int:
    print("# fast vs accurate 识别效果对比\n")
    print(f"模型：qwen3.5:9b（GPU）｜样本数：{len(SAMPLES)}\n")
    for title, text in SAMPLES:
        try:
            fast, fast_ms = call(text, "fast")
            acc, acc_ms = call(text, "accurate")
        except Exception as exc:  # noqa: BLE001
            print(f"## {title}\n\n调用失败：{exc}\n")
            continue
        print(f"## {title}\n")
        print(f"> {text}\n")
        print("| 模式 | 实体（值｜type｜role｜field） | 耗时 |")
        print("|------|------|------|")
        print(f"| fast | {fmt_entities(fast)} | {fast_ms}ms |")
        print(f"| accurate | {fmt_entities(acc)} | {acc_ms}ms |")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
