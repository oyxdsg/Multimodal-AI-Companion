# -*- coding: utf-8 -*-
"""监测脚本：检查桌宠 mod 在最近一次游戏会话里是否采集到聊天。

用法：python check_chat.py
输出：
  - deskpet/ 目录现有 jsonl 及其中 chat 事件
  - latest.log 中游戏侧收到的聊天 ([CHAT]) 行
  - 对比：游戏收到了哪些聊天，mod 采到了哪些，找出差异
"""
import io
import os
import sys
import glob
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import _mc

# .minecraft 目录：DESKPET_MC 环境变量 → 本目录 mc_path.txt → %APPDATA%\.minecraft
MC = _mc.first()
DESKPET = os.path.join(MC, "deskpet")
LOGS = os.path.join(MC, "logs", "latest.log")

print("=" * 50)
print("1) mod 采集到的聊天（deskpet/*.jsonl 中的 chat 事件）")
print("=" * 50)
mod_chats = []
if os.path.isdir(DESKPET):
    files = sorted(glob.glob(os.path.join(DESKPET, "*.jsonl")))
    if not files:
        print("  (deskpet/ 目录无 jsonl 文件)")
    for f in files[-6:]:
        print(f"  -- {os.path.basename(f)} ({os.path.getsize(f)}B) --")
        for line in open(f, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                import json
                obj = json.loads(line)
            except Exception:
                continue
            for h in obj.get("highlights", []) or []:
                if isinstance(h, dict) and (h.get("type") or "").lower() == "chat":
                    t = f"{h.get('player') or ''}: {h.get('text') or ''}".strip(" :")
                    mod_chats.append(t)
                    print(f"    [chat] {t}")
else:
    print("  (deskpet/ 目录不存在)")

print()
print("=" * 50)
print("2) 游戏侧收到的聊天（latest.log 的 [CHAT] 行）")
print("=" * 50)
game_chats = []
if os.path.isfile(LOGS):
    for line in _mc.read_text(LOGS).splitlines():
        if "[CHAT]" in line:
            m = re.search(r"\[CHAT\]\s*(.*)", line)
            if m:
                game_chats.append(m.group(1).strip())
                print("   ", m.group(1).strip())
    if not game_chats:
        print("  (latest.log 无 [CHAT] 行，可能还没进游戏聊天)")
else:
    print("  (latest.log 不存在)")

print()
print("=" * 50)
print("3) 差异分析")
print("=" * 50)
if game_chats:
    for g in game_chats:
        hit = any(g in m or m in g for m in mod_chats)
        print(f"  {'OK ' if hit else 'MISS'}  {g}")
else:
    print("  (暂无游戏聊天记录可比对)")

print()
print("提示：如果你刚才是在服务器里聊天，MISS 表示 mod 没采到该条。")
print("最新 mod jar 应含 DeskpetModClient（CHAT+GAME 双事件），请确认游戏用的是新 jar 并重启过游戏。")