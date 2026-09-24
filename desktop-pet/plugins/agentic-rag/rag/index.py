"""索引：chunks.jsonl → 嵌入 → faiss 向量库 + meta。

用法：
    python src/index.py              # 全量索引
    python src/index.py --limit 2000 # 只索引前 N 个 chunk（测试）
"""

import argparse
import json
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from .embed import embed, load_model
from .vector_store import build_from_arrays

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNKS = os.path.join(ROOT, "data", "processed", "chunks.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只索引前 N 个 chunk")
    args = ap.parse_args()

    print("[index] 加载嵌入模型 ...", flush=True)
    load_model()

    docs, metas = [], []
    with open(CHUNKS, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if args.limit and i >= args.limit:
                break
            c = json.loads(line)
            docs.append(c["text"])
            metas.append({
                "pageid": c["pageid"],
                "title": c["title"],
                "section": c["section"],
                "chunk_idx": i,
                "text": c["text"],
            })

    n = len(docs)
    print(f"[index] 待嵌入 {n} 个 chunk", flush=True)

    # 分批嵌入，避免一次性占用过多显存/内存
    batch_size = 512
    vecs = []
    t0 = time.time()
    for s in range(0, n, batch_size):
        e = min(s + batch_size, n)
        vecs.extend(embed(docs[s:e], batch_size=128))
        el = time.time() - t0
        print(f"[index] 嵌入 {e}/{n}，{e/max(el,1):.0f}条/s", flush=True)

    import numpy as np
    arr = np.asarray(vecs, dtype=np.float32)
    total = build_from_arrays(arr, metas)
    el = time.time() - t0
    print(f"[done] 共 {total} 条向量已写入 data/vector/，用时 {el:.0f}s", flush=True)


if __name__ == "__main__":
    main()
