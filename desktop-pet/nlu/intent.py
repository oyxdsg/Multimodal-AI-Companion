# -*- coding: utf-8 -*-
"""运行时意图推理入口。

职责：加载 ``nlu/data/`` 下的词表与权重，对一段用户输入给出
「意图 + 置信度」，并按阈值决定这轮要不要**自动执行**。

健壮性约定（很重要）：
* 模型/词表缺失、numpy 未安装、任何异常 → :meth:`IntentEngine.predict`
  返回 ``None``，调用方**原样回退给大 AI**，绝不阻断聊天。
* 阈值分两档：``AUTO_THRESHOLD`` 以上且意图允许自动执行 → 执行；
  ``HINT_THRESHOLD`` 以上 → 仅作为提示交给大 AI（不执行）。
"""

import os
import threading

from nlu import taxonomy

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")

# 自动执行阈值：训练报告显示 ≥0.85 时准确率已 ~99.8%，闲聊误激活率接近 0
AUTO_THRESHOLD = 0.85
# 提示阈值：低于此值视为「听不懂」，完全不干预
HINT_THRESHOLD = 0.55


class Prediction:

    __slots__ = ("intent", "label", "confidence", "margin", "probs", "text")

    def __init__(self, intent, confidence, probs, text=""):
        self.intent = intent
        self.label = taxonomy.LABELS.get(intent, intent)
        self.confidence = float(confidence)
        order = sorted(probs.items(), key=lambda kv: -kv[1])
        self.margin = float(order[0][1] - order[1][1]) if len(order) > 1 else 1.0
        self.probs = probs
        self.text = text

    @property
    def auto_ok(self):
        return taxonomy.is_auto_ok(self.intent)

    def topk(self, k=3):
        return sorted(self.probs.items(), key=lambda kv: -kv[1])[:k]

    def __repr__(self):
        return "<Prediction %s %.3f>" % (self.intent, self.confidence)


class IntentEngine:
    """线程安全的推理器（内部只读，可被多线程共享）。"""

    def __init__(self, featurizer, net, data_dir=DATA_DIR):
        self.feat = featurizer
        self.net = net
        self.data_dir = data_dir

    # ---------------- 加载 ----------------

    @classmethod
    def load(cls, data_dir=DATA_DIR):
        from nlu import features
        from nlu.model import IntentNet
        model_path = os.path.join(data_dir, "model.npz")
        vocab_path = os.path.join(data_dir, "vocab.json")
        if not (os.path.isfile(model_path) and os.path.isfile(vocab_path)):
            raise FileNotFoundError("NLU 模型未训练：缺少 %s / %s" % (model_path, vocab_path))
        feat = features.Featurizer.from_json(vocab_path)
        net = IntentNet.load(model_path)
        return cls(feat, net, data_dir)

    # ---------------- 推理 ----------------

    def predict(self, text):
        """返回 :class:`Prediction`；任何异常一律返回 None（调用方回退大 AI）。"""
        text = (text or "").strip()
        if not text:
            return None
        try:
            import numpy as np
            ids = np.asarray([self.feat.encode(text)], dtype="int32")
            p = self.net.proba(ids, temperature=1.0)[0]
            labels = self.net.labels
            probs = {labels[i]: float(p[i]) for i in range(len(labels))}
            best = max(probs.items(), key=lambda kv: kv[1])
            return Prediction(best[0], best[1], probs, text)
        except Exception:
            return None

    def decide(self, text):
        """返回 ``(prediction|None, mode)``，mode ∈ {"auto", "hint", "none"}。"""
        pred = self.predict(text)
        if pred is None or pred.confidence < HINT_THRESHOLD:
            return pred, "none"
        if pred.confidence >= AUTO_THRESHOLD and pred.auto_ok:
            return pred, "auto"
        return pred, "hint"


# ---------------- 全局单例（懒加载 + 只加载一次） ----------------

_LOCK = threading.Lock()
_SHARED = None
_FAILED = False


def shared(data_dir=DATA_DIR):
    """进程内共享的推理器；加载失败返回 None（并记住失败，避免反复重试）。"""
    global _SHARED, _FAILED
    if _SHARED is not None or _FAILED:
        return _SHARED
    with _LOCK:
        if _SHARED is not None or _FAILED:
            return _SHARED
        try:
            _SHARED = IntentEngine.load(data_dir)
        except Exception:
            _FAILED = True
            _SHARED = None
    return _SHARED


def reset_shared():
    """测试用：清空单例。"""
    global _SHARED, _FAILED
    with _LOCK:
        _SHARED = None
        _FAILED = False


def available(data_dir=DATA_DIR):
    return (os.path.isfile(os.path.join(data_dir, "model.npz"))
            and os.path.isfile(os.path.join(data_dir, "vocab.json")))
