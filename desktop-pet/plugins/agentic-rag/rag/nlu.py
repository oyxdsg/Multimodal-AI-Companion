# -*- coding: utf-8 -*-
"""NLU 小模型推理：加载 data/nlu/{task}/ 的 vocab.json + model.npz 做预测。

零框架依赖（纯 numpy），毫秒级。
"""

import json
import os

import numpy as np

from .nlu_features import Featurizer
from .nlu_model import IntentNet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "nlu")

_cache = {}


def _load(task):
    if task in _cache:
        return _cache[task]
    d = os.path.join(DATA_DIR, task)
    feat = Featurizer.from_json(os.path.join(d, "vocab.json"))
    net = IntentNet.load(os.path.join(d, "model.npz"))
    _cache[task] = (feat, net)
    return feat, net


def predict(task, text, top=3):
    """返回 [(label, prob), ...] 按概率降序。"""
    feat, net = _load(task)
    ids = feat.encode(text)
    X = np.asarray([ids], dtype="int32")
    p = net.proba(X)[0]
    order = np.argsort(p)[::-1][:top]
    return [(net.labels[i], float(p[i])) for i in order]


def need_retrieval(text, thr=0.7):
    """是否需要检索。返回 (label, prob)。label in {need, no_need}。"""
    preds = predict("need", text, top=1)
    label, prob = preds[0]
    return label, prob


def infer_page_type(text, thr=0.5):
    """推断 page_type。返回 label（str）或 None（低于阈值/other）。"""
    preds = predict("pagetype", text, top=1)
    label, prob = preds[0]
    if prob < thr or label == "other":
        return None
    return label


if __name__ == "__main__":
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    tests = [
        "僵尸在哪里生成", "钻石镐怎么合成", "你好呀", "下界传送门怎么做",
        "红石中继器是什么", "末影之眼有什么用", "谢谢", "沙漠有什么群系",
        "1.20更新了什么", "这个圆石", "铁傀儡怎么制作",
    ]
    print("== need ==")
    for t in tests:
        print("  %-18s -> %s" % (t, need_retrieval(t)))
    print("\n== pagetype ==")
    for t in tests:
        print("  %-18s -> %s" % (t, infer_page_type(t)))
