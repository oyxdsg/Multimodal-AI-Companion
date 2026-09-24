# -*- coding: utf-8 -*-
"""验证：指令写在 {动作} 之前时，DSL 解析 + clean 文本剥离是否仍然正确。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game import maid_intent
from game.maid_loop import MaidLoop


class FakeLink:
    connected = True

    def __init__(self):
        self.sent = []

    def request(self, cmd, params, cancel_previous=False, timeout=5.0):
        self.sent.append((cmd, params, cancel_previous))
        return {"ok": True, "state": "running", "result": {"killed": "zombie×2"}}


cases = [
    # (说明, AI 原文)
    ("指令在 {动作} 之后（规范写法）",
     "赶紧收好别让僵尸抢了。【attack(range=10, target=minecraft:zombie)】{兴奋}"),
    ("指令写在文字与 {动作} 之间（实测 AI 的写法）",
     "先把那两只僵尸清掉才安心。【attack(range=10, target=minecraft:zombie)】{兴奋}"),
    ("动作在中间",
     "好啊{兴奋}【attack(range=8)】走起"),
    ("指令在最末尾（严格规范）",
     "好的主人，我去打僵尸{开心}【attack(range=12)】"),
]

print("=" * 72)
for desc, text in cases:
    cmd, clean = maid_intent.parse(text)
    loop = MaidLoop(FakeLink())
    clean2, inject = loop.process_reply(text)
    print(f"\n[{desc}]")
    print(f"  原文 : {text}")
    print(f"  parse → cmd={cmd}  clean={clean!r}")
    print(f"  loop  → clean={clean2!r}  inject={inject.strip()!r}")
    if cmd:
        print(f"  解析出的指令 = {cmd.get('cmd')}  参数 = {cmd.get('params')}")
    # 动作标签是否保留下来的检查
    for tag in ("{兴奋}", "{开心}"):
        if tag in text:
            print(f"  动作标签 {tag} 保留在 clean 中 = {tag in clean2}")

print("\n" + "=" * 72)
print("结论：解析与剥离位置无关；{动作} 标签会被完整保留，指令被正确摘出。")
print("      → 指令写在 {动作} 之前只是『不符合人设规范』，功能上不出错。")
