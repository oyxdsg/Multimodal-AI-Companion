"""faiss + numpy 轻量向量库（替代 chromadb，规避其 Windows 持久化 bug）。

存储：
    data/vector/index.faiss      # faiss 索引（float32 矩阵，余弦 ≈ L2 归一化后点积）
    data/vector/meta.jsonl       # 一行一个 chunk 元数据（与索引顺序一一对应）

检索：
    search(vec, top_k) -> [{"doc", "meta", "score"}]
"""

import json
import os

import faiss
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VEC_DIR = os.path.join(ROOT, "data", "vector")
INDEX_FILE = os.path.join(VEC_DIR, "index.faiss")
META_FILE = os.path.join(VEC_DIR, "meta.jsonl")
DIM = 768

_index = None
_metas = None


def _ensure_dir():
    os.makedirs(VEC_DIR, exist_ok=True)


def build_from_arrays(vecs, metas):
    """从向量矩阵 + 元数据构建索引并持久化。vecs: numpy (N, DIM)，已 L2 归一化。"""
    _ensure_dir()
    arr = np.asarray(vecs, dtype=np.float32)
    idx = faiss.IndexFlatIP(arr.shape[1])
    idx.add(arr)
    # faiss 原生 write_index 对含中文路径支持不佳（fopen 编码问题），改用 serialize 后由 Python 写盘
    data = faiss.serialize_index(idx)
    with open(INDEX_FILE, "wb") as f:
        f.write(data)
    with open(META_FILE, "w", encoding="utf-8") as f:
        for m in metas:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    global _index, _metas
    _index = idx
    _metas = metas
    return idx.ntotal


def load():
    """加载索引和元数据到内存。"""
    global _index, _metas
    if _index is not None:
        return _index, _metas
    if not os.path.exists(INDEX_FILE):
        raise FileNotFoundError(f"索引不存在: {INDEX_FILE}，请先运行 index.py")
    with open(INDEX_FILE, "rb") as f:
        data = f.read()
    _index = faiss.deserialize_index(np.frombuffer(data, dtype=np.uint8))
    _metas = []
    with open(META_FILE, encoding="utf-8") as f:
        for line in f:
            _metas.append(json.loads(line))
    return _index, _metas


def search(query_vec, top_k=5, page_type=None):
    """按查询向量检索。query_vec: list[float]（已归一化）。

    page_type 指定时，先从更大候选集中取回再过滤（faiss 不支持元数据过滤，
    用"放大候选 + 过滤"实现），不足时回退到 unfiltered 结果。
    """
    idx, metas = load()
    q = np.asarray([query_vec], dtype=np.float32)
    fetch = max(top_k * 10, 200) if page_type else min(top_k, idx.ntotal)
    scores, idxs = idx.search(q, min(fetch, idx.ntotal))
    results = []
    for score, i in zip(scores[0], idxs[0]):
        if i < 0:
            continue
        m = metas[i]
        results.append({
            "id": i,
            "doc": m["text"],
            "meta": m,
            "score": float(score),
        })
    if page_type:
        matched = [r for r in results if r["meta"].get("page_type") == page_type]
        if matched:
            results = matched[:top_k]
        # 匹配不足 top_k 时保留 matched + 补充非匹配项（保底召回）
        if len(results) < top_k:
            results = results[:]
            rest = [r for r in results if r["meta"].get("page_type") != page_type]
            results += rest[: top_k - len(results)]
        else:
            results = results[:top_k]
    return results


def count():
    idx, _ = load()
    return idx.ntotal


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("count:", count())
