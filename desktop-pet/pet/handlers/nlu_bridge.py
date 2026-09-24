# -*- coding: utf-8 -*-
"""NLU ⇄ 女仆联动的适配层。

把 :class:`pet.handlers.maid_handler.MaidHandler`（以及它背后的
:class:`game.maid_link.MaidLink`）适配成 :mod:`nlu.router` 需要的 Bridge 协议，
让 NLU 子包**不直接依赖**桌宠的 UI / 线程模型（便于单测时替换为假对象）。

线程约定：这里的方法会**阻塞等回执**，只能在**工作线程**调用
（聊天 / 语音 worker），绝不能在 Qt 主线程调用。
"""


class MaidNluBridge:
    """Bridge 协议实现（鸭子类型，见 ``nlu.router`` 模块文档）。"""

    def __init__(self, handler):
        self._h = handler

    # ---- 只读状态 ----

    @property
    def connected(self):
        try:
            return bool(self._h.connected)
        except Exception:
            return False

    def item_index(self):
        link = getattr(self._h, "_link", None)
        if link is None:
            return {}
        try:
            return link.item_index()
        except Exception:
            return {}

    def inventory(self):
        link = getattr(self._h, "_link", None)
        if link is None:
            return {}
        try:
            return link.inventory_counts()
        except Exception:
            return {}

    def inventory_slots(self):
        """背包槽位明细（含槽位号），用于拼 ``inv:<n>`` 转移源。"""
        link = getattr(self._h, "_link", None)
        if link is None:
            return []
        try:
            return link.inventory_slots()
        except Exception:
            return []

    def maid_pos(self):
        link = getattr(self._h, "_link", None)
        if link is None:
            return None
        try:
            return link.maid_pos()
        except Exception:
            return None

    def owner_pos(self):
        link = getattr(self._h, "_link", None)
        if link is None:
            return None
        try:
            return link.owner_pos()
        except Exception:
            return None

    # ---- 指令 ----

    def query_craft(self, item_id, count=1, timeout=3.0):
        link = getattr(self._h, "_link", None)
        if link is None or not link.connected:
            return None
        try:
            return link.query_craft(item_id, int(count), timeout=timeout)
        except Exception:
            return None

    def query_status(self, timeout=2.0):
        link = getattr(self._h, "_link", None)
        if link is None or not link.connected:
            return None
        try:
            return link.query_status(timeout=timeout)
        except Exception:
            return None

    def wait_task_done(self, task_id, timeout=4.0):
        """等异步任务跑完（如 chestopen 完成后容器才算「已打开」）。"""
        link = getattr(self._h, "_link", None)
        if link is None or not link.connected:
            return False
        try:
            return link.wait_task_done(task_id, timeout=timeout)
        except Exception:
            return False

    def send_command(self, cmd, params=None, cancel_previous=False, timeout=6.0):
        link = getattr(self._h, "_link", None)
        if link is None or not link.connected:
            return None
        try:
            return link.request(cmd, params or {}, timeout=timeout,
                                cancel_previous=cancel_previous)
        except Exception:
            return None

    def speak(self, text, maid=""):
        try:
            return self._h.speak(text, maid=maid)
        except Exception:
            return False
