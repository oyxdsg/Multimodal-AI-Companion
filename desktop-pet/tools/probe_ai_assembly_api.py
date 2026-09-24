# -*- coding: utf-8 -*-
"""模拟任务 → **官方 API**，检查 AI 能否用原子指令自由组装 script。

网页版对应脚本：`Temp/opencode/probe_ai_assembly.py`（SessionBackend + 全长人设）。
本脚本走 **MessagesBackend（无状态）** + **API 精简人设**（`prompt_api.txt`，
省 token <6000）——每轮重新组装 messages，是 API 端真实链路。

隔离测试：每轮新建 ChatContext（无历史），等价网页版 `memory=False`。

用法：python tools/probe_ai_assembly_api.py [--persona maid] [--mode 3] [--pet-name 大肥鱼]
"""
import argparse
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai import client as ai_client
from ai.backends.messages import MessagesBackend
from ai.context import ChatContext, TransientContext

MAID_STATE = (
    "【当前状态】女仆：生命 20.0，位置 (120,-60,10)，任务 无；"
    "背包：木斧×1、铁镐×1、石头×8、面包×4；"
    "附近：橡树×3（各约5原木）、白桦树×1、铁矿×2、僵尸×1（8格外）；"
    "主人位置 (121,-60,9)。"
)

TASKS = [
    ("砍树·目标数量", "帮我把这附近的橡树砍 5 棵。"),
    ("采集·凑够回来", "去砍点木头，凑够 6 个就回来。"),
    ("挖矿", "去挖点铁矿石吧。"),
    ("打猪", "把那边那头猪打掉，我要猪肉。"),
    ("存箱子", "把背包里的石头都收进旁边的箱子。"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="maid")
    ap.add_argument("--mode", type=int, default=3)
    ap.add_argument("--pet-name", default="大肥鱼")
    ap.add_argument("--out", default="_api_assembly_result.txt")
    a = ap.parse_args()

    from ai.deepseek_api import DeepSeekApiClient
    key = ai_client.load_deepseek_api_key()
    model = ai_client.load_deepseek_api_model()
    if not key:
        print("API Key 未配置（ai/deepseek-api/key）")
        return
    client = DeepSeekApiClient(key, model)
    be = MessagesBackend(client)

    sysp = config.get_api_prompt(a.persona, a.mode, a.pet_name, style="slim")
    print("=" * 66)
    print("后端 = deepseek-api   模型 = %s   人格 = %s   宠物名 = %s"
          % (model, a.persona, a.pet_name))
    print("人设文件 = %s" % config.persona_prompt_file(
        config.persona_info(a.persona), backend=True, style="slim"))
    print("人设长度 = %d 字   mode = %d" % (len(sysp), a.mode))
    print("=" * 66)

    lines = []
    w = lines.append
    for name, msg in TASKS:
        ctx = ChatContext()                 # 每轮隔离（无历史）
        tr = TransientContext(status_full=MAID_STATE)
        built = be.build(ctx, tr, msg, system_prompt=sysp, mode_id=a.mode)
        w("")
        w("#" * 66)
        w("# 任务: %s | %s" % (name, msg))
        w("# 发出 messages: %d 条  roles=%s" % (
            len(built), [m["role"] for m in built]))
        t0 = time.time()
        try:
            reply = be.request(built)
        except Exception as e:
            reply = "<调用失败: %s: %s>" % (type(e).__name__, e)
        w("耗时: %.1fs" % (time.time() - t0))
        w("--- AI 回复 ---")
        w(reply)
        w("--- 分析 ---")
        analyze(name, reply, w)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), a.out)
    io.open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n结果已写:", out)


def analyze(name, content, w):
    scripts = re.findall(r"【script\(([\s\S]*?)\)】", content)
    if scripts:
        for s in scripts:
            try:
                d = json.loads(s)
                steps = d.get("steps", [])
                cmds = []
                flags = {"loop": False, "terminate": False, "assign": False}
                _walk(steps, cmds, flags)
                has_find = "find" in cmds
                has_harvest = "harvest" in cmds
                vars_ok = ("collected" in d.get("vars", {})
                           if flags["assign"] else True)
                w("script: 顶层steps=%d | find=%s harvest=%s loop=%s assign=%s terminate=%s | vars.collected=%s"
                  % (len(steps), has_find, has_harvest, flags["loop"],
                     flags["assign"], flags["terminate"], vars_ok))
                w("  cmds(递归): %s" % cmds)
                if has_harvest and not flags["loop"]:
                    w("  !! 有 harvest 但无 loop——砍一棵就停，凑不够目标")
                if flags["assign"] and not vars_ok:
                    w("  !! assign 计数但 vars 没初始化 collected")
                if not flags["terminate"]:
                    w("  !! 无 terminate——脚本结束没回执")
            except Exception as e:
                w("script 解析失败: %s" % e)
        return
    singles = re.findall(r"【(\w+\([^】]*\))】", content)
    if singles:
        w("单条指令: %s" % singles)
        return
    w("无指令（纯对话？）")


def _walk(steps, cmds, flags):
    for st in steps or []:
        if "if" in st:
            for key in ("then", "else"):
                if key in st:
                    _walk(st[key], cmds, flags)
        if "loop" in st and isinstance(st.get("loop"), dict):
            flags["loop"] = True
            _walk(st["loop"].get("body", []), cmds, flags)
        if "assign" in st:
            flags["assign"] = True
        if "terminate" in st:
            flags["terminate"] = True
        if "cmd" in st:
            cmds.append(st["cmd"])


if __name__ == "__main__":
    main()
