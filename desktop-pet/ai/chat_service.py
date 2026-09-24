# -*- coding: utf-8 -*-
"""共享 AI 会话服务：工作 / 游戏 / 新闻 / 修仙 / 语音 共用同一个 AI 会话与全局调用锁。

从 pet/modes.py 的 _ai_chat 提取而来：
- 统一人格切换注入（换人格时附上新人格完整提示词）
- 后端路由三选一：
  - `deepseek`（网页版）：SessionBackend，服务端自动记忆，只发当轮 prompt
  - `deepseek-api`（官方 API）：MessagesBackend，客户端维护历史 + 滑动窗口 + 提炼
  - `qwen`：MessagesBackend（复用同一套上下文管理）
- 全局锁保证并发串行，避免多线程破坏会话上下文
- 记录 AI 处理耗时到性能日志（logs/perf.log）

两种后端的信息流差异（用户强调的红线）：
- 网页版 = 服务端自动记忆，客户端**只发当轮 prompt**、不做历史提炼。
- 官方 API / 千问 = 无状态，客户端**每次组装 messages**、**滑动窗口 + 定期提炼**，
  绝不整段重发全量历史。
"""
import threading
import time

from PySide6.QtCore import QSettings

import config
import ai.client as ai_client
from ai.context import ChatContext
from core.trace import traced


