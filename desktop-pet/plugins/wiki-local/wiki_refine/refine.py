# -*- coding: utf-8 -*-
"""精炼模型推理：候选句打分 → 选知识 top-k + 趣闻 1~2 条 → 拼装注入文本。

用法（给 processor 用）::

    from wiki_refine import refine
    text = refine.refine(candidates, terms=["铁镐"])

``candidates`` 来自 ``wiki_kb.candidates()``（标题 + 小节链 + 候选句）。
输出分两槽：【要点】+【趣闻】，让 AI 区分硬知识与对话素材。
"""
import os
import re
import threading

from wiki_refine import features, model as rm

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")

KNOW_BUDGET = 120     # 要点预算（字）
FUN_BUDGET = 40       # 趣闻预算（字）
QUERY_BOOST = 0.12    # query 命中小节/句子的得分加成
MIN_PIECE = 12        # 单条要点短于该长度不值得占篇幅
SIM_RATIO = 0.7       # 与已选句子的字符 Jaccard 相似度超过该值视为重复
# 单个注入块的硬上限（含【实体名】/【要点】/【趣闻】前缀）：
# 不超过 L3 原文兜底的单页上限（`game.wiki_kb._SNIPPET_LEN` = 180）
# ——「精炼」在任何情况下都不该比原文的一页更长。
# 注意：这是**单块**上限；整条 `{wiki}` 注入的总额由调用方
# `config.GAME_WIKI_MAX_CHARS` 把关（多实体时会产出多块）。
MAX_TOTAL = KNOW_BUDGET + FUN_BUDGET + 12


def _too_similar(text, picked):
    """与已选句子高度重合则跳过（如「该区域有13张贴纸/该区域有12张贴纸」）。"""
    if not picked:
        return False
    a = set(text)
    if not a:
        return True
    for p in picked:
        b = set(p)
        if b and len(a & b) / len(a | b) >= SIM_RATIO:
            return True
    return False


def cut_at_boundary(text, limit):
    """把 text 截到 limit 字以内，尽量收在换行/句读边界上。

    直接 `text[:limit]` 会切出半句话（实测会注入到「…功能上与木」这种），
    这里优先在换行、句末标点、空格处收尾，否则才硬切。
    调用方（含 `game/processor.py` 的整条注入预算）共用这一份口径。
    """
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n", "。", "！", "？", "；", "：", " "):
        i = cut.rfind(sep)
        if i >= MIN_PIECE:
            return cut[:i + 1].strip()
    return cut.strip()

# 知识价值分层（挑选时的排序加权，弥补"所有知识句概率都 ~1.0"导致的先到先得）
# 高价值：警告/行为红线（激怒/不能/受伤/攻击）、能力/配方（挖掘等级/合成）
_WARN_RE = re.compile(
    r"激怒|注视|盯|不要|不能|无法|只能|否则|会攻击|不会攻击|受伤害|受伤|危险|被.{0,6}杀死")
_ABILITY_RE = re.compile(
    r"能挖掘|可以开采|挖掘等级|最高|可合成|合成配方|→|可用于|用作|作为工具")
_GEN_DROP_RE = re.compile(r"生成于|生成在|会生成|掉落|获得")
# 低价值：次要细节（耐久/修复/音效/纹理/动画/视角/粒子/恢复/攻速数据）
_LOW_RE = re.compile(
    r"耐久|修复|音效|纹理|动画|视角|粒子|攻击冷却|恢复|合并|声音|弹射物|碰撞箱"
    r"|攻击速度|攻击伤害|攻速")


def _value(row):
    """知识价值分层（0.30 引言 > 0.22 警告红线 > 0.16 能力配方 > 0.10 生成掉落）。"""
    text = row.get("text") or ""
    if (row.get("sec") or "").split("/") == ["引言"]:
        return 0.30
    if _WARN_RE.search(text):
        return 0.22
    if _ABILITY_RE.search(text):
        return 0.16
    if _GEN_DROP_RE.search(text):
        return 0.10
    if _LOW_RE.search(text):
        return -0.12
    return 0.0


