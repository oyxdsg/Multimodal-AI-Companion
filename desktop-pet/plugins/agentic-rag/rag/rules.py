"""规则层（零模型）：判断是否需要检索、推断页面类型、元数据过滤条件。

设计原则：规则能覆盖的场景绝不调模型；规则未命中返回 None，交给上层小模型。

page_type 分类（供元数据过滤检索使用）：
    item/block/mob/biome/recipe/version/command/guide/other
"""

import re

# ── 是否需要检索 ──

_NO_RETRIEVAL_PATTERNS = [
    r"^(你好|hi|hello|谢谢|再见|你好呀)",
    r"^(你是谁|你能做什么|你有什么功能)",
    r"^.{0,4}$",  # 过短无意义输入
]

_MC_KEYWORDS = [
    "合成", "配方", "方块", "生物", "怪物", "物品", "附魔", "红石",
    "矿石", "矿", "生物群系", "群系", "地形", "维度", "下界", "末地",
    "僵尸", "骷髅", "苦力怕", "末影人", "村民", "铁傀儡", "苦力", "药水",
    "剑", "镐", "斧", "锹", "锄", "锭", "盔甲", "装备", "工具", "食物",
    "怎么", "如何", "哪里", "多少", "是什么", "怎么做", "有什么用",
]


def rule_need_retrieval(query):
    """判断是否需要检索。返回 True/False/None（None 表示规则未命中，交小模型）。"""
    q = (query or "").strip()
    for p in _NO_RETRIEVAL_PATTERNS[:-1]:
        if re.match(p, q):
            return False
    if any(kw in q for kw in _MC_KEYWORDS):
        return True
    if re.match(r"^.{0,4}$", q):  # 过短且无 MC 关键词 → 无意义
        return False
    return None


# ── page_type 推断 ──

_PAGE_TYPE_RULES = [
    ("command", ["命令_", "命令", "/give", "指令"]),
    ("biome", ["生物群系", "群系", "地形"]),
    ("version", ["Java版", "基岩版", "携带版", "教育版", "原主机版", "快照", "预览版", "版1."]),
    ("guide", ["指南", "教程", "入门", "进阶", "更新指南"]),
    ("mob", ["僵尸", "骷髅", "苦力怕", "末影人", "村民", "铁傀儡", "雪傀儡", "猪", "牛", "羊",
              "鸡", "马", "狼", "猫", "蝙蝠", "鱿鱼", "溺尸", "尸壳", "幻翼", "疣猪兽", "猪灵",
              "末影龙", "凋灵", "循声守卫", "嗅探兽", "骆驼"]),
    ("block", ["方块", "矿石", "石头", "泥土", "原木", "木板", "玻璃", "砖", "门", "梯子",
                "活塞", "红石", "铁轨", "箱子", "熔炉", "工作台", "床", "灯", "栅栏", "台阶"]),
    ("item", ["剑", "镐", "斧", "锹", "锄", "锭", "盔甲", "药水", "食物", "箭", "弓", "鱼竿",
              "书", "唱片", "烟花", "附魔", "鞘翅", "盾牌", "苹果", "面包", "蛋糕", "锅", "桶"]),
    ("recipe", ["合成", "配方", "烧炼", "冶炼", "锻造", "染色", "酿造"]),
]

_PAGE_TYPE_PATTERNS = [(pt, re.compile(kw)) for pt, kws in _PAGE_TYPE_RULES for kw in kws]


def rule_infer_page_type(title_or_query):
    """从标题或查询推断 page_type。返回类型字符串或 None。"""
    text = title_or_query or ""
    for ptype, pat in _PAGE_TYPE_PATTERNS:
        if pat.search(text):
            return ptype
    return None


def page_type_for_chunk(title):
    """给 chunk 标题打 page_type 标签（建索引用）。"""
    return rule_infer_page_type(title) or "other"


# ── 元数据过滤条件 ──

def rule_infer_filters(query):
    """从查询推断元数据过滤条件，返回 {"page_type": str} 或 None。

    只有查询明确提到某类实体时才启用过滤（避免过度限制召回）。
    """
    ptype = rule_infer_page_type(query)
    if ptype in ("mob", "block", "biome"):
        return {"page_type": ptype}
    return None


# ── NLU 小模型兜底（规则未命中时调用，<200KB，纯 numpy）──

def _nlu():
    from .nlu import infer_page_type, need_retrieval
    return infer_page_type, need_retrieval


def nlu_need_retrieval(query, thr=0.7):
    """规则未命中时用 NLU 模型判断。返回 True/False。"""
    try:
        infer_pt, need = _nlu()
        label, prob = need(query, thr=thr)
        return label == "need"
    except Exception:
        return True  # 模型不可用时保守假设需要检索


def nlu_page_type(query, thr=0.55):
    """规则未命中时用 NLU 模型推断 page_type。返回 str 或 None。"""
    try:
        infer_pt, need = _nlu()
        return infer_pt(query, thr=thr)
    except Exception:
        return None


def smart_need_retrieval(query):
    """组合决策：规则优先，未命中走 NLU。返回 True/False。"""
    r = rule_need_retrieval(query)
    if r is not None:
        return r
    return nlu_need_retrieval(query)


def smart_page_type(query):
    """组合决策：规则优先，未命中走 NLU。返回 str 或 None。"""
    r = rule_infer_page_type(query)
    if r is not None and r != "other":
        return r
    return nlu_page_type(query)


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    for q in ["你好", "僵尸在哪里生成", "下界合金锭怎么合成", "红石中继器怎么用", "你是谁"]:
        print(f"{q!r}: need={rule_need_retrieval(q)}, page_type={rule_infer_page_type(q)}, filters={rule_infer_filters(q)}")
