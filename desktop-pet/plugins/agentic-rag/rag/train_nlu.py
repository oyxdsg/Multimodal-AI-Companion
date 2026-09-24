# -*- coding: utf-8 -*-
"""RAG NLU 小模型训练（复用桌宠 NLU：字符 n-gram + 纯 numpy 网络）。

用法：
    python src/train_nlu.py need       # 训练 need_retrieval 二分类
    python src/train_nlu.py pagetype   # 训练 page_type 多分类
    python src/train_nlu.py both       # 两个都训（默认）

产物（data/nlu/{task}/）：
    vocab.json    字符 n-gram 词表
    model.npz     模型权重（float16，<100KB）
    dataset.jsonl 本次训练语料
    report.json   评估指标
"""

import argparse
import json
import os
import random
import sys
import time

import numpy as np

from .nlu_features import Featurizer
from .nlu_model import IntentNet
from .nlu_tasks import (NEED_KEYS, NEED_LABELS, PAGETYPE_KEYS, PAGETYPE_LABELS,
                       generate_need, generate_pagetype, read_jsonl, write_jsonl)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "nlu")

_TASKS = {
    "need": {"keys": NEED_KEYS, "labels": NEED_LABELS, "gen": generate_need,
             "manual": "manual_need.jsonl", "n_per": 600},
    "pagetype": {"keys": PAGETYPE_KEYS, "labels": PAGETYPE_LABELS, "gen": generate_pagetype,
                 "manual": "manual_pagetype.jsonl", "n_per": 300},
}


def _batches(n, bs, rng):
    idx = rng.permutation(n)
    for i in range(0, n, bs):
        yield idx[i:i + bs]


def evaluate(net, feat, rows, keys):
    if not rows:
        return 0.0, np.array([]), np.array([])
    X = feat.encode_batch([r["text"] for r in rows])
    y = np.array([keys.index(r["intent"]) for r in rows], dtype="int64")
    p = net.proba(X)
    return float((p.argmax(axis=1) == y).mean()), p.argmax(axis=1), p.max(axis=1)


def per_class_report(pred, y, keys, labels):
    out = {}
    for c, k in enumerate(keys):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[k] = {"label": labels.get(k, k), "precision": round(prec, 4),
                  "recall": round(rec, 4), "f1": round(f1, 4),
                  "support": int((y == c).sum())}
    return out


def top_confusions(pred, y, keys, k=10):
    from collections import Counter
    c = Counter()
    for p, t in zip(pred, y):
        if p != t:
            c[(keys[t], keys[p])] += 1
    return [{"true": a, "pred": b, "n": n} for (a, b), n in c.most_common(k)]


def split(rows, val_ratio=0.1, seed=0):
    rng = random.Random(seed)
    rows = list(rows)
    rng.shuffle(rows)
    nv = max(1, int(len(rows) * val_ratio))
    return rows[nv:], rows[:nv]


