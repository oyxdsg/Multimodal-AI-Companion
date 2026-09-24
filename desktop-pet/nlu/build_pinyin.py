# -*- coding: utf-8 -*-
"""离线生成拼音表 ``nlu/data/pinyin.json``（一次性构建，运行时不依赖 pypinyin）。

为什么不用运行时 pypinyin：

* 桌宠**不带**深度学习/中文 NLP 依赖，多装一个包就多一份部署风险；
* 需要拼音的只有「物品名模糊匹配」这一件事，字典规模有限（MC 物品名用到的
  汉字两千出头），固化成一张小表即可，几万字节、毫秒级查询；
* 训练期与运行期**用同一张表**，避免「训练时拼音对齐、运行时对不上」的漂移。

表结构：``{"镐": "gao", "锭": "ding", ...}``（单字一个主读音，无声调）。

用法（在 desktop-pet/ 下）::

    python -m nlu.build_pinyin            # 用 wiki 词条里的汉字建表
    python -m nlu.build_pinyin --extra 谱谱   # 追加自定义汉字
"""

import argparse
import json
import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "data", "pinyin.json")
WIKI_DB = os.path.join(os.path.dirname(_HERE), "wiki_data", "mcwiki.db")

# 兜底：给「常用物品名汉字」再补一批（wiki 缺失时也能用）
_FALLBACK_CHARS = (
    "镐锹斧锄剑弓箭盾盔甲裤靴鞘袋瓶桶箱炉台门梯栏桩桌床椅灯烛火把灯笼"
    "铁铜金银钻石绿宝青红石煤木炭锭块粒粉末板棍条片丝线皮革羽毛骨火药"
    "黏土沙砾泥革绳圈环钉螺齿轮杆柄头刃锋锤凿锯针钉碗盘锅罐壶杯箸"
    "麦稻薯豆瓜果菜根叶种苗木竹藤棉麻稻穗壳仁浆汁酪饼糕饼糖盐酱醋"
    "僵尸骷髅苦力怕蜘蛛末影蟹溺尸壳烈焰魂岩浆凋灵掠夺卫道唤魔幻术"
    "潜影贝守卫远古监守猪疣兽村民流浪商铁傀儡雪狐狸兔熊猫北极羊驼豚"
)


def wiki_chars(db_path=WIKI_DB, limit=40000):
    """收集 wiki 词条/别名里出现过的汉字（覆盖绝大多数 MC 物品名用字）。"""
    chars = set()
    if not os.path.isfile(db_path):
        return chars
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT title FROM pages WHERE category='common'")
        for (t,) in rows:
            chars.update(c for c in (t or "") if "\u4e00" <= c <= "\u9fff")
        rows = conn.execute("SELECT alias FROM redirects")
        for (t,) in rows:
            chars.update(c for c in (t or "") if "\u4e00" <= c <= "\u9fff")
            if len(chars) > limit:
                break
        conn.close()
    except Exception:
        pass
    return chars


def cjk_block(lo=0x4E00, hi=0x9FA5):
    """CJK 基本区汉字（约 2 万字）。

    **必须覆盖整个常用字区**而不是只覆盖物品名用字——用户的语音错字可能是
    任意同音字（「镐」打成「稿/搞/告/高」），错字本身未必出现在任何物品名里，
    只覆盖物品名用字会让这些错字的拼音查不到，匹配直接失效。
    """
    return {chr(c) for c in range(lo, hi + 1)}


def build(chars, out=OUT):
    from pypinyin import lazy_pinyin
    table = {}
    for c in sorted(chars):
        try:
            py = lazy_pinyin(c)
        except Exception:
            continue
        if not py:
            continue
        p = py[0]
        if p and p != c:
            table[c] = p
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, separators=(",", ":"))
    return table


def main():
    ap = argparse.ArgumentParser(description="生成离线拼音表")
    ap.add_argument("--extra", default="", help="追加的汉字（字符串）")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--partial", action="store_true",
                    help="只覆盖 wiki 物品名用字（体积更小，但语音错字可能查不到拼音）")
    args = ap.parse_args()

    chars = wiki_chars() if args.partial else cjk_block()
    from_wiki = len(chars)
    chars.update(c for c in args.extra if "\u4e00" <= c <= "\u9fff")
    chars.update(_FALLBACK_CHARS)
    table = build(chars, args.out)
    print("候选汉字 %d 个 → 输出 %d 条（%.1f KB）"
          % (from_wiki, len(table), os.path.getsize(args.out) / 1024.0))
    print("样例：" + "、".join("%s=%s" % (k, table[k])
                              for k in ["镐", "稿", "锭", "定", "剑", "盾"]
                              if k in table))


if __name__ == "__main__":
    main()
