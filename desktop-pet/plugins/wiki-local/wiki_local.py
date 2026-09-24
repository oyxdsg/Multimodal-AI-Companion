# -*- coding: utf-8 -*-
"""wiki-local 插件的知识实现（原 ``game/processor.py`` 的 Wiki 链路搬入）。

三层知识链路：curated 命中 → 本地精炼模型 / 大 AI → 原文兜底；
实体提取、低频过滤、会话去重、总预算裁剪都在这里完成。

宿主只调用 :meth:`WikiLocal.know`，对 wiki 无感知。
"""

import wi_config

_MIN_TAIL = 40
_STORE = None


def _store():
    """惰性打开 QSettings（与宿主同库同键，读取「提炼开关 / 过滤模式 / 引擎」）。"""
    global _STORE
    if _STORE is None:
        from PySide6.QtCore import QSettings
        _STORE = QSettings("DesktopPet", "PetWindow")
    return _STORE


def _cut_at_boundary(text, limit):
    from wiki_refine import refine as wiki_refine
    return wiki_refine.cut_at_boundary(text, limit)


def _clip_knowledge(items, budget):
    """按顺序把知识块塞进总预算，返回 ``(注入文本, 存活的块列表)``。

    items: ``[{"term": 检索词或 None, "text": 块文本}]``，``term=None`` 表示
    L2/L3 的批量兜底块。顺序即优先级，超预算先丢兜底块。
    """
    kept, used, out = [], 0, []
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        sep = 1 if out else 0
        remain = budget - used - sep
        if remain <= 0:
            break
        if len(text) > remain:
            if remain < _MIN_TAIL:
                break
            text = _cut_at_boundary(text, remain)
        out.append(text)
        kept.append(it)
        used += len(text) + sep
    return "\n".join(out), kept


class WikiLocal:
    """本地 Wiki 知识实现（``wiki-knowledge`` 扩展点）。"""

    def __init__(self):
        self._sid = None       # 去重绑定的会话 id
        self._done = set()     # 已产出过知识的检索词

    # ---------- 设置 ----------

    def _engine(self):
        """精炼引擎开关：旧开关 ai_wiki_refine=false → off；否则按引擎键。"""
        st = _store()
        refine_on = str(st.value("ai_wiki_refine", True)).lower() not in (
            "false", "0", "")
        if not refine_on:
            return "off"
        eng = str(st.value("wiki_refine_engine",
                           wi_config.WIKI_REFINE_ENGINE)).strip() or "local"
        return eng if eng in ("local", "ai", "off") else "local"

    def _lowfreq(self):
        return str(_store().value("ai_wiki_filter", "normal")) == "lowfreq"

    # ---------- 扩展点入口 ----------

    def know(self, events_text, *, session_key="", max_chars=480, hints=()):
        """从游戏事件文本提取实体 → 检索本地库 → 提炼 → 按预算裁剪。"""
        try:
            import wiki_kb
            lowfreq = self._lowfreq()
            terms = list(wiki_kb.match_terms(events_text,
                                             include_rare=lowfreq) or [])
            terms = list(dict.fromkeys(terms + list(hints or [])))
            if lowfreq:
                terms = [t for t in terms if wiki_kb.is_worth_wiki(t)]
            # 会话去重：换会话重置
            if self._sid != session_key:
                self._sid = session_key
                self._done = set()
            new_terms = [t for t in terms if t not in self._done]
            if not new_terms:
                return ""
            text, produced = self._lookup(new_terms, max_chars)
            self._done.update(produced)
            return text
        except Exception:
            return ""

    # ---------- 三层链路 ----------

    def _lookup(self, terms, max_chars):
        import wiki_kb
        from wiki_refine import curated as wiki_curated
        from wiki_refine import refine as wiki_refine

        engine = self._engine()
        items = []
        todo = list(terms or [])
        if engine in ("local", "ai"):
            miss = []
            for t in todo:
                out = wiki_curated.prompt([t])
                if out:
                    items.append({"term": t, "text": out})
                else:
                    miss.append(t)
            todo = miss
        if todo:
            if engine == "local":
                cands = wiki_kb.search_candidates(todo, 1, per_term=1)
                out = wiki_refine.refine(cands, terms=todo)
                if not out:
                    out = wiki_kb.search_text(todo, wi_config.GAME_WIKI_LIMIT)
            elif engine == "ai":
                text = wiki_kb.search_text(todo, wi_config.GAME_WIKI_LIMIT)
                out = ""
                if text:
                    ref = self._ai_refine(text)
                    out = "" if ref == "无" else (ref or text)
            else:
                out = wiki_kb.search_text(todo, wi_config.GAME_WIKI_LIMIT)
            if out:
                items.append({"term": None, "text": out})
        text, _kept = _clip_knowledge(items, max_chars)
        hit = [it["term"] for it in items if it["term"]]
        if any(it["term"] is None for it in items):
            hit.extend(todo)
        return text, hit

    def _ai_refine(self, text):
        """ai 档：借宿主 AI 会话做独立提炼（失败返回空 → 原文兜底）。"""
        try:
            import ai.client as ai_client
            return ai_client.refine_wiki(text)
        except Exception:
            return ""
