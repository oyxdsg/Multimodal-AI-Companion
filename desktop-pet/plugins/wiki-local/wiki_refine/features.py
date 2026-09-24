# -*- coding: utf-8 -*-
"""精炼模型特征器：字符 n-gram + 结构化特征（小节类型/位置/配方行…）。

与意图模型（nlu.features）同风格：纯 numpy、训练与推理同一份代码。
"""
import json
import re

from wi_ngrams import ngrams as _wi_ngrams

PAD = 0
UNK = 1
MAX_LEN = 48

# 小节类型（来自小节链）→ 下标；未知 → last
_SEC_TYPES = ("引言", "合成", "获取", "掉落", "行为", "用途", "其他")
_SEC_TYPE_RE = [
    ("引言", lambda c: c == ["引言"]),
    ("合成", lambda c: any(re.search(r"合成|配方|制作", s) for s in c)),
    ("获取", lambda c: any(re.search(r"获取|获得|得到|来源", s) for s in c)),
    ("掉落", lambda c: any(re.search(r"掉落|战利品", s) for s in c)),
    ("行为", lambda c: any(re.search(r"行为|习性|AI", s) for s in c)),
    ("用途", lambda c: any(re.search(r"用途|使用|作用", s) for s in c)),
]

_RECIPE_RE = re.compile(r"合成配方|→|\d\s*\+\s*\d")


def sec_type(chain):
    for name, pred in _SEC_TYPE_RE:
        if pred(list(chain or [])):
            return name
    return "其他"


def struct_dim():
    return len(_SEC_TYPES) + 4   # 小节类型 + 是否配方 + 是否引言 + 长度 + 位置


def ngrams(text):
    return _wi_ngrams(text)


class Featurizer:
    """词表 + 编码器（n-gram ids + 结构化特征向量）。"""

    def __init__(self, vocab=None, max_len=MAX_LEN):
        self.vocab = dict(vocab or {})
        self.max_len = max_len

    @classmethod
    def build(cls, texts, min_count=3, max_features=40000, max_len=MAX_LEN):
        from collections import Counter
        cnt = Counter()
        for t in texts:
            cnt.update(ngrams(t))
        items = [(f, c) for f, c in cnt.items() if c >= min_count]
        items.sort(key=lambda x: (-x[1], -len(x[0])))
        items = items[:max_features]
        vocab = {f: i + 2 for i, (f, _) in enumerate(items)}
        return cls(vocab, max_len=max_len)

    def encode_ids(self, text):
        L = self.max_len
        ids = [self.vocab.get(f, UNK) for f in ngrams(text)]
        if len(ids) > L:
            head = L // 2
            ids = ids[:head] + ids[-(L - head):]
        ids += [PAD] * (L - len(ids))
        return ids

    def struct(self, sec_chain, text, pos_ratio):
        """结构化特征向量（归一化到 0~1，除 one-hot）。"""
        st = sec_type(sec_chain)
        si = _SEC_TYPES.index(st) if st in _SEC_TYPES else len(_SEC_TYPES) - 1
        out = [0.0] * len(_SEC_TYPES)
        out[si] = 1.0
        out += [
            1.0 if _RECIPE_RE.search(text) else 0.0,
            1.0 if list(sec_chain) == ["引言"] else 0.0,
            min(1.0, len(text) / self.max_len),
            min(1.0, max(0.0, pos_ratio)),
        ]
        return out

    def encode(self, row, text=None, pos_ratio=0.0):
        """row: {"sec":"小节链/", "text":句} → (ids, struct)。"""
        text = text or (row.get("text") or "")
        chain = (row.get("sec") or "").split("/")
        return self.encode_ids(text), self.struct(chain, text, pos_ratio)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"max_len": self.max_len, "vocab": self.vocab},
                      f, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("vocab") or {}, max_len=data.get("max_len", MAX_LEN))

    @property
    def size(self):
        return len(self.vocab) + 2
