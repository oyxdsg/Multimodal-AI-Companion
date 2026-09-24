# -*- coding: utf-8 -*-
"""Minecraft 中文 Wiki 本地知识库检索（SQLite，离线零延迟）。

数据由 wiki_build.py 生成（wiki_data/mcwiki.db）。
检索优先级：标题精确 > 重定向别名 > 标题包含 > 正文全文（FTS5 trigram）。
供游戏模式把相关知识注入 AI prompt，代替在线 wiki 查询。
"""

import os
import re
import sqlite3

import wi_config as config

# 插件目录 = plugins/wiki-local；桌面宠物根 = 上两级（db 仍在 <pet>/wiki_data/）
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PET_DIR = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
_DB = os.path.join(_PET_DIR, "wiki_data", "mcwiki.db")
_SNIPPET_LEN = 180
_TITLE_ILIKE = 5     # 标题模糊匹配候选条数
_HAS_CATEGORY = None  # pages 是否已分类（首次连接检测，兼容旧库）


def _has_category(conn):
    """pages 表是否带 category 列（分类后的库才做内容过滤）。"""
    global _HAS_CATEGORY
    if _HAS_CATEGORY is None:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pages)").fetchall()]
        _HAS_CATEGORY = "category" in cols
    return _HAS_CATEGORY


# 实体词索引（标题 + 重定向别名），按库文件 mtime 刷新。
# 之所以必须带上别名：MC 中文 Wiki 的**材质工具/装备没有独立页面**，
# 变体只作为重定向存在（铁镐 →「镐」、钻石剑 →「剑」、樱花木 →「木头」），
# 只按标题字面匹配会把玩家最常用的一批实体整批漏掉。
_INDEX_CACHE = {}          # include_rare(bool) -> (terms: set, max_len: int)
_INDEX_MTIME = None
_INDEX_FAILED = False
_MAX_TERM_LEN = 16         # n-gram 滑窗的词长上限（实体名不会更长，防止长标题拖慢）
_TERM_CHAR_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]")   # 至少含一个汉字或字母
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")          # 纯汉字判定


def _valid_term(t):
    """词是否值得进入提取词表。

    剔除纯数字/纯符号的标题与别名（实测别名里混着「5」这类，会让
    「[破坏] 钻石矿石 x5」多提取出一个 '5'）；单字只收汉字
    （「猪」「剑」要，『A』『5』不要）。
    """
    if not t:
        return False
    if len(t) >= 2:
        return bool(_TERM_CHAR_RE.search(t))
    return bool(_CJK_CHAR_RE.fullmatch(t))


def _term_index(include_rare=False):
    """返回 (common 词集合, rare 词集合, 最长词长)。带缓存，库文件变更后自动重建。

    别名只有在**目标页属于允许类别**时才收（与 `_search_one` 的别名口径一致），
    因此 version / tech / disambig 的重定向不会污染实体提取。
    include_rare=False 时 rare 集合为空。
    """
    global _INDEX_CACHE, _INDEX_MTIME, _INDEX_FAILED
    if _INDEX_FAILED:
        return set(), set(), 0
    try:
        mt = os.path.getmtime(_DB)
    except OSError:
        return set(), set(), 0
    if mt != _INDEX_MTIME:
        _INDEX_CACHE = {}
        _INDEX_MTIME = mt
    key = bool(include_rare)
    hit = _INDEX_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        conn = _connect()
        by_cat = {}
        for r in conn.execute(
                "SELECT title, category FROM pages "
                "WHERE category IN ('common','rare')"):
            cat = r["category"]
            if cat == "common" or include_rare:
                if _valid_term(r["title"]):
                    by_cat.setdefault(cat, set()).add(r["title"])
        for alias, cat in conn.execute(
                "SELECT r.alias, p.category FROM redirects r "
                "LEFT JOIN pages p ON p.title = r.title"):
            if cat in ("common", "rare") and (cat == "common" or include_rare):
                if _valid_term(alias):
                    by_cat.setdefault(cat, set()).add(alias)
        # 语义别名（config.WIKI_ALIASES）：玩家口语 → 页面标题，
        # wiki 重定向没覆盖的叫法也进提取词表（P2-3，不依赖重建库）。
        if config.WIKI_ALIASES:
            cat_of = dict(conn.execute("SELECT title, category FROM pages"))
            for alias, title in config.WIKI_ALIASES.items():
                cat = cat_of.get(title)
                if cat in ("common", "rare") and (cat == "common" or include_rare):
                    if _valid_term(alias):
                        by_cat.setdefault(cat, set()).add(alias)
        conn.close()
    except sqlite3.Error:
        _INDEX_FAILED = True
        return set(), set(), 0
    common = by_cat.get("common", set())
    rare = by_cat.get("rare", set()) if include_rare else set()
    terms = common | rare
    maxlen = min(max((len(t) for t in terms), default=0), _MAX_TERM_LEN)
    _INDEX_CACHE[key] = (common, rare, maxlen)
    return common, rare, maxlen


