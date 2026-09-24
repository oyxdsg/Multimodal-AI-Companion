# -*- coding: utf-8 -*-
"""Wiki 链路基线审计工具（可重复运行，优化前后对比用）。

用法（**必须用带 PySide6/numpy 的系统 Python**）::

    python tools\wiki_audit.py

产出：``tools/wiki_audit_report.txt``（UTF-8）。
覆盖：数据层体量 / curated 覆盖与数据卫生 / 标题与别名口径 /
      实体提取（match_terms）效果 / 三层链路产出与体量 / 耗时。

改动 Wiki 相关代码或数据后重跑本工具，与上一次报告对比即可量化收益。
"""
import os
import re
import sqlite3
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # plugins/wiki-local
PET = os.path.dirname(os.path.dirname(ROOT))                        # desktop-pet
sys.path.insert(0, ROOT)
sys.path.insert(0, PET)
OUT = os.path.join(ROOT, "tools", "wiki_audit_report.txt")

L = []
def w(*a):
    line = " ".join(str(x) for x in a)
    L.append(line)
    print(line)


def main():
    import config
    import game.wiki_kb as kb
    from nlu.wiki_refine import curated as cu
    from nlu.wiki_refine import refine as rf

    # ================= 1. 数据层 =================
    w("=" * 72)
    w("[1] 数据层")
    w("=" * 72)
    db = kb.db_path()
    w("DB:", db, "exists =", os.path.exists(db))
    if not os.path.exists(db):
        return
    w("DB 体量 = %.1f MB" % (os.path.getsize(db) / 1024 / 1024))
    con = sqlite3.connect(db)

    def titles(cat):
        return [r[0] for r in con.execute(
            "SELECT title FROM pages WHERE category=?", (cat,)).fetchall()]

    w("pages 总数 =", con.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
    for cat, n in con.execute(
            "SELECT category, COUNT(*) FROM pages GROUP BY 1 ORDER BY 2 DESC"):
        w("   category %-9s = %d" % (cat, n))
    w("redirects 别名 =", con.execute("SELECT COUNT(*) FROM redirects").fetchone()[0])
    w("FTS5 表 pages_fts 存在（当前检索路径未使用；仅占空间）")

    # ================= 2. curated =================
    w("")
    w("=" * 72)
    w("[2] curated 知识库（nlu/wiki_refine/data/curated_all.jsonl）")
    w("=" * 72)
    idx = cu.shared()
    if idx is None:
        w("!! curated 载入失败")
        return
    rows = idx.rows
    w("条数 =", len(rows))
    w("  实体 =", sum(1 for r in rows if r.get("category") not in ("成就", "进度")),
      " 成就 =", sum(1 for r in rows if r.get("category") == "成就"),
      " 进度 =", sum(1 for r in rows if r.get("category") == "进度"))
    w("  fun（趣闻）非空 = %d / %d" % (
        sum(1 for r in rows if (r.get("fun") or "").strip()), len(rows)))
    w("  facts 总数 =", sum(len(r.get("facts") or []) for r in rows))
    w("  facts.type 种类 =", len({f.get("type") for r in rows
                                  for f in (r.get("facts") or [])}))
    sl = sorted(len(r.get("summary") or "") for r in rows)
    w("  summary 长度 min/p50/max = %d / %d / %d" % (sl[0], sl[len(sl) // 2], sl[-1]))
    w("  facts 为空的条目 =", sum(1 for r in rows if not r.get("facts")))

    # 可达性：每个条目用自己的 name / id 查，能否查回自己
    names = Counter((r.get("name") or "").strip() for r in rows)
    dup = {k: v for k, v in names.items() if v > 1 and k}
    idup = [k for k, v in Counter(
        (r.get("id") or "").strip() for r in rows).items() if v > 1 and k]
    w("  同名（不同 id）= %d 组 / %d 条；id 重复 = %d 组"
      % (len(dup), sum(dup.values()), len(idup)))
    w("  同名 top5:", sorted(dup.items(), key=lambda x: -x[1])[:5])
    miss_name, miss_id = [], []
    for r in rows:
        nm = (r.get("name") or "").strip()
        if len(nm) >= 2 and cu.search([nm]) is not r:
            miss_name.append((nm, r.get("category")))
        i = (r.get("id") or "").strip()
        if len(i) >= 2 and cu.search([i]) is not r:
            miss_id.append(i)
    w("  按 name 查不回自己 = %d 条（同名条目里的后几条，仍可按 id 查到）"
      % len(miss_name))
    w("     样例:", miss_name[:6])
    w("  按 id 查不回自己 = %d 条" % len(miss_id))

    # 覆盖率
    for cat in ("common", "rare"):
        ts = titles(cat)
        hit = sum(1 for t in ts if cu.prompt([t]))
        w("curated 覆盖率 [%s] = %d/%d = %.1f%%" % (cat, hit, len(ts), hit / max(len(ts), 1) * 100))

    # ================= 3. 标题 / 别名口径 =================
    w("")
    w("=" * 72)
    w("[3] 标题与别名口径（实体提取的地基）")
    w("=" * 72)
    probe = ["铁镐", "钻石镐", "木镐", "镐", "钻石剑", "剑", "钻石", "铁锭", "木棍",
             "樱花木", "樱花木板", "嗅探兽", "铜灯", "试炼密室", "猪", "牛", "僵尸",
             "工作台", "铁傀儡", "木板", "铁块", "下界合金残骸"]
    w("%-10s %-12s %-12s %s" % ("查询", "pages标题", "别名指向", "curated命中"))
    for t in probe:
        r = con.execute("SELECT category FROM pages WHERE title=?", (t,)).fetchone()
        r2 = con.execute("SELECT title FROM redirects WHERE alias=?", (t,)).fetchone()
        h = cu.search([t])
        w("%-10s %-12s %-12s %s" % (
            t, r[0] if r else "✗", r2[0] if r2 else "✗",
            (h.get("name") or "") if h else "✗"))

    c1 = len([t for t in titles("common") if len(t) == 1])
    w("")
    w("!! common 中单字标题 = %d 条（match_terms 的 len>=2 过滤使其永不可检出）" % c1)
    w("   单字样例:", sorted(t for t in titles("common") if len(t) == 1))
    noise_kw = ("指南", "披风", "音乐", "更新", "版本", "工作室", "Java版", "基岩版",
                "MOD", "模组", "漫画", "电影", "周边", "投票", "奖项", "格式")
    noise = [t for t in titles("common") if any(k in t for k in noise_kw)]
    w("!! common 中疑似非游戏实体噪音 = %d / %d" % (len(noise), len(titles("common"))))
    w("   样例:", noise[:20])

    # ================= 4. 实体提取 =================
    w("")
    w("=" * 72)
    w("[4] 实体提取 wiki_kb.match_terms（事件行 → 检索词）")
    w("=" * 72)
    for flag in (False, True):
        terms, maxlen = kb._term_index(flag)
        w("  词表[%s] = %d 词，最长 %d"
          % ("common+rare" if flag else "common", len(terms), maxlen))
    lines = ["[获得] 橡木原木 x3", "[获得] 铁镐 x1", "[获得] 钻石剑 x1",
             "[击杀] 苦力怕 x2", "[破坏] 钻石矿石 x5", "[获得] 樱花木 x8",
             "[受伤] 来自 骷髅 3 点", "[进度] 怪物猎人", "[击杀] 猪 x1",
             "[获得] 木镐 x1", "今天天气怎么样呀", "我去水边看看风景"]
    for s in lines:
        w("  %-24s → %s" % (s, kb.match_terms(s)))
    t0 = time.perf_counter()
    for _ in range(20):
        kb.match_terms("\n".join(lines))
    w("  单次耗时 = %.1f ms" % ((time.perf_counter() - t0) / 20 * 1000))
    t0 = time.perf_counter()
    for t in titles("common")[:2000]:
        cu.prompt([t])
    w("  curated.prompt 2000 次 = %.0f ms (%.3f ms/次)"
      % ((time.perf_counter() - t0) * 1000, (time.perf_counter() - t0) / 2000 * 1000))

    # ================= 5. 三层链路产出 =================
    w("")
    w("=" * 72)
    w("[5] 三层链路实际产出（curated → 精炼模型 → 原文）")
    w("=" * 72)
    w("-- curated 命中样例 --")
    for t in ["钻石镐", "铁镐", "僵尸", "铁傀儡", "铜灯", "试炼密室",
              "樱花木", "嗅探兽", "木板", "猪"]:
        p = cu.prompt([t])
        if p:
            w("  「%s」%d 字：%s" % (t, len(p), p.replace("\n", " | ")[:180]))
        else:
            w("  「%s」未命中" % t)
    w("-- 未命中走精炼模型（用模组真会报的实体名探测，而不是按字母序挑到无关页）--")
    probe2 = [t for t in ["猪", "牛", "木板", "樱花木", "嗅探兽", "橡木原木",
                          "圆石", "小麦", "木镐", "石剑", "熔炉", "床"]
              if not cu.prompt([t])]
    shorter = longer = equal = 0
    tot_r = tot_w = 0
    for t in probe2:
        cands = kb.search_candidates([t], wi_config.GAME_WIKI_LIMIT)
        out = rf.refine(cands, terms=[t]) if cands else ""
        raw = kb.search_text([t], wi_config.GAME_WIKI_LIMIT)
        tot_r += len(out)
        tot_w += len(raw)
        if out and raw:
            if len(out) < len(raw):
                shorter += 1
            elif len(out) > len(raw):
                longer += 1
            else:
                equal += 1
        w("  「%s」候选=%d  refine=%d 字  raw=%d 字" % (t, len(cands), len(out), len(raw)))
    w("  体量对比（n=%d）：refine 更短 %d / 更长 %d / 相同 %d；均值 refine=%.0f  raw=%.0f"
      % (len(probe2), shorter, longer, equal,
         tot_r / max(len(probe2), 1), tot_w / max(len(probe2), 1)))
    for t in probe2[:3]:
        cands = kb.search_candidates([t], wi_config.GAME_WIKI_LIMIT)
        out = rf.refine(cands, terms=[t]) if cands else ""
        w("  样例「%s」refine → %s" % (t, out[:180].replace("\n", " | ")))

    # ================= 6. 配置 =================
    w("")
    w("=" * 72)
    w("[6] 相关配置")
    w("=" * 72)
    w("GAME_WIKI_LIMIT =", wi_config.GAME_WIKI_LIMIT)
    w("WIKI_REFINE_ENGINE =", config.WIKI_REFINE_ENGINE)
    w("WIKI_FILTER_DEFAULT =", config.WIKI_FILTER_DEFAULT)
    w("WIKI_COMMON_TERMS 白名单条数 =", len(wi_config.WIKI_COMMON_TERMS))
    con.close()


try:
    main()
except Exception as e:
    import traceback
    w("!! 审计异常:", e)
    w(traceback.format_exc())

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L))
print("\n>>> 报告写入", OUT)
