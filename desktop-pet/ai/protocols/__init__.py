# -*- coding: utf-8 -*-
"""协议适配器包：按注册表选协议实现（见 DESIGN_AI_PROVIDERS.md §2.5）。

对外只有一个入口 :func:`make_adapter`。协议模块**惰性导入**，
避免 `ai.protocols` 与 `ai.deepseek_api` / `ai.qwen` 之间绕出循环依赖。
"""

from ai import credentials_store as creds
from ai import providers as prov

__all__ = ["make_adapter", "adapter_class"]


def adapter_class(protocol):
    """协议名 → 适配器类（惰性导入）。

    注意：`deepseek-web` **不是**本工厂能造的 —— 它走的是 Cookie/PoW 的
    服务端会话链路（`ai.deepseek.DeepSeekClient` + `SessionBackend`），
    请用 `ai.client.make_client()`。这里显式报错，避免误配成 OpenAI 兼容端点。
    """
    if protocol == prov.PROTO_DEEPSEEK_WEB:
        raise ValueError(
            "deepseek-web 走 Cookie 会话链路，请用 ai.client.make_client()")
    if protocol == prov.PROTO_ANTHROPIC:
        from ai.protocols.anthropic_messages import AnthropicAdapter
        return AnthropicAdapter
    if protocol == prov.PROTO_OPENAI:
        from ai.protocols.openai_chat import OpenAIChatAdapter
        return OpenAIChatAdapter
    raise ValueError("未知协议：%r" % (protocol,))


def make_adapter(pid, api_key=None, base_url=None, model=None, **kwargs):
    """按供应商 id 构造适配器（凭据默认从 :mod:`ai.credentials_store` 取）。

    :param pid: 供应商 id（旧 id 会被归一化）
    :param api_key: 显式密钥（None = 从存储读）
    :param base_url: 显式端点（None = 用户覆盖 → 注册表默认）
    :param model: 显式模型（None = 从存储读 → 注册表默认）
    """
    pid = prov.canonical(pid)
    p = prov.profile(pid)
    cls = adapter_class(p.protocol)

    if api_key is None:
        api_key = creds.get_key(pid)
    if base_url is None:
        base_url = creds.get_base_url(pid)
    if model is None:
        model = creds.get_model(pid)

    common = dict(
        api_key=api_key,
        base_url=base_url,
        model=model,
        auth=p.auth,
        # 网页版没有 API Key（走 cookie），允许空
        api_key_optional=(not p.env),
    )
    common.update(kwargs)
    adapter = cls(**common)
    adapter.provider_id = pid
    return adapter
