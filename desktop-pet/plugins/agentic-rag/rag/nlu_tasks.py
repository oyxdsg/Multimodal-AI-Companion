# -*- coding: utf-8 -*-
"""RAG 任务 NLU：标签定义 + 合成语料生成器（复用桌宠 NLU 思路）。

两个任务（各训一个小模型，单标签头）：
1. need_retrieval：need（MC 问题，需检索）/ no_need（寒暄/闲聊/无关）
2. page_type：推断查询/标题涉及的实体类型

合成语料：任务模板 × 实体词典 × 口语变体 × 噪声自动扩增；
再并入 `data/nlu/manual_*.jsonl` 的手工真实句（过采样放大权重）。
"""

import json
import os
import random

# ---------------------------------------------------------------- 标签体系

NEED_KEYS = ["need", "no_need"]
NEED_LABELS = {
    "need": "需要检索（MC 相关提问/查询）",
    "no_need": "无需检索（寒暄/闲聊/无关）",
}

PAGETYPE_KEYS = ["mob", "block", "item", "biome", "command", "version", "guide", "other"]
PAGETYPE_LABELS = {
    "mob": "生物", "block": "方块", "item": "物品", "biome": "生物群系",
    "command": "命令/指令", "version": "版本/更新", "guide": "指南/教程", "other": "其他",
}

# ---------------------------------------------------------------- 实体词典

MOBS = ["僵尸", "骷髅", "苦力怕", "末影人", "村民", "铁傀儡", "雪傀儡", "猪", "牛", "羊",
        "鸡", "马", "狼", "猫", "蝙蝠", "鱿鱼", "溺尸", "尸壳", "幻翼", "疣猪兽",
        "猪灵", "末影龙", "凋灵", "循声守卫", "嗅探兽", "骆驼", "蜘蛛", "女巫",
        "烈焰人", "恶魂", "史莱姆", "守卫者", "潜影贝", "海豚", "蜜蜂", "狐狸"]

BLOCKS = ["石头", "圆石", "原木", "木板", "玻璃", "砖块", "铁轨", "箱子", "熔炉",
          "工作台", "床", "火把", "活塞", "红石", "命令方块", "黑曜石", "基岩",
          "沙子", "沙砾", "泥土", "草方块", "矿石", "铁矿石", "金矿石", "钻石矿石",
          "下界岩", "末地石", "石头台阶", "楼梯", "栅栏", "门", "压力板", "按钮",
          "灯笼", "梯子", "书架", "酿造台", "附魔台", "铁砧",
          "红石中继器", "红石比较器", "红石火把", "红石粉", "下界传送门", "末地传送门",
          "结构方块", "移动的活塞", "去皮原木", "探测铁轨", "铜箱子", "木栅栏",
          "栅栏门", "木活板门", "末地折跃门", "下落的方块", "末地传送门框架",
          "蜂巢", "上锁的箱子", "虫蚀方块", "染色玻璃板", "木桶"]

ITEMS = ["剑", "镐", "斧", "锹", "锄", "弓", "箭", "盾牌", "铁锭", "金锭", "铜锭",
         "钻石", "绿宝石", "下界合金锭", "煤炭", "红石粉", "火药", "药水", "面包",
         "苹果", "胡萝卜", "小麦", "甘蔗", "蛋糕", "书", "纸", "皮革", "线",
         "骨头", "羽毛", "鱼竿", "烟花", "鞘翅", "附魔书", "铁头盔", "钻石胸甲",
         "钻石头盔", "下界合金胸甲",
         "末影之眼", "末影珍珠", "喷溅药水", "滞留药水", "炼药锅", "钓鱼竿",
         "烟花火箭", "音乐唱片", "药箭", "附魔金苹果", "金苹果", "水桶", "铁桶",
         "马铠", "骷髅头颅", "凋灵骷髅头颅", "僵尸的头", "苦力怕的头",
         "剧毒药水", "迟缓药水", "药水酿造", "盔甲架", "成书", "书与笔", "唱片机"]

BIOMES = ["森林", "沙漠", "草原", "雪原", "海洋", "沼泽", "丛林", "针叶林",
          "恶地", "蘑菇岛", "山地", "平原", "冰原", "河流", "海滩", "热带草原",
          "下界", "末地", "虚空", "巨型生物群系", "单一生物群系"]

COMMANDS = ["fillbiome", "give", "tp", "kill", "summon", "execute", "gamemode",
            "time", "weather", "effect", "gamerule", "setblock", "fill", "clone",
            "spreadplayers", "title", "playsound", "data", "scoreboard"]

VERSIONS = ["1.19", "1.20", "1.21", "23w04a", "快照", "预览版", "Java版", "基岩版"]

GUIDE_WORDS = ["教程", "指南", "入门", "进阶", "怎么玩", "攻略"]

# ---------------------------------------------------------------- 口语变体

