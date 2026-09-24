# -*- coding: utf-8 -*-
"""大模型精选校准：从全量 seed 抽取样本，按审查结果修正标签，
生成 curated.jsonl 并报告「弱标签 vs 大模型」一致率。

用法::

    python -m nlu.wiki_refine.curate

产物: ``nlu/wiki_refine/data/curated.jsonl``
每行: {"text":.., "title":.., "sec":.., "weak":"know|fun|drop", "label":"…"}
``label`` 为大模型（人工审查）的最终标签。
"""
import json
import os
import random

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")
SEED = os.path.join(DATA_DIR, "seed.jsonl")
OUT = os.path.join(DATA_DIR, "curated.jsonl")

# 审查修正表：文本前缀 → 大模型最终标签。
# 这些是我对 50 条抽样逐一审查后，与弱标签不一致/边界确认的样本。
CORRECTIONS = [
    ("如果有任何一步出错，都将移动回", "drop"),          # 残句（丢字）
    ("伤害变为d+0.11r+0.25m+0.97v", "drop"),             # 硬核公式句
    ("发芽爆炸毒马铃薯块（Sprouted Explosive", "drop"),  # 列表碎片
    ("山地生物群系将最早进行更新", "know"),               # 版本更新=知识，弱标签误 drop
    ("共发行81,581套，总计458,329.98美元", "fun"),        # Mojam 募捐=趣闻
    ("MinecraftCon，一次由50名以上自发聚集", "fun"),      # 大事记=趣闻
    ("这些版本大多数与生存测试重叠", "fun"),              # Notch 开发史=趣闻
    ("Mojang可以将他们的游戏命名为", "fun"),             # 卷轴诉讼=趣闻
    ("台阶的高度只有对应方块的一半", "know"),             # 确认：知识
    ("雷击可以减轻其锈蚀程度", "know"),                   # 确认：知识
]


def main():
    rows = []
    with open(SEED, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    # 按修正表匹配
    out = []
    corrected = 0
    for corr, my in CORRECTIONS:
        hit = [r for r in rows if corr in r["text"]]
        if not hit:
            print("!! 修正表未命中：%s" % corr)
            continue
        r = hit[0]
        weak = r["label"]
        r["weak"] = weak
        r["label"] = my
        r.pop("label_weak", None)
        out.append({k: r.get(k) for k in
                    ("text", "title", "sec", "weak", "label")})
        if weak != my:
            corrected += 1
            print("  修正 %s → %s  [%s] %s" % (weak, my, r["title"], r["text"][:50]))
        else:
            print("  确认 %s  [%s] %s" % (my, r["title"], r["text"][:50]))

    # 再补充几条代表性正确样本（know/fun/drop 各 2），形成基准
    import re
    samples = []
    for lb in ("know", "fun", "drop"):
        pool = [r for r in rows if r["label"] == lb and
                r["text"] not in {o["text"] for o in out}]
        picked = random.Random(2026).sample(pool, min(2, len(pool)))
        for r in picked:
            r = dict(r)
            r["weak"] = r["label"]
            samples.append({k: r.get(k) for k in
                            ("text", "title", "sec", "weak", "label")})
    out = samples + out

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 一致率：weak 与 label 相同的比例（修正表内）
    agree = sum(1 for r in out if r["weak"] == r["label"])
    print("\n校准集 %d 条，弱标签与大模型一致 %d 条（%.0f%%），修正 %d 条"
          % (len(out), agree, 100.0 * agree / len(out), corrected))
    print("已写入 %s" % OUT)


if __name__ == "__main__":
    main()
