# -*- coding: utf-8 -*-
"""字符 n-gram 特征（原 nlu/features.py 的最小复制）。

wiki-local 插件自带一份，避免依赖宿主 ``nlu`` 包。
"""

import re

NGRAM_ORDER = (1, 2, 3)

# 归一化：去标点/空白/emoji，保留汉字、字母、数字
_CLEAN_RE = re.compile(r"[^\u4e00-\u9fff\u3400-\u4dbfa-zA-Z0-9]+")


def normalize(text):
    """归一化：全角转半角、统一小写、去标点。"""
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
