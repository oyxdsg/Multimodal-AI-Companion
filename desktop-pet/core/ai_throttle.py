# -*- coding: utf-8 -*-
"""全局软限流 AICallThrottle：控制「游戏/女仆闭环链路」的 AI 调用频率。

设计（DESIGN_LOOP_DEEPENED §8）：
- 只作用于「AI 决策 / AI 润色播报 / 游戏互动」这些非用户主动的 AI 调用，
  **不波及主对话**（用户主动聊天永远优先）。
- 滑动窗口计数：30 秒内 ≤5 次；超出 → 合并成一次批量播报（降级模板播报）。
- 5 分钟 ≤35 次 → 全部降级模板播报；1 小时 ≤300 次 → 提示用户休息。
- 线程安全（lock 保护），可被多个 handler 线程共享。
"""

import threading
import time


class AICallThrottle:
    """按时间窗口限流 AI 调用（滑动窗口计数器）。"""

    def __init__(self, short=5, short_secs=30,
                 medium=35, medium_secs=300,
                 long=300, long_secs=3600):
        self._short, self._short_secs = short, short_secs
        self._medium, self._medium_secs = medium, medium_secs
        self._long, self._long_secs = long, long_secs
        self._calls = []          # [timestamp, ...]
        self._lock = threading.Lock()

    def allow(self, now=None):
        """是否允许本次 AI 调用。返回 True 放行并记录；False 应降级。"""
        now = now or time.time()
        with self._lock:
            # 清理超窗记录
            cutoff = now - self._long_secs
            self._calls = [t for t in self._calls if t >= cutoff]
            # 1 小时上限
            if len(self._calls) >= self._long:
                return False
            # 5 分钟上限
            if sum(1 for t in self._calls if t >= now - self._medium_secs) >= self._medium:
                return False
            # 30 秒上限
            if sum(1 for t in self._calls if t >= now - self._short_secs) >= self._short:
                return False
            self._calls.append(now)
            return True

    def reset(self):
        with self._lock:
            self._calls = []

    def stats(self, now=None):
        now = now or time.time()
        with self._lock:
            s30 = sum(1 for t in self._calls if t >= now - self._short_secs)
            m5 = sum(1 for t in self._calls if t >= now - self._medium_secs)
            h1 = len(self._calls)
        return {"30s": s30, "5min": m5, "1h": h1}


# 全局单例（进程内共享：AI 决策 + AI 润色播报共用同一计数）
shared = AICallThrottle()
