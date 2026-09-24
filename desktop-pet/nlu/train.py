# -*- coding: utf-8 -*-
"""意图识别模型的离线训练 / 评估 / 导出。

用法（在 ``desktop-pet/`` 目录下）::

    python -m nlu.train                 # 合成语料 + 训练 + 评估 + 导出
    python -m nlu.train --epochs 40 --n-per-intent 2500

产物（默认落在 ``nlu/data/``）::

    vocab.json    字符 n-gram 词表
    model.npz     模型权重（float16 压缩）
    dataset.jsonl 本次使用的训练语料（可读、可复用、便于回归）
    report.json   评估指标（准确率 / 每类 P-R-F1 / 混淆对）
"""

import argparse
import json
import os
import time

import numpy as np

from nlu import features, synth, taxonomy
from nlu.model import IntentNet

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")


def _batches(n, bs, rng):
    idx = rng.permutation(n)
    for i in range(0, n, bs):
        yield idx[i:i + bs]


def evaluate(net, feat, rows, batch=512):
    """返回 (准确率, 预测数组, 最高置信度数组)。"""
    if not rows:
        return 0.0, np.array([]), np.array([])
    X = feat.encode_batch([r["text"] for r in rows])
    y = np.array([taxonomy.KEYS.index(r["intent"]) for r in rows], dtype="int64")
    preds, confs = [], []
    for i in range(0, len(rows), batch):
        p = net.proba(X[i:i + batch])
        preds.append(p.argmax(axis=1))
        confs.append(p.max(axis=1))
    pred = np.concatenate(preds)
    conf = np.concatenate(confs)
    return float((pred == y).mean()), pred, conf


def per_class_report(pred, y):
    C = len(taxonomy.KEYS)
    out = {}
    for c in range(C):
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[taxonomy.KEYS[c]] = {"label": taxonomy.LABELS[taxonomy.KEYS[c]],
                                 "precision": round(prec, 4),
                                 "recall": round(rec, 4),
                                 "f1": round(f1, 4),
                                 "support": int((y == c).sum())}
    return out


def top_confusions(pred, y, k=15):
    from collections import Counter
    c = Counter()
    for p, t in zip(pred, y):
        if p != t:
            c[(taxonomy.KEYS[t], taxonomy.KEYS[p])] += 1
    return [{"true": a, "pred": b, "n": n} for (a, b), n in c.most_common(k)]


def _soft_matrix(rows):
    """把带 ``soft`` 字段的样本行变成 (B, C) 的归一化概率矩阵。"""
    C = len(taxonomy.KEYS)
    m = np.zeros((len(rows), C), dtype="float32")
    for i, r in enumerate(rows):
        for k, v in (r.get("soft") or {}).items():
            try:
                j = taxonomy.KEYS.index(k)
            except ValueError:
                continue
            m[i, j] = v
    s = m.sum(axis=1, keepdims=True)
    m = m / np.maximum(s, 1e-9)
    return m


