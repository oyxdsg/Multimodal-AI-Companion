# -*- coding: utf-8 -*-
"""通用 OpenAI 兼容适配器。

覆盖范围（依据 opencode 实证，见 DESIGN_AI_PROVIDERS.md §二）：
本机 models.dev 缓存里 **222 家供应商有 181 家（81.5%）** 走的就是
`/chat/completions` 这一套方言，前 4 个协议覆盖 88.7%。
所以这一个适配器 + 可编辑 `base_url`，就拿下"任意 AI"的大头。

它**吸收**了原先散在两个类里的重复实现（`DeepSeekApiClient` / `QwenClient`
的 chat 半边），二者现在只是它的薄子类，见各自模块。
"""

import base64
import json

from curl_cffi import requests as cffi_requests

from ai.protocols.base import (
    KIND_AUDIO, KIND_DONE, KIND_TEXT, KIND_THINK, ProtocolAdapter, StreamEvent,
)

DEFAULT_CHAT_PATH = "/chat/completions"
DEFAULT_TIMEOUT = 120

#: 原生语音的输出格式（P9）。**实测**：百炼 Omni 的流式音频**永远**是
#: 裸 PCM16 @24kHz 单声道——连 `format:"wav"` 都不给 RIFF 头，
#: 官方文档也写明"不支持自定义输出采样率"。这个值随事件下发（`StreamEvent.rate`），
#: 播放端按事件里的值走，不在这里之外再抄一份。
AUDIO_SAMPLE_RATE = 24000

#: 非 effort 语义的两个档位名（注册表里用它们表示"开关型"与"预算型"）
REASONING_VARIANT_TOGGLE = "toggle"
REASONING_VARIANT_BUDGET = "budget"


class OpenAICompatError(Exception):
    """OpenAI 兼容端点的通用错误。"""