# 排除的通用动词/泛概念（不是具体实体，避免稀释检索重点）
_STOP_TERMS = {
    "移动", "伤害", "挖掘", "使用", "放置", "进入", "获得", "击杀", "攻击",
    "掉落", "生成", "死亡", "受伤", "拾取", "合成", "破坏", "建造", "驯服",
    "繁殖", "喂养", "骑乘", "玩家", "维度", "模式", "难度", "时间", "天气",
    "环境", "世界", "生物", "怪物", "村民", "实体", "方块", "物品", "工具",
}


# 事件上下文标记：单字实体（猪/剑/床/门…）必须附近有这些词才采信，
# 否则「水」「火」「铁」这类单字页会在闲聊与任意文本里乱命中。
_CTX_RE = re.compile(
    r"获得|得到|拾取|捡|击杀|杀死|打死|攻击|受伤|来自|破坏|挖掉|挖掘|放置|使用"
    r"|进度|成就|合成|烧炼|熔炼|驯服|繁殖|喂养|掉落|生成|死亡|跨维度|完成|交换"
    r"|[x×]\s*\d|\d+\s*[个只块组根张把条件双支]"
)
_MIN_TRUSTED_LEN = 2   # 达到该长度即直接采信；1 字词需通过上下文守卫


def _ctx_ok(text, i, ln):
    """单字（超短）词的上下文守卫。"""
    return bool(_CTX_RE.search(text[max(0, i - 6): i + ln + 5]))


def _match_in_text(text, terms, maxlen, limit):
    """从 text 里挑出词表命中的实体（纯函数，便于单测注入假词表）。

    规则见 `match_terms`：n-gram 滑窗 → 单字需上下文守卫 → 长子串优先去重。
    """
    if not text or not terms or maxlen <= 0:
        return []
    n = len(text)
    found = set()
    for i in range(n):
        for ln in range(1, min(maxlen, n - i) + 1):
            w = text[i:i + ln]
            if w in terms and (ln >= _MIN_TRUSTED_LEN or _ctx_ok(text, i, ln)):
                found.add(w)
    result = []
    for t in sorted(found, key=len, reverse=True):
        if t in _STOP_TERMS or any(t in r for r in result):
            continue
        result.append(t)
        if len(result) >= limit:
            break
    return result


def match_terms(text, limit=3, include_rare=False):
    """从文本中反向提取本地知识库存在的实体名（**标题 + 重定向别名**）。

    与旧实现的差别（旧版只对 common 标题做逐条 `t in text` 字面扫描）：

    * **带别名**：材质变体只是重定向（铁镐→镐、钻石剑→剑、樱花木→木头），
      只按标题匹配会把最常用的工具/装备整批漏掉，并把「钻石剑」降级成子串「钻石」；
    * **n-gram 滑窗查表**：复杂度 O(len(text) × 词长)，不再遍历全部标题；
    * **单字实体上下文守卫**：「猪」「剑」「床」这类单字页只有附近出现事件动词
      或数量标记（`[击杀] 猪 x1`）时才采信；
    * **长子串优先**：「钻石剑」命中后不再产出被它包含的「钻石」。

    include_rare=True（低频模式）同时收 rare 冷门词，避免漏掉 AI 训练语料未必
    覆盖的冷门实体。返回前 limit 个。
    """
    common, rare, maxlen = _term_index(include_rare)
    terms = common | rare if include_rare else common
    return _match_in_text(text or "", terms, maxlen, limit)


