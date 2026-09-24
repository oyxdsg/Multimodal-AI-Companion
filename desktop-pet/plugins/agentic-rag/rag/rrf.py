"""RRF（Reciprocal Rank Fusion）：只依赖排名位置，跨检索路可比。"""


def rrf_fusion(result_lists, k=60, top_k=10):
    """融合多路检索结果。

    每路结果形如 [{"id": str, "doc": str, "meta": dict, "score": float}, ...]
    返回按 RRF 分数降序的前 top_k 条，并附 rrf_score。
    """
    scores = {}
    doc_map = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            doc_id = item["id"]
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            doc_map[doc_id] = item
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_k]
    return [{**doc_map[i], "rrf_score": scores[i]} for i in sorted_ids]


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    a = [{"id": "x", "doc": "a", "meta": {}, "score": 0.9},
         {"id": "y", "doc": "b", "meta": {}, "score": 0.8}]
    b = [{"id": "y", "doc": "b", "meta": {}, "score": 0.7},
         {"id": "z", "doc": "c", "meta": {}, "score": 0.6}]
    print(rrf_fusion([a, b], top_k=3))
