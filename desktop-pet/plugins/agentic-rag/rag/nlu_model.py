# -*- coding: utf-8 -*-
"""意图识别小模型：字符 n-gram → EmbeddingBag(均值池化) → 隐层 → softmax。

纯 numpy 实现（前向 + 反向 + Adam），**训练与推理同一份代码**：

* 训练：离线跑训练脚本（只有 numpy 依赖）
* 推理：加载导出的 ``model.npz``，毫秒级、零框架依赖

权重导出用 float16 存储、加载时转 float32，体积约为 fp32 的一半。
（从 desktop-pet/nlu/model.py 复制，去掉 taxonomy 依赖，labels 由外部传入）
"""

import json
import os

import numpy as np


class IntentNet:

    def __init__(self, vocab_size, n_class, emb=32, hidden=64, seed=20260911,
                 labels=None):
        rng = np.random.default_rng(seed)
        self.vocab_size = int(vocab_size)
        self.n_class = int(n_class)
        self.emb = int(emb)
        self.hidden = int(hidden)
        # 词嵌入：PAD/UNK 行初始化为 0，不参与梯度（PAD 由 mask 排除，UNK 可训）
        self.W_emb = (rng.normal(0, 0.05, (self.vocab_size, emb))).astype("float32")
        self.W_emb[0] = 0.0
        self.W1 = (rng.normal(0, 0.1, (emb, hidden))).astype("float32")
        self.b1 = np.zeros(hidden, dtype="float32")
        self.W2 = (rng.normal(0, 0.1, (hidden, self.n_class))).astype("float32")
        self.b2 = np.zeros(self.n_class, dtype="float32")
        self.labels = list(labels or [])
        # Adam 状态
        self._m = {}
        self._v = {}
        self._t = 0

    # ---------------------------------------------------------- 前向

    def forward(self, ids, training=False, dropout=0.0, rng=None):
        mask = (ids != 0).astype("float32")            # (B, L)
        cnt = mask.sum(axis=1, keepdims=True)
        cnt = np.maximum(cnt, 1.0)
        E = self.W_emb[ids]                            # (B, L, D)
        pooled = (E * mask[:, :, None]).sum(axis=1) / cnt   # (B, D)
        pre = pooled @ self.W1 + self.b1               # (B, H)
        hid = np.maximum(pre, 0.0)
        if training and dropout > 0:
            if rng is None:
                rng = np.random.default_rng()
            keep = (rng.random(hid.shape) >= dropout).astype("float32") / (1.0 - dropout)
            hid = hid * keep
        else:
            keep = None
        logits = hid @ self.W2 + self.b2               # (B, C)
        cache = (ids, mask, cnt, pooled, pre, hid, keep)
        return logits, cache

    # ---------------------------------------------------------- 损失/反向

    def loss_and_grad(self, ids, y=None, soft_y=None, l2=1e-4, dropout=0.0,
                      rng=None, label_smooth=0.02):
        """交叉熵（硬标签）或蒸馏损失（软标签 soft_y，老师概率分布）。

        ``soft_y`` 为 (B, C) 的归一化概率时走蒸馏：``dlogits = (p - soft_y)/B``，
        让学生拟合老师的软判断（学会类间相似性），反向与硬标签完全相同。
        """
        B = ids.shape[0]
        logits, (ids_, mask, cnt, pooled, pre, hid, keep) = self.forward(
            ids, training=(dropout > 0), dropout=dropout, rng=rng)
        # softmax + 交叉熵（含标签平滑）
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

        # 反向
        dlogits = (p - Y) / B                                  # (B, C)
        gW2 = hid.T @ dlogits + l2 * self.W2
        gb2 = dlogits.sum(axis=0)
        dhid = dlogits @ self.W2.T
        if keep is not None:
            dhid = dhid * keep
        dpre = dhid * (pre > 0)
        gW1 = pooled.T @ dpre + l2 * self.W1
        gb1 = dpre.sum(axis=0)
        dpooled = dpre @ self.W1.T                             # (B, D)
        dE = dpooled[:, None, :] * mask[:, :, None] / cnt[:, :, None]
        # 词嵌入梯度：按特征 id 散射累加。np.add.at 太慢，改用 bincount
        # （C 实现，逐维加权累加，实测比 add.at 快一个量级）。
        flat_ids = ids.reshape(-1)
        flat_dE = dE.reshape(-1, self.emb)
        gEmb = np.zeros_like(self.W_emb)
        for d in range(self.emb):
            gEmb[:, d] = np.bincount(flat_ids, weights=flat_dE[:, d],
                                     minlength=self.vocab_size)
        gEmb[0] = 0.0                                          # PAD 不更新
        gEmb += l2 * self.W_emb

        grads = {"W_emb": gEmb, "W1": gW1, "b1": gb1, "W2": gW2, "b2": gb2}
        return float(loss), grads

    # ---------------------------------------------------------- 优化器

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

    # ---------------------------------------------------------- 推理

    def logits(self, ids):
        out, _ = self.forward(ids, training=False)
        return out

    def proba(self, ids, temperature=1.0):
        z = self.logits(ids) / max(temperature, 1e-6)
        z = z - z.max(axis=1, keepdims=True)
        exp = np.exp(z)
        return exp / exp.sum(axis=1, keepdims=True)

    # ---------------------------------------------------------- 持久化

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
                "vocab_size": self.vocab_size, "n_class": self.n_class,
                "emb": self.emb, "hidden": self.hidden,
                "labels": self.labels}, ensure_ascii=False)]),
        )

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta"][0]))
        net = cls(meta["vocab_size"], meta["n_class"], meta["emb"], meta["hidden"])
        net.W_emb = z["W_emb"].astype("float32")
        net.W1 = z["W1"].astype("float32")
        net.b1 = z["b1"].astype("float32")
        net.W2 = z["W2"].astype("float32")
        net.b2 = z["b2"].astype("float32")
        net.labels = list(meta.get("labels") or [])
        return net

    @property
    def param_count(self):
        return (self.W_emb.size + self.W1.size + self.b1.size
                + self.W2.size + self.b2.size)
