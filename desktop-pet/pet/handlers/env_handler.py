# -*- coding: utf-8 -*-
"""环境提醒 Handler：本地时间整点关怀 + 联网天气播报。

从 pet/modes.py 的 PetModesMixin 环境域迁移：
- 整点提醒（09:00 早安 / 12:00 午饭 / 15:30 提神 / 18:30 收工 / 23:30 早睡）
- 天气拉取与播报（下雨 / 高温 / 低温 / 大风），城市默认按 IP 定位
结果经 app.weather_ready 信号交给窗口展示。
"""
import datetime
import random
import threading
import time

import config
import info.env as env_info
from pet.handlers.base import BaseHandler


class EnvHandler(BaseHandler):
    """环境提醒（时间 + 天气）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._env_enabled = str(
            store.value("env_enabled", True)) != "false"
        self._manual_city = str(store.value("env_city", "") or "")
        self._env_city_auto = str(store.value("env_city_auto", "") or "")
        self._env_day_triggered = {}     # category -> 触发日期(YYYY-MM-DD)
        self._weather = None
        self._last_weather_fetch = 0.0
        self._weather_fetching = False

    # ---------- 心跳轮询 ----------

    def tick(self):
        """由窗口统一心跳按周期调用。"""
        if self._env_enabled:
            now = datetime.datetime.now()
            today = now.strftime("%Y-%m-%d")
            cat = env_info.time_reminder(now)
            if cat and self._env_day_triggered.get(cat) != today:
                self._env_day_triggered[cat] = today
                self._say_reminder(cat)
            self._maybe_fetch_weather()

    def _maybe_fetch_weather(self):
        if self._weather_fetching:
            return
        if time.time() - self._last_weather_fetch < config.ENV_WEATHER_CHECK_MS / 1000:
            return
        self._last_weather_fetch = time.time()
        self._weather_fetching = True
        threading.Thread(target=self._weather_worker, daemon=True).start()

    def _weather_worker(self):
        try:
            city = (self._manual_city.strip()
                    or self._env_city_auto
                    or env_info.get_city())
            if not city:
                return
            w = env_info.fetch_weather(city)
            if not w:
                return
            self._weather = w
            cat = env_info.weather_category(w)
            self.app.weather_ready.emit(w, cat)
        finally:
            self._weather_fetching = False

    def on_weather_ready(self, w, cat):
        """weather_ready 信号槽：当天首次汇报/特殊天气提醒。"""
        if not self._env_enabled or not w:
            return
        if not self._manual_city:
            self.store.setValue("env_city_auto", w.get("city", ""))
            self._env_city_auto = w.get("city", "")
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        # 有特殊天气提醒优先；否则做一次当日日常天气汇报（每天一次）
        key = cat or "weather"
        if self._env_day_triggered.get(key) == today:
            return
        self._env_day_triggered[key] = today
        self._say_reminder(key, w)

    def _say_reminder(self, category, ctx=None):
        lines = config.ENV_REMINDER_LINES.get(category)
        if not lines:
            return
        candidates = [l for l in lines if l != self.app._last_line]
        line = random.choice(candidates or lines)
        if ctx:
            try:
                line = line.format(**ctx)
            except (KeyError, ValueError, IndexError):
                pass
        self.app._last_line = line
        self.app._last_line_time = time.monotonic()
        self.show_bubble(line, speak=True, kind="system")
