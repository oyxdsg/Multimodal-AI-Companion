# -*- coding: utf-8 -*-
"""女仆指令闭环协调器（阶段二）：DSL 解析 → 下发 → 回执注入。

把「AI 回复里夹带的【DSL 指令】」变成对女仆的真实操作，并把执行结果
注入下一轮 AI 上下文，形成 用户 → AI → 女仆 → 回执 → AI 的闭环。

使用（工作线程，会阻塞等回执）：:

    loop = MaidLoop(link)               # link = maid_link.MaidLink（鸭子类型）
    clean, inject = loop.process_reply("好的，我去打僵尸 {开心}【attack(range=12)】")
    # clean   = "好的，我去打僵尸 {开心}"      （气泡展示用）
    # inject  = "[系统] 女仆任务完成：attack（击杀 zombie×2）" 或 ""（下轮注入）

线程约定
--------
- ``process_reply`` 用 ``link.request``（阻塞等回执），只能在**工作线程**调用
  （聊天 worker / 语音 worker），绝不在 Qt 主线程调用。
- 未连接 / 解析失败 / 下发失败 → 优雅降级：指令丢弃，回复照常显示，
  注入文本为空的（下一轮不带女仆上下文，不报错）。
"""

import threading

from core.trace import trace
from game import maid_intent


class MaidLoop:
    """女仆指令闭环协调器（线程安全：只读 link 引用 + 自带锁）。"""

    def __init__(self, link):
        self._link = link            # maid_link.MaidLink（鸭子类型）
        self._lock = threading.Lock()
        self._last_replies = []      # 最近指令回执（[系统] 行，最多 3 条）

    # ---------------- 对外 ----------------

    @property
    def connected(self):
        try:
            return bool(self._link.connected)
        except Exception:
            return False

    def process_reply(self, text):
        """解析 AI 回复中的 DSL 指令并下发，返回 ``(clean_text, inject_text)``。

        :param text: AI 回复全文
        :return: ``(clean, inject)``
            - clean: 去掉【指令】后的纯文本（供气泡/动作标签）
            - inject: 回执注入文本（`[系统] ...`，下轮拼进 transient.replies）；
              无指令 / 未连接 / 失败时为空串。
        """
        if not text:
            return text or "", ""
        cmd, clean = maid_intent.parse(text)
        if cmd is None:
            return clean, ""
        if not self.connected:
            return clean, ""
        with trace("maid.dispatch", cmd=cmd.get("cmd")):
            reply = self._send(cmd)
            inject = self._reply_text(cmd, reply)
            self._push_reply(inject)
        return clean, inject

    def execute(self, cmd):
        """直接下发**已结构化**的指令（P1-1 原生 tool_calls 通道）。

        :param cmd: ``{"cmd", "params", "cancel_previous"}``
            （由调用方按 `nlu.mod_contract` 白名单 + `clamp` 清洗后传入）。
        :return: 回执注入文本（`[系统] ...`）；未连接 / 无指令时为空串。
        """
        if not cmd or not cmd.get("cmd"):
            return ""
        if not self.connected:
            return ""
        with trace("maid.dispatch", cmd=cmd.get("cmd")):
            reply = self._send(cmd)
            inject = self._reply_text(cmd, reply)
            self._push_reply(inject)
        return inject

    def drain_replies(self):
        """取走最近回执（供调用方拼进下轮 TransientContext.replies），并清空。"""
        with self._lock:
            out, self._last_replies = list(self._last_replies), []
        return out

    def peek_replies(self):
        """查看最近回执（不消费）。"""
        with self._lock:
            return list(self._last_replies)

    def status_line(self, maid=""):
        """女仆当前状态一行中文摘要（未连接时空串）。"""
        try:
            return self._link.status_line(maid)
        except Exception:
            return ""

    def snapshot(self, maid=""):
        """女仆合并快照 dict（AI 决策/状态注入用）；无数据返回 {}。"""
        try:
            return self._link.state(maid)
        except Exception:
            return {}

    def events(self):
        """取走最近事件中文行（供注入当轮上下文）。"""
        try:
            evs = self._link.drain_events() or []
            from game import maid_link
            return [t for t in (maid_link.event_to_text(e) for e in evs) if t]
        except Exception:
            return []

    # ---------------- 内部 ----------------

    def _send(self, cmd):
        """下发指令（默认排队，不抢占女仆当前任务），阻塞等回执。"""
        try:
            return self._link.request(
                cmd["cmd"], cmd["params"],
                cancel_previous=bool(cmd.get("cancel_previous")))
        except Exception:
            return None

    @staticmethod
    def _reply_text(cmd, reply):
        """把回执转成一行 [系统] 注入文本；失败/无回执返回空。"""
        name = cmd["cmd"]
        # 脚本回执带脚本名（如「砍橡树」），更贴近人话
        if name == "script" and isinstance(cmd.get("params"), dict):
            sname = (cmd["params"].get("name") or "").strip()
            if sname:
                name = "脚本「%s」" % sname
        if reply is None:
            return ""
        ok = bool(reply.get("ok"))
        err = (reply.get("result") or {}).get("error") or ""
        state = reply.get("state") or ""
        if ok:
            tail = "已受理" if state == "running" else "完成"
            return "[系统] 我的任务%s：%s" % (tail, name)
        if err:
            return "[系统] 我的任务失败：%s（%s）" % (name, err)
        return "[系统] 我的任务失败：%s" % name

    def _push_reply(self, inject):
        if not inject:
            return
        with self._lock:
            self._last_replies.append(inject)
            if len(self._last_replies) > 3:
                self._last_replies = self._last_replies[-3:]
