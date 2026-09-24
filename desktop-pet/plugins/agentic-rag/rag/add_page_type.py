"""给 data/vector/meta.jsonl 的每个 chunk 添加 page_type 字段（基于标题规则推断）。

向量本身不变，只更新元数据文件。秒级完成。
"""

import json
import os
import sys
from collections import Counter

from .rules import page_type_for_chunk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_FILE = os.path.join(ROOT, "data", "vector", "meta.jsonl")


def main():
    rows = []
    with open(META_FILE, encoding="utf-8") as f:
        for line in f:
            m = json.loads(line)
            m["page_type"] = page_type_for_chunk(m.get("title", ""))
            rows.append(m)

    with open(META_FILE, "w", encoding="utf-8") as f:
        for m in rows:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    c = Counter(m["page_type"] for m in rows)
    print("共 %d 条，page_type 分布:" % len(rows))
    for k, v in c.most_common():
        print("  %-10s %d" % (k, v))


if __name__ == "__main__":
    main()
