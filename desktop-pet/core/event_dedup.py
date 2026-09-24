# -*- coding: utf-8 -*-
"""事件去重（WS 与文件通道共享）：避免同一事件被播报两次。

背景（DESIGN_LOOP_DEEPENED §7）：
- 女仆联动有两条下行通道：WS `event`（实时）+ 文件 jsonl（回放，WS 断线时读）。
- 若两条都开着，同一事件可能被播报两次。需要一个**共享去重集**：
  `maid_handler`（WS）与 `game_handler`（文件）都往里查。

设计：
- 指纹 = 事件中文文本（经 event_to_text / highlights_to_text 转出后的行）。
- 同指纹 60 秒内只放行一次；超时自动过期。
- 例外：`task_started` / `task_done`（任务启停）每次都要播报，不参与去重。
- 线程安全（lock 保护，两个 handler 可能在不同线程）。
"""

import threading
import time


class EventDedup:
    """按指纹去重，TTL 秒内只放行一次。"""

    def __init__(self, ttl=60):
        self._ttl = ttl
        self._seen = {}       # fingerprint -> timestamp
        self._lock = threading.Lock()

    def seen(self, fingerprint, now=None):
        """查询指纹是否已存在（不登记）。"""
        if not fingerprint:
            return False
        now = now or time.time()
        with self._lock:
            last = self._seen.get(fingerprint)
            return last is not None and (now - last) < self._ttl

    def check(self, fingerprint, now=None):
        """若指纹未在 TTL 内出现过 → 登记并返回 True（放行）；否则 False（去重）。"""
        if not fingerprint:
            return True
        now = now or time.time()
        with self._lock:
            self._prune(now)
            last = self._seen.get(fingerprint)
            if last is not None and (now - last) < self._ttl:
                return False
            self._seen[fingerprint] = now
            return True

    def _prune(self, now):
        expired = [k for k, t in self._seen.items()
                   if (now - t) >= self._ttl]
        for k in expired:
            self._seen.pop(k, None)

    def reset(self):
        with self._lock:
            self._seen.clear()


# 全局单例（maid_handler WS 与 game_handler 文件共用）
shared = EventDedup(ttl=60)


def is_task_boundary(text):
    """任务启停类事件不参与去重（每次都要播报）。

    由事件文本特征判断：含「开始」「完成」且提到任务。
    """
    return ("开始" in text and "了" in text) or ("完成" in text and "了" in text)
