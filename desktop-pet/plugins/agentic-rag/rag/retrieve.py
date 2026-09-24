"""检索层（阶段2）：向量 + BM25 多路召回 + RRF 融合。

用法：
    python src/retrieve.py "下界合金锭怎么合成？"           # 默认 RRF 融合
    python src/retrieve.py "下界合金锭" --retriever vec    # 仅向量
    python src/retrieve.py "下界合金锭" --retriever bm25   # 仅 BM25
"""

import argparse
import os
import pickle
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from .embed import embed_query
from .rerank import rerank
from .rrf import rrf_fusion
from .rules import rule_infer_filters, smart_need_retrieval, smart_page_type
from .vector_store import load as load_vec, search as vec_search

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BM25_FILE = os.path.join(ROOT, "data", "bm25", "bm25.pkl")

_bm25_cache = None


def load_bm25():
    global _bm25_cache
    if _bm25_cache is None:
        with open(BM25_FILE, "rb") as f:
            _bm25_cache = pickle.load(f)
    return _bm25_cache["bm"], _bm25_cache["metas"]


def bm25_search(query, top_k=20):
    bm, metas = load_bm25()
    hits = bm.search(query, top_k=top_k)
    results = []
    for idx, score in hits:
        m = metas[idx]
        results.append({
            "id": idx,
            "doc": m.get("text") or _doc_from_store(idx),
            "meta": m,
            "score": score,
        })
    return results


def _doc_from_store(idx):
    """BM25 metas 未存 text 时，从向量库 meta 取回。"""
    _, metas = load_vec()
    return metas[idx]["text"]


def vector_search(query, top_k=20, page_type=None):
    q_vec = embed_query(query)
    return vec_search(q_vec, top_k=top_k, page_type=page_type)


def filtered_vector_search(query, page_type, top_k=20):
    """元数据过滤检索：按 page_type 过滤向量召回（规则层条件触发）。"""
    return vector_search(query, top_k=top_k, page_type=page_type)


def hybrid_retrieve(query, top_k=10, vec_k=20, bm25_k=20):
    """多路召回 + RRF 融合 + 规则重排。

    规则层推断 page_type 时，追加一路元数据过滤召回。
    """
    vec_results = vector_search(query, top_k=vec_k)

    # 规则 + NLU 推断过滤条件
    ptype = smart_page_type(query)
    filters = {"page_type": ptype} if ptype in ("mob", "block", "biome") else None
    lists = [vec_results]
    if filters:
        filtered = filtered_vector_search(query, filters["page_type"], top_k=vec_k)
        lists.append(filtered)

    lists.append(bm25_search(query, top_k=bm25_k))
    fused = rrf_fusion(lists, k=60, top_k=15)
    return rerank(query, fused, top_k=top_k)


def agent_retrieve(query, top_k=10):
    """供 Agentic 层调用的入口：判断是否需要检索，并返回 (need, results)。

    need=False：无需检索（如寒暄），results 为空。
    """
    need = smart_need_retrieval(query)
    results = hybrid_retrieve(query, top_k=top_k) if need else []
    return need, results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--retriever", choices=["hybrid", "vec", "bm25"], default="hybrid")
    args = ap.parse_args()

    if args.retriever == "vec":
        results = vector_search(args.query, args.top_k)
    elif args.retriever == "bm25":
        results = bm25_search(args.query, args.top_k)
    else:
        results = hybrid_retrieve(args.query, top_k=args.top_k)

    print(f"查询: {args.query}  召回方式: {args.retriever}\n")
    for i, r in enumerate(results, 1):
        meta = r.get("meta") or {}
        title = meta.get("title", "?")
        section = meta.get("section") or "<引言>"
        score = r.get("rrf_score", r.get("score", 0))
        print(f"--- Top {i} (score={score:.4f}) [{title} / {section}] ---")
        print(r["doc"][:180])
        print()


if __name__ == "__main__":
    main()
