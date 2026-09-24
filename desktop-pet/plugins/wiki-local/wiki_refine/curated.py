# -*- coding: utf-8 -*-
"""curated 知识库检索：按**精确名称 / id / 别名**匹配条目，按 query 选 facts 生成注入文本。

数据：nlu/wiki_refine/data/curated_all.jsonl（build_curated.py 生成）。

与自动提炼（refine.py）的分工：
* ``search()`` 命中 curated → 直接注入（高质量、免模型）
* 未命中 → 回退精炼模型（refine.py）兜底长尾

**匹配口径**：只认精确（名称 / id / 条目自带 ``aliases``）。不做宽松子串——
子串匹配会让没有对应条目的实体被「包含它的另一个条目」抢答并注入错误知识。
条目可选带 ``aliases``（字符串列表）来显式声明同义写法。
"""
import json
import os
import re
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")
_ALL = os.path.join(DATA_DIR, "curated_all.jsonl")

# 非实体类别（成就 / 进度）：可以按精确名查到，但排在实体条目之后，
# 避免「嗅探兽」被进度条目【小小嗅探兽】抢答那类错配。
_RARE_CATEGORIES = ("成就", "进度")

# query → 优先的 facts.type
_TYPE_HINT = [
    (re.compile(r"配方|合成|怎么做|怎么做|制作|合成表|怎么合|材料"), "配方"),
    (re.compile(r"打|杀|打掉|怎么打|打死|击杀|击败|弱|克制|攻略"), "战斗"),
    (re.compile(r"挖|开采|等级|能挖|采矿|矿石"), "能力"),
    (re.compile(r"耐久|多少耐久|修复|能用多久"), "耐久"),
    (re.compile(r"吃|食物|恢复|饱|饿"), "效果"),
    (re.compile(r"掉落|掉什么|战利品"), "掉落"),
    (re.compile(r"繁殖|驯服|养"), "繁殖"),
    (re.compile(r"生成|哪里|在哪|怎么找"), "获取"),
    (re.compile(r"附魔|附什么魔"), "常见附魔"),
    (re.compile(r"有毒|中毒|危险|注意"), "备注"),
]


def _load_rows():
    if not os.path.isfile(_ALL):
        return []
    rows = []
    with open(_ALL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


class CuratedIndex:
    """线程安全的 curated 检索器（只读）。"""

    def __init__(self, rows=None):
        rows = rows if rows is not None else _load_rows()
        # 先去掉完全重复的条目（name + id 相同），再让实体条目优先占位：
        # 同名条目里先让「方块/物品/生物…」胜出、成就/进度条目退到最后，
        # 修掉「同名的前者被后者覆盖、永远查不到」的问题。
        seen = set()
        uniq = []
        for r in rows:
            sig = (self._norm(r.get("name") or ""), self._norm(r.get("id") or ""))
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(r)
        self.rows = sorted(uniq, key=self._rank)
        self.by_name = {}
        for r in self.rows:
            for k in self._keys(r):
                self.by_name.setdefault(k, r)
            for a in (r.get("aliases") or []):
                na = self._norm(a)
                if na:
                    self.by_name.setdefault(na, r)

    @staticmethod
    def _rank(r):
        """排序权重：实体条目 0（优先），成就/进度 1。"""
        return 1 if (r.get("category") or "") in _RARE_CATEGORIES else 0

    @classmethod
    def _keys(cls, r):
        """条目可被查到的键：中文名 + id。"""
        out = []
        for field in ("name", "id"):
            k = cls._norm(r.get(field) or "")
            if k:
                out.append(k)
        return out

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", "", s or "").lower()

    def find(self, terms):
        """按「名称 / id / 别名」**精确**匹配，返回第一个条目或 None。

        故意**不做宽松子串匹配**：旧实现是「双向子串 + 字典顺序首命中」，
        当查询词没有对应条目时会拿「包含该词的另一个条目」抢答，实测把
        「樱花木」答成【樱花木板】、「嗅探兽」答成进度【小小嗅探兽】、
        「木板」答成【金合欢木板】——**注入了错误实体的知识**。
        现在宁可返回 None（交给 L2 精炼模型 / L3 原文兜底），也不答错。

        :param terms: 字符串或词列表（依次尝试）
        """
        if isinstance(terms, str):
            terms = [terms]
        for t in (terms or []):
            nt = self._norm(t)
            if not nt or len(nt) < 2:
                continue
            hit = self.by_name.get(nt)
            if hit:
                return hit
        return None

    @staticmethod
    def _pick_facts(entry, terms):
        """按 query 排序 facts：命中的 type 靠前，其余按原文顺序补足。"""
        blob = " ".join(terms or [])
        order = []
        for rx, tp in _TYPE_HINT:
            if rx.search(blob) and tp not in order:
                order.append(tp)
        facts = list(entry.get("facts") or [])
        ranked = sorted(
            facts,
            key=lambda f: (order.index(f["type"]) if f["type"] in order else 99))
        return ranked

    def to_prompt(self, entry, terms=None, fact_limit=4):
        """条目 → 注入文本（【条目名】+ 关键 facts + 可选趣闻）。"""
        name = entry.get("name") or ""
        lines = ["【%s】%s" % (name, entry.get("summary") or "")]
        facts = self._pick_facts(entry, terms)[:fact_limit]
        for f in facts:
            t = (f.get("text") or "").strip()
            if t:
                lines.append("· %s：%s" % (f.get("type") or "", t))
        fun = (entry.get("fun") or "").strip()
        if fun:
            lines.append("【趣闻】%s" % fun)
        return "\n".join(lines)


_LOCK = threading.Lock()
_SHARED = None
_FAILED = False


def shared():
    global _SHARED, _FAILED
    if _SHARED is not None or _FAILED:
        return _SHARED
    with _LOCK:
        if _SHARED is not None or _FAILED:
            return _SHARED
        try:
            _SHARED = CuratedIndex()
            if not _SHARED.rows:
                _SHARED = None
                _FAILED = True
        except Exception:
            _SHARED = None
            _FAILED = True
    return _SHARED


def search(terms):
    """便捷入口：返回条目 dict；未命中返回 None。"""
    idx = shared()
    if idx is None:
        return None
    try:
        return idx.find(terms)
    except Exception:
        return None


def prompt(terms, fact_limit=4):
    """便捷入口：返回注入文本；未命中返回 ""。"""
    idx = shared()
    if idx is None:
        return ""
    try:
        hit = idx.find(terms)
        if not hit:
            return ""
        return idx.to_prompt(hit, terms=terms, fact_limit=fact_limit)
    except Exception:
        return ""
