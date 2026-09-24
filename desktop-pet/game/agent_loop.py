# -*- coding: utf-8 -*-
"""Agent 循环状态机（P3-1，借鉴 LangGraph 的显式状态图，**不引入框架**）。

把「一轮用户消息 → NLU 前置 → 主 AI 生成 → 工具下发 → 回执注入下轮」的
流转建模成**显式状态机**：状态转移集中在一个类里，每状态打一个 trace span，
`steps` 记录实际经过的路径（供测试/排查断言"这轮到底经历了什么"）。

关键设计：
* **纯逻辑、线程无关**：状态机不持有线程/锁，由调用方（`_ChatWorker` /
  `ChatService`）在自己线程里驱动 —— 不碰现有线程模型与锁边界。
* **行为零变更**：接入只是"记录状态 + trace"，不改任何返回/分支/异常语义。
* **trace 联动**：每个状态对应一个 `logs/trace` 里的 span（`agent.*`），
  配合 P2-4 的 `chat_window`/`chat` 根 span 串成完整父子链。

状态转移表::

    IDLE ──┬─(有 NLU 前置)→ NLU ──→ GENERATE ──┬─(有工具下发)→ DISPATCH ──→ DONE
          └─(无 NLU)───────→ GENERATE           └─(无工具)──────→ DONE
"""
import uuid

from core.trace import trace

# ---------------- 状态常量 ----------------

IDLE = "idle"           # 初始
NLU = "nlu"             # 本地意图识别前置（可选：有 preprocess 才进）
GENERATE = "generate"   # 主 AI 生成回复
DISPATCH = "dispatch"   # 工具/指令下发 + 回执
DONE = "done"           # 本轮结束

_STATES = (IDLE, NLU, GENERATE, DISPATCH, DONE)

# 合法转移表
_TRANSITIONS = {
    IDLE: {NLU, GENERATE},       # 无 NLU 前置时直接生成
    NLU: {GENERATE},
    GENERATE: {DISPATCH, DONE},  # 无工具指令 → 直接结束
    DISPATCH: {DONE},
    DONE: set(),
}


class IllegalTransition(ValueError):
    """非法状态转移（测试/调试时尽早暴露状态机使用错误）。"""


class AgentLoop:
    """一轮处理的显式状态机。"""

    def __init__(self, trace_id=None):
        self.state = IDLE
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        self._steps = [IDLE]
        self._spans = []          # 保留每个状态 span 引用（调试/测试）

    # ---------------- 查询 ----------------

    @property
    def steps(self):
        """本轮实际经过的状态序列（含初始 IDLE）。"""
        return list(self._steps)

    @property
    def done(self):
        return self.state == DONE

    def in_state(self, s):
        return self.state == s

    # ---------------- 转移（带 trace span） ----------------

    def _go(self, to, name, **meta):
        if to not in _TRANSITIONS[self.state]:
            raise IllegalTransition(
                "非法状态转移 %s -> %s（合法: %s）"
                % (self.state, to, sorted(_TRANSITIONS[self.state])))
        self.state = to
        self._steps.append(to)
        with trace(name, trace_id=self.trace_id, **meta) as t:
            self._spans.append(t)
        return t

    def to_nlu(self, **meta):
        """NLU 前置（本地意图识别）阶段。"""
        return self._go(NLU, "agent.nlu", **meta)

    def to_generate(self, **meta):
        """主 AI 生成阶段。"""
        return self._go(GENERATE, "agent.generate", **meta)

    def to_dispatch(self, **meta):
        """工具/指令下发阶段（有回执才进）。"""
        return self._go(DISPATCH, "agent.dispatch", **meta)

    def to_done(self, **meta):
        """本轮结束。"""
        return self._go(DONE, "agent.done", **meta)
