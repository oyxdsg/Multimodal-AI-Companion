# -*- coding: utf-8 -*-
"""字符 n-gram 特征器（训练与推理共用，保证两端一致）。

中文短句的意图识别不需要分词——字粒度 1/2/3-gram 已足够区分
「合成/挖矿/攻击」这类动词主导的意图，且天然免去分词器依赖。

表示：每条样本 → 定长索引数组（不足补 0 = PAD）+ 掩码。
"""

import json
import re

PAD = 0          # 0 号位留给 padding
UNK = 1          # 1 号位留给未登录特征
NGRAM_ORDER = (1, 2, 3)

# 归一化：去掉标点/空白/emoji 的干扰，但保留中文、字母、数字
_CLEAN_RE = re.compile(r"[^\u4e00-\u9fff\u3400-\u4dbfa-zA-Z0-9]+")


def normalize(text):
    """归一化：全角转半角、统一小写、去标点（标点对意图无信息）。"""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:            # 全角空格
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out).lower()
    return _CLEAN_RE.sub("", s)


def ngrams(text):
    """返回字符 n-gram 列表（含字本身）。"""
    s = normalize(text)
    if not s:
        return []
    feats = []
    n = len(s)
    for k in NGRAM_ORDER:
        if n < k:
            continue
        for i in range(n - k + 1):
            feats.append(s[i:i + k])
    return feats


class Featurizer:
    """词表 + 编码器。训练时 build，推理时 load。"""

    def __init__(self, vocab=None, max_len=96):
        self.vocab = dict(vocab or {})
        self.max_len = max_len

    # ---- 训练用 ----
    @classmethod
    def build(cls, texts, min_count=3, max_features=40000, max_len=96):
        from collections import Counter
        cnt = Counter()
        for t in texts:
            cnt.update(ngrams(t))
        # 频率降序、长度降序（更长的 n-gram 信息量更大，同频优先保留）
        items = [(f, c) for f, c in cnt.items() if c >= min_count]
        items.sort(key=lambda x: (-x[1], -len(x[0])))
        items = items[:max_features]
        vocab = {f: i + 2 for i, (f, _) in enumerate(items)}
        return cls(vocab, max_len=max_len)

    # ---- 编码 ----
    def encode(self, text, max_len=None):
        """返回定长 list[int]（PAD 0 填充）。"""
        L = max_len or self.max_len
        ids = [self.vocab.get(f, UNK) for f in ngrams(text)]
        if len(ids) > L:
            # 截断：保留头尾（动词常在句首，目标在句尾）
            head = L // 2
            ids = ids[:head] + ids[-(L - head):]
        ids += [PAD] * (L - len(ids))
        return ids

    def encode_batch(self, texts):
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("nlu 需要 numpy：pip install numpy") from exc
        return np.asarray([self.encode(t) for t in texts], dtype="int32")

    # ---- 持久化 ----
    def to_json(self):
        return {"max_len": self.max_len, "ngram_order": list(NGRAM_ORDER),
                "vocab": self.vocab}

    @classmethod
    def from_json(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("vocab") or {}, max_len=data.get("max_len", 96))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, ensure_ascii=False, separators=(",", ":"))

    @property
    def size(self):
        return len(self.vocab) + 2
