# -*- coding: utf-8 -*-
"""从 wiki 库生成精炼训练样本（切句 + 三分类弱标签）。

用法::

    python -m nlu.wiki_refine.build_data            # 全量 common+rare
    python -m nlu.wiki_refine.build_data --limit 20 --preview 8

产物: ``nlu/wiki_refine/data/seed.jsonl``
每行: {"title":.., "sec":"小节链/", "text":句, "label":"know|fun|drop"}
"""
import argparse
import json
import os
import sys

from wiki_refine import segment, weak_labels

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")


def collect(limit=0, titles=None):
    """从 db 读页面，切句 + 弱标签。返回 rows。

    :param titles: 指定页面标题列表（逗号分隔）；None 时按字母序取 limit 个
    """
    conn = segment.connect()
    if titles:
        ph = ",".join("?" * len(titles))
        sql = ("SELECT title, text FROM pages WHERE title IN (%s) "
               "ORDER BY title" % ph)
        rows = conn.execute(sql, list(titles)).fetchall()
    else:
        sql = ("SELECT title, text FROM pages WHERE category IN ('common','rare') "
               "ORDER BY title")
        if limit:
            sql = ("SELECT title, text FROM ("
                   "SELECT title, text, ROW_NUMBER() OVER (ORDER BY title) rn "
                   "FROM pages WHERE category IN ('common','rare')) WHERE rn <= ?")
            rows = conn.execute(sql, (limit,)).fetchall()
        else:
            rows = conn.execute(sql).fetchall()
    conn.close()
    out = []
    tagged = []
    for r in rows:
        for c in segment.page_candidates(r["title"], r["text"]):
            c["title"] = r["title"]
            c["label"] = weak_labels.label(c, r["title"])
            tagged.append(c)
    return tagged


def main():
    ap = argparse.ArgumentParser(description="生成 wiki 精炼训练样本")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个页面（测试用）")
    ap.add_argument("--titles", default="", help="指定页面标题，逗号分隔（优先于 --limit）")
    ap.add_argument("--preview", type=int, default=8, help="每类预览条数")
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "seed.jsonl"))
    args = ap.parse_args()

    from collections import Counter
    titles = [t.strip() for t in args.titles.split(",") if t.strip()] or None
    rows = collect(args.limit, titles)
    cnt = Counter(r["label"] for r in rows)
    print("候选句 %d 条：%s" % (len(rows), dict(cnt)))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("已写入 %s" % args.out)

    if args.preview:
        lines = []
        for lb in ("know", "fun", "drop"):
            lines.append("\n──── %s（前 %d 条）────" % (lb, args.preview))
            n = 0
            for r in rows:
                if r["label"] != lb:
                    continue
                n += 1
                if n > args.preview:
                    break
                lines.append("  [%s] %s" % (r["title"], r["text"][:70]))
        prev = "\n".join(lines)
        pfile = os.path.join(os.path.dirname(args.out), "seed.preview.txt")
        with open(pfile, "w", encoding="utf-8") as f:
            f.write(prev)
        print("预览已写入 %s" % pfile)


if __name__ == "__main__":
    main()
