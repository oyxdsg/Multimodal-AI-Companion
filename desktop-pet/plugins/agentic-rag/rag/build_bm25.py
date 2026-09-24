"""构建并持久化 BM25 索引（jieba 分词）。

用法：
    python src/build_bm25.py            # 全量
    python src/build_bm25.py --limit 5000
输出：data/bm25/bm25.pkl
"""

import argparse
import json
import os
import pickle
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from .bm25 import BM25
from .rules import page_type_for_chunk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = os.path.join(ROOT, "data", "processed", "chunks.jsonl")
BM25_DIR = os.path.join(ROOT, "data", "bm25")
BM25_FILE = os.path.join(BM25_DIR, "bm25.pkl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(BM25_DIR, exist_ok=True)

    texts = []
    metas = []
    with open(CHUNKS, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if args.limit and i >= args.limit:
                break
            c = json.loads(line)
            texts.append(c["text"])
            metas.append({"title": c["title"], "section": c["section"], "pageid": c["pageid"],
                          "chunk_idx": i, "text": c["text"],
                          "page_type": page_type_for_chunk(c["title"])})
    print(f"[bm25] 加载 {len(texts)} 篇文档，开始 fit（jieba 分词）...", flush=True)

    t0 = time.time()
    bm = BM25()
    bm.fit(texts)
    el = time.time() - t0
    print(f"[bm25] fit 完成，用时 {el:.0f}s", flush=True)

    with open(BM25_FILE, "wb") as f:
        pickle.dump({"bm": bm, "metas": metas}, f)
    print(f"[bm25] 索引已保存 -> {BM25_FILE}（{os.path.getsize(BM25_FILE)/1e6:.1f}MB）", flush=True)


if __name__ == "__main__":
    main()