def _load_test_texts(path):
    """读人工测试集句子集合（用于训练时**自动去泄漏**）。"""
    if not path or not os.path.isfile(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {c["text"].strip() for c in json.load(f) if c.get("text")}
    except Exception:
        return set()


def _drop_leak(rows, test_texts, tag, log, short_exempt=3):
    """剔除与测试集**逐字相同**的训练样本（极短命令除外，见 tools/check_leak.py）。

    测试集若混进训练集，准确率会虚高十来个点而产品毫无改善——
    这里做自动防护，并把剔除条数打进日志。
    """
    if not test_texts:
        return rows
    kept, dropped = [], []
    for r in rows:
        t = (r.get("text") or "").strip()
        if t in test_texts and len(t) > short_exempt:
            dropped.append(t)
        else:
            kept.append(r)
    if dropped:
        log("   [去泄漏] %s 剔除 %d 条与测试集逐字相同的样本：%s"
            % (tag, len(dropped), "；".join(dropped[:6])))
    return kept


def manual_eval(net, feat, path, auto_thr=0.85):
    """在**人工测试集**（真实口吻、含对抗样本）上评测。

    合成验证集的分句规则与训练语料同源，准确率天然虚高；真实语句才是
    产品指标。这里把两个数一起报出来，避免被 100% 迷惑。
    """
    with open(path, "r", encoding="utf-8") as f:
        cases = json.load(f)
    cases = [c for c in cases
             if c.get("text") and c.get("intent") in taxonomy.KEYS]
    if not cases:
        return None
    acc, pred, conf = evaluate(net, feat, cases, batch=64)
    y = np.array([taxonomy.KEYS.index(c["intent"]) for c in cases], dtype="int64")
    auto_mask = np.array([taxonomy.is_auto_ok(k) for k in taxonomy.KEYS])
    chat_i = taxonomy.KEYS.index("chat")
    am = (conf >= auto_thr) & auto_mask[pred]
    fa = (y == chat_i) & (conf >= auto_thr) & auto_mask[pred]
    by_intent = {}
    for c in range(len(taxonomy.KEYS)):
        m = (y == c)
        if m.any():
            by_intent[taxonomy.KEYS[c]] = {
                "correct": int(((pred == c) & m).sum()), "total": int(m.sum())}
    errors = []
    for i, c in enumerate(cases):
        if pred[i] != y[i]:
            errors.append({"text": c["text"], "true": c["intent"],
                           "pred": taxonomy.KEYS[pred[i]],
                           "conf": round(float(conf[i]), 3),
                           "note": c.get("note", "")})
    return {
        "file": os.path.basename(path),
        "total": len(cases),
        "accuracy": round(acc, 4),
        "auto_hits": int(am.sum()),
        "auto_accuracy": round(float((pred[am] == y[am]).mean()), 4) if am.any() else 0.0,
        "chat_false_activation": int(fa.sum()),
        "by_intent": by_intent,
        "errors": errors,
    }


def train(epochs=30, n_per_intent=1800, bs=128, lr=3e-3, emb=32, hidden=64,
          dropout=0.15, noise=0.5, min_count=3, max_features=40000,
          seed=20260911, out_dir=DATA_DIR, log=print,
          manual=None, manual_w=20, distill=None, distill_w=10,
          resume_dir=None, manual_test=None, hard_weight=0.25):
    t0 = time.time()
    log("① 合成语料 …")
    test_texts = _load_test_texts(manual_test)
    rows = synth.generate(n_per_intent=n_per_intent, seed=seed, noise=noise,
                          hard_weight=hard_weight)
    rows = _drop_leak(rows, test_texts, "合成语料", log)
    tr, va = synth.split(rows, val_ratio=0.1, seed=seed + 1)

    # 并入人工训练数据（真实口吻样本）与教师蒸馏数据（可含软标签），过采样放大权重。
    # 只进 train，不进 val —— 保证验证集仍是纯合成分布。
    extra_n = 0
    distill_n = 0
    if manual and os.path.isfile(manual):
        extra = synth.read_jsonl(manual)
        extra = [r for r in extra if r.get("text") and r.get("intent")]
        extra = _drop_leak(extra, test_texts, "人工语料", log)
        if extra:
            extra_n = len(extra)
            tr = list(tr) + extra * manual_w
            log("   并入人工语料 %d 条 × %d = %d 条（train=%d）"
                % (extra_n, manual_w, extra_n * manual_w, len(tr)))
    if distill and os.path.isfile(distill):
        dset = synth.read_jsonl(distill)
        dset = [r for r in dset if r.get("text") and r.get("intent")]
        dset = _drop_leak(dset, test_texts, "蒸馏语料", log)
        if dset:
            distill_n = len(dset)
            tr = list(tr) + dset * distill_w
            log("   并入蒸馏语料 %d 条 × %d = %d 条（train=%d）"
                % (distill_n, distill_w, distill_n * distill_w, len(tr)))
    log("   train=%d  val=%d  意图数=%d" % (len(tr), len(va), len(taxonomy.KEYS)))

    log("② 构建字符 n-gram 词表 …")
    if resume_dir:
        vocab_path = os.path.join(resume_dir, "vocab.json")
        model_path = os.path.join(resume_dir, "model.npz")
        feat = features.Featurizer.from_json(vocab_path)
        net = IntentNet.load(model_path)
        log("   从 %s 恢复：词表=%d（复用，保证权重对齐）" % (resume_dir, feat.size))
    else:
        feat = features.Featurizer.build(
            [r["text"] for r in tr], min_count=min_count, max_features=max_features)
        net = IntentNet(feat.size, len(taxonomy.KEYS), emb=emb, hidden=hidden, seed=seed)
        log("   词表大小=%d（含 PAD/UNK）" % feat.size)

    # 训练样本分两组：硬标签（交叉熵）+ 软标签（蒸馏损失）
    tr = list(tr)
    hard_rows = [r for r in tr if not r.get("soft")]
    soft_rows = [r for r in tr if r.get("soft")]
    Xh = feat.encode_batch([r["text"] for r in hard_rows])
    Yh = np.array([taxonomy.KEYS.index(r["intent"]) for r in hard_rows], dtype="int64")
    Xs = feat.encode_batch([r["text"] for r in soft_rows])
    Ys = _soft_matrix(soft_rows)
    log("   硬标签 %d 条 + 软标签 %d 条" % (len(hard_rows), len(soft_rows)))

    log("③ 训练（epochs=%d, bs=%d, lr=%g, dropout=%g%s）…"
        % (epochs, bs, lr, dropout, "，resume 微调" if resume_dir else ""))
    rng = np.random.default_rng(seed + 7)
    best = {"acc": -1.0, "epoch": 0, "state": None}
    hist = []
    for ep in range(1, epochs + 1):
        tot, nb = 0.0, 0
        # 硬标签批次
        for bidx in _batches(len(Xh), bs, rng):
            loss, grads = net.loss_and_grad(Xh[bidx], Yh[bidx], dropout=dropout, rng=rng)
            net.adam_step(grads, lr=lr)
            tot += loss
            nb += 1
        # 软标签批次（蒸馏）
        for bidx in _batches(len(Xs), bs, rng):
            loss, grads = net.loss_and_grad(Xs[bidx], soft_y=Ys[bidx],
                                            dropout=dropout, rng=rng)
            net.adam_step(grads, lr=lr)
            tot += loss
            nb += 1
        acc, _, _ = evaluate(net, feat, va)
        hist.append({"epoch": ep, "loss": round(tot / max(nb, 1), 4), "val_acc": round(acc, 4)})
        if acc > best["acc"]:
            best = {"acc": acc, "epoch": ep,
                    "state": {k: getattr(net, k).copy() for k in
                              ("W_emb", "W1", "b1", "W2", "b2")}}
        if ep % 5 == 0 or ep == 1 or ep == epochs:
            log("   ep%-3d loss=%.4f  val_acc=%.4f" % (ep, tot / max(nb, 1), acc))

    # 回滚到最佳
    for k, v in best["state"].items():
        setattr(net, k, v)
    log("   最佳 epoch=%d  val_acc=%.4f" % (best["epoch"], best["acc"]))

    log("④ 评估 …")
    acc, pred, conf = evaluate(net, feat, va)
    y = np.array([taxonomy.KEYS.index(r["intent"]) for r in va], dtype="int64")
    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sizes": {"train": len(tr), "val": len(va), "vocab": feat.size,
                  "params": net.param_count,
                  "manual_extra": extra_n, "manual_weight": manual_w,
                  "distill_extra": distill_n, "distill_weight": distill_w,
                  "soft_rows": len(soft_rows)},
        "hparams": {"epochs": epochs, "bs": bs, "lr": lr, "emb": emb,
                    "hidden": hidden, "dropout": dropout, "noise": noise,
                    "resume": bool(resume_dir)},
        "val_acc": round(acc, 4),
        "best_epoch": best["epoch"],
        "history": hist,
        "per_class": per_class_report(pred, y),
        "top_confusions": top_confusions(pred, y),
        "thresholds": {},
    }
    # 置信度分档：用于运行时「要不要自动执行」的阈值选择。
    # 关键风险指标是 chat_false_activation —— 真闲聊被判成「可自动执行的指令」，
    # 会导致桌宠乱指挥女仆；这个数必须压到接近 0。
    chat_i = taxonomy.KEYS.index("chat")
    is_chat = (y == chat_i)
    auto_mask = np.array([taxonomy.is_auto_ok(k) for k in taxonomy.KEYS])
    for thr in (0.5, 0.7, 0.85, 0.9):
        m = conf >= thr
        cov = float(m.mean())
        prec = float((pred[m] == y[m]).mean()) if m.any() else 0.0
        am = m & auto_mask[pred]
        auto_prec = float((pred[am] == y[am]).mean()) if am.any() else 0.0
        fa = is_chat & m & auto_mask[pred]
        report["thresholds"]["%.2f" % thr] = {
            "coverage": round(cov, 4), "accuracy": round(prec, 4),
            "auto_coverage": round(float(am.mean()), 4),
            "auto_precision": round(auto_prec, 4),
            "chat_false_activation": round(float(fa.sum()) / max(int(is_chat.sum()), 1), 4)}

    log("   val_acc=%.4f" % acc)
    for t, v in report["thresholds"].items():
        log("   置信度≥%s：覆盖 %.1f%% / 准确 %.4f ｜ 可自动执行占 %.1f%%、其中准确 %.4f ｜ "
            "闲聊误激活 %.2f%%"
            % (t, v["coverage"] * 100, v["accuracy"],
               v["auto_coverage"] * 100, v["auto_precision"],
               v["chat_false_activation"] * 100))
    log("   前若干混淆对：")
    for c in report["top_confusions"][:8]:
        log("     %s → %s  ×%d" % (c["true"], c["pred"], c["n"]))
    worst = sorted(report["per_class"].items(), key=lambda kv: kv[1]["f1"])[:5]
    log("   F1 最低的 5 类：")
    for k, v in worst:
        log("     %-10s F1=%.3f  P=%.3f R=%.3f (n=%d)"
            % (k, v["f1"], v["precision"], v["recall"], v["support"]))

    # ---- 真实语句（人工测试集）：产品意义上的准确率 ----
    if manual_test and os.path.isfile(manual_test):
        log("④b 人工测试集评测（真实口吻，产品指标）…")
        me = manual_eval(net, feat, manual_test)
        if me:
            report["manual_eval"] = me
            log("   准确率 %.2f%%（%d/%d）｜可自动执行 %d 条、其中准确 %.2f%% ｜ 闲聊误激活 %d 条"
                % (me["accuracy"] * 100, int(round(me["accuracy"] * me["total"])),
                   me["total"], me["auto_hits"], me["auto_accuracy"] * 100,
                   me["chat_false_activation"]))
            bad = sorted(me["by_intent"].items(),
                         key=lambda kv: kv[1]["correct"] / max(kv[1]["total"], 1))[:6]
            log("   最弱的类：" + "、".join(
                "%s %d/%d" % (k, v["correct"], v["total"]) for k, v in bad))

    log("⑤ 导出 …")
    os.makedirs(out_dir, exist_ok=True)
    feat.save(os.path.join(out_dir, "vocab.json"))
    net.save(os.path.join(out_dir, "model.npz"))
    synth.write_jsonl(os.path.join(out_dir, "dataset.jsonl"), rows)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "model.npz")
    log("   %s（%.1f KB）" % (mp, os.path.getsize(mp) / 1024.0))
    log("完成，用时 %.1fs" % (time.time() - t0))
    return report


