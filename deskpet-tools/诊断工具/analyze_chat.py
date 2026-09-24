# -*- coding: utf-8 -*-
"""分析 latest.log 中 [CHAT] 消息的数量、类型分布，评估 20s 窗口数据量。"""
import io, os, sys, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import _mc

# .minecraft 目录：DESKPET_MC 环境变量 → 本目录 mc_path.txt → %APPDATA%\.minecraft
MC = _mc.first()
LOG = os.path.join(MC, "logs", "latest.log")

lines = _mc.read_text(LOG).split("\n")
chat_lines = []
for l in lines:
    m = re.search(r"\[CHAT\]\s*(.*)", l)
    if m:
        chat_lines.append(m.group(1).strip())

print(f"[CHAT] 总行数: {len(chat_lines)}")
print()

# 时间戳分布
ts_re = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
by_min = {}
for l in lines:
    m = ts_re.match(l)
    if not m:
        continue
    t = f"{m.group(1)}:{m.group(2)}"
    has_chat = "[CHAT]" in l
    by_min.setdefault(t, [0, 0])
    by_min[t][0] += 1
    if has_chat:
        by_min[t][1] += 1

print("每分钟 行数 / [CHAT]数:")
for t in sorted(by_min):
    print(f"  {t}  总{by_min[t][0]:4d}  [CHAT]{by_min[t][1]:4d}")

# 分类 [CHAT] 消息
cat = {"击杀": [], "死亡": [], "获得": [], "玩家聊天": [], "加入离开": [], "其他系统": []}
for c in chat_lines:
    s = c
    if re.search(r"(被\s*\S+\s*(击杀|杀死)|killed by|was slain)", s, re.I):
        cat["击杀"].append(c)
    elif re.search(r"(摔落|炸死|烧死|淹死|而死|died|fell|drowned)", s, re.I) and not re.search(r"(奖励|完成)", s):
        cat["死亡"].append(c)
    elif re.search(r"(获得了|获得|你获得了|得到|拿到|收到|+.*硬币|coins)", s) and not re.search(r"(Network Booster|Reward)", s):
        cat["获得"].append(c)
    elif re.search(r"(加入游戏|离开游戏|joined|left|游戏开始|游戏结束)", s, re.I):
        cat["加入离开"].append(c)
    elif re.search(r"^\s*<[^>]+>", s) or re.search(r"^\s*[^:：]{1,20}\s*[:：]\s*\S", s):
        cat["玩家聊天"].append(c)
    else:
        cat["其他系统"].append(c)

for k, v in cat.items():
    print(f"\n=== {k} ({len(v)} 条) ===")
    for c in v[:15]:
        print("   ", c[:80])
    if len(v) > 15:
        print(f"    ... 共 {len(v)} 条")