# 常见知识白名单（归一化集合）与低频过滤判定，见 config.WIKI_COMMON_TERMS


def db_path():
    return _DB


def db_exists():
    return os.path.isfile(_DB)


def _connect():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


# 常见知识白名单（归一化集合，见 config.WIKI_COMMON_TERMS）
_WIKI_COMMON_NORM = {_norm(t) for t in config.WIKI_COMMON_TERMS}


def is_common(term):
    """低频模式：term 是否为白名单中的「明确常见」实体（AI 铁定知晓）。

    用于过滤事件提取出的常见实体；白名单外的不过滤——
    common 类里的冷门/较新版本内容与 rare 冷门知识都会继续走 wiki 检索。
    """
    return _norm(term) in _WIKI_COMMON_NORM


# 低频模式「较新版本内容」判定：curated summary/facts 标注 1.xx+ 的条目，
# 或配置里显式声明的变体写法（WIKI_NEW_TERMS）——这些 AI 训练语料未必覆盖，
# 低频模式下应保留、照常查 wiki。
_VERSION_RE = re.compile(r"1\.\d{2}\+")

_NEW_NORM = None

# common / rare 类别的归一化集合（标题 + 重定向别名），低频过滤判定用。
_NORM_CACHE = None   # (mtime, common_norm, rare_norm)


def _category_norm_sets():
    """common / rare 类别（标题+别名）的归一化集合，带 mtime 缓存。"""
    global _NORM_CACHE
    try:
        mt = os.path.getmtime(_DB)
    except OSError:
        mt = None
    if _NORM_CACHE is not None and _NORM_CACHE[0] == mt:
        return _NORM_CACHE[1], _NORM_CACHE[2]
    common, rare, _ = _term_index(True)
    cn = {_norm(t) for t in common}
    rn = {_norm(t) for t in rare}
    _NORM_CACHE = (mt, cn, rn)
    return cn, rn


def _new_version_norm():
    """低频模式下应保留的「较新版本内容」归一化集合。

    来源 = curated 知识库中 summary/facts 标注 1.xx+ 的条目名/别名
          + config.WIKI_NEW_TERMS（变体写法与未来新内容的补充）。
    文件缺失 / 解析失败时静默回退到配置词表，不影响判定。
    """
    global _NEW_NORM
    if _NEW_NORM is not None:
        return _NEW_NORM
    s = set()
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "nlu", "wiki_refine", "data", "curated_all.jsonl")
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except ValueError:
                    continue
                blob = (d.get("summary") or "") + " " + " ".join(
                    (ft.get("text") or "")
                    for ft in (d.get("facts") or []) if isinstance(ft, dict))
                if _VERSION_RE.search(blob):
                    s.add(_norm(d.get("name") or ""))
                    for a in (d.get("aliases") or []):
                        if a:
                            s.add(_norm(a))
    except OSError:
        pass
    s.update(_norm(t) for t in getattr(config, "WIKI_NEW_TERMS", ()))
    _NEW_NORM = s
    return s


