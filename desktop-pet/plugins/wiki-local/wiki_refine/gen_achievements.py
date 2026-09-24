# -*- coding: utf-8 -*-
"""从 wiki 成就页生成 curated 成就条目（英文 id → 中文名映射）。

产物：nlu/wiki_refine/data/curated_achievements.jsonl
"""
import json
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

import os

_REFINE = os.path.dirname(os.path.abspath(__file__))          # wiki_refine/
_PLUGIN = os.path.dirname(_REFINE)                            # plugins/wiki-local
_PET = os.path.dirname(os.path.dirname(_PLUGIN))              # desktop-pet
DB = os.path.join(_PET, "wiki_data", "mcwiki.db")
OUT = os.path.join(_REFINE, "data", "curated_achievements.jsonl")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT text FROM pages WHERE title='成就'").fetchone()
conn.close()
text = row["text"]

# 成就[英文id]（...）；需求：XXX；分值：XG。
PAT = re.compile(
    r"成就\[([^\]]+)\]（[^）]*）"
    r"(?:；需求：([^；。]*))?"
    r"(?:；分值：([^；。]*))?[。；]")

# 英文 id → 中文名（基岩版官方成就译名；不确定处取最合理译名）
ZH = {
    "awarded-all-trophies": "获得所有奖杯",
    "music-to-my-ears": "悦耳音乐",
    "change-of-sheets": "换上新床单",
    "taking-inventory": "获得物品",
    "getting-wood": "获得木头",
    "benchmaking": "制作工作台",
    "time-to-mine": "采矿时间到了",
    "hot-topic": "热门话题",
    "acquire-hardware": "获得硬通货",
    "time-to-farm": "农耕时间到了",
    "bake-bread": "烤面包",
    "the-lie": "蛋糕是个谎言",
    "getting-an-upgrade": "升级工具",
    "delicious-fish": "美味的鱼",
    "在铁路上": "在铁路上",
    "time-to-strike": "攻击时间到了",
    "monster-hunter": "怪物猎人",
    "cow-tipper": "斗牛士",
    "when-pigs-fly": "猪会飞",
    "sniper-duel": "狙击对决",
    "钻石！": "钻石！",
    "into-the-nether": "进入下界",
    "return-to-sender": "原路返回",
    "into-fire": "进入火焰",
    "local-brewery": "本地酿造厂",
    "the-end?": "结束？",
    "结束了？": "结束了？",
    "enchanter": "附魔师",
    "赶尽杀绝": "赶尽杀绝",
    "librarian": "图书管理员",
    "adventuring-time": "探索的时光",
    "the-beginning?": "开始？",
    "the-beginning": "开始",
    "the-beaconator": "信标工程师",
    "repopulation": "人口复兴",
    "给你钻石！": "给你钻石！",
    "君临天下": "君临天下",
    "moar-tools": "更多工具",
    "dispense-with-this": "发射此物",
    "leader-of-the-pack": "狼群领袖",
    "pork-chop": "猪排",
    "passing-the-time": "消磨时间",
    "the-haggler": "讨价还价",
    "pot-planter": "盆栽",
    "its-a-sign": "这是告示牌",
    "iron-belly": "铁胃",
    "have-a-shearful-day": "剪羊毛日",
    "rainbow-collection": "彩虹收藏",
    "stayin-frosty": "保持寒冷",
    "chestful-of-cobblestone": "一箱圆石",
    "renewable-energy": "可再生能源",
    "body-guard": "保镖",
    "iron-man": "钢铁侠",
    "zombie-doctor": "僵尸医生",
    "lion-hunter": "狮子猎人",
    "archer": "弓箭手",
    "tie-dye-outfit": "扎染套装",
    "trampoline": "蹦床",
    "camouflage": "伪装",
    "map-room": "地图室",
    "freight-station": "货运站",
    "smelt-everything": "熔炼一切",
    "taste-of-your-own-medicine": "自食其果",
    "inception": "盗梦空间",
    "saddle-up": "上鞍",
    "artificial-selection": "人工选择",
    "free-diver": "自由潜水",
    "rabbit-season": "兔子季节",
    "the-deep-end": "深海",
    "dry-spell": "干旱期",
    "super-fuel": "超级燃料",
    "you-need-a-mint": "你需要薄荷",
    "beam-me-up": "传送我吧",
    "the-end--again": "再次结束",
    "great-view-from-up-here": "高处美景",
    "super-sonic": "超音速",
    "treasure-hunter": "寻宝猎人",
    "organizational-wizard": "整理大师",
    "cheating-death": "欺骗死亡",
    "feeling-ill": "感觉不适",
    "let-it-go": "随它去吧",
    "so-i-got-that-going-for-me": "这也算好事",
    "atlantis?": "亚特兰蒂斯？",
    "sail-the-7-seas": "七海扬帆",
    "castaway": "漂流者",
    "ahoy": "啊嗬",
    "i-am-a-marine-biologist": "我是海洋生物学家",
    "me-gold": "我的金块",
    "sleep-with-the-fishes": "与鱼共眠",
    "替代性燃料": "替代性燃料",
    "do-a-barrel-roll": "做桶滚",
    "one-pickle-two-pickle-sea-pickle-four": "一泡菜二泡菜海泡菜四泡菜",
    "echolocation": "回声定位",
    "moskstraumen": "莫斯肯漩涡",
    "top-of-the-world": "世界之巅",
    "where-have-you-been?": "你去哪儿了？",
    "zoologist": "动物学家",
    "fruit-on-the-loom": "织布机上的果实",
    "plethora-of-cats": "群猫环绕",
    "kill-the-beast": "杀死野兽",
    "buy-low-sell-high": "低买高卖",
    "disenchanted": "祛魔",
    "were-being-attacked": "我们遭到袭击了",
    "sound-the-alarm": "拉响警报",
    "ive-got-a-bad-feeling-about-this": "我有种不祥的预感",
    "master-trader": "交易大师",
    "time-for-stew": "炖菜时间",
    "bee-our-guest": "蜜蜂来客",
    "total-beelocation": "全面蜂巢",
    "sticky-situation": "棘手的情况",
    "bullseye": "正中靶心",
    "oooh-shiny": "噢，闪闪发光",
    "cover-me-in-debris": "残骸裹身",
    "hot-tourist-destination": "热门旅游胜地",
    "wax-on-wax-off": "涂蜡去蜡",
    "whatever-floats-your-goat": "随山羊漂流",
    "the-healing-power-of-friendship": "友谊的治愈力量",
    "caves-and-cliffs": "洞穴与山崖",
    "feels-like-home": "家的感觉",
    "sound-of-music": "音乐之声",
    "star-trader": "星际商人",
    "birthday-song": "生日快乐歌",
    "it-spreads": "它蔓延了",
    "sneak-100": "潜行100级",
    "with-our-powers-combined": "齐心协力",
    "planting-the-past": "播种往事",
    "careful-restoration": "精心修复",
    "smithing-with-style": "匠心独具",
    "revaulting": "宝经磨炼",
    "crafters-crafting-crafters": "合成器合成合成器",
    "who-needs-rockets?": "还要啥火箭啊？",
    "over-overkill": "天赐良击",
    "heart-transplanter": "心脏移植",
    "stay-hydrated": "保持水分",
    "mob-kabob": "生物串串香",
    "uh-oh": "坏了",
}

rows = []
seen = set()
for m in PAT.finditer(text):
    eid = m.group(1).strip()
    req = (m.group(2) or "").strip()
    val = (m.group(3) or "").strip()
    if eid in seen:
        continue
    seen.add(eid)
    cn = ZH.get(eid, eid)
    facts = []
    if req:
        facts.append({"type": "要求", "text": req})
    if val:
        facts.append({"type": "分值", "text": val})
    rows.append({"id": "ach_" + eid, "name": cn, "category": "成就",
                 "summary": ("%s成就：%s" % (cn, req)) if req else ("%s成就" % cn),
                 "facts": facts})

rows.sort(key=lambda r: r["id"])
with open(OUT, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print("成就条目:", len(rows), "（未映射中文名的 %d 个）" %
      sum(1 for r in rows if r["id"][4:] not in ZH or r["id"][4:] == r["name"]))
print("已写入:", OUT)
for r in rows[:15]:
    print("  [%s] %s: %s" % (r["id"], r["name"], r["summary"][:36]))
