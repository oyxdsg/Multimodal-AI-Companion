"""共享 AI 会话的数据模型：对话历史 / 长期记忆 / 瞬时状态。

只定义数据结构与常量，不涉及任何后端调用，供
`ai.backends.messages`（官方 API / 千问）与 `ai.backends.session`（网页版）复用。

两种后端的信息流差异（用户强调的红线）：
- **网页版（SessionBackend）**：服务端自动记忆，客户端**只发当轮 prompt**，
  不维护本地历史、不做提炼。
- **官方 API / 千问（MessagesBackend）**：无状态，客户端**每次组装 messages**，
  必须自己做「滑动窗口 + 定期提炼」，**绝不整段重发全量历史**。
"""

from dataclasses import dataclass, field

# 上下文窗口常量（仅 MessagesBackend 使用；SessionBackend 不适用）。
# 单一事实来源在 config.py（HISTORY_WINDOW / CONDENSE_EVERY），这里做别名便于导入。
import config
HISTORY_WINDOW = config.HISTORY_WINDOW
CONDENSE_EVERY = config.CONDENSE_EVERY

CONDENSE_PROMPT = (
    "从以下对话中提炼需要长期记住的信息，输出 JSON：\n"
    "- facts: 用户偏好、约定、关系进展、重要事件（key-value，可带 expire）\n"
    "- summary: 100 字以内的关系与近期状态概述\n"
    "不要提炼：具体坐标、临时血量、瞬时敌人、日常寒暄。\n"
    "已有记忆：{memory}\n"
    "本次对话：{turns}"
)


@dataclass
class Turn:
    """一轮纯净对话（不含任何瞬时状态 / 事件）。"""
    user: str
    assistant: str


@dataclass
class Fact:
    """一条长期记忆事实。expire 为 ISO 日期（None = 永久）。"""
    key: str
    value: str
    expire: str | None = None


@dataclass
class ChatContext:
    """共享主会话上下文（官方 API / 千问时由 MessagesBackend 维护）。

    history 只存「纯净对话」，瞬时状态 / 事件 / 回执永远不写进这里。
    """
    history: list = field(default_factory=list)   # list[Turn]
    memory: list = field(default_factory=list)    # list[Fact]
    summary: str = ""                             # 提炼出的对话摘要（100 字内）
    persona: str = ""                             # 当前人格 key
    topic_id: str = ""                            # 会话标识（跨重启持久化用）


@dataclass
class TransientContext:
    """瞬时状态：只用于「当轮组装」，永不进历史。

    - status_full    完整形态（MessagesBackend 用，~250 token）
    - status_compact 极短形态（SessionBackend 用，~20 token）
    - events         最近 1~3 条事件中文摘要
    - replies        最近 1~3 条指令回执（[系统] 前缀）
    """
    status_full: str = ""
    status_compact: str = ""
    events: list = field(default_factory=list)
    replies: list = field(default_factory=list)

    def render_messages_backend(self, user_msg: str) -> str:
        """MessagesBackend：完整状态 + 事件 + 回执 + 用户消息，拼成当轮 user 内容。"""
        parts = []
        if self.status_full:
            parts.append("[态] " + self.status_full)
        for ev in self.events:
            parts.append("[事件] " + str(ev))
        for rp in self.replies:
            parts.append("[系统] " + str(rp))
        if user_msg:
            parts.append(user_msg)
        return "\n".join(parts)

    def render_session_backend(self, user_msg: str) -> str:
        """SessionBackend：极短状态行 + 用户消息（服务端会记住，必须压缩到最小）。"""
        parts = []
        if self.status_compact:
            parts.append(self.status_compact)
        if user_msg:
            parts.append(user_msg)
        return " ".join(parts)


def render_memory(ctx: ChatContext) -> str:
    """把长期记忆渲染成一条 system 消息内容（恒定小）。"""
    parts = []
    if ctx.summary:
        parts.append("对话概述：" + ctx.summary)
    if ctx.memory:
        alive = []
        for f in ctx.memory:
            alive.append(f"{f.key}={f.value}")
        if alive:
            parts.append("已记住：" + "；".join(alive))
    return "\n".join(parts)