def is_worth_wiki(term):
    """低频模式：term 是否值得查 wiki 注入（True=保留，False=过滤）。

    过滤（False）：
    * 白名单 config.WIKI_COMMON_TERMS（AI 铁定知晓的核心常识）；
    * common 类基础实体（树苗/燧石/骨头…，AI 训练语料铁定覆盖）。
    保留（True）：
    * rare 类冷门知识（AI 语料未必覆盖）；
    * 较新版本内容（curated 标注 1.xx+ 或 config.WIKI_NEW_TERMS，如樱花木/铜灯/试炼密室）；
    * 不在任何类别词表（保守，宁可查也不错漏）。
    """
    nt = _norm(term)
    if not nt:
        return False
    if nt in _WIKI_COMMON_NORM:
        return False
    common_norm, rare_norm = _category_norm_sets()
    if nt in rare_norm:
        return True
    if nt in _new_version_norm():
        return True
    if nt in common_norm:
        return False
    return True


def _is_nav_page(text):
    """退化导航页（消歧义列表 / 重定向残留），正文短且无实质知识。"""
    t = (text or "").strip()
    return len(t) < 150 and ("可以指" in t or t.startswith("REDIRECT"))


# 掉落表/数据表在清理时被压成单行文本（"[数据表] 数量范围 / 掉落概率 …"），
# 可读性差且占篇幅，摘要提取时整段跳过
_NOISE_RE = re.compile(
    r"\[数据表\]"
    r"|数量范围\s*/\s*掉落概率\s*/\s*平均数量"
    r"|概率\s*/\s*平均掉落数量"
    r"|无抢夺"
)


def _is_noise_seg(seg):
    """小节片段是否主要为数据表噪音（含模板占位符或表头行）。"""
    return bool(_NOISE_RE.search(seg or ""))


def _snippet(text, n=_SNIPPET_LEN, anchor=None):
    """摘要：引言段 + 各关键小节标题与首句，拼接压缩到 n 字以内。

    文本已按「引言 + 有价值小节」清理，此处按小节提取要点，
    比硬取前 n 字的信息密度更高（生成/掉落/行为/变种各留一段）。
    提供 anchor 且正文命中时，优先截取该词附近的内容
    （如搜索进度名时直接给出对应进度词条，而非页面泛化引言）。
    """
    if not text:
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if anchor and anchor in text:
        m = re.search(r"(?<![0-9A-Za-z\u4e00-\u9fff])"
                      + re.escape(anchor)
                      + r"(?![0-9A-Za-z\u4e00-\u9fff])", text)
        idx = m.start() if m else text.find(anchor)
        start = max(0, idx - 40)
        seg = text[start:start + n].strip()
        if seg:
            # 剔除数据表噪音（掉落表被压成单行）：保留噪音出现前的干净内容
            m_noise = _NOISE_RE.search(seg)
            if m_noise:
                seg = seg[:m_noise.start()].strip()
            # 引言足够时只保留引言，去掉小节标题残影
            sec_at = seg.find("\n==")
            if sec_at > 0 and len(seg[:sec_at].strip()) >= 30:
                seg = seg[:sec_at].strip()
            if len(seg) >= 30:
                return seg
            # 截断后过短 → 落到下方按小节提取（已过滤噪音小节）
    sec_re = re.compile(r"^==\s*([^=]+?)\s*==$", re.M)
    first = sec_re.search(text)
    parts = []
    budget = n
    if first:
        intro = text[:first.start()].strip()
        if intro:
            parts.append(intro[:100])
            budget -= len(parts[-1])
        pos = first.end()
    else:
        pos = 0
    while budget > 12 and pos < len(text):
        m = sec_re.search(text, pos)
        if not m:
            rest = text[pos:].strip()
            if rest:
                parts.append(rest[:budget])
            break
        seg_start = m.end()
        nxt = sec_re.search(text, seg_start)
        seg = text[seg_start: nxt.start() if nxt else len(text)].strip()
        # 去掉小节内开头的多级子节标题行，再取首句
        seg = re.sub(r"^={2,}\s*[^=\n]*\s*=*\s*$", "", seg, flags=re.M).strip()
        # 数据表噪音（掉落表/概率表被压成单行）整体跳过，不占摘要篇幅
        if _is_noise_seg(seg):
            pos = nxt.start() if nxt else len(text)
            continue
        head = re.split(r"(?<=[。！？])", seg)[0].strip()[:60]
        piece = f"{m.group(1).strip()}：{head}".strip()
        if len(piece) > budget:
            parts.append(piece[:budget])
            break
        parts.append(piece)
        budget -= len(piece)
        pos = nxt.start() if nxt else len(text)
    out = " ".join(p for p in parts if p).strip()
    return out


