# -*- coding: utf-8 -*-
"""Agent 循环状态机（P3-1）测试：game/agent_loop.py + _ChatWorker 接入。

无框架纯断言，风格同 test_chat_unit.py：
- 状态机：合法转移 / 非法转移抛错 / steps 记录 / done
- 全链路状态：有工具（IDLE→NLU→GENERATE→DISPATCH→DONE）与无工具（…→GENERATE→DONE）
- `_ChatWorker._run_plain` 接入：跑一轮 fake 链路，trace 文件出现 agent.generate / dispatch / done

运行：python tests/test_agent_loop.py
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game.agent_loop import AgentLoop, IllegalTransition
from ai.chat import _ChatWorker

_TRACE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "trace")


def _today_spans():
    files = sorted(glob.glob(os.path.join(_TRACE_DIR, "*.jsonl")))
    out = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    out.append(json.loads(ln))
    return out


class _FakeLink:
    connected = True

    def __init__(self):
        self.sent = []

    def request(self, cmd, params, cancel_previous=False, timeout=5.0):
        self.sent.append((cmd, params, cancel_previous))
        return {"ok": True, "state": "running", "result": {}}

    def status_line(self, maid=""):
        return "生命=18"


class _FakeClient:
    def __init__(self, reply="好的"):
        self.reply = reply
        self.calls = []

    def chat(self, prompt, **kw):
        self.calls.append(prompt)
        return self.reply, ""

    def upload_image_file(self, path):
        return "fid"


def _opts():
    return {"ai_mode": 1, "prompt": "你是女仆", "model": "x",
            "thinking": False, "search": False, "memory": True,
            "qwen_model": "qwen-turbo", "backend": "deepseek"}


def test_states_and_transitions():
    """合法转移 + steps 记录 + done。"""
    loop = AgentLoop()
    assert loop.state == "idle" and not loop.done
    loop.to_nlu()
    loop.to_generate()
    loop.to_dispatch()
    loop.to_done()
    assert loop.steps == ["idle", "nlu", "generate", "dispatch", "done"]
    assert loop.done
    print("[ok] 状态机合法转移 + steps 记录")


def test_skip_nlu_direct_generate():
    """无 NLU 前置：IDLE → GENERATE → DONE。"""
    loop = AgentLoop()
    loop.to_generate()
    loop.to_done()
    assert loop.steps == ["idle", "generate", "done"]
    print("[ok] 无 NLU 直接生成")


def test_illegal_transition_raises():
    """非法转移抛错（如 DONE 后不能再走、从 IDLE 直接 DISPATCH）。"""
    loop = AgentLoop()
    loop.to_generate()
    loop.to_done()
    try:
        loop.to_generate()   # DONE 后不能再走
        assert False, "应抛 IllegalTransition"
    except IllegalTransition:
        pass
    loop2 = AgentLoop()
    try:
        loop2.to_dispatch()   # 从 IDLE 直接 dispatch 不合法
        assert False
    except IllegalTransition:
        pass
    print("[ok] 非法转移抛 IllegalTransition")


def test_no_tool_dispatch_skipped():
    """无工具指令：GENERATE → DONE（不经过 DISPATCH）。"""
    loop = AgentLoop()
    loop.to_generate()
    loop.to_done()
    assert "dispatch" not in loop.steps
    print("[ok] 无工具不经过 DISPATCH")


def test_chat_worker_emits_agent_spans():
    """`_ChatWorker._run_plain` 接入：fake 链路跑一轮 → trace 出现 agent.* span。"""
    from game.maid_loop import MaidLoop
    link = _FakeLink()
    loop = MaidLoop(link)
    client = _FakeClient(reply="好的主人！【attack(range=10)】")
    opts = _opts()
    before = len(_today_spans())
    worker = _ChatWorker(client, "去打僵尸", opts, maid_loop=loop)
    got = []
    worker.finished.connect(lambda t, e: got.append((t, e)))
    worker.run()
    spans = _today_spans()[before:]
    names = [s["name"] for s in spans]
    assert "agent.generate" in names, names
    assert "agent.dispatch" in names, names
    assert "agent.done" in names, names
    assert link.sent == [("attack", {"range": 10}, False)], "应下发 attack"
    print("[ok] _ChatWorker 接入后 trace 出现 agent.generate/dispatch/done")


def main():
    test_states_and_transitions()
    test_skip_nlu_direct_generate()
    test_illegal_transition_raises()
    test_no_tool_dispatch_skipped()
    test_chat_worker_emits_agent_spans()
    print("\n全部通过")


if __name__ == "__main__":
    main()
