"""批量处理：data/raw/*.txt → 解析 → 切分 → data/processed/chunks.jsonl。

输出一行一个 chunk（JSON），字段：pageid, title, section, text。
支持断点续传：已写入的 pageid 跳过。
"""

import argparse
import json
import os
import re
import sys
import time

from .parse import parse_file, RAW, PROCESSED
from .chunk import chunk_page

OUT = os.path.join(PROCESSED, "chunks.jsonl")
IDX = os.path.join(PROCESSED, "index.json")


def load_done():
    done = set()
    if os.path.exists(IDX):
        with open(IDX, encoding="utf-8") as f:
            done = set(json.load(f))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件（测试）")
    ap.add_argument("--pageid", type=str, default="", help="只处理指定 pageid（逗号分隔）")
    args = ap.parse_args()

    os.makedirs(PROCESSED, exist_ok=True)
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".txt"))
    if args.pageid:
        pids = {p.strip() for p in args.pageid.split(",")}
        files = [f for f in files if re.match(r"^(\d+)_", f) and re.match(r"^(\d+)_", f).group(1) in pids]

    done = load_done()
    pending = files
    if args.limit:
        pending = pending[:args.limit]

    # 追加模式写 chunks.jsonl；若首次则清空
    mode = "a" if os.path.exists(OUT) and done else "w"
    n_chunks = 0
    n_pages = 0
    t0 = time.time()
    with open(OUT, mode, encoding="utf-8") as f:
        for i, fname in enumerate(pending):
            m = re.match(r"^(\d+)_", fname)
            pageid = m.group(1) if m else fname
            if pageid in done:
                continue
            try:
                page = parse_file(fname)
                chunks = chunk_page(page)
            except Exception as e:
                print(f"[err] {fname}: {e}", flush=True)
                continue
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
                n_chunks += 1
            n_pages += 1
            done.add(pageid)
            if (i + 1) % 200 == 0:
                el = time.time() - t0
                print(f"[proc] {i+1}/{len(pending)} 页，本页 {len(chunks)} chunk，累计 {n_chunks} chunk，用时 {el:.0f}s",
                      flush=True)
            if args.limit and n_pages >= args.limit:
                break
            if args.pageid:
                pass  # 全部处理

    with open(IDX, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f)
    print(f"[done] 处理 {n_pages} 页，新增 {n_chunks} chunk -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