def train_task(task, epochs=30, bs=128, lr=3e-3, emb=32, hidden=64, dropout=0.15,
               min_count=2, max_features=20000, manual_w=20, seed=20260921,
               out_dir=None, log=print):
    cfg = _TASKS[task]
    keys = cfg["keys"]
    labels = cfg["labels"]
    t0 = time.time()
    log("① 合成语料 …")
    rows = cfg["gen"](n_per=cfg["n_per"], seed=seed)
    tr, va = split(rows, seed=seed + 1)

    # 手工语料并入 train（过采样）
    manual_path = os.path.join(DATA_DIR, cfg["manual"])
    extra = read_jsonl(manual_path)
    extra = [r for r in extra if r.get("text") and r.get("intent") in keys]
    if extra:
        tr = list(tr) + extra * manual_w
        log("   并入手工语料 %d 条 × %d = %d（train=%d）" % (len(extra), manual_w, len(extra) * manual_w, len(tr)))
    log("   train=%d  val=%d  类数=%d" % (len(tr), len(va), len(keys)))

    log("② 构建词表 …")
    feat = Featurizer.build([r["text"] for r in tr], min_count=min_count,
                            max_features=max_features)
    net = IntentNet(feat.size, len(keys), emb=emb, hidden=hidden, seed=seed, labels=keys)
    log("   词表大小=%d" % feat.size)

    X = feat.encode_batch([r["text"] for r in tr])
    Y = np.array([keys.index(r["intent"]) for r in tr], dtype="int64")
    log("③ 训练（epochs=%d, bs=%d, lr=%g, dropout=%g）…" % (epochs, bs, lr, dropout))
    rng = np.random.default_rng(seed + 7)
    best = {"acc": -1.0, "epoch": 0, "state": None}
    hist = []
    for ep in range(1, epochs + 1):
        tot, nb = 0.0, 0
        for bidx in _batches(len(X), bs, rng):
            loss, grads = net.loss_and_grad(X[bidx], Y[bidx], dropout=dropout, rng=rng)
            net.adam_step(grads, lr=lr)
            tot += loss
            nb += 1
        acc, _, _ = evaluate(net, feat, va, keys)
        hist.append({"epoch": ep, "loss": round(tot / max(nb, 1), 4), "val_acc": round(acc, 4)})
        if acc > best["acc"]:
            best = {"acc": acc, "epoch": ep,
                    "state": {k: getattr(net, k).copy() for k in ("W_emb", "W1", "b1", "W2", "b2")}}
        if ep % 5 == 0 or ep == 1 or ep == epochs:
            log("   ep%-3d loss=%.4f  val_acc=%.4f" % (ep, tot / max(nb, 1), acc))
    for k, v in best["state"].items():
        setattr(net, k, v)
    log("   最佳 epoch=%d  val_acc=%.4f" % (best["epoch"], best["acc"]))

    log("④ 评估 …")
    acc, pred, conf = evaluate(net, feat, va, keys)
    y = np.array([keys.index(r["intent"]) for r in va], dtype="int64")
    report = {
        "task": task, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sizes": {"train": len(tr), "val": len(va), "vocab": feat.size, "params": net.param_count},
        "hparams": {"epochs": epochs, "bs": bs, "lr": lr, "emb": emb, "hidden": hidden,
                    "dropout": dropout, "manual_extra": len(extra)},
        "val_acc": round(acc, 4), "best_epoch": best["epoch"], "history": hist,
        "per_class": per_class_report(pred, y, keys, labels),
        "top_confusions": top_confusions(pred, y, keys),
    }
    log("   val_acc=%.4f" % acc)
    worst = sorted(report["per_class"].items(), key=lambda kv: kv[1]["f1"])[:5]
    log("   F1 最低的 5 类：")
    for k, v in worst:
        log("     %-10s F1=%.3f P=%.3f R=%.3f (n=%d)" % (k, v["f1"], v["precision"], v["recall"], v["support"]))

    log("⑤ 导出 …")
    dout = out_dir or os.path.join(DATA_DIR, task)
    os.makedirs(dout, exist_ok=True)
    feat.save(os.path.join(dout, "vocab.json"))
    net.save(os.path.join(dout, "model.npz"))
    write_jsonl(os.path.join(dout, "dataset.jsonl"), rows)
    with open(os.path.join(dout, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(dout, "model.npz")
    log("   %s（%.1f KB）" % (mp, os.path.getsize(mp) / 1024.0))
    log("完成，用时 %.1fs" % (time.time() - t0))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["need", "pagetype", "both"], default="both", nargs="?")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--bs", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--emb", type=int, default=32)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--dropout", type=float, default=0.15)
    ap.add_argument("--manual-w", type=int, default=20)
    args = ap.parse_args()
    tasks = ["need", "pagetype"] if args.task == "both" else [args.task]
    for t in tasks:
        print("\n######## 训练任务: %s ########" % t)
        train_task(t, epochs=args.epochs, bs=args.bs, lr=args.lr, emb=args.emb,
                   hidden=args.hidden, dropout=args.dropout, manual_w=args.manual_w)


if __name__ == "__main__":
    main()
