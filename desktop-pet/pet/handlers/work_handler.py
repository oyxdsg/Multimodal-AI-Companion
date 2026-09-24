# -*- coding: utf-8 -*-
"""工作模式 Handler：检测前台窗口软件/文件，AI 生成实用小知识。

从 pet/modes.py 的 PetModesMixin 工作域迁移：
- 前台窗口变化立即提示；停留同一程序按设定频率提示（默认 5 分钟）
- 提示由 AI 生成（共享 AI 会话），内容去重
结果经 app.tip_ready 信号交给窗口展示。
"""
import threading
import time

import config
import info.work_mode as work_mode
from ai.chat_service import shared as chat_service
from pet.handlers.base import BaseHandler


class WorkHandler(BaseHandler):
    """工作模式（前台窗口 → AI 实用小知识）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._work_enabled = str(
            store.value("work_enabled", True)) != "false"
        self._work_freq_min = int(store.value("work_freq", 5) or 5)
        self._work_last_key = None      # 上次检测到的 (进程, 文件)
        self._work_last_tip = 0.0
        self._said_tips = []            # 已播报过的提示，去重（保留最近 50 条）

    # ---------- 心跳轮询 ----------

    def tick(self):
        """由窗口统一心跳按周期调用。"""
        if not self._work_enabled:
            return
        info = work_mode.detect()
        if not info:
            return
        key = (info.get("process", ""), info.get("file"))
        changed = (key != self._work_last_key)
        self._work_last_key = key
        # 新打开文件立即提示；否则按用户设定的频率（默认每 5 分钟一条）
        if not changed and time.time() - self._work_last_tip < self._work_freq_min * 60:
            return
        self._work_last_tip = time.time()
        threading.Thread(target=self._tip_worker, args=(info,), daemon=True).start()

    def _tip_worker(self, info):
        # 提示由 AI 生成；未登录时不提示（不回退预设）
        if not str(self.store.value("ai_token", "") or ""):
            return
        try:
            said = "；".join(self._said_tips[-15:]) or "无"
            # 模式声明仅网页版需要（system 只在首条注入）；API / 千问已把当前模式
            # 写进每轮 system，user 消息里不再重复声明。
            decl = "" if config.is_messages_backend() else "现在切换到 2、工作模式。\n"
            prompt = (decl
                      + f"用户当前的前台窗口信息：进程={info.get('process') or '未知'}，"
                      f"窗口标题={info.get('title') or '未知'}，"
                      f"进程路径={info.get('path') or ''}。\n"
                      f"已提示过的内容：{said}\n"
                      "请判断用户正在做什么，生成一条全新的针对性实用小知识。")
            content = chat_service.chat(prompt, "工作")
            content = (content or "").strip()
            if content and content not in self._said_tips:
                self.app.tip_ready.emit(content)
        except Exception:
            pass

    def on_tip_ready(self, tip):
        """tip_ready 信号槽：去重后显示气泡。"""
        if not self._work_enabled or not tip:
            return
        if tip in self._said_tips:
            return
        self._said_tips.append(tip)
        if len(self._said_tips) > 50:
            self._said_tips = self._said_tips[-50:]
        self.show_bubble(self.app._strip_action_tags(tip), speak=True, kind="log")
