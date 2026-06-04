"""LLM 后端客户端（OpenAI 兼容协议）。

用于 accurate 模式下识别语义类实体（公司名/人名/地址）及角色、字段。
通过 OpenAI 兼容的 /chat/completions 接口调用，因此 Ollama、vLLM、
以及任意兼容网关都可对接（部署在 EC2 host 上）。

设计要点：
  - LLM 只返回实体「值 + 类型 + 角色 + 字段」，不要求它给字符偏移
    （8B 模型数不准字符位置）；偏移由 engine 在原文回填。
  - 长文本按段落切块，分块调用后合并。
  - 任意异常（超时/连接失败/JSON 解析失败）都抛 LlmUnavailable，
    由 engine 决定降级为纯规则，绝不让请求失败。

注意：合同为敏感数据，LLM 必须是自托管后端（EC2 host），
正文不得发往任何第三方 API。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger("desenti.llm")

# 仅交给 LLM 处理的语义实体类型（强格式实体仍由正则负责）
LLM_ENTITY_TYPES = ("company_name", "person_name", "address")

_ROLE_VALUES = {"party_a", "party_b", "party_c", "unknown"}

_SYSTEM_PROMPT = (
    "你是中文合同信息抽取助手。从给定合同文本中识别以下三类实体：\n"
    "- company_name：公司/组织全称\n"
    "- person_name：人名（法定代表人、联系人等）\n"
    "- address：注册地址、经营地址等\n\n"
    "同时判定每个实体所属角色 role：\n"
    "- party_a（甲方/委托方/出让方/供方/卖方）\n"
    "- party_b（乙方/受托方/受让方/需方/买方）\n"
    "- party_c（丙方/第三方/担保方）\n"
    "- unknown（无法判定）\n\n"
    "以及语义字段 context_field，取值之一：\n"
    "签约主体、法定代表人、联系人、注册地址、经营地址、其他。\n\n"
    "严格只输出 JSON，格式为：\n"
    '{\"entities\":[{\"value\":\"...\",\"type\":\"company_name\",'
    '\"role\":\"party_a\",\"context_field\":\"签约主体\"}]}\n'
    "不要输出任何解释或额外文本。value 必须是原文中出现的精确子串。"
)


class LlmUnavailable(Exception):
    """LLM 后端不可用（超时/连接失败/响应不可解析）。"""


class LlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        max_chars_per_chunk: int,
        default_confidence: float,
        disable_thinking: bool = True,
        use_json_format: bool = False,
        api_style: str = "ollama",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_chars_per_chunk = max_chars_per_chunk
        self.default_confidence = default_confidence
        self.disable_thinking = disable_thinking
        self.use_json_format = use_json_format
        # api_style: "ollama" 用原生 /api/chat（正确支持 think=false，
        # 避免 Qwen3.5 在 OpenAI 兼容端点上无视该参数而过度生成推理 token，
        # 实测可将单次延迟从 ~60s 降到 ~4s）；"openai" 用 /v1/chat/completions
        # （vLLM 等标准 OpenAI 兼容后端）。
        self.api_style = api_style

    # ------------------------------------------------------------------
    def extract(self, text: str) -> list[dict]:
        """返回 LLM 识别的语义实体（不含字符偏移）。

        每项：{value, type, role, context_field, confidence}
        失败时抛 LlmUnavailable。
        """
        entities: list[dict] = []
        for chunk in self._chunk(text):
            entities.extend(self._extract_chunk(chunk))
        return entities

    # ------------------------------------------------------------------
    def _chunk(self, text: str) -> list[str]:
        if len(text) <= self.max_chars_per_chunk:
            return [text]
        # 按段落（空行/换行）累积切块，尽量不破坏语义边界
        chunks: list[str] = []
        buf: list[str] = []
        size = 0
        for line in text.splitlines(keepends=True):
            if size + len(line) > self.max_chars_per_chunk and buf:
                chunks.append("".join(buf))
                buf, size = [], 0
            buf.append(line)
            size += len(line)
        if buf:
            chunks.append("".join(buf))
        return chunks

    # ------------------------------------------------------------------
    def _extract_chunk(self, chunk: str) -> list[dict]:
        raw = self._call_chat(chunk)
        parsed = self._parse_entities(raw)
        return parsed

    def _call_chat(self, content: str) -> str:
        if self.api_style == "ollama":
            return self._call_ollama_native(content)
        return self._call_openai(content)

    def _ollama_root(self) -> str:
        """Ollama 原生 API 根路径。配置的 base_url 通常以 /v1 结尾
        （OpenAI 兼容），原生端点在其上一级。"""
        root = self.base_url
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root.rstrip("/")

    def _call_ollama_native(self, content: str) -> str:
        """调用 Ollama 原生 /api/chat。正确支持 think=false。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "options": {"temperature": 0},
        }
        if self.disable_thinking:
            payload["think"] = False
        if self.use_json_format:
            payload["format"] = "json"
        body = self._post_json(f"{self._ollama_root()}/api/chat", payload)
        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LlmUnavailable(f"Ollama 响应结构异常: {exc}") from exc

    def _call_openai(self, content: str) -> str:
        """调用 OpenAI 兼容 /v1/chat/completions（vLLM 等）。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "stream": False,
        }
        # response_format=json_object 在 llama.cpp + 混合架构模型上会触发
        # 昂贵的语法约束解码，故默认关闭，依赖提示 + 容错解析。
        if self.use_json_format:
            payload["response_format"] = {"type": "json_object"}
        if self.disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        body = self._post_json(f"{self.base_url}/chat/completions", payload)
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmUnavailable(f"LLM 响应结构异常: {exc}") from exc

    def _post_json(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmUnavailable(f"LLM 请求失败: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmUnavailable(f"LLM 响应非 JSON: {exc}") from exc

    # ------------------------------------------------------------------
    def _parse_entities(self, raw: str) -> list[dict]:
        obj = self._loads_lenient(raw)
        if not isinstance(obj, dict):
            raise LlmUnavailable("LLM 输出不是 JSON 对象")
        items = obj.get("entities", [])
        if not isinstance(items, list):
            raise LlmUnavailable("LLM 输出缺少 entities 数组")

        out: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            value = str(it.get("value", "")).strip()
            etype = str(it.get("type", "")).strip()
            if not value or etype not in LLM_ENTITY_TYPES:
                continue
            role = str(it.get("role", "unknown")).strip()
            if role not in _ROLE_VALUES:
                role = "unknown"
            context_field = str(it.get("context_field", "其他")).strip() or "其他"
            out.append(
                {
                    "value": value,
                    "type": etype,
                    "role": role,
                    "context_field": context_field,
                    "confidence": self.default_confidence,
                }
            )
        return out

    @staticmethod
    def _loads_lenient(raw: str):
        """容错解析：模型偶尔会包裹 ```json```、夹带前后缀文本，
        或（思考模式未完全关闭时）输出 <think>...</think> 块。"""
        raw = raw.strip()
        # 剥离思考块（防御性：即便已请求关闭思考，部分后端仍可能输出）
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 退而求其次：截取第一个 { 到最后一个 }
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(raw[start : end + 1])
                except json.JSONDecodeError as exc:
                    raise LlmUnavailable(f"无法解析 LLM JSON: {exc}") from exc
            raise LlmUnavailable("LLM 输出中未找到 JSON 对象")