def main():
    ap = argparse.ArgumentParser(description="意图识别模型训练")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--n-per-intent", type=int, default=1800)
    ap.add_argument("--bs", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--emb", type=int, default=32)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--dropout", type=float, default=0.15)
    ap.add_argument("--noise", type=float, default=0.5)
    ap.add_argument("--min-count", type=int, default=4)
    ap.add_argument("--max-features", type=int, default=24000)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--out", default=DATA_DIR)
    ap.add_argument("--manual", default=None,
                    help="人工训练语料 jsonl（存在则并入 train 并过采样）")
    ap.add_argument("--manual-w", type=int, default=20,
                    help="人工样本过采样倍数（默认 20）")
    ap.add_argument("--distill", default=None,
                    help="教师蒸馏语料 jsonl（含 soft 字段的走蒸馏损失）")
    ap.add_argument("--distill-w", type=int, default=10,
                    help="蒸馏样本过采样倍数（默认 10）")
    ap.add_argument("--resume-dir", default=None,
                    help="从该目录的 vocab.json+model.npz 恢复继续微调（复用词表）")
    ap.add_argument("--manual-test", default=None,
                    help="人工测试集 json（真实口吻，评测结果写进 report.manual_eval）")
    ap.add_argument("--hard-weight", type=float, default=0.25,
                    help="对抗语料（易混淆边界/复合句）权重倍数，0 = 不用（默认 0.25）")
    args = ap.parse_args()
    train(epochs=args.epochs, n_per_intent=args.n_per_intent, bs=args.bs,
          lr=args.lr, emb=args.emb, hidden=args.hidden, dropout=args.dropout,
          noise=args.noise, min_count=args.min_count,
          max_features=args.max_features, seed=args.seed, out_dir=args.out,
          manual=args.manual, manual_w=args.manual_w,
          distill=args.distill, distill_w=args.distill_w,
          resume_dir=args.resume_dir, manual_test=args.manual_test,
          hard_weight=args.hard_weight)


if __name__ == "__main__":
    main()
