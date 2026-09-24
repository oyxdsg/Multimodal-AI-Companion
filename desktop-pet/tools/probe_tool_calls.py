# -*- coding: utf-8 -*-
"""真机探针（P1-1）：验证原生 tool_calls 在真实后端上能否被正确返回。

针对支持 CAP_TOOLS 的后端（deepseek-api / qwen / anthropic / custom），
用 `mod_contract.to_openai_tools()` 作为 schema，发一个"需要调用工具"的请求，
检查：
1. 后端是否返回 `tool_calls`（而非只给文本）；
2. tool_calls 的 name 是否在女仆指令白名单内；
3. arguments 能否被 `mod_contract.clamp` 接受（结构性合法）。

用法：python tools/probe_tool_calls.py [--backend deepseek-api|qwen|...]
没有配置对应 API Key 时自动跳过（探针不报错）。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import client as ai_client
from ai import providers as prov
from nlu import mod_contract as mc

# 提示模型"只调工具不写字"，最大化触发 tool_calls 的概率
PROMPT = (
    "你是《我的世界》里的女仆。玩家对你说：\"去把那边那头猪打掉，我要猪肉。\"\n"
    "请直接调用合适的工具完成这件事，不要输出任何文字解释。"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="deepseek-api",
                    help="deepseek-api / qwen / anthropic / custom")
    ap.add_argument("--out", default="_tool_calls_probe.txt")
    a = ap.parse_args()
    pid = a.backend

    if not prov.supports(pid, "", "tools"):
        print("后端 %s 不支持 CAP_TOOLS（或未安装），跳过" % pid)
        return

    from ai.protocols import make_adapter
    try:
        adapter = make_adapter(pid)
    except Exception as e:
        print("后端 %s 未配置 / 构造失败：%s（跳过）" % (pid, e))
        return
    tools = mc.to_openai_tools()
    msgs = [{"role": "user", "content": PROMPT}]

    lines = []
    w = lines.append
    w("=" * 66)
    w("后端 = %s    模型 = %s    工具数 = %d"
      % (pid, getattr(adapter, "model", "?"), len(tools)))
    w("=" * 66)
    try:
        msg = adapter.chat_message(msgs, tools=tools)
    except Exception as e:
        w("!! 调用失败: %s" % e)
        return
    w("assistant content = %r" % (msg.get("content") or ""))
    calls = msg.get("tool_calls") or []
    if not calls:
        w("!! 后端未返回 tool_calls（只返回文本）。")
        w("   可能原因：该端点/模型不支持工具调用，或模型选择先解释。")
        return
    w("tool_calls 数 = %d" % len(calls))
    ok_all = True
    for c in calls:
        fn = c.get("function") or {}
        name = fn.get("name") or ""
        args_raw = fn.get("arguments") or "{}"
        w("  - %s(%s)" % (name, args_raw))
        if name not in mc.COMMANDS or \
                mc.COMMANDS[name].layer == "meta-dryrun":
            w("    !! 不在可下发白名单内")
            ok_all = False
            continue
        try:
            args = json.loads(args_raw)
        except ValueError:
            w("    !! arguments 不是合法 JSON")
            ok_all = False
            continue
        missing = mc.missing_required(name, args)
        if missing:
            w("    !! 缺必填参数: %s" % "、".join(missing))
            ok_all = False
            continue
        clean, dropped = mc.clamp(name, args)
        w("    -> clamp 后 = %s（丢弃 %s）" % (clean, dropped or "无"))
    w("=" * 66)
    w("结论: %s" % ("全部工具调用结构性合法" if ok_all else "存在结构问题"))

    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    for line in lines:
        print(line)
    print("\n>>> 结果写入", a.out)


if __name__ == "__main__":
    main()
