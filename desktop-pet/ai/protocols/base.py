# -*- coding: utf-8 -*-
"""协议适配器层：把"怎么发请求"从"用哪家"里拆出来。

设计要点（见 DESIGN_AI_PROVIDERS.md §2.5 / §11.1）：

* 适配器**只负责**「把 messages 发出去、把内容拿回来」，不管历史、不管人设、
  不管 UI。上下文策略由 `ai.backends.messages`（无状态）/
  `ai.backends.session`（服务端记忆）负责。
* 流式的原语是 :meth:`ProtocolAdapter.stream_events`（事件流），
  :meth:`ProtocolAdapter.stream` 只是"只要文本增量"的便捷包装。
  之所以不直接把 `Iterator[str]` 当原语：原生语音模型会在同一个响应里
  混出文本与音频（§11.1），文本通道装不下音频。
* 明确**不做**配置化的 JSON 路径映射引擎（§2.5 已论述），
  4 个协议就写 4 个真适配器。
"""

from dataclasses import dataclass, field

# 流式事件种类
KIND_TEXT = "text"      # 正文增量（唯一会进显示 / 朗读的）
KIND_THINK = "think"    # 思考增量（**永不进显示、永不进 TTS**，见 §11.3）
#: 原生语音增量（P9）。`audio` 是**裸 PCM16 字节**，`rate` 是采样率。
#: 目前由 `openai_chat` 适配器产出（百炼 Omni 系列，实测 PCM16 @24kHz 单声道）。
KIND_AUDIO = "audio"
KIND_DONE = "done"      # 本轮结束


@dataclass
class StreamEvent:
    """流式事件。用 dataclass 而非 tuple，方便后续加字段而不破坏调用方。"""

    kind: str
    text: str = ""
    audio: bytes = b""
    rate: int = 0
    meta: dict = field(default_factory=dict)


class ProtocolError(Exception):
    """所有协议错误的基类。各客户端保留自己的子类以兼容既有 `except`。"""


class ProtocolAdapter:
    """协议适配器基类：接口刻意做到最薄。"""

    #: 该适配器对应的传输协议（ai.providers.PROTO_*）
    protocol = ""
    #: 供应商 id（由工厂填入，便于报错信息里指认）
    provider_id = ""

    # ---------------- 核心 ----------------

    def chat_messages(self, messages, thinking=False, model=None):
        """发完整 messages，返回完整回复文本。"""
        raise NotImplementedError

    def chat_message(self, messages, thinking=False, model=None, tools=None):
        """发消息，返回**完整 assistant message**（含 tool_calls，P1-1）。

        :param tools: 工具 schema 列表，**统一 OpenAI 风格**：
            ``[{"type": "function", "function": {"name", "description", "parameters"}}]``
            （调用方经 `ai.tools_openai` 从契约导出后传入；Anthropic 适配器内部转自家格式）。
        :return: OpenAI 风格 message dict：``{"role": "assistant", "content": str,
            "tool_calls": [{"id", "type", "function": {"name", "arguments"}}] | None}``。
        默认实现不支持 tools（回退到纯文本 `chat_messages`）。
        """
        text = self.chat_messages(messages, thinking=thinking, model=model)
        return {"role": "assistant", "content": text or "",
                "tool_calls": None}

    def supports_tools(self):
        """适配器是否实现原生 tool_calls（P1-1；与能力位 CAP_TOOLS 对应）。"""
        return False

    def stream_events(self, messages, thinking=False, model=None,
                      audio_voice=""):
        """流式：yield :class:`StreamEvent`。默认实现=不支持，回退到非流式。

        `audio_voice` 非空表示"让**模型自己出语音**"（P9）。默认实现不产出
        音频事件——只有实现了它的适配器（目前 `openai_chat` 的 Omni 路径）
        才会 yield :data:`KIND_AUDIO`。传了但适配器不支持时**静默降级为纯文本**，
        由调用方（`ai/chat.py`）根据模型能力位决定要不要外部 TTS 兜底。
        """
        text = self.chat_messages(messages, thinking=thinking, model=model)
        if text:
            yield StreamEvent(KIND_TEXT, text=text)
        yield StreamEvent(KIND_DONE)

    # ---------------- 便捷包装 ----------------

    def stream(self, messages, thinking=False, model=None):
        """只要正文增量的便捷包装（保留 `Iterator[str]` 契约）。"""
        for ev in self.stream_events(messages, thinking=thinking, model=model):
            if ev.kind == KIND_TEXT and ev.text:
                yield ev.text

    def chat(self, prompt, system_prompt=None, memory=True, **kwargs):
        """单轮调用：返回 ``(text, thinking_text)``，与既有客户端签名一致。

        思考档位按优先级取：``thinking_variant``（档位字符串，如 ``high``/``8192``）
        → ``thinking`` → ``thinking_enabled``。**保留字符串原样**传给适配器，
        这样 effort 档才不会被压成一个布尔"开"（§11.3）。
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        variant = (kwargs.get("thinking_variant")
                   or kwargs.get("thinking")
                   or kwargs.get("thinking_enabled")
                   or False)
        return self.chat_messages(messages, thinking=variant), ""

    def stream_prompt(self, prompt, system_prompt=None, model=None):
        """按 prompt 流式（兼容 `QwenClient.chat_stream` 的调用形态）。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        for ev in self.stream_events(messages, model=model):
            if ev.kind == KIND_TEXT and ev.text:
                yield ev.text

    def verify(self):
        """发一条最小请求验证凭据是否可用。"""
        try:
            return bool(self.chat_messages([{"role": "user", "content": "ping"}]))
        except Exception:
            return False

    # ---------------- 别名（兼容既有调用点命名） ----------------

    def chat_stream(self, prompt, system_prompt=None, model=None):
        """`stream_prompt` 的别名，形态与既有 `QwenClient.chat_stream` 一致。"""
        return self.stream_prompt(prompt, system_prompt=system_prompt, model=model)

    # ---------------- 能力（由注册表驱动，此处仅作默认） ----------------

    def supports_stream(self):
        return type(self).stream_events is not ProtocolAdapter.stream_events