def search(terms, limit=2):
    """按关键词列表检索，返回 [(标题, 摘要), ...]（按序去重，最多 limit 条）。

    terms: 字符串或字符串列表，依次尝试，前一个命中后继续补充直到凑满 limit。
    已分类的库按「常用优先、冷门兜底」两级检索（version/tech 被过滤）。
    数据库缺失或异常时返回 []（调用方应回退在线查询）。
    """
    if isinstance(terms, str):
        terms = [terms]
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not terms or not db_exists():
        return []
    try:
        conn = _connect()
    except sqlite3.Error:
        return []
    try:
        seen = set()
        results = []
        for term in terms:
            if len(results) >= limit:
                break
            # 常用知识优先
            for title, snippet in _search_one(conn, term,
                                              limit - len(results), "common"):
                if title and title not in seen:
                    seen.add(title)
                    results.append((title, snippet))
            # 冷门知识兜底（仅标题精确/别名，不用 FTS，避免带出正文沾边的版本进度页）
            if len(results) < limit:
                for title, snippet in _search_one(conn, term,
                                                  limit - len(results),
                                                  "rare", fuzzy=False):
                    if title and title not in seen:
                        seen.add(title)
                        results.append((title, snippet))
        return results[:limit]
    finally:
        conn.close()


def _coverage(title, term):
    """标题对查询词的覆盖度（0~1）：term 是标题子串时，term 占标题比例越高越聚焦。

    借鉴 mcwiki-agentic-rag `rerank.py` 的「实体精确匹配」思想——
    「下界合金锭怎么合成」命中「下界合金锭」应比「下界合金升级」排前。
    """
    nt = _norm(term)
    ntitle = _norm(title)
    if not nt or not ntitle:
        return 0.0
    if nt in ntitle:
        return min(1.0, len(nt) / len(ntitle))
    return 0.0


