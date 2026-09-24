"""MessagesBackend：官方 API / 千问 的上下文组装后端。

两种后端差异的核心：**官方 API / 千问 无状态**，每次请求都要自带完整 messages，
所以历史组装、滑动窗口、定期提炼都由本后端负责（网页版见 `session.py`）。

设计（用户强调的红线）：
- 历史只存「纯净对话」（Turn），瞬时状态 / 事件 / 回执永不写入 history。
- 每轮组装：system(人格) + 记忆(facts+summary) + 最近 WINDOW 轮原文 + 当轮。
- 历史超 CONDENSE_EVERY 触发提炼：把「滑出窗口的旧历史 + 现有记忆」增量
  合并成新的 facts + summary，**绝不整段重发全量历史**。
- ephemeral=True 的轮次（游戏事件 / AI 决策 / 播报润色）不写回历史，避免污染。
"""

import threading

import config
from ai import context as ctx_mod
from ai.context import ChatContext, TransientContext

class MessagesBackend:
    """客户端维护 ctx.history 的后端。线程安全由调用方保证（ChatService 全局锁）。"""

    def __init__(self, client, condense=None):
        self.client = client            # DeepSeekApiClient / QwenClient
        self.condense = condense        # 提炼回调 (ctx, turns) -> None；None=跳过提炼
        self._condense_lock = threading.Lock()   # 提炼防并发（提炼会改 memory/history）

    # ---------------- 组装 ----------------

    def build(self, ctx, transient, user_msg, system_prompt=None, mode_id=None):
        """组装完整 messages 列表（每轮调用）。

        :param ctx: ChatContext（history 由本后端维护）
        :param transient: TransientContext（当轮瞬时状态，不进历史）
        :param user_msg: 用户本轮原文
        :param system_prompt: 人格 system 提示词（None 时只带记忆 system）
        :param mode_id: 当前模式 id（1 日常 / 2 工作 / 3 游戏）。本后端无状态、
            system 每轮重发，因此「当前模式」直接写进 system，而不是像网页版那样
            往 user 消息前缀塞「现在切换到 x、模式名」（那会污染 history 并被反复重放）。
        """
        messages = []
        sys_text = system_prompt or ""
        if mode_id is not None:
            mode = config.mode_prompt(mode_id)
            mode_block = (f"【当前模式】\n你当前处于模式{mode['id']}（{mode['name']}），"
                          f"本轮按此模式的规则回应：\n{mode['prompt']}")
            sys_text = (sys_text.rstrip() + "\n\n" + mode_block) if sys_text \
                else mode_block
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
        mem = ctx_mod.render_memory(ctx)
        if mem:
            messages.append({"role": "system", "content": mem})
        for t in ctx.history[-config.HISTORY_WINDOW:]:
            messages.append({"role": "user", "content": t.user})
            messages.append({"role": "assistant", "content": t.assistant})
        current = transient.render_messages_backend(user_msg)
        messages.append({"role": "user", "content": current})
        return messages

    def request(self, messages, thinking=False, model=None):
        """实际调后端，返回回复文本。"""
        if hasattr(self.client, "chat_messages"):
            return self.client.chat_messages(messages, thinking=thinking,
                                             model=model)
        if hasattr(self.client, "chat"):
            return self.client.chat(messages, thinking=thinking, model=model)
        raise TypeError("MessagesBackend 需要支持 chat_messages(messages) 的客户端")

    # ---------------- 历史维护 ----------------

    def append_history(self, ctx, user_msg, assistant_msg, ephemeral=False):
        """写回历史。ephemeral=True（游戏/决策/播报）不写回。"""
        if ephemeral:
            return
        ctx.history.append(ctx_mod.Turn(user=user_msg, assistant=assistant_msg))
        self._maybe_condense(ctx)

    def _maybe_condense(self, ctx):
        """历史超 CONDENSE_EVERY → 提炼滑出窗口的部分，与现有记忆增量合并。"""
        if self.condense is None:
            return
        if len(ctx.history) <= config.CONDENSE_EVERY:
            return
        with self._condense_lock:
            # 提炼「滑出 WINDOW 之外、仍保留在 history 里的旧轮次」
            overflow = len(ctx.history) - config.HISTORY_WINDOW
            if overflow < 2:      # 太少不值得提炼
                return
            old = ctx.history[:overflow]
            ctx.history = ctx.history[overflow:]
            try:
                self.condense(ctx, old)
            except Exception:
                # 提炼失败：把滑出的旧历史塞回去（保证不丢），下次再试
                ctx.history = old + ctx.history
