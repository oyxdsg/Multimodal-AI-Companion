# -*- coding: utf-8 -*-
"""API 后端 · 游戏模式专项复测：验证 (1) 动作标签用 {}  (2) 指令顺序在 {动作} 之后
   (3) AI 是否读到瞬态 [态]。结果写 UTF-8 文件。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import config
from ai.backends.messages import MessagesBackend
from ai.context import ChatContext, TransientContext
from game import maid_intent

GAME_TC = TransientContext(
    status_full="世界=主世界 血量=16/20 饥饿=14/20 手持=钻石镐 位置=(128,42,-315) 附近=僵尸×2(8格)",
    events=["挖到钻石×3", "获得成就：钻石！"],
)

MSGS = [
    "刚挖到钻石了！运气爆棚！",
    "有僵尸一直在追我，怎么办啊",
    "帮我造个小房子吧，晚上太危险了",
]

TAG_RE = re.compile(r"[\[{]([^\]\}]{1,10})[\]}]")
VALID = set("待机 思考 开心 害羞 兴奋 挥手 惊讶 审阅 跳跃 睡眠".split())


def run(persona, pet_name, out):
    from ai.deepseek_api import DeepSeekApiClient
    from ai.client import load_deepseek_api_key, load_deepseek_api_model
    client = DeepSeekApiClient(load_deepseek_api_key(), load_deepseek_api_model())
    be = MessagesBackend(client)

    L = []
    w = L.append
    w("=" * 72)
    w(f"游戏模式专项复测 · 人格={persona} ({config.persona_info(persona)['name']}) "
      f"· 宠物名={pet_name}")
    w(f"人设文件 = {config.persona_prompt_file(config.persona_info(persona), 'deepseek-api')}")
    w("=" * 72)

    ctx = ChatContext()
    for i, msg in enumerate(MSGS, 1):
        sysp = config.get_api_prompt(persona, 3, pet_name)
        built = be.build(ctx, GAME_TC, msg, system_prompt=sysp, mode_id=3)
        reply = be.request(built)
        be.append_history(ctx, msg, reply)

        w("")
        w(f"--- 第{i}条 ---")
        w(f"USER : {msg}")
        w(f"AI   : {reply}")

        # 校验 1：动作标签格式
        tags = TAG_RE.findall(reply)
        raw_tags = re.findall(r"[\[\{][^\]\}]{1,10}[\]\}]", reply)
        braces = re.findall(r"\{([^}]{1,10})\}", reply)
        brackets = re.findall(r"\[([^\]]{1,10})\]", reply)
        w(f"  ▸ 动作标签：{{}}={braces}  []={brackets}")
        bad_bracket = [b for b in brackets if b in VALID]
        if bad_bracket:
            w(f"  ✗ 违规：动作词用了方括号 {bad_bracket}")
        else:
            w("  ✓ 动作标签格式合规（未把动作词写成 []）")

        # 校验 2：指令位置
        cmd, clean = maid_intent.parse(reply)
        if cmd:
            pos_cmd = reply.find("【")
            pos_tag = reply.rfind("{")
            order_ok = (pos_tag == -1) or (pos_tag < pos_cmd)
            w(f"  ▸ 解析出指令：{cmd.get('cmd')}  {cmd.get('params')}")
            w(f"     指令位置={pos_cmd}  最后{{动作}}位置={pos_tag}  "
              f"→ {'✓ 顺序正确（动作在指令前）' if order_ok else '✗ 顺序违规（指令在动作前）'}")
            w(f"     剥净后文本 = {clean!r}")
        else:
            w("  ▸ 本轮无指令")

        # 校验 3：是否用到了瞬态状态
        used = any(k in reply for k in ("僵尸", "血", "钻石镐"))
        w(f"  ▸ 引用了瞬时状态（血量/僵尸/手持） = {used}")

    io.open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    run("maid", "大肥鱼", "_game_maid.txt")
    run("mikoto", "小美", "_game_mikoto.txt")
    print("done")
