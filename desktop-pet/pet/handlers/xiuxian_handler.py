# -*- coding: utf-8 -*-
"""凡人修仙游戏联动 Handler：桥接子进程生命周期 + 事件轮询 + AI 互动。

从 pet/modes.py 的 PetModesMixin 修仙域迁移：
- 启动/停止/清理桥接子进程（启服务器 + 拉浏览器）
- 增量读事件，关键事件立即互动 / 普通事件按周期汇总
- AI 互动（组装后交给 game.processor，结果经 app.game_ready 信号展示）

窗口级共享状态（_xiuxian_enabled / _game_type / _set_game_type）保留在窗口骨架。
"""
import os
import threading
import time

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QMessageBox

import config
import game.xiuxian as xiuxian
import game.processor as game_processor
from pet.handlers.base import BaseHandler


def _log_worker_error(tag, exc):
    """把 worker 线程被吞掉的异常写入 logs/crash.log，便于定位「无播报」类问题。"""
    try:
        import traceback as _tb
        log = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "logs", "crash.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== WorkerError[{tag}] =====")
            _tb.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


class XiuxianHandler(BaseHandler):
    """凡人修仙游戏联动（方案 C：playwright 直接读浏览器状态）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._xiuxian_game_dir = str(
            store.value("xiuxian_game_dir", "") or "") \
            or config.XIUXIAN_GAME_DIR
        self._xiuxian_proc = None           # 桥接子进程（QProcess）
        self._xiuxian_events_path = xiuxian.events_path()
        self._xiuxian_events_pos = 0        # 事件文件字节偏移
        self._xiuxian_accum = []            # 待处理的修仙事件
        self._xiuxian_last_key = ""         # 上次互动的事件文本（去重）
        self._xiuxian_last_ai = 0.0         # AI 调用冷却
        self._xiuxian_last_summary = 0.0    # 普通事件汇总周期
        self._xiuxian_launch_at = 0.0       # 上次拉起桥接时间（冷却防重复）

    # ---------- 生命周期 ----------

    def start(self):
        """启动修仙游戏桥接（子进程：启服务器 + 拉浏览器）。
        互斥切换：启动修仙会把游戏联动类型切到修仙（关闭 Minecraft 联动）。"""
        if xiuxian.is_bridge_running() or (
                self._xiuxian_proc is not None
                and self._xiuxian_proc.state() == QProcess.Running):
            QMessageBox.information(self.app, "修仙游戏", "修仙游戏已在运行中")
            return
        self.app._set_game_type(2)
        proc = xiuxian.start_bridge(self._xiuxian_game_dir)
        if proc is None:
            QMessageBox.warning(
                self.app, "修仙游戏",
                "无法启动修仙游戏桥接\n请确认已安装 playwright：pip install playwright")
            return
        self._xiuxian_proc = proc
        self._xiuxian_launch_at = time.time()
        self._xiuxian_events_pos = 0
        self.show_bubble("修仙游戏启动中，去开始你的修仙之旅吧～",
                         speak=True, kind="system")
        QTimer.singleShot(8000, self.bridge_check)

    def stop(self):
        """停止修仙游戏桥接（子进程 + 它拉起的浏览器），联动类型回到关闭。"""
        proc = self._xiuxian_proc
        if proc is not None:
            if proc.state() == QProcess.Running:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
            self._xiuxian_proc = None
        if self.app._game_type == 2:
            self.app._set_game_type(0)
        self.show_bubble("修仙游戏已停止～", speak=True, kind="system")

    def cleanup(self):
        """桌宠退出时终止桥接子进程，避免残留。"""
        proc = self._xiuxian_proc
        if proc is not None:
            if proc.state() == QProcess.Running:
                proc.terminate()
                if not proc.waitForFinished(3000):
                    proc.kill()
            self._xiuxian_proc = None

    def bridge_check(self):
        """启动数秒后检查桥接子进程是否存活，给出反馈。"""
        if self._xiuxian_proc is None:
            return
        if self._xiuxian_proc.state() != QProcess.Running:
            self._xiuxian_proc = None
            self.show_bubble(
                "修仙游戏启动失败：请检查游戏目录是否存在、Node 是否安装～",
                error=True, speak=False, kind="system")

    # ---------- 心跳轮询 ----------

    def tick(self):
        """由窗口统一心跳按周期调用。"""
        if not self.app._xiuxian_enabled:
            return
        now = time.time()
        # 桥接已在跑（心跳存活）则只读事件，不重复拉起
        if xiuxian.is_bridge_running():
            if (self._xiuxian_proc is not None
                    and self._xiuxian_proc.state() == QProcess.NotRunning):
                self._xiuxian_proc = None
        elif now - self._xiuxian_launch_at < 60:
            pass   # 距上次拉起 60s 内（覆盖子进程启动期）不再重复拉起
        else:
            # 清理残留的 QProcess 引用，再启动一个
            self._xiuxian_proc = None
            self._xiuxian_launch_at = now
            self._xiuxian_proc = xiuxian.start_bridge(self._xiuxian_game_dir)
            self._xiuxian_events_pos = 0
        # 增量读事件
        evs, pos = xiuxian.read_events(
            self._xiuxian_events_path, self._xiuxian_events_pos)
        self._xiuxian_events_pos = pos
        if not evs:
            return
        self._xiuxian_accum.extend(evs)
        # 关键事件立即互动；普通事件按周期汇总互动
        urgent = [e for e in evs
                  if e.get("type") in ("start", "advance", "ending", "death")]
        now = time.time()
        if urgent or now - self._xiuxian_last_summary >= 30:
            self._xiuxian_last_summary = now
            if self._xiuxian_accum:
                if time.time() - self._xiuxian_last_ai < config.GAME_AI_COOLDOWN:
                    return
                self._xiuxian_last_ai = time.time()
                batch = list(self._xiuxian_accum)
                self._xiuxian_accum = []
                state = batch[-1].get("state") or {}
                threading.Thread(
                    target=self._xiuxian_worker, args=(batch, state),
                    daemon=True).start()

    def _xiuxian_worker(self, events, state_summary):
        """修仙事件 → AI 互动（processor 内部组装 prompt 并调用共享 AI 会话）。"""
        try:
            result = game_processor.shared.process_xiuxian(events, state_summary)
            if result:
                self.app.game_ready.emit(result["text"])
        except Exception as e:
            _log_worker_error("xiuxian", e)
