# -*- coding: utf-8 -*-
"""训练语料 ↔ 人工测试集 **泄漏审计**。

    python tools/check_leak.py
    python tools/check_leak.py --prefix 6      # 改前缀阈值（默认 8）

为什么需要
----------
意图模型是分类任务，「对抗语料」天生和测试集长得像。一旦把测试句抄进训练集，
准确率会虚高十来个点，产品上却毫无改善——本项目就踩过一次（27 条逐字重合，
人工测试集从 89.9% 虚报到 98.1%）。

审计三类来源：
* ``nlu/hard_cases.py``  —— 对抗语料（**必须零重合**）
* ``nlu/synth.py`` 模板   —— 合成模板的字面形式（**必须零重合**）
* ``nlu/data/manual_train.jsonl`` / ``distill_*.jsonl`` —— 人工/蒸馏语料（**报告，不改**）

判据：精确重合，或「前 N 字前缀」（默认 8）一方包含另一方。

**极短命令豁免**：长度 ≤ 3 字的句子（「过来」「坐下」「停下」）就是该意图的
最简规范形式，**无法**改写成别的说法；把它们从训练集剔掉只会让产品变差。
因此这类重合不判失败，但会**如实统计受影响的测试条数**并在结论里报出来——
看准确率时要心里有数。

退出码非 0 表示有必须修的重合。
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

DATA = os.path.join(BASE, "nlu", "data")

# 极短命令豁免长度（含）
SHORT_EXEMPT = 3


def _read_jsonl(path):
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def _sources():
    """返回 {来源名: [句子]}。"""
    from nlu import hard_cases, synth

    hard = []
    for texts in hard_cases.DISAMBIG.values():
        hard += list(texts)
    hard += [t for t, _ in hard_cases.COMPOUND]

    tpl = []
    for texts in synth.TEMPLATES.values():
        tpl += list(texts)

    manual_tr = [r["text"] for r in _read_jsonl(os.path.join(DATA, "manual_train.jsonl"))
                 if r.get("text")]
    distill = []
    for name in ("distill_all.jsonl", "distill_data.jsonl", "distill_data2.jsonl"):
        distill += [r["text"] for r in _read_jsonl(os.path.join(DATA, name))
                    if r.get("text")]

    return {
        "hard_cases（对抗语料，必须零重合）": hard,
        "synth 模板（必须零重合）": tpl,
        "manual_train（报告）": manual_tr,
        "distill（报告）": list(dict.fromkeys(distill)),
    }


def _hits(train, tests, prefix):
    """返回 (必改重合, 极短豁免重合, 前缀重合)。"""
    exact, short, near = [], [], []
    tset = {t.strip() for t in tests}
    for s in train:
        s = (s or "").strip()
        if not s:
            continue
        if s in tset:
            (short if len(s) <= SHORT_EXEMPT else exact).append(s)
            continue
        n = min(len(s), prefix)
        if n < prefix:
            continue
        for t in tests:
            t = (t or "").strip()
            if len(t) < prefix:
                continue
            if s[:prefix] == t[:prefix] or s[:prefix] in t or t[:prefix] in s:
                near.append((s, t))
                break
    return exact, short, near


def main():
    ap = argparse.ArgumentParser(description="训练语料与测试集的泄漏审计")
    ap.add_argument("--test", default=os.path.join(DATA, "manual_test.json"))
    ap.add_argument("--prefix", type=int, default=8)
    args = ap.parse_args()

    if not os.path.isfile(args.test):
        print("找不到测试集：%s" % args.test)
        sys.exit(1)
    with open(args.test, "r", encoding="utf-8") as f:
        tests = [c["text"] for c in json.load(f) if c.get("text")]

    print("=" * 66)
    print("泄漏审计  测试集 %d 条 · 前缀阈值 %d 字 · 极短豁免 ≤%d 字"
          % (len(tests), args.prefix, SHORT_EXEMPT))
    print("=" * 66)

    blocking = 0
    exempt_total = set()
    for name, train in _sources().items():
        uniq = list(dict.fromkeys([s for s in train if s]))
        exact, short, near = _hits(uniq, tests, args.prefix)
        must = "必须零重合" in name
        mark = "[失败]" if (exact or near) else "[通过]"
        print("\n%s %s：%d 条候选 → 完全重合 %d，极短豁免 %d，前缀重合 %d"
              % (mark, name, len(uniq), len(exact), len(short), len(near)))
        for s in exact:
            print("     [完全重合] %s" % s)
        for s in short:
            exempt_total.add(s)
            print("     [极短豁免] %s" % s)
        for s, t in near[:12]:
            print("     [前缀重合] %s   ≒   %s" % (s, t))
        if must and (exact or near):
            blocking += len(exact) + len(near)

    print("\n" + "=" * 66)
    if exempt_total:
        affected = [t for t in tests if t.strip() in exempt_total]
        print("提示：%d 条极短命令（%s）属于「无法改写的最简形式」，与训练集共享。"
              % (len(exempt_total), "、".join(sorted(exempt_total))))
        print("      测试集中受此影响的样本 %d 条（%s），看准确率时应扣除。"
              % (len(affected), "、".join(affected)))
    if blocking:
        print("[失败] 有 %d 处**必须修**的重合：改 nlu/hard_cases.py 或 nlu/synth.py 的句式" % blocking)
        sys.exit(1)
    print("[通过] 必须零重合的两类均无重合（人工/蒸馏语料的重合仅作提示）")


if __name__ == "__main__":
    main()
