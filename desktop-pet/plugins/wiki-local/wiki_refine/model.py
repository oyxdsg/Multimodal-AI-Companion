# -*- coding: utf-8 -*-
"""精炼模型：字符 n-gram EmbeddingBag + 结构化特征 → 隐层 → 3 类 softmax。

标签：0=know 1=fun 2=drop（见 weak_labels）。
纯 numpy（前向 + 反向 + Adam），训练与推理同一份代码。
"""
import json
import os

import numpy as np

LABELS = ("know", "fun", "drop")
LABEL_ID = {k: i for i, k in enumerate(LABELS)}


class RefineNet:

    def __init__(self, vocab_size, n_struct, emb=32, hidden=64, seed=20260913):
        rng = np.random.default_rng(seed)
        self.vocab_size = int(vocab_size)
        self.n_struct = int(n_struct)
        self.emb = int(emb)
        self.hidden = int(hidden)
        self.n_class = len(LABELS)
        self.W_emb = (rng.normal(0, 0.05, (self.vocab_size, emb))).astype("float32")
        self.W_emb[0] = 0.0
        self.W1 = (rng.normal(0, 0.1, (emb + n_struct, hidden))).astype("float32")
        self.b1 = np.zeros(hidden, dtype="float32")
        self.W2 = (rng.normal(0, 0.1, (hidden, self.n_class))).astype("float32")
        self.b2 = np.zeros(self.n_class, dtype="float32")
        self._m = {}
        self._v = {}
        self._t = 0

    # ---------------- 前向 ----------------

    def forward(self, ids, struct, dropout=0.0, rng=None):
        mask = (ids != 0).astype("float32")
        cnt = np.maximum(mask.sum(axis=1, keepdims=True), 1.0)
        E = self.W_emb[ids]
        pooled = (E * mask[:, :, None]).sum(axis=1) / cnt
        cat = np.concatenate([pooled, struct], axis=1)
        pre = cat @ self.W1 + self.b1
        hid = np.maximum(pre, 0.0)
        keep = None
        if dropout > 0:
            if rng is None:
                rng = np.random.default_rng()
            keep = (rng.random(hid.shape) >= dropout).astype("float32") / (1.0 - dropout)
            hid = hid * keep
        logits = hid @ self.W2 + self.b2
        cache = (ids, mask, cnt, pooled, cat, pre, hid, keep)
        return logits, cache

    # ---------------- 损失/反向 ----------------

    def loss_and_grad(self, ids, struct, y=None, soft_y=None, l2=1e-4,
                      dropout=0.0, rng=None, label_smooth=0.02):
        B = ids.shape[0]
        logits, (ids_, mask, cnt, pooled, cat, pre, hid, keep) = self.forward(
            ids, struct, dropout=dropout, rng=rng)
        z = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(z)
        p = exp / exp.sum(axis=1, keepdims=True)
        C = self.n_class
        if soft_y is not None:
            Y = soft_y.astype("float32")
        else:
            Y = np.full((B, C), label_smooth / C, dtype="float32")
            Y[np.arange(B), y] += (1.0 - label_smooth)
        loss = -(Y * np.log(np.clip(p, 1e-9, 1.0))).sum(axis=1).mean()

        dlogits = (p - Y) / B
        gW2 = hid.T @ dlogits + l2 * self.W2
        gb2 = dlogits.sum(axis=0)
        dhid = dlogits @ self.W2.T
        if keep is not None:
            dhid = dhid * keep
        dpre = dhid * (pre > 0)
        gW1 = cat.T @ dpre + l2 * self.W1
        gb1 = dpre.sum(axis=0)
        # 只回传 embedding 部分（struct 是常量特征，不回传）
        emb = self.emb
        dpooled = dpre @ self.W1[:emb].T
        dE = dpooled[:, None, :] * mask[:, :, None] / cnt[:, :, None]
        flat_ids = ids.reshape(-1)
        flat_dE = dE.reshape(-1, self.emb)
        gEmb = np.zeros_like(self.W_emb)
        for d in range(self.emb):
            gEmb[:, d] = np.bincount(flat_ids, weights=flat_dE[:, d],
                                     minlength=self.vocab_size)
        gEmb[0] = 0.0
        gEmb += l2 * self.W_emb

        grads = {"W_emb": gEmb, "W1": gW1, "b1": gb1, "W2": gW2, "b2": gb2}
        return float(loss), grads

    def adam_step(self, grads, lr=3e-3, b1=0.9, b2=0.999, eps=1e-8):
        self._t += 1
        t = self._t
        params = {"W_emb": self.W_emb, "W1": self.W1, "b1": self.b1,
                  "W2": self.W2, "b2": self.b2}
        for name, p in params.items():
            g = grads[name]
            m = self._m.setdefault(name, np.zeros_like(p))
            v = self._v.setdefault(name, np.zeros_like(p))
            m *= b1
            m += (1 - b1) * g
            v *= b2
            v += (1 - b2) * (g * g)
            mhat = m / (1 - b1 ** t)
            vhat = v / (1 - b2 ** t)
            p -= (lr * mhat / (np.sqrt(vhat) + eps)).astype(p.dtype)

    # ---------------- 推理 ----------------

    def proba(self, ids, struct):
        z, _ = self.forward(ids, struct)
        z = z - z.max(axis=1, keepdims=True)
        exp = np.exp(z)
        return exp / exp.sum(axis=1, keepdims=True)

    # ---------------- 持久化 ----------------

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        np.savez_compressed(
            path,
            W_emb=self.W_emb.astype("float16"),
            W1=self.W1.astype("float16"),
            b1=self.b1.astype("float32"),
            W2=self.W2.astype("float16"),
            b2=self.b2.astype("float32"),
            meta=np.array([json.dumps({
                "vocab_size": self.vocab_size, "n_struct": self.n_struct,
                "emb": self.emb, "hidden": self.hidden}, ensure_ascii=False)]),
        )

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"][0]))
        net = cls(meta["vocab_size"], meta["n_struct"], meta["emb"], meta["hidden"])
        net.W_emb = z["W_emb"].astype("float32")
        net.W1 = z["W1"].astype("float32")
        net.b1 = z["b1"].astype("float32")
        net.W2 = z["W2"].astype("float32")
        net.b2 = z["b2"].astype("float32")
        return net

    @property
    def param_count(self):
        return (self.W_emb.size + self.W1.size + self.b1.size
                + self.W2.size + self.b2.size)