class OpenAIChatAdapter(ProtocolAdapter):
    """OpenAI 兼容客户端。

    :param api_key: 密钥；为空时构造即报错（与既有客户端行为一致）
    :param base_url: 端点根（如 ``https://api.deepseek.com``）
    :param model: 默认模型
    :param auth: 鉴权形态：``bearer``（默认）/ ``x-api-key`` / ``none``
    :param chat_path: 路径，默认 ``/chat/completions``。
        留出方言位：若某端点用 ``/responses`` 之类，只换这个参数（§10.1 修正 4）
    :param error_cls: 抛出的异常类型；子类指定以兼容既有 ``except``
    :param thinking_mode: ``"param"``（思考=请求参数）/ ``"model"``（思考=换模型，
        子类覆写 :meth:`_resolve_model`）/ ``""``（不支持）
    """

    protocol = "openai"

    def __init__(self, api_key, base_url, model="", auth="bearer",
                 chat_path=DEFAULT_CHAT_PATH, error_cls=None, extra_headers=None,
                 timeout=DEFAULT_TIMEOUT, thinking_mode="param",
                 stream_path=None, api_key_optional=False):
        if not api_key_optional and not (api_key or "").strip():
            raise (error_cls or OpenAICompatError)("未配置 API Key")
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or ""
        self.auth = auth or "bearer"
        self.chat_path = chat_path or DEFAULT_CHAT_PATH
        self.stream_path = stream_path or self.chat_path
        self.error_cls = error_cls or OpenAICompatError
        self.extra_headers = dict(extra_headers or {})
        self.thinking_mode = thinking_mode or ""
        self._session = cffi_requests.Session(
            impersonate="chrome131", timeout=timeout)

    # ---------------- 组装 ----------------

    def _url(self, path=None):
        if not self.base_url:
            raise self.error_cls("未配置端点（base_url）")
        return self.base_url + (path or self.chat_path)

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            if self.auth == "bearer":
                h["Authorization"] = "Bearer %s" % self.api_key
            elif self.auth == "x-api-key":
                h["x-api-key"] = self.api_key
        h.update(self.extra_headers)
        return h

    def _resolve_model(self, thinking, model):
        """决定本次实际用哪个模型。

        ``thinking_mode="model"`` 的子类（如 DeepSeek 官方 API）在这里把
        "思考"翻译成"换模型"——这是**模型级**语义，不是请求参数（§11.3）。
        """
        return model or self.model

    def _build_body(self, messages, thinking, model, stream, audio_voice="",
                    tools=None):
        body = {
            "model": self._resolve_model(thinking, model),
            "messages": list(messages or []),
            "stream": bool(stream),
        }
        if tools:
            body["tools"] = list(tools)
        self._apply_reasoning(body, thinking)
        self._apply_audio(body, audio_voice, stream)
        return body

    def _apply_audio(self, body, audio_voice, stream):
        """请求**模型原生出语音**（P9，OpenAI 标准的 modalities/audio 形态）。

        ⚠️ **只有流式才有音频**（实机取证，见 DESIGN_AI_PROVIDERS.md §十六）：
        非流式响应里根本没有 `audio` 字段，但 `usage.completion_tokens_details`
        里照样有 `audio_tokens` —— 音频被生成、被计费、然后被丢掉。
        所以这里在非流式路径**直接报错**，宁可不让用户白花钱。

        `format` 固定 `pcm16`：实测流式下它其实被忽略（永远给裸 PCM16），
        写它是为了对齐官方示例里的请求形状。
        """
        voice = (audio_voice or "").strip()
        if not voice:
            return
        if not stream:
            raise self.error_cls(
                "模型原生语音只能走流式：非流式请求会照常计费但拿不到音频")
        body["modalities"] = ["text", "audio"]
        body["audio"] = {"voice": voice, "format": "pcm16"}

    def _apply_reasoning(self, body, thinking):
        """把思考档位写进请求体（OpenAI 方言：`reasoning_effort`）。

        设计要点（§11.3）：**只在真正开了思考时才写字段**。
        这样默认路径（思考关）完全不受影响 —— 即便某家端点不认这个字段，
        也不会伤到日常聊天；风险被限制在"用户主动开思考"那一刻。

        `thinking` 的取值来自注册表的模型级 `reasoning`（含 `off` / effort 档位 /
        `toggle` / `budget`）。`toggle` 与 `budget` 不是 effort 语义，分别映射与跳过。
        """
        if self.thinking_mode != "param" or not thinking:
            return
        variant = "high" if thinking is True else str(thinking).strip()
        if variant in ("", "off", "none", "false", "0"):
            return
        if variant == REASONING_VARIANT_BUDGET:
            return                      # 预算型不走 effort 方言（Anthropic 适配器处理）
        if variant == REASONING_VARIANT_TOGGLE:
            variant = "medium"          # 纯开关：取中间档
        body["reasoning_effort"] = variant

    def _raise_http(self, resp):
        try:
            err = resp.json().get("error", {}).get("message", resp.text[:200])
        except Exception:
            err = resp.text[:200]
        raise self.error_cls("请求失败（HTTP %s）：%s" % (resp.status_code, err))

    # ---------------- 非流式 ----------------

    def chat_messages(self, messages, thinking=False, model=None):
        body = self._build_body(messages, thinking, model, stream=False)
        resp = self._session.post(self._url(self.chat_path), json=body,
                                  headers=self._headers())
        if resp.status_code != 200:
            self._raise_http(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise self.error_cls("返回异常，请重试")
        try:
            return (payload["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise self.error_cls("返回内容异常，请重试")

    def chat_message(self, messages, thinking=False, model=None, tools=None):
        """带 tools 的完整 message（P1-1）：返回含 tool_calls 的 assistant message。

        tools 为 OpenAI function tools 格式（`to_openai_tools()` 的产物）。
        未传 tools 时退化为纯文本（与 `chat_messages` 等价，但保留 message 结构）。
        """
        body = self._build_body(messages, thinking, model, stream=False,
                                tools=tools)
        resp = self._session.post(self._url(self.chat_path), json=body,
                                  headers=self._headers())
        if resp.status_code != 200:
            self._raise_http(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise self.error_cls("返回异常，请重试")
        try:
            msg = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise self.error_cls("返回内容异常，请重试")
        out = {"role": "assistant", "content": msg.get("content") or "",
               "tool_calls": None}
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        return out

    def supports_tools(self):
        return True

    # ---------------- 流式（事件流原语） ----------------

    def stream_events(self, messages, thinking=False, model=None,
                      audio_voice=""):
        body = self._build_body(messages, thinking, model, stream=True,
                                audio_voice=audio_voice)
        resp = self._session.post(self._url(self.stream_path), json=body,
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
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield StreamEvent(KIND_TEXT, text=text)
                # 思考增量单独成事件：**永不进显示、永不进 TTS**（§11.3 坑 4）
                think = delta.get("reasoning_content")
                if think:
                    yield StreamEvent(KIND_THINK, text=think)
                # 原生语音增量（P9）：base64 裸 PCM16，落在 delta.audio.data
                au = delta.get("audio") or {}
                raw = au.get("data")
                if raw:
                    try:
                        pcm = base64.b64decode(raw)
                    except Exception:
                        pcm = b""
                    if pcm:
                        yield StreamEvent(KIND_AUDIO, audio=pcm,
                                          rate=AUDIO_SAMPLE_RATE)
                # 有的 Omni 模型把文字放在 audio.transcript 而不是 content
                # （官方 Qwen2.5-Omni 示例就是这么读的）→ 补成正文，别丢字
                if not text:
                    tr = au.get("transcript")
                    if tr:
                        yield StreamEvent(KIND_TEXT, text=tr)
        yield StreamEvent(KIND_DONE)
