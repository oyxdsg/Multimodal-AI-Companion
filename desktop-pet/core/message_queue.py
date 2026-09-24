# -*- coding: utf-8 -*-
"""统一消息管理：所有并发消息源（语音输入 / AI 回复 / 游戏日志 / 新闻 / 工作 /
环境提醒 / 随机台词）统一提交到此队列，按优先级串行排队显示，避免短时间内
多条消息互相打断、音画错位。

消息源（任意线程）──post(text, kind, speak)──▶ PetMessageQueue
                                                   │ QTimer 轮询：不忙时取最高优先级一条
                                                   ▼
                                       pet._show_ai_bubble（气泡+语音，音画同步）
                                                   │ pet 渲染完成（气泡超时 / 语音播完）
                                                   ▼ 下一条

- 优先级：见 config.MSG_PRIORITY（voice_input > ai_reply > log > system > chatline）
- 去重：同 kind 且相邻同文本丢弃（防系统提醒重复刷屏）
- 上限：MSG_QUEUE_MAX 条，满则丢弃最低优先级
- 不打断：当前消息显示期间后续消息排队，宠物说完才播下一条
"""

import time

from PySide6.QtCore import QObject, QTimer

import config


class PetMessageQueue(QObject):
    """统一消息调度队列。pet 提供渲染入口 _show_ai_bubble，并负责通知完成。"""

    def __init__(self, pet, parent=None):
        super().__init__(parent)
        self.pet = pet
        self._items = []          # [priority, seq, (text, kind, speak, error)]
        self._seq = 0
        self._last_key = None     # 去重：上一条 (kind, text)
        # 消费轮询：pet._render_idle() 为真（上一条显示完）时取最高优先级下一条
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._maybe_dispatch)
        self._timer.start(config.MSG_POLL_MS)

    # ---------- 对外入口 ----------

    def post(self, text, kind="ai_reply", speak=False, error=False,
             priority=None, force=False):
        """提交一条消息。priority 缺省按 kind 查表；force=True 跳过相邻去重。"""
        text = (text or "").strip()
        if not text:
            return
        if not force:
            key = (kind, text)
            if key == self._last_key:
                return            # 相邻重复，丢弃
            self._last_key = key
        pr = priority if priority is not None else config.MSG_PRIORITY.get(kind, 20)
        self._seq += 1
        self._items.append([pr, self._seq, (text, kind, bool(speak), bool(error))])
        # 上限：保留高优先级，优先丢最低优先级（相同优先级丢最旧）
        if len(self._items) > config.MSG_QUEUE_MAX:
            self._items.sort(key=lambda x: (x[0], x[1]))
            self._items.pop(0)
        # 不立即渲染：统一由定时器调度，保证同一批次内高优先级消息插队在前

    def clear(self):
        """清空待显示队列（换会话 / 语音输入开始时可选）。"""
        self._items.clear()

    def pending_count(self):
        return len(self._items)

    # ---------- 内部调度 ----------

    def _maybe_dispatch(self):
        if not self._items:
            return
        if not self.pet._render_idle():
            return            # 渲染层忙（正在显示上一条），排队等待
        # 取最高优先级（降序），同优先级按提交先后（FIFO）
        self._items.sort(key=lambda x: (-x[0], x[1]))
        _pr, _s, (text, kind, speak, error) = self._items.pop(0)
        try:
            self.pet._render_message(text, error=error, speak=speak)
        except Exception:
            pass