def _search_one(conn, term, limit, only=None, fuzzy=True):
    """单关键词检索：收集候选 → 打分排序 → 取 top-k（P2-1，借鉴 RAG rerank.py）。

    分数：标题精确 1.0 > 重定向别名 0.6 > 标题包含 0.3×覆盖率。
    不用正文 FTS 兜底：正文沾边会把无关页带出（如搜「怪物猎人」命中版本页）——
    正文路由 P2-2 作为独立一路经 RRF 融合，不在此处混入。
    only: 'common' / 'rare' / None。None 表示不过滤（旧库无 category 列）。
    fuzzy: False 时只做标题精确/别名（rare 兜底用）。
    已分类的库只检索指定类别，过滤版本历史/技术数据/消歧义页。

    与旧「硬短路」的差别（P2-1）：
    * 命中**实质页**（非退化导航页）仍直接返回，不补模糊候选
      ——避免「僵尸」带出「僵尸马」这类名字沾边但实质不同的页；
    * 标题包含候选从 `ORDER BY length(title)` 改为**按覆盖率打分排序**，
      相关度更高的候选（query 词占标题比例高）排前。
    """
    if limit <= 0:
        return []
    out = []
    nt = _norm(term)
    # 分类过滤
    if not _has_category(conn):
        cat = ""
    elif only:
        cat = f" AND category='{only}'"
    else:
        cat = " AND category IN ('common','rare')"

    scored = []  # (score, title, snippet)

    # 1) 标题精确（大小写/空白归一）——命中实质页直接返回
    row = conn.execute(
        "SELECT title, text FROM pages WHERE lower(replace(title,' ',''))=?"
        + cat,
        (nt,)).fetchone()
    if row:
        if not _is_nav_page(row["text"]):
            return [(row["title"], _snippet(row["text"], anchor=term))]
        scored.append((1.0, row["title"], _snippet(row["text"], anchor=term)))

    # 2) 重定向别名
    row = conn.execute(
        "SELECT p.title, p.text FROM redirects r "
        "JOIN pages p ON r.title = p.title "
        "WHERE lower(replace(r.alias,' ',''))=?"
        + cat.replace("category", "p.category"),
        (nt,)).fetchone()
    if row and not any(t == row["title"] for _s, t, _sn in scored):
        scored.append((0.6, row["title"], _snippet(row["text"], anchor=term)))

    # 2b) 语义别名（config.WIKI_ALIASES，玩家口语 → 页面标题）
    target = None
    for alias, title in config.WIKI_ALIASES.items():
        if _norm(alias) == nt:
            target = title
            break
    if target and not any(t == target for _s, t, _sn in scored):
        row = conn.execute(
            "SELECT title, text FROM pages WHERE title=?" + cat,
            (target,)).fetchone()
        if row:
            scored.append((0.6, row["title"], _snippet(row["text"], anchor=term)))

    # 3) 标题包含（fuzzy 才做；按覆盖率打分，避免把沾边页排前）
    if fuzzy:
        excluded = [t for _s, t, _sn in scored]
        sql = "SELECT title, text FROM pages WHERE title LIKE ?" + cat
        params = [f"%{term}%"]
        if excluded:
            sql += " AND title NOT IN (%s)" % ",".join("?" * len(excluded))
            params += excluded
        sql += " ORDER BY length(title) LIMIT ?"
        params.append(_TITLE_ILIKE * 6)
        for row in conn.execute(sql, params):
            cov = _coverage(row["title"], term)
            if cov > 0:
                scored.append((0.3 * cov, row["title"], _snippet(row["text"])))

    # 打分排序取 top-limit（去重）
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    seen = set()
    for _s, t, sn in scored:
        if t in seen:
            continue
        seen.add(t)
        out.append((t, sn))
        if len(out) >= limit:
            break
    return out


def search_text(terms, limit=2):
    """返回「标题：摘要；标题：摘要」拼接文本（与在线 mc.wiki_search 格式兼容）。"""
    return "；".join(f"{t}：{s}" for t, s in search(terms, limit))


def search_candidates(terms, limit=2, per_term=None):
    """检索返回候选句列表 `[{"sec", "text"[,"term","title"]}]`，供本地精炼模型挑选。

    与 search_text 互补：search_text 给「规则摘要」（信息已压缩），
    这里给「页面原始候选句」+ 小节链，交给 nlu.wiki_refine 的打分模型选句。

    :param per_term: **每个词各自取几个页面**。
        * ``None``（默认，旧行为）：整批共享 ``limit`` 页 —— 注意 `search()` 一到
          limit 就停，所以多词查询只会覆盖**头 1~2 个实体**，其余实体完全没有候选。
        * 传数字（精炼路径用 1）：逐词各取 N 页，并给每行打上 ``term`` / ``title``，
          让上游能按实体分组、避免跨实体混排；**同一页面不会被两个词重复取用**。
    """
    from wiki_refine import segment
    if isinstance(terms, str):
        terms = [terms]
    terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    out = []
    try:
        conn = _connect()
    except sqlite3.Error:
        return out

    def _collect(title, term):
        row = conn.execute(
            "SELECT text FROM pages WHERE title=?", (title,)).fetchone()
        if not row:
            return
        for c in segment.page_candidates(title, row["text"]):
            if term is not None:
                c["term"] = term
                c["title"] = title
            out.append(c)

    try:
        if per_term is None:
            for title, _snip in search(terms, limit):
                _collect(title, None)
        else:
            used_pages = set()
            for t in terms:
                for title, _snip in search([t], per_term):
                    if title in used_pages:
                        continue
                    used_pages.add(title)
                    _collect(title, t)
    finally:
        conn.close()
    return out