# -*- coding: utf-8 -*-
"""AI 客户端与凭据的共享封装：供聊天 / 工作模式 / 游戏模式共用。

P0/P1/P2 改造后（见 DESIGN_AI_PROVIDERS.md §三）：

* **后端名单不再写死**：`BACKENDS` 由 :mod:`ai.providers` 注册表推导
  （原来是 `("deepseek","deepseek-api","qwen")` 硬编码元组，与
  `config.MESSAGES_BACKENDS` 构成**两份可能不同步的事实**）。
* **凭据不再扁平**：读写统一走 :mod:`ai.credentials_store` 的命名空间
  `ai/<id>/key`，旧键（`qwen_api_key` / `deepseek_api_key` …）**只读回退**，
  所以本模块原有的 8 个存取函数**签名一律保留**，调用方零改动。
* **客户端按协议造**：`make_client()` 按注册表的 `protocol` 选适配器，
  网页版（Cookie 会话）单独一条路。

保留的模块级名字（对外契约，勿删）：
`load_credentials` / `save_credentials` / `is_logged_in` / `make_client` /
`parse_ai_output` / `strip_action_tags` / `save_thread_state` /
`load_thread_state` / `clear_thread_state` / `chat_once` / `refine_wiki` /
以及 6 个 Key/模型存取函数。
"""

import config
from ai import credentials_store as creds
from ai import providers as prov
from ai.credentials import load_credentials, save_credentials, is_logged_in
# 标签正则的规范定义在 ai/stream_strip.py（流式剥离器与这里共用同一套，避免两份事实）
from ai.stream_strip import ACTION_TAG_RE as _ACTION_TAG_RE
from ai.stream_strip import DSL_RE as _DSL_RE

# 与 credentials_store 共享同一个 QSettings 实例
_STORE = creds.store()


# ---------- 会话状态（跨重启续接；只有 server_memory 后端需要） ----------

def save_thread_state(session_id, parent_id):
    """保存当前会话状态（跨重启续接对话）。按**当前后端**存，不再写死默认。"""
    creds.set_session(backend_name(), session_id, parent_id)


def load_thread_state():
    """读取持久化的会话状态，返回 (session_id, parent_message_id)。"""
    return creds.session(backend_name())


def clear_thread_state():
    """清空持久化的会话状态（开启新会话时调用）。"""
    creds.clear_session(backend_name())


# ---------- 后端选择（注册表驱动） ----------

#: 可选后端 id（由注册表推导；`selectable=False` 的不在其中）
BACKENDS = prov.selectable_ids()


def backend_name():
    """当前 AI 后端（规范 id）。

    ``deepseek-web``（网页版）/ ``deepseek-api``（官方 API）/ ``qwen`` / …
    未知或不可选的存量值一律回退到默认后端。
    """
    pid = creds.provider()
    return pid if pid in BACKENDS else prov.DEFAULT_PROVIDER


def set_backend(name):
    """设置后端（旧 id 会被归一化；不可选的值忽略）。"""
    pid = prov.canonical(name)
    if pid in BACKENDS:
        creds.set_provider(pid)


def current_provider():
    """当前供应商 id（`backend_name` 的语义化别名）。"""
    return backend_name()


def current_model():
    """当前后端选用的模型 id。"""
    return creds.get_model(backend_name())


def current_caps():
    """当前后端的传输级能力集合（`ai.providers.CAP_*`）。"""
    return prov.caps(backend_name())


# ---------- 凭据存取（薄包装，签名与改造前一致） ----------

def load_qwen_key():
    """读取千问（阿里云百炼）API Key。"""
    return creds.get_key("qwen")


def save_qwen_key(key):
    creds.set_key("qwen", key)


def load_qwen_model():
    return creds.get_model("qwen") or "qwen-turbo"


def save_qwen_model(model):
    creds.set_model("qwen", model)


def load_deepseek_api_key():
    """读取 DeepSeek 官方 API Key。"""
    return creds.get_key("deepseek-api")


def save_deepseek_api_key(key):
    creds.set_key("deepseek-api", key)


def load_deepseek_api_model():
    return creds.get_model("deepseek-api") or "deepseek-chat"


