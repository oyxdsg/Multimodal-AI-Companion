# -*- coding: utf-8 -*-
"""agentic-rag 的知识实现：``wiki-knowledge`` + ``knowledge-qa`` 两个扩展点共用一份检索链路。

链路（代码见 ``rag/``，自 RAG 方案 mcwiki-agentic-rag 迁入）：

    规则层（need / page_type）→ 多路召回（向量 + BM25 [+ 元数据过滤]）
    → RRF 融合（k=60）→ 规则重排 → 按预算裁剪

两条入口只差「宿主喂什么」：

* :meth:`AgenticRag.know`   游戏事件文本 → 知识块（被动、事件驱动）
* :meth:`AgenticRag.ask`    用户提问     → 参考上下文（主动、提问驱动）

**只检索、不生成**：回答始终由主对话 AI 写，人格与口吻才统一，也省一次 LLM 调用。

降级策略（三级，任一环缺失都不会把宿主带崩）：

1. ``data/vector/`` + faiss + sentence-transformers 齐全 → 向量 + BM25 多路召回 + RRF + 重排
2. 只有 ``data/bm25/bm25.pkl`` → BM25 单路（只依赖 jieba）
3. 数据也没有 → 返回 ``""``（宿主按「不注入」处理）
"""

import os
import pickle
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:          # 让 ``import rag`` 在直接导入本模块时也成立
    sys.path.insert(0, _HERE)

_BM25_PKL = os.path.join(_HERE, "data", "bm25", "bm25.pkl")
_VECTOR_DIR = os.path.join(_HERE, "data", "vector")

_MIN_BLOCK = 80        # 剩余预算不足这么多字就不再塞新块

_bm25_cache = None


# ---------------------------------------------------------------- 状态

def data_ready():
    """检索数据是否就绪（BM25 索引是**最小**依赖）。"""
    return os.path.isfile(_BM25_PKL)


def vector_ready():
    """向量库是否就绪（决定能否走多路召回）。"""
    return os.path.isdir(_VECTOR_DIR) and bool(os.listdir(_VECTOR_DIR))


def status():
    if not data_ready():
        return "索引缺失（需先建库，见本插件 README）"
    if vector_ready():
        return "索引就绪（向量 + BM25 多路召回）"
    return "索引就绪（仅 BM25，向量库缺失）"


# ---------------------------------------------------------------- 检索

def _bm25_search(query, top_k):
    """BM25 单路检索（只依赖 jieba，不碰 faiss）。

    ``bm25.pkl`` 的 metas 自带 ``text``，所以这条路能独立产出正文。
    """
    global _bm25_cache
    from rag.bm25 import BM25  # noqa: F401  （确保插件目录在 sys.path 时可导入）
    if _bm25_cache is None:
        with open(_BM25_PKL, "rb") as f:
            _bm25_cache = pickle.load(f)
    bm, metas = _bm25_cache["bm"], _bm25_cache["metas"]
    out = []
    for idx, score in bm.search(query, top_k=top_k):
        meta = metas[idx]
        out.append({"id": idx, "doc": meta.get("text") or "",
                    "meta": meta, "score": score})
    return out


def _hybrid_retrieve(query, top_k):
    """多路召回 + RRF + 重排（需要 faiss / sentence-transformers）。"""
    from rag.retrieve import hybrid_retrieve
    return hybrid_retrieve(query, top_k=top_k)


# ---------------------------------------------------------------- 扩展点实现

class AgenticRag:
    """``wiki-knowledge`` + ``knowledge-qa`` 双扩展点实现。"""

    def __init__(self, host=None):
        self._host = host
        self._sid = None
        self._done = set()      # 会话内已注入过的页面标题（防重复刷屏）
        self._warned = set()

    # ---------- 工具 ----------

    def _note(self, msg):
        """记一行日志（只在首次出现时记，避免每轮刷屏）。"""
        if msg in self._warned:
            return
        self._warned.add(msg)
        if self._host is not None:
            try:
                self._host.log(msg)
            except Exception:
                pass

    def _retrieve(self, query, top_k):
        """检索并返回 ``(results, mode)``；向量路不可用时自动退回 BM25。"""
        if vector_ready():
            try:
                return _hybrid_retrieve(query, top_k=top_k), "hybrid"
            except Exception as e:
                self._note("向量路不可用（%s），本次退回 BM25 单路" % type(e).__name__)
        return _bm25_search(query, top_k=top_k), "bm25"

    @staticmethod
    def _format(results, max_chars):
        """把检索结果拼成可注入文本，按预算裁剪。"""
        blocks, used = [], 0
        for r in results or []:
            meta = r.get("meta") or {}
            doc = (r.get("doc") or "").strip()
            if not doc:
                continue
            title = meta.get("title") or ""
            section = meta.get("section") or ""
            head = ("【%s】%s" % (title, section)).strip() if title else ""
            block = ((head + "\n" + doc) if head else doc).strip()
            remain = max_chars - used
            if remain <= _MIN_BLOCK:
                break
            if len(block) > remain:
                block = block[:remain].rstrip()
            blocks.append(block)
            used += len(block) + 2
            if used >= max_chars:
                break
        return "\n\n".join(blocks)

    # ---------- 扩展点入口 ----------

    def know(self, events_text, *, session_key="", max_chars=480, hints=()):
        """游戏事件 → 知识块（与 wiki-local 同一扩展点，这里是多路召回的版本）。"""
        try:
            if not data_ready():
                return ""
            parts = [str(events_text or "")] + [str(h) for h in (hints or [])]
            query = " ".join(p for p in parts if p).strip()
            if not query:
                return ""
            if self._sid != session_key:
                self._sid = session_key
                self._done = set()
            results, mode = self._retrieve(query, top_k=6)
            self._note("检索命中 %d 条（%s）" % (len(results), mode))
            fresh = [r for r in results
                     if (r.get("meta") or {}).get("title") not in self._done]
            text = self._format(fresh, max_chars)
            for r in fresh:
                t = (r.get("meta") or {}).get("title")
                if t:
                    self._done.add(t)
            return text
        except Exception as e:
            self._note("know() 失败：%s" % type(e).__name__)
            return ""

    def ask(self, question, *, session_key="", max_chars=1200):
        """用户提问 → 参考上下文（宿主把它拼进当轮 prompt，回答仍由主对话 AI 写）。"""
        try:
            if not data_ready():
                return ""
            q = (question or "").strip()
            if not q:
                return ""
            # 规则层先判「这句话需不需要查知识」（寒暄 / 纯情绪 → 不查），省一次嵌入
            try:
                from rag.rules import smart_need_retrieval
                if not smart_need_retrieval(q):
                    return ""
            except Exception:
                pass
            results, mode = self._retrieve(q, top_k=5)
            self._note("问答检索命中 %d 条（%s）" % (len(results), mode))
            return self._format(results, max_chars)
        except Exception as e:
            self._note("ask() 失败：%s" % type(e).__name__)
            return ""
