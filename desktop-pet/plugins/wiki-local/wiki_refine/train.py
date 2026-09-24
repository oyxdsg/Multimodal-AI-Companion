# -*- coding: utf-8 -*-
"""训练精炼模型（3 类：know/fun/drop）。

用法::

    python -m nlu.wiki_refine.train --epochs 12 --bs 256

数据：nlu/wiki_refine/data/seed.jsonl（全量弱标签）+ curated.jsonl（校准）。
fun 过采样缓解类别不平衡（fun 仅 ~2.4%）。
产物：nlu/wiki_refine/data/{vocab.json, model.npz, report.json}
"""
import argparse
import json
import os
import time

import numpy as np

from wiki_refine import features, model as rm
from wiki_refine import weak_labels

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")

FUN_WEIGHT = 10      # fun 过采样倍数
VAL_RATIO = 0.02     # seed 留出做验证
SEED = 20260913


def read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _batches(n, bs, rng):
    idx = rng.permutation(n)
    for i in range(0, n, bs):
        yield idx[i:i + bs]


def encode_rows(feat, rows):
    ids, struct = [], []
    for r in rows:
        i, s = feat.encode(r)
        ids.append(i)
        struct.append(s)
    return (np.asarray(ids, dtype="int32"),
            np.asarray(struct, dtype="float32"))


def eval_net(net, feat, rows, bs=512):
    if not rows:
        return 0.0, {}
    ids, struct = encode_rows(feat, rows)
    y = np.array([rm.LABEL_ID[r["label"]] for r in rows], dtype="int64")
    preds, confs = [], []
    for i in range(0, len(rows), bs):
        p = net.proba(ids[i:i + bs], struct[i:i + bs])
        preds.append(p.argmax(axis=1))
        confs.append(p.max(axis=1))
    pred = np.concatenate(preds)
    conf = np.concatenate(confs)
    acc = float((pred == y).mean())
    rep = {}
    for c, name in enumerate(rm.LABELS):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rep[name] = {"precision": round(prec, 4), "recall": round(rec, 4),
                     "f1": round(f1, 4), "support": int((y == c).sum())}
    return acc, rep, conf, y, pred


def train(epochs=12, bs=256, lr=3e-3, emb=32, hidden=64, dropout=0.15,
          out_dir=DATA_DIR, log=print):
    t0 = time.time()
    seed_rows = read_jsonl(os.path.join(out_dir, "seed.jsonl"))
    curated = read_jsonl(os.path.join(out_dir, "curated.jsonl"))
    # 校准集并入训练（修正后的 label 优先）
    curated = [r for r in curated if r.get("label")]
    log("① 数据：seed=%d 条 + curated=%d 条" % (len(seed_rows), len(curated)))
    if not seed_rows:
        log("缺少 seed.jsonl，先运行 python -m nlu.wiki_refine.build_data")
        return None

    rng = np.random.default_rng(SEED)
    rng.shuffle(seed_rows)
    n_val = max(int(len(seed_rows) * VAL_RATIO), 200)
    va_rows = seed_rows[:n_val]
    tr_rows = seed_rows[n_val:]
    # fun 过采样
    fun = [r for r in tr_rows if r["label"] == "fun"]
    other = [r for r in tr_rows if r["label"] != "fun"]
    tr_rows = other + fun * FUN_WEIGHT
    rng.shuffle(tr_rows)
    log("   训练 %d（fun ×%d=%d）/ 验证 %d / curated %d"
        % (len(tr_rows), FUN_WEIGHT, len(fun) * FUN_WEIGHT, len(va_rows),
           len(curated)))

    log("② 构建词表 …")
    feat = features.Featurizer.build(
        [r["text"] for r in tr_rows + va_rows], min_count=3, max_features=40000)
    log("   词表=%d，结构特征=%d" % (feat.size, features.struct_dim()))

    log("③ 训练（epochs=%d, bs=%d, lr=%g）…" % (epochs, bs, lr))
    net = rm.RefineNet(feat.size, features.struct_dim(), emb=emb, hidden=hidden,
                       seed=SEED)
    Xt, St = encode_rows(feat, tr_rows)
    Yt = np.array([rm.LABEL_ID[r["label"]] for r in tr_rows], dtype="int64")
    best = {"acc": -1.0, "epoch": 0, "state": None}
    for ep in range(1, epochs + 1):
        tot, nb = 0.0, 0
        for bidx in _batches(len(tr_rows), bs, rng):
            loss, grads = net.loss_and_grad(Xt[bidx], St[bidx], Yt[bidx],
                                            dropout=dropout, rng=rng)
            net.adam_step(grads, lr=lr)
            tot += loss
            nb += 1
        acc, _rep, _c, _y, _p = eval_net(net, feat, va_rows)
        if acc > best["acc"]:
            best = {"acc": acc, "epoch": ep,
                    "state": {k: getattr(net, k).copy() for k in
                              ("W_emb", "W1", "b1", "W2", "b2")}}
        if ep % 2 == 0 or ep == epochs:
            log("   ep%-3d loss=%.4f  val_acc=%.4f  funF1=%.3f"
                % (ep, tot / max(nb, 1), acc, _rep["fun"]["f1"]))
    for k, v in best["state"].items():
        setattr(net, k, v)
    log("   最佳 epoch=%d  val_acc=%.4f" % (best["epoch"], best["acc"]))

    log("④ 评估 …")
    val_acc, per = eval_net(net, feat, va_rows)[:2]
    cur_acc, cur_per = eval_net(net, feat, curated)[:2]
    log("   验证集 acc=%.4f %s" % (val_acc, per))
    log("   校准集 acc=%.4f %s" % (cur_acc, cur_per))

    log("⑤ 导出 …")
    os.makedirs(out_dir, exist_ok=True)
    feat.save(os.path.join(out_dir, "vocab.json"))
    net.save(os.path.join(out_dir, "model.npz"))
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sizes": {"train": len(tr_rows), "val": len(va_rows),
                  "curated": len(curated), "vocab": feat.size,
                  "params": net.param_count, "fun_weight": FUN_WEIGHT},
        "hparams": {"epochs": epochs, "bs": bs, "lr": lr, "emb": emb,
                    "hidden": hidden, "dropout": dropout},
        "val_acc": round(val_acc, 4),
        "per_class": per,
        "curated_acc": round(cur_acc, 4),
        "curated_per_class": cur_per,
        "best_epoch": best["epoch"],
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "model.npz")
    log("   %s（%.1f KB）" % (mp, os.path.getsize(mp) / 1024.0))
    log("完成，用时 %.1fs" % (time.time() - t0))
    return report


def main():
    ap = argparse.ArgumentParser(description="训练 wiki 精炼模型")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--bs", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--emb", type=int, default=32)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--dropout", type=float, default=0.15)
    ap.add_argument("--out", default=DATA_DIR)
    args = ap.parse_args()
    train(epochs=args.epochs, bs=args.bs, lr=args.lr, emb=args.emb,
          hidden=args.hidden, dropout=args.dropout, out_dir=args.out)


if __name__ == "__main__":
    main()
