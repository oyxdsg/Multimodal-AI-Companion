# -*- coding: utf-8 -*-
"""合并/校验 curated 分片 → curated_all.jsonl（供检索读取）。

用法::

    python -m nlu.wiki_refine.build_curated

数据源：nlu/wiki_refine/data/curated*.jsonl（按批次分片，不覆盖原 wiki）。
产物：curated_all.jsonl（合并去重）+ 打印统计。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")

_ALLOWED_TYPES = {
    "配方", "能力", "获取", "用途", "效果", "耐久", "掉落", "行为", "战斗",
    "克制", "繁殖", "驯服", "骑乘", "召唤", "交易", "感染", "治愈", "机制",
    "常见附魔", "增强", "步骤", "位置", "内容", "寻找", "激活", "搭建",
    "性质", "备注", "燃料", "保护", "健康", "使用", "货币", "幼崽", "伙伴",
    "通关", "附魔", "修复", "材质", "适用", "入口", "资源", "进入", "危险",
    "Boss", "战利品", "来源", "解除", "种植", "攻略", "玩法", "升级", "恢复",
    "影响", "特点", "生物", "结构", "出现", "生成", "工作方块", "纹饰",
    "流程", "条件", "常用", "奖励", "功能", "孵化", "触发", "变体", "原理",
    "注意", "技巧", "主世界", "下界", "末地", "防御", "应对", "基础", "效果",
    "逻辑", "挑战", "扩展", "准备", "装备", "用法", "网格", "熔炉", "高炉",
    "烟熏炉", "方法", "类型", "分布", "操作", "宝藏", "材料", "关键", "目标",
    "流程", "选址", "优势", "奖励", "工具", "附魔台", "铁砧", "友好", "中立",
    "敌对", "饱食", "食物", "经验", "死亡", "村民", "职业", "交易", "繁殖",
}


def main():
    paths = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.startswith("curated") and fn.endswith(".jsonl") \
                and fn != "curated_all.jsonl":
            paths.append(os.path.join(DATA_DIR, fn))
    rows, seen, errors = [], set(), []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError as exc:
                    errors.append("%s: %s" % (os.path.basename(p), exc))
                    continue
                if not r.get("id") or not r.get("name") or not r.get("summary"):
                    errors.append("缺字段: %s" % (r.get("name") or r))
                    continue
                if r["id"] in seen:
                    errors.append("重复 id: %s" % r["id"])
                    continue
                for f in r.get("facts") or []:
                    if not f.get("type") or not f.get("text"):
                        errors.append("facts 缺字段: %s (%s)" % (f, r["id"]))
                seen.add(r["id"])
                rows.append(r)
    if errors:
        for e in errors[:20]:
            print("[注意]", e)
    rows.sort(key=lambda r: r["id"])
    out = os.path.join(DATA_DIR, "curated_all.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    cats = Counter(r["category"] for r in rows)
    print("合并 %d 个文件 → %d 条 curated（%s）" % (len(paths), len(rows), out))
    print("类别分布：%s" % dict(cats))


if __name__ == "__main__":
    main()