class RefineEngine:
    """线程安全的精炼器（内部只读）。"""

    def __init__(self, feat, net, data_dir=DATA_DIR):
        self.feat = feat
        self.net = net
        self.data_dir = data_dir

    @classmethod
    def load(cls, data_dir=DATA_DIR):
        model_path = os.path.join(data_dir, "model.npz")
        vocab_path = os.path.join(data_dir, "vocab.json")
        if not (os.path.isfile(model_path) and os.path.isfile(vocab_path)):
            raise FileNotFoundError("wiki 精炼模型未训练：%s" % data_dir)
        feat = features.Featurizer.from_json(vocab_path)
        net = rm.RefineNet.load(model_path)
        return cls(feat, net, data_dir)

    def _score(self, rows):
        """rows: [{"sec","text"}] → [(idx, know_prob, fun_prob, drop_prob)]"""
        if not rows:
            return []
        ids, struct = [], []
        n = len(rows)
        for i, r in enumerate(rows):
            i_, s = self.feat.encode(r, pos_ratio=(i + 0.5) / max(n, 1))
            ids.append(i_)
            struct.append(s)
        import numpy as np
        ids = np.asarray(ids, dtype="int32")
        struct = np.asarray(struct, dtype="float32")
        p = self.net.proba(ids, struct)
        return [(i, float(p[i, 0]), float(p[i, 1]), float(p[i, 2]))
                for i in range(n)]

    def _query_hit(self, row, terms):
        terms = [t for t in (terms or []) if t]
        if not terms:
            return False
        blob = (row.get("sec") or "") + " " + (row.get("text") or "")
        return any(t in blob for t in terms)

    def refine(self, rows, terms=None, know_limit=6, fun_limit=2, label=None):
        """打分拼装，返回注入文本（无内容返回 ""）。

        候选行若带 ``term``（来自 `wiki_kb.search_candidates(..., per_term=N)`），
        会**按实体分组**分别选句并给每块加 `【实体名】` 前缀。不做分组会出问题：
        多实体的句子被一起打分排序后**跨实体混排**（实测「猪」的段落后面
        直接接上「它们是皮革、生牛肉、牛排和奶桶的主要来源」——那句讲的是牛）。
        """
        rows = list(rows or [])
        if not rows:
            return ""
        # 按 term 切连续分组（search_candidates 逐词产出，天然同词连续）
        groups = []
        for r in rows:
            k = r.get("term")
            if groups and groups[-1][0] == k:
                groups[-1][1].append(r)
            else:
                groups.append((k, [r]))
        if len(groups) > 1:
            outs = []
            for k, sub in groups:
                t = self._pick_join(sub, [k] if k else terms,
                                    know_limit, fun_limit, label=k)
                if t:
                    outs.append(t)
            return "\n".join(outs)
        return self._pick_join(rows, terms, know_limit, fun_limit,
                               label=label or groups[0][0])

    def _pick_join(self, rows, terms=None, know_limit=6, fun_limit=2, label=None):
        """单组候选句 → 注入文本（可选带【实体名】前缀）。"""
        scored = self._score(rows)

        def boost(row):
            return QUERY_BOOST if self._query_hit(row, terms) else 0.0

        # 知识：know 概率 + 价值分层 + query 加成排序
        know = [s for s in scored if s[1] >= s[2]]
        know = sorted(
            [(i, p_k + _value(rows[i]) + boost(rows[i]))
             for i, p_k, p_f, p_d in know],
            key=lambda x: -x[1])
        # 趣味：fun 概率排序（排除明显是垃圾的）
        fun = sorted(
            [(i, p_f + boost(rows[i])) for i, p_k, p_f, p_d in scored
             if p_f > 0.5 and p_f > p_k],
            key=lambda x: -x[1])

        out_know, out_fun, used = [], [], set()
        budget = KNOW_BUDGET
        for i, _p in know:
            t = rows[i]["text"].strip()
            if not t or i in used or budget < MIN_PIECE:
                continue
            if _too_similar(t, out_know):
                continue
            # 放不下就跳过这条、给后面的短句机会 —— **不注入半句话**。
            # （P1-3 修的「整句丢弃」bug 指的是**什么都没选上**：那条路径由下面
            #   的兜底分支保证至少有一条内容，而不是靠切碎句子来填满预算。）
            if len(t) > budget:
                continue
            out_know.append(t)
            used.add(i)
            budget -= len(t)
            if len(out_know) >= know_limit:
                break
        if not out_know:
            # 兜底：第一条可用句子，**同样受预算约束**
            # （旧实现无上限，实测兜底输出比 L3 原文还长，与
            #  「减小主对话上下文」的立项目标相反）。
            for i, r in enumerate(rows):
                if i in used:
                    continue
                t = (r.get("text") or "").strip()
                if t:
                    out_know.append(cut_at_boundary(t, KNOW_BUDGET))
                    used.add(i)
                    break

        budget = FUN_BUDGET
        for i, _p in fun:
            t = rows[i]["text"].strip()
            if not t or i in used or budget < MIN_PIECE:
                continue
            if _too_similar(t, out_know) or _too_similar(t, out_fun):
                continue
            if len(t) > budget:
                continue
            out_fun.append(t)
            used.add(i)
            budget -= len(t)
            if len(out_fun) >= fun_limit:
                break

        parts = []
        if out_know:
            parts.append(("要点：" if label else "【要点】") + " ".join(out_know))
        if out_fun:
            parts.append(("趣闻：" if label else "【趣闻】") + " ".join(out_fun))
        body = "\n".join(parts)
        text = ("【%s】%s" % (label, body)) if (label and body) else body
        # 单块硬上限：任何情况下都不超过 `MAX_TOTAL`
        # （整条注入的总上限由调用方 `config.GAME_WIKI_MAX_CHARS` 把关）
        return cut_at_boundary(text, MAX_TOTAL)


# ---------------- 全局单例（懒加载） ----------------

_LOCK = threading.Lock()
_SHARED = None
_FAILED = False


def shared(data_dir=DATA_DIR):
    global _SHARED, _FAILED
    if _SHARED is not None or _FAILED:
        return _SHARED
    with _LOCK:
        if _SHARED is not None or _FAILED:
            return _SHARED
        try:
            _SHARED = RefineEngine.load(data_dir)
        except Exception:
            _FAILED = True
            _SHARED = None
    return _SHARED


def reset_shared():
    global _SHARED, _FAILED
    with _LOCK:
        _SHARED = None
        _FAILED = False


def refine(rows, terms=None):
    """便捷入口：返回注入文本；模型不可用返回 ""（调用方回退）。"""
    eng = shared()
    if eng is None or not rows:
        return ""
    try:
        return eng.refine(rows, terms=terms)
    except Exception:
        return ""
