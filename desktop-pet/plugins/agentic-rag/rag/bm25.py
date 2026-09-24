"""BM25 自实现（jieba 中文分词）。

用法：
    bm = BM25()
    bm.fit(texts)            # texts: list[str]
    bm.search(query, top_k)  # 返回 [(idx, score)]
"""

import math
from collections import Counter

import jieba


class BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = []
        self.doc_len = []
        self.doc_freqs = []
        self.idf = {}
        self.N = 0
        self.avgdl = 0.0

    def fit(self, corpus):
        self.docs = [list(jieba.cut(d)) for d in corpus]
        self.N = len(self.docs)
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = sum(self.doc_len) / self.N if self.N else 0.0
        df = Counter()
        for doc in self.docs:
            for word in set(doc):
                df[word] += 1
        for word, n in df.items():
            self.idf[word] = math.log((self.N - n + 0.5) / (n + 0.5) + 1)
        self.doc_freqs = [Counter(doc) for doc in self.docs]

    def score(self, query, doc_idx):
        q_words = list(jieba.cut(query))
        score = 0.0
        for word in q_words:
            if word not in self.idf:
                continue
            f = self.doc_freqs[doc_idx].get(word, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[doc_idx] / self.avgdl)
            score += self.idf[word] * (f * (self.k1 + 1)) / denom
        return score

    def search(self, query, top_k=10):
        scores = [(i, self.score(query, i)) for i in range(self.N)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    corpus = ["圆石是破坏石头获得的方块", "钻石是稀有矿物，用于制作钻石镐", "下界合金锭由下界残骸和金锭合成"]
    bm = BM25()
    bm.fit(corpus)
    for q in ["圆石怎么获得", "钻石镐", "下界合金"]:
        print(q, "->", bm.search(q, 3))
