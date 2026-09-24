# -*- coding: utf-8 -*-
"""从 wiki 进度页自动生成 curated 进度条目。

进度页格式规范（自带中文名+描述+需求+上游），可全自动提取。
产物：nlu/wiki_refine/data/curated_advancements.jsonl
"""
import json
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

import os

_REFINE = os.path.dirname(os.path.abspath(__file__))          # wiki_refine/
_PLUGIN = os.path.dirname(_REFINE)                            # plugins/wiki-local
_PET = os.path.dirname(os.path.dirname(_PLUGIN))              # desktop-pet
DB = os.path.join(_PET, "wiki_data", "mcwiki.db")
OUT = os.path.join(_REFINE, "data", "curated_advancements.jsonl")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT text FROM pages WHERE title='进度'").fetchone()
conn.close()
if not row:
    print("!! 无进度页")
    sys.exit(1)
text = row["text"]

# 进度[中文名]（英文id）；描述：XXX；需求：XXX；上游：XXX。
PAT = re.compile(
    r"进度\[([^\]]+)\]（([^）]+)）"
    r"(?:；描述：([^；。]*))?"
    r"(?:；需求：([^；。]*))?"
    r"(?:；上游：([^；。]*))?[。；]")

rows = []
seen = set()
for m in PAT.finditer(text):
    cn = m.group(1).strip()
    eid = m.group(2).strip()
    desc = (m.group(3) or "").strip()
    req = (m.group(4) or "").strip()
    up = (m.group(5) or "").strip()
    if eid in seen:
        continue
    seen.add(eid)
    facts = []
    if req:
        facts.append({"type": "要求", "text": req})
    if up:
        facts.append({"type": "上游", "text": up})
    rows.append({"id": "adv_" + eid, "name": cn, "category": "进度",
                 "summary": desc or cn, "facts": facts})

# 按英文 id 排序
rows.sort(key=lambda r: r["id"])
with open(OUT, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("进度条目:", len(rows))
print("已写入:", OUT)
# 预览前 12
for r in rows[:12]:
    print("  [%s] %s: %s" % (r["id"], r["name"], r["summary"][:40]))