def save_deepseek_api_model(model):
    creds.set_model("deepseek-api", model)


# ---------- 客户端构造（按协议分流） ----------

def make_web_client():
    """造 DeepSeek 网页版客户端（由可选插件提供）。

    插件未安装 / 未注册工厂时返回 ``None``。客户端由插件工厂创建，
    并已从 QSettings 恢复上次持久化的会话（跨重启续接）。
    """
    try:
        from plugin import host as _ph
        factory = _ph.registry().adapter_factory(prov.PROTO_DEEPSEEK_WEB)
    except Exception:
        factory = None
    if factory is None:
        return None
    return factory("deepseek-web", prov.profile("deepseek-web"))


def make_client():
    """按当前后端创建客户端（延迟导入，避免启动加载重量级依赖）。

    - ``deepseek-web``：由可选插件提供（Cookie/PoW 会话链路）
    - 其余（``openai`` / ``anthropic`` 协议）：由 `ai.protocols.make_adapter` 造
    """
    pid = backend_name()
    p = prov.profile(pid)
    if p.protocol == prov.PROTO_DEEPSEEK_WEB:
        client = make_web_client()
        if client is None:
            raise prov.ProviderUnavailable(
                "网页版后端未安装（缺 deskpet-plugin-deepseek-web）")
        return client
    from ai.protocols import make_adapter
    return make_adapter(pid)


def chat_once(prompt, system_prompt=None):
    """按当前后端做一次性 AI 调用（主窗口工作/游戏/新闻模式共用），返回完整文本。

    非网页版后端统一走 `chat_messages(messages)`：原先千问那条路走的是
    `chat()` → `chat_stream()`，但 `chat_once` 每次新建客户端、流式结果不落历史，
    二者**结果等价**，统一后少一次流式往返。
    """
    pid = backend_name()
    if prov.profile(pid).protocol == prov.PROTO_DEEPSEEK_WEB:
        client = make_web_client()
        if client is None:
            return ""
        content, _ = client.chat(prompt, system_prompt=system_prompt, memory=True)
        return (content or "").strip()

    client = make_client()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return (client.chat_messages(messages) or "").strip()


def default_mode():
    """默认 AI 模式 id（1 正常 / 2 工作 / 3 游戏）。"""
    return int(_STORE.value("ai_default_mode", 1) or 1)


def mode_name(mode_id):
    for m in config.AI_MODES:
        if m["id"] == mode_id:
            return m["name"]
    return ""


# ---------- 输出解析（显示层唯一入口） ----------

def parse_ai_output(text):
    """解析 AI 输出：提取动作标签并移除，返回 (纯文本, 动作标签列表)。

    同时剥离【DSL 指令】块（千问流式等不走 MaidLoop 的路径，指令会残留，
    必须在这里兜底剥掉，避免「【attack(...)】」原样出现在气泡里）。
    """
    actions = []

    def _sub(m):
        actions.append(m.group(1).strip())
        return ""

    clean = _ACTION_TAG_RE.sub(_sub, text)
    clean = _DSL_RE.sub("", clean)
    return clean.strip(), actions


def strip_action_tags(text):
    """移除 AI 回复中夹带的动作标签与 DSL 指令块（正常聊天才用标签驱动宠物）。"""
    clean = _ACTION_TAG_RE.sub("", text)
    return _DSL_RE.sub("", clean).strip()


def refine_wiki(text):
    """把 wiki 原文片段交给辅助调用做提炼，返回要点；失败返回 ""。

    P4 改造：原先这里**写死 DeepSeek 网页版客户端**（还自带一套会话持久化），
    导致"只填了 API Key、没登录网页版"的用户 Wiki 提炼静默失效。
    现在统一走 `ai.utility.utility_chat`：网页版复用独立持久会话（保留优化），
    其余供应商走 stateless 一次性调用。会话键沿用 `ai_refine_*`，跨重启不中断。
    """
    if not (text or "").strip():
        return ""
    try:
        from ai.utility import utility_chat
        return utility_chat(
            [{"role": "user", "content": text}],
            purpose="wiki_refine",
            system_prompt=config.REFINE_PROMPT,
        )
    except Exception:
        return ""
