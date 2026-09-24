# -*- coding: utf-8 -*-
"""Handler 基类：各功能域 Handler（环境/新闻/工作/游戏/修仙/STT）的公共能力。

Handler 持有 app（PetWindow）引用，只通过以下三处与窗口交互：
- app._store（QSettings 设置）
- app._msg / app._show_ai_bubble（统一消息队列与气泡显示）
- app 的 Qt Signal（weather_ready / news_ready / tip_ready / game_ready / stt_*_ready）
AI 调用统一走 ai.chat_service（全局串行），业务处理走 game.processor。
"""


class BaseHandler:
    """Handler 基类。由各功能域 Handler 继承。"""

    def __init__(self, app):
        self.app = app

    # ---------- 共享能力 ----------

    @property
    def store(self):
        """用户设置（QSettings）。"""
        return self.app._store

    @property
    def msg(self):
        """统一消息队列。"""
        return self.app._msg

    def show_bubble(self, text, error=False, speak=False,
                    kind="ai_reply", priority=None):
        """经统一消息队列显示气泡（+可选语音）。"""
        if not text:
            return
        self.app._show_ai_bubble(text, error=error, speak=speak,
                                 kind=kind, priority=priority)

    @property
    def game_type(self):
        """当前游戏联动类型（0 关闭 / 1 Minecraft / 2 修仙，窗口级共享状态）。"""
        return self.app._game_type
