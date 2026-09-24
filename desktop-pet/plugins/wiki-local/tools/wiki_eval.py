# -*- coding: utf-8 -*-
"""Wiki 检索质量评测（P0，端到端：事件 → 实体提取 → 检索 → 命中期望页）。

借鉴 mcwiki-agentic-rag/evaluate.py 的 Recall@k 口径，但**走真实链路**：
不是拿人工给的词直接检索（那会绕过提取层），而是

    event ──match_terms──> 提取词 ──search──> top-k 页面 ──page_hit──> 命中 expect?

用法（**必须用带 numpy 的系统 Python**）::

    python tools\\wiki_eval.py

产出：``tools/wiki_eval_report.txt``（UTF-8）。

指标（对 ``eval/retrieval_cases.json`` 的每个用例）：
- **提取层**：match_terms 是否提取到能覆盖 ``expect`` 的词（归一化相等或互为重定向）
- **检索层**：用提取词（提取为空时用 [expect] 兜底，隔离检索层）检索 top-5，
  ``expect`` 是否在 top-k（first_hit_rank / recall@k）
- **端到端**：提取命中 **且** 检索命中
- 分 **normal / lowfreq** 两档（lowfreq 里被过滤的词不算失败）
- **三层链路**：curated(L1) → 精炼(L2) → 原文(L3) 命中分布（按 expect 判定）

命中判定（归一化标题匹配，口径见 WIKI_AUDIT.md §3.2 / §5.3）：
- 候选标题与期望词归一化后相等；
- 或两者互为重定向别名/目标（如「铁镐」→「镐」、「樱花木」→「木头」）。

改动 Wiki 检索代码（打分排序 / FTS 融合 / 词表）后重跑本工具，与上次报告对比。
"""
import io
import json
import os
import re
import sqlite3
import sys

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer,
                                  encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # plugins/wiki-local
PET = os.path.dirname(os.path.dirname(ROOT))                        # desktop-pet
sys.path.insert(0, ROOT)
sys.path.insert(0, PET)
OUT = os.path.join(ROOT, "tools", "wiki_eval_report.txt")
CASES = os.path.join(ROOT, "eval", "retrieval_cases.json")
_TOP = 5

L = []
def w(*a):
    line = " ".join(str(x) for x in a)
    L.append(line)
    print(line)


def _norm(s):
    """归一化标题：去全角括号后缀（方块）/（物品）…、去空白、小写。"""
    s = re.sub(r"（[^）]*）", "", s or "")
    s = re.sub(r"\s+", "", s).lower()
    return s


def load_cases():
    with open(CASES, "r", encoding="utf-8") as f:
        return json.load(f)


def related(conn, a, b):
    """两个词是否同一实体（归一化相等 / 互为重定向 / 互为语义别名）。"""
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    import wi_config as config
    for alias, title in config.WIKI_ALIASES.items():
        if (na == _norm(alias) and nb == _norm(title)) \
                or (nb == _norm(alias) and na == _norm(title)):
            return True
    for x, y in ((a, b), (b, a)):
        if conn.execute("SELECT 1 FROM redirects WHERE alias=? AND title=?",
                        (x, y)).fetchone():
            return True
    return False


def page_hit(conn, title, expect):
    """候选页标题是否命中期望词。"""
    return related(conn, title, expect)


def first_rank(conn, results, expect):
    """返回第一个命中页的 1-based 排名；未命中返回 None。"""
    for i, (t, _s) in enumerate(results):
        if page_hit(conn, t, expect):
            return i + 1
    return None


def _layer(conn, expect):
    """三层链路：L1 curated → L2 精炼模型 → L3 原文兜底（按 expect 判定）。"""
    from wiki_refine import curated as cu
    from wiki_refine import refine as rf
    import wiki_kb as kb
    if cu.prompt([expect]):
        return "L1"
    cands = kb.search_candidates([expect], 1, per_term=1)
    if cands and rf.refine(cands, terms=[expect]):
        return "L2"
    return "L3"


