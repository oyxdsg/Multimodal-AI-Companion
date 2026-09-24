"""规则重排：对 RRF 融合结果做轻量二次排序（零模型成本）。

打分规则（在 RRF 分数基础上加权）：
    base   = rrf_score
    +0.18  * 标题命中查询核心词（完整词，去掉"(方块)/(物品)"后缀后匹配）→ 最强信号
    +0.10  * 正文关键词命中率
    +0.03  * 章节名命中
    -0.05  * 正文完全不含查询词（纯语义命中的降权）

核心：MC Wiki 的"XX怎么合成"类查询，主页面标题几乎就是 XX，
标题匹配是提升 recall@1 的最强信号。
"""

import jieba

_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "么", "怎么", "如何", "为什么", "吗", "呢", "啊", "吧",
    "可以", "能", "需要", "什么", "多少", "哪个", "哪些", "请", "问", "告诉",
    "wiki", "minecraft", "我的世界", "mc",
}

# 查询意图词（问"怎么合成"时这些词不该当实体匹配）
_INTENT_WORDS = {
    "怎么", "如何", "什么", "哪里", "哪些", "为什么", "有什么用", "合成",
    "制作", "获得", "生成", "繁殖", "驯服", "种植", "使用", "激活", "召唤",
    "建造", "是什么", "在哪里", "有什么用", "怎么做", "怎么用",
}

_CACHE = {}


def query_keywords(query, top_n=10):
    """jieba 提取查询关键词，过滤停用词/单字/意图词。"""
    if query in _CACHE:
        return _CACHE[query]
    words = [w for w in jieba.cut(query) if w.strip()]
    kws = []
    seen = set()
    for w in words:
        w = w.strip().lower()
        if not w or w in _STOPWORDS or w in _INTENT_WORDS or w in seen:
            continue
        if len(w) == 1 and not w.isascii():
            continue
        seen.add(w)
        kws.append(w)
        if len(kws) >= top_n:
            break
    # 兜底：意图词过滤后为空时，保留最长词
    if not kws:
        cand = [w for w in words if len(w) > 1]
        if cand:
            kws = [max(cand, key=len)]
    _CACHE[query] = kws
    return kws


def _norm_title(title):
    """去掉标题的"(方块)"/"(物品)"/"（方块）"等后缀变体。"""
    t = title or ""
    for suf in ("（方块）", "(方块)", "（物品）", "(物品)", "（生物群系）", "(生物群系)"):
        if t.endswith(suf):
            t = t[: -len(suf)]
            break
    return t.lower()


def _norm_query(query):
    """查询归一化：去标点空白、小写。"""
    import re
    return re.sub(r"[\s\W_]+", "", (query or "").lower())


def _title_hit(title, keywords):
    """标题命中核心词数。"""
    t = _norm_title(title)
    return sum(1 for kw in keywords if kw in t)


def rerank(query, results, top_k=None):
    """对检索结果重排。results: list of {id, doc, meta, rrf_score|score}。

    标题评分（最强信号）：
      - entity_exact：标题（去后缀）是查询的子串 → 查询问的就是这个页面，强奖励，
        按标题长度占查询比例加权（避免"红石"这类短标题压过"红石中继器"）。
      - coverage：标题命中查询核心词的比例。
    """
    if not results:
        return []
    keywords = query_keywords(query)
    if not keywords:
        return results[:top_k] if top_k else results

    norm_q = _norm_query(query)
    scored = []
    for r in results:
        doc = (r.get("doc") or "").lower()
        meta = r.get("meta") or {}
        base = r.get("rrf_score", r.get("score", 0.0))

        norm_t = _norm_title(meta.get("title"))
        if norm_t and norm_q and norm_t in norm_q:
            entity_bonus = 0.30 * min(len(norm_t) / len(norm_q), 1.0)
        else:
            entity_bonus = 0.0
        coverage = _title_hit(meta.get("title"), keywords) / len(keywords)

        # 正文关键词命中率
        hit = sum(1 for kw in keywords if kw in doc)
        hit_rate = hit / len(keywords)

        score = base
        score += entity_bonus
        score += 0.15 * coverage
        score += 0.10 * hit_rate
        score += 0.03 * min(sum(1 for kw in keywords if kw in (meta.get("section") or "").lower()), 1.0)
        if hit_rate == 0.0 and coverage == 0.0:
            score -= 0.05

        scored.append((score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [r for _, r in scored]
    return ranked[:top_k] if top_k else ranked


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    kws = query_keywords("下界合金锭怎么合成")
    print("关键词:", kws)
    print("标题匹配:", _title_hit("下界合金锭（方块）", kws))
