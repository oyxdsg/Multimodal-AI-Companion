# -*- coding: utf-8 -*-
"""API 后端多模式实测（结果直接写 UTF-8 文件，避免 shell 编码问题）。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai.backends.messages import MessagesBackend
from ai.context import ChatContext, TransientContext

CASES = {
    1: ("日常模式", [
        "今天上班累死了，感觉整个人都被掏空了……",
        "我跟你说个好事！我今天面试通过了！",
        "帮我想想晚上吃什么呗，附近有啥推荐的？",
    ]),
    2: ("工作模式", [
        "我现在在用 Excel 做季度报表。",
        "刚打开 Visual Studio Code 准备改代码。",
        "在 PowerPoint 里调一份汇报稿的排版。",
    ]),
    3: ("游戏模式", [
        "刚挖到钻石了！运气爆棚！",
        "唉，在下界被猪灵围殴死了……",
        "我准备去打末影龙，你觉得要带什么？",
    ]),
}

GAME_TRANSIENT = TransientContext(
    status_full="世界=主世界 血量=16/20 饥饿=14/20 手持=钻石镐 位置=(128,42,-315) 附近=僵尸×2(8格)",
    events=["挖到钻石×3", "获得成就：钻石！"],
)


def run(persona, pet_name, out_path, modes=None):
    from ai.deepseek_api import DeepSeekApiClient
    from ai.client import load_deepseek_api_key, load_deepseek_api_model

    model = load_deepseek_api_model()
    key = load_deepseek_api_key()
    client = DeepSeekApiClient(key, model)
    be = MessagesBackend(client)

    persona = persona or "maid"
    info = config.persona_info(persona)
    lines = []
    w = lines.append
    w("=" * 72)
    w(f"后端 = deepseek-api    模型 = {model}")
    w(f"人格 = {persona}（{info['name']}）    宠物名 = {pet_name or info['default_name']}")
    w(f"走的人设文件 = {config.persona_prompt_file(info, 'deepseek-api')}")
    w("=" * 72)

    for mode_id in (modes or sorted(CASES)):
        mode_name, msgs = CASES[mode_id]
        w("")
        w("#" * 72)
        w(f"# 模式 {mode_id} · {mode_name}")
        w("#" * 72)
        ctx = ChatContext()
        for i, user_msg in enumerate(msgs, 1):
            sysp = config.get_api_prompt(persona, mode_id, pet_name)
            tc = GAME_TRANSIENT if mode_id == 3 else TransientContext()
            built = be.build(ctx, tc, user_msg, system_prompt=sysp, mode_id=mode_id)
            roles = [m["role"] for m in built]
            sys0 = built[0]["content"]
            mode_seg = sys0.split("【当前模式】")[-1].strip().splitlines()
            mode_line = mode_seg[1] if len(mode_seg) > 1 else (mode_seg[0] if mode_seg else "(无)")
            w("")
            w(f"--- [{mode_name} 第{i}条] ---")
            w(f"USER : {user_msg}")
            w(f"发出 messages: {len(built)} 条  roles={roles}  "
              f"(system 共 {sum(1 for m in built if m['role'] == 'system')} 条)")
            w(f"  system[0]: {len(sys0)} 字 | 含【当前模式】={'【当前模式】' in sys0}"
              f" | 含女仆指令={'女仆指令' in sys0}")
            w(f"  当前模式段 → {mode_line}")
            if any(m["role"] == "user" and "现在切换到" in m["content"] for m in built):
                w("  !! user 消息仍含模式声明")
            try:
                reply = be.request(built)
            except Exception as e:
                reply = f"<调用失败: {type(e).__name__}: {e}>"
            w(f"AI   : {reply}")
            be.append_history(ctx, user_msg, reply)

    with io.open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(lines)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="maid")
    ap.add_argument("--pet-name", default="")
    ap.add_argument("--mode", type=int, default=0)
    ap.add_argument("--out", default="_api_result.txt")
    a = ap.parse_args()
    n = run(a.persona, a.pet_name, a.out, [a.mode] if a.mode else None)
    print("wrote", a.out, n, "lines")
