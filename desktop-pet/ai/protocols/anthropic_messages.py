# -*- coding: utf-8 -*-
"""Anthropic 原生协议适配器（`/v1/messages`）。

这是本方案里**第一个真正的异构协议**，用来验证"适配器接缝"是否成立
（DESIGN_AI_PROVIDERS.md §八 待拍板第 1 条：先只做 Anthropic，一个就够）。

与 OpenAI 兼容协议的**关键差异**（每一条都是踩点）：
1. `system` 是**顶层字段**，不是 role="system" 的消息
2. `max_tokens` **必填**
3. 鉴权是 `x-api-key` 头 + **必需的** `anthropic-version` 头
4. 响应是 `content` **块数组**（text / thinking 块要分开取）
5. **`thinking` 与 `temperature` 互斥** —— 一旦开思考就绝不能带 temperature
6. 流式事件名是 `content_block_delta` / `message_stop`，不是 `[DONE]`
"""

import json

from curl_cffi import requests as cffi_requests

from ai.protocols.base import (
    KIND_DONE, KIND_TEXT, KIND_THINK, ProtocolAdapter, StreamEvent,
)

ANTHROPIC_VERSION = "2023-06-01"
MESSAGES_PATH = "/v1/messages"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 120

#: 思考档位 → budget_tokens 的启发式映射。
#: 真实 API 只吃 budget_tokens，而注册表里给的是人类档位名（§11.3）——
#: 这层换算必须放在适配器里，不能污染数据层。
EFFORT_BUDGETS = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32768,
}

#: 真实 API 的预算下限（注册表里以 reasoning_min 表达，这里做最后一道兜底）
MIN_BUDGET = 1024


class AnthropicError(Exception):
    """Anthropic API 错误。"""


