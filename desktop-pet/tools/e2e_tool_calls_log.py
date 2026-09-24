# -*- coding: utf-8 -*-
"""端到端真机测试（P1-1 双通道）：用**历史玩家日志**驱动原生 tool_calls。

数据源：``logs/chat_trace.log`` 里真实的 USER 指令（去重取前 N 条）。
链路：真实 deepseek-api 适配器 + 精简人设（含女仆指令清单 + 游戏模式）
       → ``_ChatWorker`` 原生 tool_calls 通道 → mock 女仆链路（只记录不下发）。

用法：python tools/e2e_tool_calls_log.py [--limit 8] [--backend deepseek-api]
需要已配置对应 API Key；缺 Key 时优雅跳过。
"""
import argparse
import io
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import ai.chat as chat_mod
from ai import providers as prov
from ai.chat import _ChatWorker
from ai.protocols import make_adapter
from game.maid_loop import MaidLoop

# 避免污染真实日志：把 trace 重定向到临时文件
_TMP_TRACE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_e2e_tool_calls_trace.log")
chat_mod._CHAT_TRACE = _TMP_TRACE


class _Link:
    """mock 女仆链路：connected + request + status_line（只记录不下发）。"""

    connected = True

    def __init__(self):
        self.sent = []

    def request(self, cmd, params, cancel_previous=False, timeout=5.0):
        self.sent.append((cmd, params, cancel_previous))
        return {"ok": True, "state": "running", "result": {}}

    def status_line(self, maid=""):
        return ("生命=18，位置 (120,-60,10)，任务 无；背包：铁镐×1、铁锭×8、"
                "木头×16；附近：橡树×2、铁矿×1、僵尸×1（8格外）")


def _player_commands(trace_path, limit):
    """从历史 chat_trace 提取真实玩家指令（去重、去空）。"""
    cmds = []
    with open(trace_path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if "USER" not in ln:
                continue
            m = re.search(r"USER\s*\|\s*(.*)$", ln)
            if not m:
                continue
            text = m.group(1).strip()
            if len(text) < 2 or text in cmds:
                continue
            cmds.append(text)
    return cmds[:limit]


def _opts(pid):
    return {
        "backend": pid,
        "ai_mode": 3,                 # 游戏模式（女仆指令全域可用）
        "persona": "maid",
        "pet_name": "大肥鱼",
        "prompt_style": "slim",       # API 精简人设
        "thinking": False,
        "thinking_variant": "off",
        "search": False,
        "memory": True,
        "model": "deepseek-flash",
        "provider_model": "deepseek-flash",
        "voice_mode": "off",
        "audio_voice": "",
        "qwen_model": "qwen-turbo",
        "prompt": "你是女仆",          # 网页版用（本测试走 API，不生效）
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--backend", default="deepseek-api")
    a = ap.parse_args()
    pid = a.backend

    trace = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "logs", "chat_trace.log")
    if not os.path.exists(trace):
        print("!! 找不到历史日志:", trace)
        return
    cmds = _player_commands(trace, a.limit)
    if not cmds:
        print("!! 历史日志里没有 USER 指令")
        return
    # 补一组历史中真实出现过的**明确指令**，用于验证 tool_calls 真正触发
    # （闲聊叙述类历史话术本就不应触发工具）
    for extra in ("去打僵尸", "去挖矿", "穿金胸甲", "看一下周围",
                  "把旁边那头猪杀掉"):
        if extra not in cmds:
            cmds.append(extra)

    if not prov.supports(pid, "", "tools"):
        print("后端 %s 不支持 CAP_TOOLS，跳过" % pid)
        return
    try:
        client = make_adapter(pid)
    except Exception as e:
        print("后端 %s 未配置 / 构造失败：%s（跳过）" % (pid, e))
        return

    print("=" * 70)
    print("双通道端到端（真实玩家日志 → 原生 tool_calls → mock 女仆）")
    print("后端 = %s    模型 = %s    指令数 = %d" % (pid, getattr(client, "model", "?"), len(cmds)))
    print("数据源 = %s" % trace)
    print("=" * 70)

    ok = 0
    for i, cmd in enumerate(cmds, 1):
        link = _Link()
        loop = MaidLoop(link)
        worker = _ChatWorker(client, cmd, _opts(pid), maid_loop=loop)
        got = []
        worker.finished.connect(lambda t, e: got.append((t, e)))
        t0 = time.time()
        try:
            worker.run()
        except Exception as e:
            print("[%2d] %-16s  ✗ 异常: %s" % (i, cmd, e))
            continue
        dt = time.time() - t0
        if not got:
            print("[%2d] %-16s  ? 无结果" % (i, cmd))
            continue
        text, err = got[0]
        issued = ", ".join("%s(%s)" % (n, json_s(p))
                           for n, p, _c in link.sent) or "（无）"
        chan = "原生" if worker._native_tools_enabled() else "DSL"
        mark = "✓" if not err and (link.sent or not worker.dsl_inject) else "✗"
        if not err:
            ok += 1
        print("[%2d] %-16s %s 通道=%s 耗时=%.1fs 正文=%r" % (i, cmd, mark, chan, dt, text[:30]))
        print("     下发 → %s" % issued)
    print("=" * 70)
    print("结果：%d/%d 正常返回" % (ok, len(cmds)))
    print("（trace 已写入 %s）" % _TMP_TRACE)


def json_s(p):
    import json
    return json.dumps(p, ensure_ascii=False)


if __name__ == "__main__":
    main()
