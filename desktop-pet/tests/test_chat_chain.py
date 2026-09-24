# -*- coding: utf-8 -*-
"""用户 → 桌宠 → AI → 女仆 通讯链路集成测试（L1）。

无框架纯断言。用 FakeBackend + FakeLink 驱动真实 `_ChatWorker` + `MaidLoop`，
覆盖全链路测试方案 §四 L1：
- C1  用户消息 → NLU 前置 → AI 回复含【DSL】→ 下发 → 回执进下一轮上下文
- C2  女仆未连接（maid_loop=None）→ DSL 不解析、文本原样
- C3  AI 回复无 DSL → 原样透传、不下发
- C4  千问流式后端 → 增量走 delta，不接 DSL（已知缺口，标记为预期行为）
- C5  多轮回执注入：上一轮指令回执出现在下一轮组装上下文

运行：python tests/test_chat_chain.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai.chat import _ChatWorker
from game.maid_loop import MaidLoop


class _FakeBackend:
    """模拟非流式后端（网页版/官方 API）：记录调用，返回固定回复元组。"""

    def __init__(self, reply="好的"):
        self.reply = reply
        self.calls = []

    def chat(self, prompt, **kw):
        self.calls.append((prompt, kw))
        return self.reply, ""


class _StreamBackend:
    """模拟千问流式后端。"""

    def __init__(self, pieces=("你好", "，我", "来了")):
        self.pieces = pieces
        self.calls = []

    def chat_stream(self, prompt, system_prompt=None, model=None):
        self.calls.append((prompt, system_prompt, model))
        for p in self.pieces:
            yield p


class _FakeLink:
    """模拟女仆 WS 链路。"""

    def __init__(self):
        self.sent = []
        self.connected = True

    def request(self, cmd, params, cancel_previous=False, timeout=5.0):
        self.sent.append((cmd, params, cancel_previous))
        return {"ok": True, "state": "running", "result": {}}

    def status_line(self, maid=""):
        return "生命=18 任务=guard"


def _opts(backend="deepseek"):
    """构造 worker 选项。`backend` 决定走流式还是非流式（见 ai.providers.CAP_CHAT_STREAM）：
    网页版（deepseek/deepseek-web）走非流式；qwen 走流式。"""
    return {
        "ai_mode": 1,
        "backend": backend,
        "prompt": "你是女仆",
        "model": config.AI_MODEL_TYPE,
        "thinking": False,
        "search": False,
        "memory": True,
        "qwen_model": "qwen-turbo",
    }


# ---------------- C1 完整对话闭环 ----------------

def test_chat_dsl_loop_with_reply_injection():
    """用户消息 → NLU 前置 → AI 回复含【attack】→ 下发 → 回执注入下一轮。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    backend = _FakeBackend("好的主人，我去打僵尸！{开心}【attack(range=12)】")
    got = []
    worker = _ChatWorker(backend, "去打僵尸", _opts(),
                         preprocess=lambda t: "【本地预处理】意图：attack（置信度 0.99）",
                         maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()

    # 回复干净文本 + 正常标志
    assert got and got[0][0] == "好的主人，我去打僵尸！{开心}", got
    assert got[0][1] is False
    # 女仆已收到 attack（默认排队）
    assert link.sent == [("attack", {"range": 12}, False)], link.sent
    # 回执已进女仆闭环缓冲
    assert loop.peek_replies(), "回执应被记录"

    # 下一轮组装上下文应包含 [系统] 回执 + [态] 状态 + NLU
    worker2 = _ChatWorker(_FakeBackend("明白"), "继续", _opts(),
                          preprocess=lambda t: "【本地预处理】闲聊",
                          maid_loop=loop)
    p2 = worker2._prepare_prompt()
    assert "[系统] 我的任务已受理：attack" in p2, "回执未进下一轮上下文"
    assert "[态] 生命=18 任务=guard" in p2, "女仆状态未进下一轮上下文"
    assert "【本地预处理】闲聊" in p2, "NLU 未前置"
    # 顺序：NLU → 女仆上下文 → 模式声明 → 用户消息
    idx = [p2.index(s) for s in ("【本地预处理】闲聊", "[态]", "现在切换到", "继续")]
    assert idx == sorted(idx), "组装顺序错: %s" % p2
    print("  [C1] 用户→AI→DSL→女仆→回执注入 闭环 OK")


# ---------------- C2 女仆未连接 ----------------

def test_chat_without_maid_loop():
    """maid_loop=None（女仆链路未开）：DSL 不解析、原样透传、不抛异常。"""
    backend = _FakeBackend("好的【attack(range=1)】")
    got = []
    worker = _ChatWorker(backend, "hi", _opts(), maid_loop=None)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert got and got[0][0] == "好的【attack(range=1)】", got
    assert got[0][1] is False
    print("  [C2] 无女仆链路 → DSL 原样透传 OK")


# ---------------- C3 AI 回复无 DSL ----------------

def test_chat_no_dsl():
    link = _FakeLink()
    loop = MaidLoop(link)
    backend = _FakeBackend("好的主人，这里没有指令")
    got = []
    worker = _ChatWorker(backend, "在吗", _opts(), maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert got and got[0][0] == "好的主人，这里没有指令", got
    assert not link.sent, "无 DSL 不应下发: %s" % link.sent
    print("  [C3] AI 回复无 DSL → 不下发 OK")


# ---------------- C4 千问流式 ----------------

def test_qwen_stream_no_dsl():
    """千问流式：增量走 delta，完成后 finished("",False)；无指令时不下发。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    backend = _StreamBackend(["你好", "，我", "来了"])
    deltas, fins = [], []
    worker = _ChatWorker(backend, "hi", _opts("qwen"), maid_loop=loop)
    worker.delta.connect(deltas.append)
    worker.finished.connect(lambda t, e: fins.append((t, e)))
    worker.run()
    assert "".join(deltas) == "你好，我来了", deltas
    assert fins == [("", False)], fins
    assert not link.sent, "本轮回复里没有指令，不应下发任何东西"
    assert backend.calls and backend.calls[0][0], "应携带组装后的 prompt"
    print("  [C4] 千问流式 → 增量 / 无指令不下发 OK")


# ---------------- C6 流式下的指令下发（P3 修复） ----------------

def test_stream_dispatches_dsl():
    """[P3] 流式原先**完全不接 DSL**（旧代码里明写"千问流式后端不接 DSL"）。
    现在收尾时在工作线程走既有的 `_apply_dsl` 路径 —— 这里把它钉住。
    """
    link = _FakeLink()
    loop = MaidLoop(link)
    backend = _StreamBackend(["好嘞", "，这就去", "【move(pos=~,~,~)】"])
    deltas, fins = [], []
    worker = _ChatWorker(backend, "过来", _opts("qwen"), maid_loop=loop)
    worker.delta.connect(deltas.append)
    worker.finished.connect(lambda t, e: fins.append((t, e)))
    worker.run()
    # 指令被下发（相对坐标原样透传）
    assert len(link.sent) == 1 and link.sent[0][0] == "move", link.sent
    assert "~" in str(link.sent[0][1]), link.sent
    # 正文里不含【指令】
    assert "【" not in (worker.clean_text or ""), worker.clean_text
    assert fins == [("", False)], fins
    print("  [C6] 流式下发 DSL（P3：补掉已知缺口）OK")


# ---------------- C5 多轮回执注入 ----------------

def test_multi_turn_reply_injection():
    """连续两轮对话：第 1 轮指令回执进第 2 轮组装上下文，且只进当轮。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    # 第 1 轮：下发 guard
    w1 = _ChatWorker(_FakeBackend("好，我守着{待机}【guard(range=10)】"),
                     "守着这里", _opts(), maid_loop=loop)
    r1 = []
    w1.finished.connect(lambda t, e: r1.append((t, e)))
    w1.run()
    assert link.sent == [("guard", {"range": 10}, False)], link.sent
    assert "[系统] 我的任务已受理：guard" in (loop.peek_replies() or [""])[0]

    # 第 2 轮：drain 后注入当轮 prompt
    w2 = _ChatWorker(_FakeBackend("明白"), "继续", _opts(), maid_loop=loop)
    p2 = w2._prepare_prompt()
    assert "[系统] 我的任务已受理：guard" in p2, "回执未注入第 2 轮"
    # drain 之后缓冲已空（下一轮不再重复）
    assert not loop.peek_replies(), "回执应只注入一次"
    print("  [C5] 多轮回执注入 → 进当轮、不重复 OK")


def main():
    test_chat_dsl_loop_with_reply_injection()
    test_chat_without_maid_loop()
    test_chat_no_dsl()
    test_qwen_stream_no_dsl()
    test_stream_dispatches_dsl()
    test_multi_turn_reply_injection()
    print("\n全部通过")


if __name__ == "__main__":
    main()