NEED_PATTERNS = [
    "{entity}怎么合成", "{entity}在哪里生成", "{entity}有什么用", "{entity}是什么",
    "{entity}怎么获得", "{entity}怎么做", "{entity}需要什么材料", "{entity}的配方",
    "如何制作{entity}", "哪里能找到{entity}", "{entity}怎么用", "{entity}怎么获得",
    "{entity}多少金币", "{entity}刷新位置", "{entity}属性是什么",
    "怎么得到{entity}", "{entity}怎么建造", "{entity}在哪", "{entity}怎么驯服",
    "{entity}怎么繁殖", "{entity}怎么附魔",
]

NO_NEED_PATTERNS = [
    "你好", "你好呀", "在吗", "谢谢", "再见", "拜拜", "你是谁", "你能做什么",
    "今天天气怎么样", "讲个笑话", "吃了吗", "在干嘛", "晚上好", "早上好",
    "加油", "真棒", "哈哈哈", "好无聊", "你叫什么名字", "能聊会天吗",
    "介绍一下你自己", "你有什么功能", "谢谢你的帮助", "再见啦", "晚安",
    "你是谁啊", "在不在", "哦", "好的", "嗯嗯", "行", "好吧",
]

# ---------------------------------------------------------------- 噪声

FILLERS = ["嗯", "那个", "请问", "就是说", "的话", "一下", "大概"]
SYNONYMS = {
    "怎么": ["怎么", "咋", "如何", "要怎样"],
    "合成": ["合成", "做", "制作", "搞", "造"],
    "获得": ["获得", "得到", "拿", "弄到"],
    "是什么": ["是什么", "是啥", "是干什么的", "是干嘛的"],
    "哪里": ["哪里", "哪儿", "什么地方", "在哪"],
    "生成": ["生成", "刷新", "出现"],
}


def _noise(s, rng, prob=0.25):
    """口语噪声：插入填充词 / 同义替换。"""
    if not s or rng.random() > prob:
        return s
    op = rng.random()
    if op < 0.4 and len(s) > 3:
        i = rng.randrange(len(s))
        s = s[:i] + rng.choice(FILLERS) + s[i:]
    elif op < 0.8:
        for w, alts in SYNONYMS.items():
            if w in s and rng.random() < 0.5:
                s = s.replace(w, rng.choice(alts), 1)
                break
    return s


# ---------------------------------------------------------------- 合成

def _page_type_of(entity):
    if entity in MOBS:
        return "mob"
    if entity in BLOCKS:
        return "block"
    if entity in ITEMS:
        return "item"
    if entity in BIOMES:
        return "biome"
    return "other"


def generate_need(n_per=600, seed=42):
    rng = random.Random(seed)
    entities = MOBS + BLOCKS + ITEMS + BIOMES
    rows = []
    # need 正样本：实体相关提问
    for _ in range(n_per):
        ent = rng.choice(entities)
        tpl = rng.choice(NEED_PATTERNS)
        text = _noise(tpl.format(entity=ent), rng)
        rows.append({"text": text, "intent": "need", "note": "synth"})
    # no_need 负样本：寒暄/闲聊（必须足量，避免误判）
    for _ in range(n_per):
        text = _noise(rng.choice(NO_NEED_PATTERNS), rng)
        rows.append({"text": text, "intent": "no_need", "note": "synth"})
    return rows


def generate_pagetype(n_per=300, seed=43):
    rng = random.Random(seed)
    rows = []
    pool = [("mob", MOBS), ("block", BLOCKS), ("item", ITEMS), ("biome", BIOMES)]
    CONTEXT = [
        "{e}怎么合成", "{e}在哪里生成", "{e}有什么用", "{e}是什么",
        "{e}怎么获得", "{e}怎么做", "如何制作{e}", "{e}的用途", "这个{e}",
        "关于{e}", "{e}怎么建造", "{e}在哪个群系", "{e}怎么用",
    ]
    for ptype, ents in pool:
        for _ in range(n_per):
            ent = rng.choice(ents)
            tpl = rng.choice(CONTEXT + ["{e}"])
            text = _noise(tpl.format(e=ent), rng)
            rows.append({"text": text, "intent": ptype, "note": "synth"})
    # command / version / guide / other
    for _ in range(n_per):
        cmd = rng.choice(COMMANDS)
        rows.append({"text": "命令 %s 怎么用" % cmd, "intent": "command", "note": "synth"})
    for _ in range(n_per):
        v = rng.choice(VERSIONS)
        rows.append({"text": "%s 更新了什么" % v, "intent": "version", "note": "synth"})
    for _ in range(n_per):
        g = rng.choice(GUIDE_WORDS)
        rows.append({"text": "有没有%s" % g, "intent": "guide", "note": "synth"})
    for _ in range(n_per):
        rows.append({"text": rng.choice(["Minecraft历史", "游戏彩蛋", "开发团队", "音乐"]),
                     "intent": "other", "note": "synth"})
    # 寒暄/无关对抗样本 → other（配合 need 模型：实际流程先判 need，no_need 不再调 pagetype）
    for _ in range(n_per):
        rows.append({"text": _noise(rng.choice(NO_NEED_PATTERNS), rng),
                     "intent": "other", "note": "chitchat-anti"})
    return rows


# ---------------------------------------------------------------- 读写

def read_jsonl(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