class ChatService:
    """全局唯一的共享 AI 会话服务（进程内单例，供各 handler / processor 调用）。"""

    def __init__(self):
        self._store = QSettings("DesktopPet", "PetWindow")
        self._client = None
        self._backend = None          # 后端实例（SessionBackend / MessagesBackend）
        self._backend_name = None     # 已创建后端的名称（后端切换时重建）
        self._lock = threading.Lock()
        # 上次 AI 调用使用的人格（None=尚未注入过，首条调用会注入当前人格）
        self._last_persona = None
        self._ctx = ChatContext()

    # ---------- 对外接口 ----------

    def reset_persona(self):
        """重置人格注入状态：下次 chat() 会重新注入当前人格完整提示词。
        皮肤带专属 prompt 切换后调用（prompt 已变化，需强制携带新设定）。"""
        self._last_persona = None

    def _prompt_style(self, backend=None):
        """读取设置里的人设精简程度，并校验为当前后端可用档位。"""
        style = str(self._store.value("prompt_style", "") or "").strip()
        return config.sanitize_prompt_style(style, backend)

    @traced("chat")
    def chat(self, prompt, source="AI", transient=None, ephemeral=False,
             thinking=False, search=False):
        """在共享会话中调用 AI（加锁串行，避免并发破坏会话上下文）。

        :param transient: TransientContext（女仆瞬时状态/事件/回执；默认空）。
            仅注入当轮，**不进历史**（MessagesBackend）或极短拼进当轮（SessionBackend）。
        :param ephemeral: 本轮是否不写回历史（游戏事件 / AI 决策 / 播报润色）。
        :param thinking: 思考模式（官方 API 的 reasoner / 网页版 thinking_enabled）。
        :param search: 联网搜索（网页版）。
        """
        t0 = time.time()
        persona = str(self._store.value("persona", "") or "") or None
        pet_name = str(self._store.value("pet_name_" + (persona or ""), "") or "")
        if not pet_name and (persona or "") == config.DEFAULT_PERSONA:
            pet_name = str(self._store.value("pet_name", "") or "")
        name = ai_client.backend_name()
        # 人格刚切换时，本次会话告知 AI 以新人格继续（不清历史）。
        # **仅网页版需要在 user 消息里补人设**：DeepSeek 多轮会话的 system_prompt
        # 只在首轮注入，之后服务端靠 parent 链记忆，换人格必须显式补一次。
        # 官方 API / 千问无状态、system 每轮重发，下面 system_prompt 一更新就已经生效，
        # 再往 user 消息塞整段人设只会污染 history 并被反复重放。
        if persona != self._last_persona:
            self._last_persona = persona
            if not config.is_messages_backend(name):
                info = config.persona_info(persona)
                full_prompt = config.load_system_prompt(
                    persona, pet_name, style=self._prompt_style(name))
                prompt = (f"现在切换人格到「{info['name']}」，以新人格身份继续，"
                          f"忽略之前的人格设定。以下是你现在的人设完整设定：\n"
                          f"{full_prompt}\n\n" + prompt)
        # system 按后端分流：MessagesBackend 用精简/极简人设（每轮重发、省 token），
        # 网页版用完整/极简人设（只在会话首条注入一次）。
        system_prompt = config.load_system_prompt(
            persona, pet_name, backend=name, style=self._prompt_style(name))

        with self._lock:
            if transient is None:
                from ai.context import TransientContext
                transient = TransientContext()
            if not config.is_messages_backend(name):
                content = self._chat_session(transient, prompt, system_prompt,
                                             ephemeral, thinking, search)
            else:  # 无状态后端（官方 API / 千问 / Claude…）均走 MessagesBackend
                content = self._chat_messages(
                    transient, prompt, system_prompt, ephemeral, thinking,
                    mode_id=int(self._store.value("ai_default_mode", 1) or 1))
            content = (content or "").strip()

        try:
            from core.perf_log import log
            log("AI", source, len(content),
                ai_ms=(time.time() - t0) * 1000)
        except Exception:
            pass
        return content

    # ---------- MessagesBackend 路由（官方 API / 千问） ----------

    def _chat_messages(self, transient, prompt, system_prompt, ephemeral, thinking,
                       mode_id=None):
        backend = self._ensure_backend(ai_client.backend_name())
        messages = backend.build(self._ctx, transient, prompt,
                                 system_prompt=system_prompt, mode_id=mode_id)
        content = backend.request(messages, thinking=thinking)
        backend.append_history(self._ctx, prompt, content, ephemeral=ephemeral)
        return content

    # ---------- SessionBackend 路由（网页版） ----------

    def _chat_session(self, transient, prompt, system_prompt, ephemeral,
                      thinking, search):
        backend = self._ensure_backend(ai_client.backend_name())
        # 始终从持久化状态同步主会话（sid + pid）：游戏/新闻/工作模式
        # 严格复用「主对话」，绝不因过期 pid 触发重开会话；
        # 仅当持久化主会话为空（如首次使用/点了新会话）才创建新会话。
        client = backend.client
        sid, pid = ai_client.load_thread_state()
        if not sid:
            client.session_id = None
            client.parent_message_id = None
        elif (str(sid) != str(client.session_id or "")
              or pid != client.parent_message_id):
            client.session_id = sid
            client.parent_message_id = pid
        when = backend.build(self._ctx, transient, prompt,
                             system_prompt=system_prompt)
        content, _ = backend.request(
            when, system_prompt=system_prompt, thinking_enabled=thinking,
            search_enabled=search, memory=True)
        # 保存最新会话状态，跨重启续接（供聊天窗口与下次启动恢复）
        ai_client.save_thread_state(client.session_id, client.parent_message_id)
        return content

    # ---------- 内部 ----------

    def _ensure_backend(self, name):
        """按后端名惰性创建后端实例；后端切换时重建。

        - 有 `server_memory` 能力（网页版）：SessionBackend（服务端自动记忆，无本地历史）
        - 其余（官方 API / 千问 / Claude…）：MessagesBackend（客户端维护历史 + 提炼）

        判据是**能力**而不是厂商名 —— 加一家无状态厂商不需要动这里。
        """
        if self._backend is not None and self._backend_name == name:
            return self._backend
        client = self._get_client()
        if not config.is_messages_backend(name):
            from ai.backends.session import SessionBackend
            self._backend = SessionBackend(client)
            self._backend_name = name
            return self._backend
        from ai.backends.messages import MessagesBackend
        from ai.memory import condense_context
        self._restore_memory_if_empty()
        self._backend = MessagesBackend(client, condense=condense_context)
        self._backend_name = name
        return self._backend

    def _restore_memory_if_empty(self):
        """MessagesBackend 首次创建时，把 logs/memory.json 的记忆恢复进 ctx。"""
        if self._ctx.memory or self._ctx.summary:
            return
        try:
            from ai.memory import load_memory
            facts, summary = load_memory()
            self._ctx.memory = facts
            self._ctx.summary = summary
        except Exception:
            pass

    def _get_client(self):
        """按当前后端创建共享客户端（工作/游戏/新闻共用）。"""
        if self._client is None or self._backend_name != ai_client.backend_name():
            from ai.client import make_client
            self._client = make_client()
        return self._client


# 全局唯一实例（进程内单例，main.py 启动后即可用）
shared = ChatService()
