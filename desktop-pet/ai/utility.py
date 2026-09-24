# -*- coding: utf-8 -*-
"""辅助调用（utility call）：提炼 / 润色 / 预检 这类"同一次会话之外的第二次调用"。

要修的两个现存问题（DESIGN_AI_PROVIDERS.md §1.2 #6 #7）
------------------------------------------------------
* `ai/client.refine_wiki()` **写死 DeepSeek 网页版客户端**（还带自己的会话持久化）
* `ai/memory._refine_backend()` **写死 `deepseek-api`**，取不到时回退到网页版独立会话

后果：**只配了一个 API Key、没登录网页版的用户，Wiki 提炼与长期记忆提炼其实都是坏的**
——而且是静默失败，从界面上看不出来。

统一入口的规则
--------------
走**当前供应商**：

* 有 `server_memory` 能力（网页版）→ 复用**独立持久会话**（保留原有优化：
  提炼不污染主对话、跨重启复用同一个会话）
* 否则 → **stateless 一次性调用**（用当前供应商的适配器）

于是"选了千问也能提炼"、"只填了 API Key 也能提炼 Wiki"这两件事同时成立。
"""

import threading

import config
from ai import providers as prov
from ai.credentials_store import store as _store

_ORG_APP_LOCK = threading.Lock()
_SESSIONS = {}          # purpose → 持久会话客户端（仅 server_memory 后端用）

#: purpose → (session_id 键, parent_id 键)。沿用既有键名，保证跨重启复用不中断。
_SESSION_KEYS = {
    "wiki_refine": ("ai_refine_session_id", "ai_refine_parent_id"),
    "condense": ("ai_condense_session_id", "ai_condense_parent_id"),
}


def _keys_for(purpose):
    return _SESSION_KEYS.get(purpose,
                             ("ai_utility_%s_session_id" % purpose,
                              "ai_utility_%s_parent_id" % purpose))


def _load_session(purpose):
    sid_key, pid_key = _keys_for(purpose)
    sid = str(_store().value(sid_key, "") or "")
    raw = _store().value(pid_key, None)
    try:
        parent = int(raw) if raw not in (None, "", "None") else None
    except (TypeError, ValueError):
        parent = None
    return sid, parent


def _save_session(purpose, session_id, parent_id):
    sid_key, pid_key = _keys_for(purpose)
    _store().setValue(sid_key, str(session_id or ""))
    if parent_id is None:
        _store().remove(pid_key)
    else:
        _store().setValue(pid_key, parent_id)


def reset_utility_session():
    """清掉所有辅助会话（换后端 / 新会话时调用）。"""
    with _ORG_APP_LOCK:
        _SESSIONS.clear()
        for purpose in _SESSION_KEYS:
            _save_session(purpose, "", None)


def _web_once(purpose, messages, system_prompt):
    """网页版：独立持久会话（不污染主对话）。客户端由可选插件提供。"""
    import ai.client as ai_client

    with _ORG_APP_LOCK:
        client = _SESSIONS.get(purpose)
        if client is None:
            client = ai_client.make_web_client()
            if client is None:
                return ""
            sid, parent = _load_session(purpose)
            if sid:
                client.session_id = sid
                client.parent_message_id = parent
            _SESSIONS[purpose] = client
        prompt = "\n".join(str(m.get("content") or "") for m in messages
                           if m.get("role") == "user")
        content, _ = client.chat(
            prompt,
            system_prompt=system_prompt or config.REFINE_PROMPT,
            memory=True,
            thinking_enabled=False,
            search_enabled=False,
        )
        # 会话可能因失效被内部重建，成功后落盘最新 id 供下次复用
        _save_session(purpose, client.session_id, client.parent_message_id)
        return (content or "").strip()


def _stateless_once(messages, system_prompt, thinking=False, pid=None, model=None):
    """无状态供应商：直接用目标供应商的适配器发一次完整 messages。"""
    from ai.protocols import make_adapter
    import ai.client as ai_client

    pid = pid or ai_client.backend_name()
    adapter = make_adapter(pid, model=model)
    msgs = list(messages or [])
    if system_prompt and not any(m.get("role") == "system" for m in msgs):
        msgs.insert(0, {"role": "system", "content": system_prompt})
    return (adapter.chat_messages(msgs, thinking=thinking) or "").strip()


def _utility_target():
    """辅助调用的目标（P8）：设置了就用它，否则跟随主对话。

    这两类调用（Wiki 提炼 / 记忆提炼）**不进主对话历史**，用便宜模型完全够用 ——
    所以允许单独指向一个更便宜的后端，是实打实的成本优化点。
    """
    import ai.client as ai_client
    pid = (creds.get_text("_utility", "provider", "")
           or ai_client.backend_name())
    model = creds.get_text("_utility", "model", "") or None
    return pid, model


def utility_available():
    """当前辅助调用目标是否具备可用凭据。"""
    try:
        import ai.client as ai_client
        pid, _m = _utility_target()
        p = prov.profile(pid)
        if p.env:
            from ai.credentials_store import get_key
            return bool(get_key(pid))
        token, _ = ai_client.load_credentials()
        return bool(token)
    except Exception:
        return False


def utility_chat(messages, purpose="refine", system_prompt=None, thinking=False):
    """辅助调用统一入口；失败返回 ""（调用方自行回退）。

    :param messages: OpenAI 风格 messages（无状态路径直接用；网页版路径只取 user 内容）
    :param purpose: 会话分组键（``wiki_refine`` / ``condense`` / 其它自定义）
    :param system_prompt: 角色提示词（网页版走 system_prompt 参数；无状态路径插成 system 消息）
    """
    from core.trace import trace
    with trace("utility", purpose=purpose):
        if not messages:
            return ""
        try:
            pid, model = _utility_target()
            if prov.cap(pid, prov.CAP_SERVER_MEMORY):
                return _web_once(purpose, messages, system_prompt)
            return _stateless_once(messages, system_prompt, thinking=thinking,
                                   pid=pid, model=model)
        except Exception:
            return ""