def run(conn, cases, include_rare):
    """跑一档（normal / lowfreq），返回统计 dict（端到端）。"""
    import wiki_kb as kb
    n = len(cases)
    ext_hits = 0          # 提取层命中
    first_hits = 0        # 检索层 first_hit
    rank_sum = 0
    rec = {1: 0, 3: 0, 5: 0}
    layers = {"L1": 0, "L2": 0, "L3": 0}
    filtered = 0
    miss_detail = []      # (event, expect, extracted, top_titles)
    for c in cases:
        expect = c["expect"]
        if include_rare and not kb.is_worth_wiki(expect):
            # lowfreq：白名单常见实体设计上不查 wiki，不算失败
            filtered += 1
            continue
        extracted = list(kb.match_terms(c["event"], include_rare=include_rare) or [])
        ext_ok = any(related(conn, ww, expect) for ww in extracted)
        if ext_ok:
            ext_hits += 1
        # 检索：用提取词（空则用 [expect] 兜底，隔离检索层）
        query = extracted if extracted else [expect]
        results = kb.search(query, limit=_TOP)
        titles = [t for t, _s in results]
        rk = first_rank(conn, results, expect)
        if rk:
            first_hits += 1
            rank_sum += rk
            for k in rec:
                if rk <= k:
                    rec[k] += 1
        else:
            miss_detail.append((c["event"], expect, extracted, titles))
        layers[_layer(conn, expect)] += 1
    stat = {
        "include_rare": include_rare, "n": n,
        "ext_hits": ext_hits, "first_hits": first_hits, "rank_sum": rank_sum,
        "rec": rec, "layers": layers,
        "filtered": filtered, "miss_detail": miss_detail,
    }
    return stat


def main():
    import wiki_kb as kb
    cases = load_cases()
    db = kb.db_path()
    w("=" * 72)
    w("[0] 评测环境")
    w("=" * 72)
    w("用例集:", CASES, "条数 =", len(cases))
    w("DB:", db, "exists =", os.path.exists(db))
    if not os.path.exists(db):
        return
    w("DB 体量 = %.1f MB" % (os.path.getsize(db) / 1024 / 1024))
    conn = sqlite3.connect(db)

    for flag in (False, True):
        w("")
        w("=" * 72)
        w("[%s] 端到端检索质量（%s）" % (1 if not flag else 2,
                                    "normal" if not flag else "lowfreq"))
        w("=" * 72)
        s = run(conn, cases, include_rare=flag)
        denom = s["n"] - s["filtered"]
        w("  用例 = %d（lowfreq 被过滤 %d，应查 %d）"
          % (s["n"], s["filtered"], denom))
        if denom:
            w("  提取层命中 = %d/%d (%.1f%%)"
              % (s["ext_hits"], denom, s["ext_hits"] / denom * 100))
            w("  检索层 first_hit_rate = %.1f%% (%d/%d)"
              % (s["first_hits"] / denom * 100, s["first_hits"], denom))
            if s["first_hits"]:
                w("  avg_first_hit_rank = %.2f" % (s["rank_sum"] / s["first_hits"]))
            w("  检索层 recall@1 / @3 / @5 = %.1f%% / %.1f%% / %.1f%%"
              % (s["rec"][1] / denom * 100, s["rec"][3] / denom * 100,
                 s["rec"][5] / denom * 100))
            w("  端到端（提取且检索命中）≈ %.1f%%（= 提取命中 ∩ 检索命中，见下明细）"
              % (s["ext_hits"] / denom * 100))
        w("  三层链路 = %s（curated / 精炼 / 原文兜底）"
          % {k: s["layers"][k] for k in ("L1", "L2", "L3")})
        if s["miss_detail"]:
            w("  !! 检索未命中（%d）:" % len(s["miss_detail"]))
            for ev, expect, extracted, titles in s["miss_detail"]:
                w("      %-22s expect=%-8s 提取=%s  top=%s"
                  % (ev, expect, extracted or "(无)", titles or "(无)"))

    w("")
    w("=" * 72)
    w("[3] 逐用例明细（normal）")
    w("=" * 72)
    w("%-22s %-10s %-18s %s" % ("事件", "expect", "提取词", "top5 命中"))
    for c in cases:
        expect = c["expect"]
        extracted = list(kb.match_terms(c["event"], include_rare=False) or [])
        query = extracted if extracted else [expect]
        results = kb.search(query, limit=_TOP)
        titles = [t for t, _s in results]
        rk = first_rank(conn, results, expect)
        w("%-22s %-10s %-18s %s" % (
            c["event"], expect, "|".join(extracted) or "(无)",
            "rank=%d" % rk if rk else "✗"))
        if not titles:
            w("%-22s %-10s %-18s   (检索无结果)" % ("", "", ""))
    conn.close()


try:
    main()
except Exception as e:
    import traceback
    w("!! 评测异常:", e)
    w(traceback.format_exc())

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L))
print("\n>>> 报告写入", OUT)
