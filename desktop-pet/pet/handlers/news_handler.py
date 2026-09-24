# -*- coding: utf-8 -*-
"""新闻播报 Handler：RSS 拉取 + AI 可爱播报。

从 pet/modes.py 的 PetModesMixin 新闻域迁移：
- 定时拉取 RSS 源（默认澎湃新闻，多镜像），读取最新 3-5 条标题
- 已登录 AI 时逐条可爱播报，未登录直接播标题
结果经 app.news_ready 信号交给窗口展示。
"""
import json
import re
import threading
import time

import config
import info.news as news
from ai.chat_service import shared as chat_service
from pet.handlers.base import BaseHandler


class NewsHandler(BaseHandler):
    """新闻播报（独立选项，非 AI 模式）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._news_enabled = str(
            store.value("news_enabled", True)) != "false"
        self._news_feeds = self.load_feeds()
        self._news_freq_min = int(store.value("news_freq", 120) or 120)
        self._last_news_fetch = 0.0
        self._news_fetching = False
        self._last_news_report = ""      # 上次播报的文本，去重用

    def load_feeds(self):
        raw = str(self.store.value("news_feeds", "") or "")
        if raw:
            try:
                feeds = json.loads(raw)
                if isinstance(feeds, list) and feeds:
                    return feeds
            except (ValueError, TypeError):
                pass
        return [dict(f) for f in config.DEFAULT_NEWS_FEEDS]

    # ---------- 心跳轮询 ----------

    def tick(self):
        """由窗口统一心跳按周期调用。"""
        self._maybe_fetch_news()

    def _maybe_fetch_news(self):
        if not self._news_enabled or not self._news_feeds:
            return
        if self._news_fetching:
            return
        if time.time() - self._last_news_fetch < self._news_freq_min * 60:
            return
        self._last_news_fetch = time.time()
        self._news_fetching = True
        threading.Thread(target=self._news_worker, daemon=True).start()

    def _news_worker(self):
        try:
            # 按顺序取第一个成功拉取的源播报
            for feed in self._news_feeds:
                titles = news.fetch_feed(feed.get("url", ""))
                if not titles:
                    continue
                source = feed.get("name", "新闻")
                items = None
                # 已登录时让 AI 用「新闻模式」逐条总结
                if str(self.store.value("ai_token", "") or ""):
                    items = self._ai_news_items(source, titles)
                if not items:
                    items = self._news_title_items(titles)
                self.app.news_ready.emit(items)
                return
        finally:
            self._news_fetching = False

    def _ai_news_items(self, source, titles):
        """让 AI 逐条播报新闻（新闻是独立选项，非 AI 模式）；失败返回 None。"""
        try:
            tlist = "\n".join(
                f"{i + 1}. {t}"
                for i, t in enumerate(titles[:config.NEWS_MAX_ITEMS]))
            prompt = config.NEWS_AI_PROMPT.format(source=source, tlist=tlist)
            content = chat_service.chat(prompt, "新闻")
            content = (content or "").strip()
            lines = [l.strip() for l in content.splitlines() if l.strip()]
            # 去掉可能残留的编号
            clean = [re.sub(r"^\d+[\.、)）]?\s*", "", l) for l in lines]
            if not clean:
                return None
            return clean
        except Exception:
            return None

    def _news_title_items(self, titles):
        """未登录时直接逐条播报标题（每条单独一行）。"""
        items = []
        for t in titles[:config.NEWS_MAX_ITEMS]:
            if len(t) > config.NEWS_MAX_LEN:
                t = t[:config.NEWS_MAX_LEN] + "…"
            items.append("· " + t)
        return items

    def on_news_ready(self, items):
        """news_ready 信号槽：与上次相同跳过，逐条进统一消息队列。"""
        if not self._news_enabled or not items:
            return
        # 与上次播报完全相同则跳过（新闻没更新）
        joined = "\n".join(items)
        if joined == self._last_news_report:
            return
        self._last_news_report = joined
        # 逐条进入统一消息队列，每条至少显示 8 秒
        for item in items:
            self.show_bubble(item, speak=True, kind="log")
