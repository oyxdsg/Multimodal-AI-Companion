# -*- coding: utf-8 -*-
"""DeepSeek 官方 API 客户端（OpenAI 兼容）。

与 `ai/deepseek.py`（网页版内部接口）不同：
- 走官方付费 API `https://api.deepseek.com/chat/completions`，需要 API Key。
- **无状态**：官方 API 不维护服务端会话历史，历史由客户端负责
  （见 `ai.backends.messages.MessagesBackend`）。

本类现在只是 `OpenAIChatAdapter` 的**薄子类**（P2 收敛，见
DESIGN_AI_PROVIDERS.md §三）——原先它和 `QwenClient` 各写了一份
几乎相同的 OpenAI 兼容实现，现已合并到 `ai/protocols/openai_chat.py`。

**思考语义已经真机验证**（2.26.0）：官方 API 只认 `deepseek-flash` 与
`deepseek-v4-pro` 两个模型（`GET /models`），思考走**请求参数 `reasoning_effort`**，
不是换模型。非法取值返回 422，错误信息直接给出权威取值集：
`none, minimal, low, medium, high, xhigh, max`（注册表 `_DS_EFFORT` 与之一致）。
省略该字段即不推理（实测无该字段时 `usage` 里没有 `reasoning_tokens`）。

历史模型 id（`deepseek-chat` / `deepseek-reasoner` / `deepseek-v4-flash`）实测仍可调用，
但会被**服务端别名**到 `deepseek-flash` —— 归一化见
`ai.providers.LEGACY_MODEL_ALIASES`，否则 perf 日志会记下与实际不符的模型名。
"""

from ai import providers as prov
from ai.protocols.openai_chat import OpenAIChatAdapter

BASE_URL = "https://api.deepseek.com"
CHAT_URL = f"{BASE_URL}/chat/completions"

# 模型清单**不再写在这里**，改为取注册表（单一事实来源，见 ai/providers.py）
CHAT_MODELS = list(prov.model_ids("deepseek-api"))
DEFAULT_CHAT_MODEL = prov.default_model("deepseek-api")


class DeepSeekApiError(Exception):
    """官方 API 错误。"""


class DeepSeekApiClient(OpenAIChatAdapter):
    """DeepSeek 官方 API 客户端（OpenAI 兼容，无状态）。"""

    def __init__(self, api_key, model=DEFAULT_CHAT_MODEL):
        super().__init__(
            api_key=api_key,
            base_url=BASE_URL,
            model=model or DEFAULT_CHAT_MODEL,
            auth="bearer",
            error_cls=DeepSeekApiError,
            # 思考走请求参数（reasoning_effort），见模块 docstring
            thinking_mode="param",
        )
        self.messages = []           # 聊天窗形态的多轮记忆（MessagesBackend 形态不用）
        # 兼容聊天窗对 DeepSeekClient 的 session 字段访问（网页版才有；这里恒 None）
        self.session_id = None
        self.parent_message_id = None

    # ---------------- 聊天窗兼容形态（返回 (text, "")） ----------------

    def chat(self, prompt, system_prompt=None, model=None, thinking=False,
             memory=True, **kwargs):
        """兼容聊天窗口的多轮调用：维护 self.messages，返回 ``(text, "")``。

        与 QwenClient.chat 的形态一致，只是非流式、走官方 API。
        多余 kwargs（model_type/search_enabled/ref_file_ids）被吞掉。
        **思考档位保留字符串原样**（`thinking_variant` 优先），这样
        `reasoning_effort` 能拿到 `high`/`max` 这样的真实档位而不是一个布尔。
        """
        variant = (kwargs.get("thinking_variant") or thinking
                   or kwargs.get("thinking_enabled") or False)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.messages)
        messages.append({"role": "user", "content": prompt})
        text = self.chat_messages(messages, thinking=variant, model=model)
        if memory:
            self.messages.append({"role": "user", "content": prompt})
            self.messages.append({"role": "assistant", "content": text})
            if len(self.messages) > 30:
                self.messages = self.messages[-30:]
        return text, ""

    def reset_thread(self):
        self.messages = []

    def verify(self):
        """验证 Key 是否有效（发一条最小请求）。"""
        return bool(self.chat_messages(
            [{"role": "user", "content": "ping"}]))
