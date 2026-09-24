# -*- coding: utf-8 -*-
"""人工测试集评测：用真实用户口吻的样本测意图模型泛化能力。

    python tools/eval_manual.py
    python tools/eval_manual.py --data nlu/data/manual_test.json --json out.json

说明
----
* 测试集（nlu/data/manual_test.json）是**人工编写**的，刻意避开合成语料模板，
  覆盖：口语变体、语音错字（ASR）、易混淆对抗样本、闲聊诱饵、极短句、复合句。
* 输出三个层面的指标：
  1) top-1 分类准确率（整体 + 按类）
  2) ≥0.85 自动执行档：判定会「自动执行」时猜对的准确率（红线：会把闲聊当指令执行）
  3) ≥0.55 提示档准确率
* 只依赖 numpy + 既有 nlu 包，不需要游戏/网络。
"""
import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DATA_DIR = os.path.join(BASE, "nlu", "data")
AUTO_THRESHOLD = 0.85
HINT_THRESHOLD = 0.55


def main():
    ap = argparse.ArgumentParser(description="人工测试集评测")
    ap.add_argument("--data", default=os.path.join(DATA_DIR, "manual_test.json"))
    ap.add_argument("--json", default="", help="把详细结果写入 JSON（可选）")
    ap.add_argument("--dir", default=DATA_DIR,
                    help="模型目录（默认 nlu/data；可指向别的目录做基线对比）")
    args = ap.parse_args()

    if not os.path.isfile(args.data):
        print("找不到测试集：%s" % args.data)
        sys.exit(1)
    with open(args.data, "r", encoding="utf-8") as f:
        cases = json.load(f)

    try:
        from nlu import intent
    except Exception as exc:
        print("无法导入 nlu 包：%s" % exc)
        sys.exit(1)
    try:
        eng = intent.IntentEngine.load(args.dir)
    except Exception as exc:
        print("模型加载失败：%s\n请先运行 python -m nlu.train" % exc)
        sys.exit(1)

    from collections import Counter, defaultdict
    ok = 0
    by_true = defaultdict(list)
    errs = []
    auto_hits = 0      # 判定会自动执行的样本数
    auto_correct = 0   # 其中猜对的
    chat_fa = []       # 闲聊被误判为可自动执行指令（红线）
    hint_hits = 0
    hint_correct = 0

    for c in cases:
        text, true = c["text"], c["intent"]
        pred = eng.predict(text)
        if pred is None:
            errs.append((text, true, "ERROR", 0.0, c.get("note", "")))
            by_true[true].append(False)
            continue
        correct = pred.intent == true
        ok += int(correct)
        by_true[true].append(correct)
        if not correct:
            errs.append((text, true, pred.intent, pred.confidence, c.get("note", "")))
        if pred.confidence >= HINT_THRESHOLD:
            hint_hits += 1
            hint_correct += int(correct)
        if pred.confidence >= AUTO_THRESHOLD and pred.auto_ok:
            auto_hits += 1
            auto_correct += int(correct)
            if true == "chat":
                chat_fa.append((text, pred.intent, pred.confidence))

    n = len(cases)
    print("=" * 62)
    print("人工测试集评测  （%d 条 · 27 类）" % n)
    print("=" * 62)
    print("① top-1 分类")
    print("   准确率  %d/%d = %.2f%%" % (ok, n, 100.0 * ok / n))

    print("\n② 按类准确率（按降序）：")
    rows = []
    for k, v in by_true.items():
        rows.append((k, sum(v), len(v)))
    for k, c, t in sorted(rows, key=lambda r: -r[1] / max(r[2], 1)):
        flag = "" if c == t else "  <-- 有错误"
        print("   %-11s %d/%d  %.0f%%%s" % (k, c, t, 100.0 * c / t, flag))

    print("\n③ 自动执行档（≥%.2f 且意图允许自动执行）" % AUTO_THRESHOLD)
    if auto_hits:
        print("   覆盖 %d/%d = %.1f%%  其中猜对 %.2f%%"
              % (auto_hits, n, 100.0 * auto_hits / n, 100.0 * auto_correct / auto_hits))
    else:
        print("   无样本命中该档")
    if chat_fa:
        print("   [红线] 闲聊被误判成可自动执行指令 %d 条：" % len(chat_fa))
        for text, pi, conf in chat_fa:
            print("      「%s」→ %s (%.3f)" % (text, pi, conf))
    else:
        print("   [红线] 闲聊误激活 0 条 [通过]")

    print("\n④ 提示档（≥%.2f）" % HINT_THRESHOLD)
    if hint_hits:
        print("   覆盖 %d/%d = %.1f%%  准确 %.2f%%"
              % (hint_hits, n, 100.0 * hint_hits / n, 100.0 * hint_correct / hint_hits))
    else:
        print("   无样本命中该档")

    print("\n⑤ 错误明细（%d 条）：" % len(errs))
    if errs:
        for text, true, pi, conf, note in errs:
            print("   「%s」  期望 %-10s 实际 %-10s  conf=%.3f  %s"
                  % (text, true, pi, conf, note or ""))
    else:
        print("   无 [通过]")

    if args.json:
        out = {
            "generated": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            "total": n,
            "correct": ok,
            "accuracy": round(ok / n, 4),
            "by_intent": {k: {"correct": sum(v), "total": len(v)}
                          for k, v in by_true.items()},
            "auto_threshold": AUTO_THRESHOLD,
            "auto_hits": auto_hits,
            "auto_correct": auto_correct,
            "chat_false_activation": len(chat_fa),
            "chat_false_activation_cases": [
                {"text": t, "pred": p, "conf": c} for t, p, c in chat_fa],
            "errors": [
                {"text": t, "true": x, "pred": p, "conf": c, "note": nt}
                for t, x, p, c, nt in errs],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n详细结果已写入：%s" % args.json)


if __name__ == "__main__":
    main()
