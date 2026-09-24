# -*- coding: utf-8 -*-
"""deepseek-web 插件入口：注册 DeepSeek 网页版后端。

网页版走 chat.deepseek.com 的**非官方内部接口**（PoW + 会话 + 识图），
是可选插件；不装它时，项目用官方 API / 千问 / 自定义端点照常工作。

**只依赖 `plugin.contracts`**（公共数据契约），不 import 主体内部实现。
"""

from plugin import contracts as c


def _web_profile():
    """网页版供应商记录（原 `ai/providers.py` 的内置记录搬来）。"""
    return c.ProviderProfile(
        id="deepseek-web",
        label="DeepSeek（网页版）",
        protocol=c.PROTO_DEEPSEEK_WEB,
        group="网页版",
        api="https://chat.deepseek.com",
        auth="cookie",
        default_model="",
        models=(
            c.ModelInfo(
                id="", label="服务端统一模型",
                attachments=True,
                reasoning=("off", "on"),
                reasoning_kind=c.REASONING_KIND_TOGGLE,
                reasoning_transport=c.REASONING_TRANSPORT_PARAM,
                reasoning_default=c.REASONING_OFF,
                note="网页版的思考是请求参数（thinking_enabled）",
            ),
        ),
        caps=frozenset({
            c.CAP_SERVER_MEMORY, c.CAP_SESSION_STATE, c.CAP_SEARCH,
            c.CAP_THINKING, c.CAP_ATTACHMENTS,
        }),
        key_hint="右键「登录 DeepSeek」自动登录，无需 API Key",
        note="非官方 API。服务端自动记忆，system 只在会话首条注入一次；目前唯一支持传图的后端",
    )


def make_web_client(provider_id, profile):
    """工厂：造 DeepSeekClient（惰性 import 重依赖 wasmtime）。

    凭证读写用宿主的 `ai.credentials` / `ai.credentials_store`（属宿主能力）。
    """
    import deepseek_client
    import ai.credentials as cred
    from ai import credentials_store as creds

    token, cookies = cred.load_credentials()
    client = deepseek_client.DeepSeekClient(token, cookies)
    sid, parent = creds.session("deepseek-web")
    if sid:
        client.session_id = sid
        client.parent_message_id = parent
    return client


def register(host):
    host.add_ai_provider(profile=_web_profile(), adapter_factory=make_web_client)
    host.log("DeepSeek 网页版后端已注册")
