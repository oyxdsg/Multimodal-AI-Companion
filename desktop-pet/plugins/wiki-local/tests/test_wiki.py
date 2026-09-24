# -*- coding: utf-8 -*-
"""Wiki 链路回归测试（无框架，纯 Python 断言，直接 python tests/test_wiki.py 运行）。

守卫 2026-09-16 的 Wiki 优化（P0–P2），对应 `WIKI_AUDIT.md` 里的问题清单：

1. **P0-1 标题↔别名口径**：`wiki_kb.match_terms` 必须带重定向别名
   （铁镐→镐 / 钻石剑→剑 / 樱花木→木头），否则最常用的工具装备拿不到；
   单字实体（猪/剑/床）需要事件上下文守卫，闲聊里的「水」「火」不能命中；
   长子串优先（「钻石剑」命中时不再产出「钻石」）。
2. **P0-2 curated 不抢答**：没有对应条目时返回 None，不许拿「包含该词的
   另一个条目」顶答（曾把「樱花木」答成【樱花木板】、「嗅探兽」答成进度页）。
   同名条目要让实体胜出、且必须可达。
3. **P1-3 精炼预算**：超预算的句子要**截断**而不是整句丢弃；空要点的兜底
   必须受长度上限约束；高度相似的句子去重。
4. **P1-1/P1-2 分类规则**：版本族页归 version、技术页归 tech、音乐/开发方/
   平台版本页归 rare，真游戏实体保持 common。

依赖：curated / wiki_kb / wiki_build 三个测试不需要 numpy 与数据库；
只有精炼预算测试需要 numpy（缺失时跳过）。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.dirname(BASE)))  # desktop-pet
sys.stdout.reconfigure(encoding="utf-8")


# ---------- 1. match_terms：别名 / 单字守卫 / 长子串优先 ----------

def test_match_terms_core():
    from wiki_kb import _match_in_text

    # 词表模拟「标题 + 别名」混合：铁镐/钻石剑 是别名，猪是单字标题
    terms = {"铁镐", "钻石剑", "钻石", "猪", "水", "水桶", "橡木原木", "原木"}
    maxlen = max(len(t) for t in terms)

    # 别名命中（旧实现只认标题 → 这里会全空）
    assert _match_in_text("[获得] 铁镐 x1", terms, maxlen, 3) == ["铁镐"]
    assert _match_in_text("[获得] 橡木原木 x3", terms, maxlen, 3) == ["橡木原木"]

    # 长子串优先：命中「钻石剑」后不再产出子串「钻石」
    got = _match_in_text("[获得] 钻石剑 x1", terms, maxlen, 3)
    assert got == ["钻石剑"], got

    # 单字实体：事件上下文里认，闲聊里不认
    assert _match_in_text("[击杀] 猪 x1", terms, maxlen, 3) == ["猪"]
    assert _match_in_text("我去水边看看风景", terms, maxlen, 3) == [], "闲聊不该命中单字实体"
    assert _match_in_text("今天天气怎么样呀", terms, maxlen, 3) == []

    # 双字起直接采信，且被更长词包含时去重
    assert _match_in_text("水桶 x1", terms, maxlen, 3) == ["水桶"]

    # limit 生效
    many = _match_in_text("[获得] 铁镐 钻石剑 水桶", terms, maxlen, 2)
    assert len(many) == 2, many
    print("[ok] match_terms（别名 / 单字守卫 / 长子串优先）")


def test_match_terms_real_db():
    """真库（存在就跑）：验证 README 招牌场景与真实事件行。"""
    import wiki_kb as kb
    if not kb.db_exists():
        print("[skip] match_terms 真库用例（wiki_data/mcwiki.db 不存在）")
        return
    cases = {
        "[获得] 铁镐 x1": "铁镐",
        "[获得] 木镐 x1": "木镐",
        "[获得] 钻石剑 x1": "钻石剑",
        "[获得] 樱花木 x8": "樱花木",
        "[击杀] 猪 x1": "猪",
        "[破坏] 钻石矿石 x5": "钻石矿石",
    }
    for line, want in cases.items():
        got = kb.match_terms(line)
        assert want in got, "%s → %s（期望含 %s）" % (line, got, want)
    # 闲聊零误触（含单字实体词）
    for line in ("今天天气怎么样呀", "我去水边看看风景", "你在干嘛呢"):
        assert kb.match_terms(line) == [], "%s → %s" % (line, kb.match_terms(line))
    print("[ok] match_terms 真库用例")


# ---------- 2. curated：精确匹配，不抢答 ----------

def _idx(rows):
    from wiki_refine.curated import CuratedIndex
    return CuratedIndex(rows)


def _row(rid, name, cat, summary="s"):
    return {"id": rid, "name": name, "category": cat,
            "summary": summary, "facts": [], "fun": ""}


def test_curated_no_steal():
    idx = _idx([
        _row("cherry_planks", "樱花木板", "方块/建筑"),
        _row("little_sniffer", "小小嗅探兽", "进度"),
        _row("sniffer", "嗅探兽", "生物/友好"),
        _row("acacia_planks", "金合欢木板", "方块/建筑"),
        _row("planks", "木板", "方块/材料"),
        _row("iron_pickaxe", "铁镐", "工具/采集"),
    ])
    # 精确命中照常工作
    assert idx.find(["铁镐"])["id"] == "iron_pickaxe"
    assert idx.find(["木板"])["id"] == "planks"
    assert idx.find(["嗅探兽"])["id"] == "sniffer"
    # 没有对应条目的实体 → None（旧实现会抢答成 樱花木板 / 金合欢木板）
    assert idx.find(["樱花木"]) is None, "樱花木 不该被别条顶答"
    assert idx.find(["樱花树"]) is None
    # 非实体条目按精确名仍可达（只是不再抢答实体查询）
    assert idx.find(["小小嗅探兽"])["category"] == "进度"
    print("[ok] curated 精确匹配（不抢答）")


def test_curated_dedupe_and_entity_priority():
    # 同名 + 同 id → 完全重复，只留一条
    dup = _row("deep_dark", "深暗之域", "生物群系", "first")
    rows = [dup, dict(dup), _row("deep_dark2", "深暗之域", "生物群系", "second")]
    idx = _idx(rows)
    assert len(idx.rows) == 2, "完全重复的条目应被去掉：%d" % len(idx.rows)
    assert idx.find(["深暗之域"])["summary"] == "first"

    # 同名但实体/进度并存 → 实体优先（旧的按文件顺序会被进度条目覆盖）
    idx2 = _idx([
        _row("adv_monster", "怪物猎人", "进度"),
        _row("mob_hunter", "怪物猎人", "生物/敌对"),
    ])
    assert idx2.find(["怪物猎人"])["id"] == "mob_hunter", "同名时实体条目应胜出"
    print("[ok] curated 去重 + 实体优先")


def test_curated_real_db():
    from wiki_refine import curated as cu
    if cu.shared() is None:
        print("[skip] curated 真库用例（curated_all.jsonl 缺失）")
        return
    # 高价值条目仍在（别名口径打通后这些词才真的会被喂进来）
    assert cu.prompt(["铁镐"]).startswith("【铁镐】")
    assert cu.prompt(["钻石剑"]).startswith("【钻石剑】")
    # 未收录实体不再被顶答
    assert cu.prompt(["樱花木"]) == "", cu.prompt(["樱花木"])[:40]
    assert cu.prompt(["嗅探兽"]) == "", cu.prompt(["嗅探兽"])[:40]
    print("[ok] curated 真库用例")


# ---------- 3. 精炼预算与去重 ----------

class _FakeFeat:
    """把任意句子编码成固定特征（只关心长度预算逻辑）。"""
    def encode(self, row, pos_ratio=0.5):
        return [0], [0.0]


class _FakeNet:
    def __init__(self, know=0.9, fun=0.05):
        self.know = know
        self.fun = fun

    def proba(self, ids, struct):
        import numpy as np
        n = len(ids)
        p = np.zeros((n, 3), dtype="float32")
        p[:, 0] = self.know
        p[:, 1] = self.fun
        p[:, 2] = max(0.0, 1.0 - self.know - self.fun)
        return p


def test_refine_budget():
    try:
        import numpy  # noqa: F401
    except ImportError:
        print("[skip] 精炼预算（缺 numpy，需系统 Python）")
        return
    from wiki_refine import refine as rf
    import wiki_kb

    # 不变量：精炼单块的硬上限不得超过 L3 原文兜底的单页上限
    assert rf.MAX_TOTAL <= wiki_kb._SNIPPET_LEN, (
        "MAX_TOTAL=%d 应 <= _SNIPPET_LEN=%d，否则「精炼」会比原文更长"
        % (rf.MAX_TOTAL, wiki_kb._SNIPPET_LEN))

    long_sent = "僵尸" + "很长的说明文字" * 30          # 远超声预算，且无句读
    assert len(long_sent) > rf.KNOW_BUDGET

    # 放不下的长句应被**跳过**（给后面能放下的短句机会），
    # 而不是切碎了塞进去 —— 修的是「填满剩余额度会切出半句话」。
    eng = rf.RefineEngine(_FakeFeat(), _FakeNet())
    out = eng.refine([
        {"sec": "引言", "text": long_sent},
        {"sec": "行为", "text": "该区域有13张贴纸。"},
        {"sec": "行为", "text": "该区域有12张贴纸。"},
    ], terms=["僵尸"])
    assert long_sent not in out, "超预算的长句不该被硬塞"
    assert "13张贴纸" in out, out
    assert len(out) <= rf.MAX_TOTAL, len(out)
    # 相似句去重：「13张贴纸」与「12张贴纸」只留一条
    assert not ("13张贴纸" in out and "12张贴纸" in out), out

    # 全部句子都放不下 → 走兜底分支，且**受预算上限约束**
    # （旧实现直接塞 rows[0] 首句、无长度上限，实测比 L3 原文还长）
    eng2 = rf.RefineEngine(_FakeFeat(), _FakeNet(know=0.1, fun=0.9))
    out2 = eng2.refine([{"sec": "引言", "text": long_sent}], terms=["僵尸"])
    assert out2, "兜底不该为空"
    assert len(out2) <= rf.MAX_TOTAL, len(out2)
    assert long_sent[:rf.KNOW_BUDGET] in out2, out2[:80]
    assert long_sent[:rf.KNOW_BUDGET + 20] not in out2, "兜底应截到预算内"

    # 空输入 → 空输出（调用方才会走原文兜底）
    assert eng.refine([]) == ""
    print("[ok] 精炼预算：跳过放不下的长句 + 相似去重 + 兜底上限")


# ---------- 4. 分类规则 ----------

def test_classify_title():
    from wiki_build import classify_title

    version = [
        ("Java版Alpha v1.0.1", "Alpha v1.0.1是Java版的一次秘密更新，加入了红石。"),
        ("Java版1.21", "1.21 是…"),
        ("24w14a", "快照…"),
        ("Java版指南/1.1版本", "这篇指南是Java版1.1所有更改的简要概览。"),
        ("Boss更新", "Boss更新是携带版Alpha的一次主要更新。"),
        ("2026年第3次小更新", "是一次即将到来的小更新。"),
        ("Gear VR版", "是为虚拟现实设计的基岩版的前称。"),
        ("Wii U版", "是由4J Studios为Mojang开发，适配于Wii U的Minecraft原主机版。"),
    ]
    for t, body in version:
        assert classify_title(t, body) == "version", (t, classify_title(t, body))

    tech = [
        ("Java版数据值/方块ID", "本页面列出方块ID。"),
        ("NBT格式", "NBT是一种用带名称的二进制标签表示的树状数据结构。"),
        ("Molang", "Molang是一种基于表达式的简单语言。"),
    ]
    for t, body in tech:
        assert classify_title(t, body) == "tech", (t, classify_title(t, body))

    rare = [
        ("A Familiar Room", "A Familiar Room是一首由Aaron Cherof创作的音乐。"),
        ("Sweden", "Sweden是一首由C418创作的音乐。"),
        ("4J Studios", "4J Studios是一个独立游戏开发工作室。"),
        ("Aaron Cherof", "谢洛夫是一名音乐制作人。"),
    ]
    for t, body in rare:
        assert classify_title(t, body) == "rare", (t, classify_title(t, body))

    common = [
        ("铁镐", "铁镐是中期主力挖掘工具。"),
        ("僵尸", "僵尸是最常见的亡灵敌对生物。"),
        ("钻石矿石", "钻石矿石是…"),
        ("苦力怕", "苦力怕会自爆。"),
        ("音乐唱片", "音乐唱片是能够放入唱片机播放的物品。"),
        ("试炼密室", "试炼密室是地下结构。"),
    ]
    for t, body in common:
        assert classify_title(t, body) == "common", (t, classify_title(t, body))
    print("[ok] 分类规则（版本族/技术/非实体 → 过滤类，真实体保 common）")


# ---------- 5. 三层链路接线：逐实体分组 + 全局注入预算 ----------

class _Store(dict):
    def value(self, k, d=None):
        return self.get(k, d)


def test_refine_per_entity_grouping():
    """L2 逐词取候选并按实体分组：不再只覆盖头 1~2 个实体、不再跨实体混排。"""
    try:
        import numpy  # noqa: F401
    except ImportError:
        print("[skip] 逐实体分组（缺 numpy，需系统 Python）")
        return
    import wiki_kb as kb
    from wiki_refine import refine as rf
    if not kb.db_exists():
        print("[skip] 逐实体分组（wiki_data/mcwiki.db 不存在）")
        return

    # 逐词路径：每行都带 term，且两个实体都拿到候选
    cands = kb.search_candidates(["猪", "牛"], 1, per_term=1)
    assert cands, "应取到候选句"
    assert all("term" in c for c in cands), "逐词路径必须给候选行打 term"
    assert {c["term"] for c in cands} == {"猪", "牛"}, {c["term"] for c in cands}

    out = rf.refine(cands, terms=["猪", "牛"])
    assert "【猪】" in out and "【牛】" in out, out[:200]
    # 关键回归：「猪」的块里不能出现牛的内容（旧实现跨实体混排）
    pig_block = out.split("【牛】")[0]
    assert "皮革、生牛肉" not in pig_block, "「猪」的块里混进了牛的内容：%s" % pig_block

    # 旧行为（整批共享页数）：不带 term，且受 limit 限制只覆盖 2 页
    old = kb.search_candidates(["猪", "牛", "木板", "樱花木"], 2)
    assert all("term" not in c for c in old), "per_term=None 应保持旧行为（不打 term）"
    pages = {c.get("title") for c in cands}
    assert len(pages) == 2, pages
    print("[ok] L2 逐实体分组（不跨实体混排）")


def test_knowledge_injection_budget():
    """整条 {wiki} 注入受 GAME_WIKI_MAX_CHARS 约束（现由 wiki-local 插件负责）。"""
    import config
    from wiki_refine import refine as rf
    from wiki_refine import curated as cu
    from wiki_local import WikiLocal

    # 单块上限必须 <= 总额上限，且总额上限至少容得下一块
    assert rf.MAX_TOTAL <= config.GAME_WIKI_MAX_CHARS, (
        "单块 %d > 总额 %d" % (rf.MAX_TOTAL, config.GAME_WIKI_MAX_CHARS))

    if cu.shared() is None:
        print("[skip] 注入预算（curated 缺失）")
        return

    w = WikiLocal()
    w._engine = lambda: "local"          # 固定 local 档（不依赖 QSettings）
    terms = ["钻石镐", "铁傀儡", "僵尸", "钻石剑", "铁锭", "工作台",
             "铁块", "铜灯", "试炼密室", "僵尸村民", "末影人", "苦力怕"]
    text, hit = w._lookup(terms, config.GAME_WIKI_MAX_CHARS)
    assert isinstance(text, str) and isinstance(hit, list), (type(text), type(hit))
    assert len(text) <= config.GAME_WIKI_MAX_CHARS, (
        "注入 %d 字，超过预算 %d" % (len(text), config.GAME_WIKI_MAX_CHARS))
    assert text, "这批词都该命中 curated"
    # 记账按「是否产出过内容」算（被预算挤掉的词不再重复检索）
    assert set(hit) == set(terms), (sorted(hit), sorted(terms))

    # 单条术语也要正常（不该被预算逻辑吃掉）
    one, hit1 = w._lookup(["铁镐"], config.GAME_WIKI_MAX_CHARS)
    assert one.startswith("【铁镐】") and hit1 == ["铁镐"], (one[:30], hit1)

    # 空输入
    assert w._lookup([], config.GAME_WIKI_MAX_CHARS) == ("", [])
    print("[ok] 注入总预算 + 记账语义")


def test_lowfreq_worth_wiki():
    """低频模式过滤判定 is_worth_wiki：过滤常见，保留冷门/较新版本内容。

    纯逻辑层（不依赖真库）：
    * 白名单常见实体 → 过滤
    * rare 冷门 / 较新版本内容（curated 1.xx+ 或 wi_config.WIKI_NEW_TERMS）→ 保留
    * common 基础实体（不在白名单）→ 过滤
    * 未知词 → 保守保留
    """
    import config
    import wiki_kb as kb

    # 用注入假词表覆盖 _category_norm_sets / _new_version_norm，测纯判定逻辑
    orig_cat = kb._category_norm_sets
    orig_new = kb._new_version_norm
    try:
        kb._category_norm_sets = lambda: (
            {"树苗", "燧石", "石头", "圆石", "钻石"},   # common
            {"怪物猎人"},)                              # rare
        kb._new_version_norm = lambda: {"樱花木", "铜灯", "试炼密室"}

        # common 基础实体（AI 铁定知晓）→ 过滤
        assert kb.is_worth_wiki("树苗") is False, "树苗是 common 基础实体，应过滤"
        assert kb.is_worth_wiki("燧石") is False
        # 白名单 → 过滤（优先级高于 common）
        assert kb.is_worth_wiki("钻石") is False, "钻石在白名单，应过滤"
        assert kb.is_worth_wiki("石头") is False
        # rare 冷门 → 保留
        assert kb.is_worth_wiki("怪物猎人") is True, "rare 冷门应保留"
        # 较新版本内容 → 保留
        assert kb.is_worth_wiki("樱花木") is True, "较新版本内容应保留"
        assert kb.is_worth_wiki("铜灯") is True
        # 未知词 → 保守保留
        assert kb.is_worth_wiki("某个未来新物品") is True, "未知词应保守保留"
    finally:
        kb._category_norm_sets = orig_cat
        kb._new_version_norm = orig_new

    if kb.db_exists():
        # 真库兜底断言：基础常见实体过滤、冷门/新内容保留（保证词表接线正确）
        assert kb.is_worth_wiki("树苗") is False, "真库：树苗应被低频过滤"
        assert kb.is_worth_wiki("燧石") is False, "真库：燧石应被低频过滤"
        assert kb.is_worth_wiki("樱花木") is True, "真库：樱花木（新内容变体）应保留"
        assert kb.is_worth_wiki("嗅探兽") is True, "真库：嗅探兽应保留"
        assert kb.is_worth_wiki("钻石") is False, "真库：钻石（白名单）应过滤"
        assert kb.is_worth_wiki("怪物猎人") is True, "真库：rare 冷门应保留"
    print("[ok] 低频过滤判定 is_worth_wiki")


if __name__ == "__main__":
    test_match_terms_core()
    test_match_terms_real_db()
    test_curated_no_steal()
    test_curated_dedupe_and_entity_priority()
    test_curated_real_db()
    test_refine_budget()
    test_classify_title()
    test_refine_per_entity_grouping()
    test_knowledge_injection_budget()
    test_lowfreq_worth_wiki()
    print("\n全部测试通过")

