# -*- coding: utf-8 -*-
"""验证：API（MessagesBackend）每轮 messages 的实际组成 —— system 带精简人设 + 当前模式，
user 消息里不再出现「现在切换到 x、模式名」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai.backends.messages import MessagesBackend
from ai.backends.session import SessionBackend
from ai.context import ChatContext, TransientContext


class FakeApiClient:
    def chat_messages(self, messages, **kw):
        return "[fake]"


def main():
    print("=" * 66)
    print("A. API 后端（MessagesBackend）每轮组装")
    print("=" * 66)
    be = MessagesBackend(FakeApiClient())
    ctx = ChatContext()
    tc = TransientContext(status_full="世界=主世界 血量=18/20 手持=铁剑")

    for rounds, mode_id in ((1, 1), (2, 3)):
        sysp = config.get_api_prompt("maid", mode_id, "大肥鱼")
        msgs = be.build(ctx, tc, f"用户第{rounds}轮", system_prompt=sysp,
                        mode_id=mode_id)
        roles = [m["role"] for m in msgs]
        print(f"\n-- 第 {rounds} 轮（mode_id={mode_id}）roles={roles}")
        print(f"   system[0] 长度 = {len(msgs[0]['content'])} 字")
        print(f"   system[0] 含【当前模式】= {'【当前模式】' in msgs[0]['content']}")
        print(f"   system[0] 含女仆指令   = {'女仆指令' in msgs[0]['content']}")
        # 检查 user 消息里没有模式声明
        for m in msgs:
            if m["role"] == "user" and "现在切换到" in m["content"]:
                print("   !! user 消息仍含模式声明：", m["content"][:60])
                break
        else:
            print("   user 消息不含「现在切换到」  OK")
        be.append_history(ctx, f"用户第{rounds}轮", "回复")

    print("\n" + "=" * 66)
    print("B. 网页版（SessionBackend）—— 模式声明走 user 消息（保持原样）")
    print("=" * 66)

    class FakeWebClient:
        def chat(self, prompt, **kw):
            return "[fake]", ""

    wbe = SessionBackend(FakeWebClient())
    wpp = wbe.build(None, TransientContext(status_compact="[态] 血量 18/20"),
                    "现在切换到 1、日常模式。\n去打僵尸")
    print(f"   当轮 prompt = {wpp!r}")
    print("   → 网页版仍靠 user 消息里的模式声明（服务端只发一次记忆）")


if __name__ == "__main__":
    main()
