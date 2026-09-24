# -*- coding: utf-8 -*-
"""三分类弱标签：知识 / 趣味 / 丢弃（启发式规则，先跑通再靠大模型精选校准）。

标签口径（与精炼目标一致）
-----------------------------
* ``know``   知识：引言、配方、获取、关键行为/用途 —— 回答「是什么/怎么用」
* ``fun``    趣味：彩蛋、周边引用、冷知识 —— 给对话增加乐趣
* ``drop``   丢弃：数据表噪音、模板/公式残骸、碎片句
"""
import re

from wiki_refine import segment

# 趣味（彩蛋/周边/冷知识）特征
_FUN_RE = re.compile(
    r"在游戏《|游戏《|彩蛋|致敬|灵感来源|文化参考|集合名词|Notch"
    r"|Dinnerbone|Grumm|顽皮熊|以撒|章鱼奶爸|枪战世界|网红之路|恐慌天堂")

# 趣闻专题页（标题特征）：这类页面以彩蛋/历史/人物/周边为主，
# 整页句子按「趣闻」放宽，避免被判成普通知识
_FUN_PAGE_RE = re.compile(
    r"彩蛋|愚人节|闪烁标语|大事记|已移除|Herobrine|Notch|Markus|周边"
    r"|文化参考|致敬|灵感|幕后|轶事|你知道吗|人物|传记")

# 知识特征（稳定正例）
_KNOW_RE = re.compile(
    r"合成配方|可合成|制作|用于|是一种|属于|掉落|生成于|生成在"
    r"|可以通过|可用作|会减少|会恢复|最大等级|耐久|挖掘速度"
    r"|可以|能够|需要|当.{0,6}时|由.{0,10}构成|由.{0,10}组成|玩家")

# 配方/数据行：含 → 的配方、含 ×/数字 的材料行
_RECIPE_RE = re.compile(
    r"合成配方|→|×\s*\d|\d\s*\+\s*\d")


def label_sec(chain):
    """小节链 → (是否为趣味小节, 是否为知识小节)。"""
    fun_sec = any(re.search(r"彩蛋|你知道吗|画廊|轶事|冷知识", s) for s in chain)
    know_sec = any(re.search(
        r"合成|获取|掉落|行为|用途|属性|生成|使用|燃料|修复", s) for s in chain)
    return fun_sec, know_sec


def label(cand, title=""):
    """对单个候选句打弱标签，返回 ``know`` / ``fun`` / ``drop``。

    :param cand: {"sec": "小节链/", "text": 句子}
    :param title: 页面标题；趣闻专题页的句子按趣闻放宽
    """
    text = cand.get("text") or ""
    if segment.is_junk(text):
        return "drop"
    chain = (cand.get("sec") or "").split("/")
    fun_sec, know_sec = label_sec(chain)
    if _FUN_RE.search(text):
        return "fun"
    # 趣闻专题页：非垃圾、非配方/强知识行的句子视为趣闻（对话素材）
    if title and _FUN_PAGE_RE.search(title) and not _RECIPE_RE.search(text):
        return "fun"
    # 引言段（页面第一段）默认是知识句
    if chain == ["引言"]:
        return "know"
    if _RECIPE_RE.search(text) or _KNOW_RE.search(text) or know_sec:
        return "know"
    # 非知识小节的普通句（如「画廊/历史」）默认丢弃，控制噪音
    return "drop"


def tag_rows(rows):
    """给候选句列表打标签，返回 (带 label 的 rows, 计数)。"""
    from collections import Counter
    cnt = Counter()
    out = []
    for c in rows:
        lb = label(c)
        cnt[lb] += 1
        c = dict(c)
        c["label"] = lb
        out.append(c)
    return out, cnt