class AnthropicAdapter(ProtocolAdapter):
    """Anthropic `/v1/messages` 适配器。"""

    protocol = "anthropic"

    def __init__(self, api_key, base_url, model="", auth="x-api-key",
                 max_tokens=DEFAULT_MAX_TOKENS, error_cls=None,
                 extra_headers=None, timeout=DEFAULT_TIMEOUT,
                 api_key_optional=False, chat_path=MESSAGES_PATH, **_ignored):
        if not api_key_optional and not (api_key or "").strip():
            raise (error_cls or AnthropicError)("未配置 Anthropic API Key")
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.model = model or ""
        self.auth = auth or "x-api-key"
        self.max_tokens = int(max_tokens or DEFAULT_MAX_TOKENS)
        self.chat_path = chat_path or MESSAGES_PATH
        self.error_cls = error_cls or AnthropicError
        self.extra_headers = dict(extra_headers or {})
        self._session = cffi_requests.Session(
            impersonate="chrome131", timeout=timeout)

    # ---------------- 组装 ----------------

    def _url(self):
        return self.base_url + self.chat_path

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self.api_key:
            h["x-api-key"] = self.api_key
        h.update(self.extra_headers)
        return h

    @staticmethod
    def _split_system(messages):
        """把 role=system 的消息抽成顶层 system（差异 1）。"""
        systems, rest = [], []
        for m in (messages or []):
            if m.get("role") == "system":
                c = m.get("content")
                if c:
                    systems.append(str(c))
            else:
                rest.append({"role": m.get("role") or "user",
                             "content": m.get("content") or ""})
        return "\n\n".join(systems), rest

    def _thinking_block(self, thinking, model=None):
        """档位 → thinking 块。

        三种输入都会接受（对应注册表的三种 `reasoning_kind`）：
        * effort 档位名（`low`/`medium`/…）→ 查表换算成预算
        * **纯数字**（预算型模型 UI 直接给 token 数）→ 原样用，仅做下限兜底
        * `True`（旧调用方的布尔开关）→ 取中档
        """
        if not thinking:
            return None
        variant = "high" if thinking is True else str(thinking).strip()
        if variant in ("", "off", "none", "false", "0"):
            return None
        if variant.isdigit():
            budget = int(variant)
        else:
            budget = EFFORT_BUDGETS.get(variant, EFFORT_BUDGETS["medium"])
        budget = max(int(budget), MIN_BUDGET)
        return {"type": "enabled", "budget_tokens": budget}, budget

    def _build_body(self, messages, thinking, model, stream, tools=None):
        system, msgs = self._split_system(messages)
        body = {
            "model": model or self.model,
            "messages": msgs,
            "stream": bool(stream),
        }
        if system:
            body["system"] = system
        if tools:
            # OpenAI 风格 tools → Anthropic 格式（name/description/input_schema）
            body["tools"] = [
                {
                    "name": (t.get("function") or {}).get("name") or "",
                    "description": ((t.get("function") or {}).get("description")
                                    or ""),
                    "input_schema": ((t.get("function") or {})
                                     .get("parameters")
                                     or {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        block = self._thinking_block(thinking)
        max_tokens = self.max_tokens
        if block:
            body["thinking"] = block[0]
            # 开思考时 max_tokens 必须留出思考预算，否则会被截断
            max_tokens = max(max_tokens, block[1] + 1024)
            # 差异 5：**绝不能**同时带 temperature（原版 API 会直接报错）
            body.pop("temperature", None)
        body["max_tokens"] = max_tokens
        return body

    def _raise_http(self, resp):
        try:
            payload = resp.json()
            err = (payload.get("error") or {}).get("message") or resp.text[:200]
        except Exception:
            err = resp.text[:200]
        raise self.error_cls("请求失败（HTTP %s）：%s" % (resp.status_code, err))

    @staticmethod
    def _text_from_blocks(blocks):
        """从 content 块数组里取正文（差异 4：thinking 块要跳过）。"""
        parts = []
        for b in (blocks or []):
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text") or "")
        return "".join(parts).strip()

    # ---------------- 非流式 ----------------

    def chat_messages(self, messages, thinking=False, model=None):
        body = self._build_body(messages, thinking, model, stream=False)
        resp = self._session.post(self._url(), json=body, headers=self._headers())
        if resp.status_code != 200:
            self._raise_http(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise self.error_cls("返回异常，请重试")
        return self._text_from_blocks(payload.get("content"))

    def chat_message(self, messages, thinking=False, model=None, tools=None):
        """带 tools 的完整 message（P1-1）：把 Anthropic 的 tool_use 块转成 OpenAI 风格。"""
        body = self._build_body(messages, thinking, model, stream=False,
                                tools=tools)
        resp = self._session.post(self._url(), json=body, headers=self._headers())
        if resp.status_code != 200:
            self._raise_http(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise self.error_cls("返回异常，请重试")
        out = {"role": "assistant",
               "content": self._text_from_blocks(payload.get("content")),
               "tool_calls": None}
        calls = []
        for b in payload.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                calls.append({
                    "id": b.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": b.get("name") or "",
                        "arguments": json.dumps(b.get("input") or {},
                                                ensure_ascii=False),
                    },
                })
        if calls:
            out["tool_calls"] = calls
        return out

    def supports_tools(self):
        return True

    # ---------------- 流式 ----------------

    def stream_events(self, messages, thinking=False, model=None,
                      audio_voice=""):
        # audio_voice 接受但忽略：Anthropic 没有"模型出语音"的原生能力。
        # 调用方只在模型声明 output_modalities 含 audio 时才传，所以这里不会静默丢东西。
        body = self._build_body(messages, thinking, model, stream=True)
        resp = self._session.post(self._url(), json=body,
                                  headers=self._headers(), stream=True)
        if resp.status_code != 200:
            self._raise_http(resp)
        buf = ""
        for chunk in resp.iter_content(chunk_size=None):
            if not chunk:
                continue
            buf += (chunk.decode("utf-8", errors="replace")
                    if isinstance(chunk, bytes) else chunk)
            while "\n" in buf:
                line, _, rest = buf.partition("\n")
                buf = rest
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                kind = obj.get("type")
                if kind == "content_block_delta":
                    delta = obj.get("delta") or {}
                    dt = delta.get("type")
                    if dt == "text_delta" and delta.get("text"):
                        yield StreamEvent(KIND_TEXT, text=delta["text"])
                    elif dt == "thinking_delta" and delta.get("thinking"):
                        # 思考增量单独成事件（永不进显示 / TTS）
                        yield StreamEvent(KIND_THINK, text=delta["thinking"])
                elif kind == "message_stop":
                    yield StreamEvent(KIND_DONE)
                    return
        yield StreamEvent(KIND_DONE)
