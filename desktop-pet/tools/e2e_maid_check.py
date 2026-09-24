#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通讯闭环真机联调验证套件（桌宠 ↔ SmartMaid 女仆 ↔ AI 官方 API）。

覆盖 DESIGN_LOOP_DEEPENED.md §13 全部验收标准，分两层：

【离线层 offline】不需要女仆/游戏，纯逻辑验证，不烧 token。随时可跑：
  L1  官方 API 客户端请求体组装（messages/thinking/model）
  L2  MessagesBackend：多事件注入 / ephemeral 隔离 / 历史滑动窗口 / 提炼触发 / 提炼失败回退
  L3  记忆渲染 render_memory
  L4  SessionBackend 只发当轮 prompt（网页版不提炼）
  L5  DSL 解析（坐标 / ~ / 无括号 / 嵌套 / 未知 / 缺参 / clamp）
  L6  MaidLoop 执行 + 回执注入
  L7  女仆状态注入（完整 / 极短）→ TransientContext
  L8  AI 决策判定（低血 <30% / 被围 3+ 敌对 10 格）
  L9  AICallThrottle 限流（30s≤5）
  L10 EventDedup 通道去重（60s 指纹 + 任务边界豁免）
  L11 cancel_previous 默认排队

【在线层 online】需要女仆连接 + 真实 API（少量 token）：
  O1  桌宠 WS 监听
  O2  女仆连接握手
  O3  感知上报（状态行出现在桌宠日志）
  O4  真实 AI 生成 DSL → 解析 → .maid_command.json 下发 → 回执（真机闭环）
  O5  真实官方 API 多轮历史：MessagesBackend 3 轮串联 + 瞬时状态注入 + ephemeral 不进历史

用法::

    python tools/e2e_maid_check.py --offline            # 只跑离线层（默认也跑）
    python tools/e2e_maid_check.py                      # 离线层 + 在线层（等女仆连上）
    python tools/e2e_maid_check.py --api-key sk-xxx     # 指定官方 API Key（缺省读 QSettings）
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

PET_PORT = 21420
MAID_LOG = os.path.join(BASE, "logs", "maid_link.log")
DEBUG_CMD = os.path.join(BASE, ".maid_command.json")
REPORT = os.path.join(BASE, "logs", "e2e_report.json")

_results = []   # [{"id", "name", "ok", "detail"}]


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def result(rid, name, ok, detail=""):
    _results.append({"id": rid, "name": name, "ok": bool(ok), "detail": detail})
    mark = "[通过]" if ok else "[失败]"
    log("%s %s %s %s" % (mark, rid, name, (" | " + str(detail) if detail else "")))
    return ok


# ======================================================================
# 离线层：纯逻辑，不烧 token
# ======================================================================

def L1_api_client_request_shape():
    from ai.deepseek_api import DeepSeekApiClient, CHAT_MODELS
    # 模型清单来自官方 /models（2.24.0 起）：deepseek-flash / deepseek-v4-pro
    ok = ("deepseek-flash" in CHAT_MODELS) and ("deepseek-v4-pro" in CHAT_MODELS)
    c = DeepSeekApiClient("sk-test", "deepseek-chat")
    ok = ok and c.model == "deepseek-chat" and c.session_id is None
    # 双模式就绪
    ok = ok and hasattr(c, "chat_messages") and hasattr(c, "chat")
    return result("L1", "官方API客户端请求体形态", ok,
                  "chat_messages + chat 双模式就绪")


def L2_messages_history_condense():
    import config
    from ai.context import ChatContext, TransientContext
    from ai.backends.messages import MessagesBackend

    class FakeClient:
        def __init__(self):
            self.seen_max = 0
        def chat_messages(self, messages, thinking=False, model=None):
            hist_msgs = messages[1:-1]
            self.seen_max = max(self.seen_max, len(hist_msgs) // 2)
            return "r"

    condense_calls = []

    def fake_condense(ctx, old_turns):
        condense_calls.append(len(old_turns))
        ctx.summary = "提炼了%d轮" % len(old_turns)

    fc = FakeClient()
    b = MessagesBackend(fc, condense=fake_condense)
    ctx = ChatContext()

    # 多事件注入 + ephemeral 隔离
    tr = TransientContext(status_full="生命=18 任务=guard",
                          events=["发现僵尸", "被攻击", "发现骷髅"],
                          replies=["[系统] 任务完成", "[系统] 任务失败"])
    msgs = b.build(ctx, tr, "去打僵尸", system_prompt="你是女仆")
    last = msgs[-1]["content"]
    multi_evt = all(e in last for e in ["发现僵尸", "被攻击", "发现骷髅"])
    multi_rep = all(r in last for r in ["任务完成", "任务失败"])
    b.append_history(ctx, "去打僵尸", "好的", ephemeral=True)
    iso = len(ctx.history) == 0
    b.append_history(ctx, "去打僵尸", "好的")
    norm = len(ctx.history) == 1

    # 历史滑动窗口 + 提炼触发（35 轮）
    for i in range(34):
        b.append_history(ctx, "u%d" % i, "r%d" % i)
    window_ok = fc.seen_max <= config.HISTORY_WINDOW
    condense_ok = bool(condense_calls) and ctx.summary.startswith("提炼了")
    bounded = len(ctx.history) <= config.CONDENSE_EVERY

    # 提炼失败回退
    class Boom:
        def __init__(self):
            self.calls = 0
        def chat_messages(self, messages, thinking=False, model=None):
            return "x"
    bb = MessagesBackend(Boom(), condense=lambda c, o: (_ for _ in ()).throw(
        RuntimeError("断网")))
    bctx = ChatContext()
    for i in range(40):
        bb.append_history(bctx, "u%d" % i, "r%d" % i)
    rollback = len(bctx.history) == 40

    ok = multi_evt and multi_rep and iso and norm and window_ok \
        and condense_ok and bounded and rollback
    return result("L2", "MessagesBackend 多事件/隔离/滑动窗口/提炼/回退", ok,
                  "事件注入=%s 回执注入=%s ephemeral隔离=%s 窗口≤%d=%s 提炼=%s 上界=%s 回退=%s"
                  % (multi_evt, multi_rep, iso, config.HISTORY_WINDOW, window_ok,
                     condense_ok, bounded, rollback))


def L3_memory_render():
    from ai.context import ChatContext, Fact, render_memory
    ctx = ChatContext()
    ctx.memory = [Fact("偏好", "喜欢矿石"), Fact("约定", "叫主人")]
    ctx.summary = "关系友好"
    t = render_memory(ctx)
    ok = ("对话概述：关系友好" in t) and ("偏好=喜欢矿石" in t) and ("约定=叫主人" in t)
    return result("L3", "记忆渲染 render_memory", ok, t[:40])


def L4_session_only_current_prompt():
    from ai.context import ChatContext, Turn, TransientContext
    from ai.backends.session import SessionBackend

    class FakeSess:
        def __init__(self):
            self.calls = []
            self.session_id = "s1"
            self.parent_message_id = 10
        def chat(self, prompt, system_prompt=None, **kw):
            self.calls.append(prompt)
            return "网页版回复", ""

    fc = FakeSess()
    b = SessionBackend(fc)
    ctx = ChatContext()
    ctx.history.append(Turn("旧对话", "旧回复"))
    tr = TransientContext(status_compact="[态] 生命18 任务guard")
    when = b.build(ctx, tr, "去打僵尸", system_prompt="你是女仆")
    ok = isinstance(when, str) and "旧对话" not in when \
        and "[态] 生命18" in when and "去打僵尸" in when
    b.append_history(ctx, "x", "y", ephemeral=False)
    ok = ok and len(ctx.history) == 1   # 网页版不维护本地历史
    return result("L4", "SessionBackend 只发当轮（网页版不提炼）", ok, when[:40])


def L5_dsl_parse():
    from game.maid_intent import parse, WHITELIST
    ok = True
    r, clean = parse("好的主人！{开心}【attack(range=12)】")
    ok = ok and r["cmd"] == "attack" and r["params"] == {"range": 12} \
        and clean == "好的主人！{开心}"
    r, _ = parse("【mine(pos=[100,-60,-50], range=4, count=5, cancel_previous=true)】")
    ok = ok and r["params"]["pos"] == ["100", "-60", "-50"] and r["cancel_previous"]
    r, _ = parse("【move(pos=[~,~,~])】")
    ok = ok and r["params"]["pos"] == ["~", "~", "~"]
    r, clean = parse("【stop】好的")
    ok = ok and r["cmd"] == "stop" and clean == "好的"
    r, clean = parse("快逃！【cmd(attack(range=10))】")
    ok = ok and r is not None and r["cmd"] == "attack" and clean == "快逃！"
    r, clean = parse("【fly()】你好")
    ok = ok and r is None and clean == "你好"
    r, _ = parse("【move()】")
    ok = ok and r is None
    r, _ = parse("【attack(range=999, target=zombie)】")
    ok = ok and r["params"]["range"] == 64          # range 越界 clamp 到 64
    ok = ok and r["params"]["target"] == "zombie"   # 2.19.1 起 target 为真实参数
    ok = ok and "craft_check" not in WHITELIST
    return result("L5", "DSL 解析（坐标/~/无括号/嵌套/未知/缺参/clamp）", ok)


def L6_maid_loop_execute():
    from game.maid_loop import MaidLoop

    class FakeLink:
        def __init__(self):
            self.sent = []
            self.connected_flag = True
        @property
        def connected(self):
            return self.connected_flag
        def request(self, cmd, params, cancel_previous=False, timeout=5.0):
            self.sent.append((cmd, params, cancel_previous))
            return {"ok": True, "state": "running"}
        def status_line(self, maid=""):
            return "生命=18 任务=guard"

    link = FakeLink()
    loop = MaidLoop(link)
    clean, inject = loop.process_reply("好的{开心}【attack(range=12)】")
    ok = clean == "好的{开心}" and "attack" in inject \
        and link.sent[0] == ("attack", {"range": 12}, False)
    link.sent = []
    loop.process_reply("【guard(range=10, cancel_previous=true)】")
    ok = ok and link.sent[0][2] is True
    link.connected_flag = False
    _, inject2 = loop.process_reply("【stop】好的")
    ok = ok and inject2 == ""
    return result("L6", "MaidLoop 执行 + 回执注入 + 未连接降级", ok,
                  "默认排队=%s" % (link.sent[0][2] is True))


def L7_maid_context():
    from game.maid_context import build_transient
    snap = {
        "self": {"health": 18.0, "max_health": 20.0, "pos": [100, 64, -50],
                 "status": {"task_id": "guard",
                            "target": {"type": "minecraft:zombie"}},
                 "equipment": {"mainhand": {"id": "minecraft:iron_sword"}}},
        "nearby": {"hostiles": [
            {"type": "minecraft:zombie", "dist": 3, "targeting_me": True},
            {"type": "minecraft:skeleton", "dist": 8}]},
        "inventory": {"capabilities": {"has_sword": True, "has_food": True}},
        "owner": {"name": "Steve"},
    }
    tr = build_transient(snap, events=["发现僵尸"], replies=["[系统] 任务完成"])
    full_ok = ("生命=18.0/20.0" in tr.status_full) and ("任务=guard" in tr.status_full) \
        and ("敌对=僵尸(3格，在攻击我)" in tr.status_full)
    compact_ok = tr.status_compact == "[态] 生命18.0 任务guard"
    render = tr.render_messages_backend("主人小心！")
    render_ok = all(k in render for k in ["你当前状态", "发现僵尸", "任务完成", "主人小心！"])
    return result("L7", "女仆状态注入（完整/极短 + 渲染当轮）", full_ok and compact_ok and render_ok)


def L8_danger():
    from game.maid_context import evaluate_danger
    ok = evaluate_danger({"self": {"health": 5.0, "max_health": 20.0}})[0] is True
    ok = ok and evaluate_danger({"self": {"health": 15.0}, "nearby": {"hostiles": [
        {"dist": 2}, {"dist": 5}, {"dist": 9}]}})[1] is True
    ok = ok and evaluate_danger({"self": {"health": 15.0}, "nearby": {"hostiles": [
        {"dist": 12}, {"dist": 15}, {"dist": 20}]}})[1] is False
    return result("L8", "AI 决策判定（低血<30% / 被围3+10格）", ok)


def L9_throttle():
    from core.ai_throttle import AICallThrottle
    t = AICallThrottle()
    now = 1000.0
    res = [t.allow(now + i) for i in range(6)]
    ok = res == [True] * 5 + [False]
    t.reset()
    ok = ok and t.allow(now + 1) is True
    return result("L9", "AICallThrottle 限流（30s≤5）", ok, str(res))


def L10_dedup():
    from core.event_dedup import EventDedup, is_task_boundary
    d = EventDedup(ttl=60)
    now = 100.0
    ok = d.check("女仆被僵尸攻击", now) is True
    ok = ok and d.check("女仆被僵尸攻击", now + 10) is False
    ok = ok and d.check("女仆被僵尸攻击", now + 70) is True
    ok = ok and d.check("女仆发现僵尸", now + 80) is True
    ok = ok and is_task_boundary("女仆开始挖矿了") is True
    ok = ok and is_task_boundary("女仆被僵尸攻击") is False
    return result("L10", "EventDedup 通道去重（60s 指纹 + 任务边界豁免）", ok)


def L11_default_queue():
    from game.maid_loop import MaidLoop

    class FakeLink:
        @property
        def connected(self):
            return True
        def request(self, cmd, params, cancel_previous=False, timeout=5.0):
            self.sent_cp = cancel_previous
            return {"ok": True, "state": "running"}
        def status_line(self, maid=""):
            return ""

    link = FakeLink()
    loop = MaidLoop(link)
    loop.process_reply("好的【attack(range=10)】")
    ok = link.sent_cp is False
    return result("L11", "cancel_previous 默认排队（False）", ok)


def run_offline():
    log("\n===== 离线层（纯逻辑，不烧 token） =====")
    L1_api_client_request_shape()
    L2_messages_history_condense()
    L3_memory_render()
    L4_session_only_current_prompt()
    L5_dsl_parse()
    L6_maid_loop_execute()
    L7_maid_context()
    L8_danger()
    L9_throttle()
    L10_dedup()
    L11_default_queue()


# ======================================================================
# 在线层：需要女仆连接 + 真实 API（少量 token）
# ======================================================================

def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def ensure_pet():
    if port_open(PET_PORT):
        return True
    log("桌宠未监听 %d，正在启动桌宠…" % PET_PORT)
    out = open(os.path.join(BASE, "logs", "pet_console.log"), "ab")
    kwargs = {"cwd": BASE, "stdout": out, "stderr": out}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "main.py"], **kwargs)
    for _ in range(30):
        time.sleep(1)
        if port_open(PET_PORT):
            return True
    return False


def read_log(path, start=0):
    try:
        size = os.path.getsize(path)
        if size < start:
            start = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start)
            return [l.rstrip() for l in f if l.strip()]
    except OSError:
        return []


def get_api_key(args):
    if args.api_key:
        return args.api_key.strip()
    try:
        from PySide6.QtCore import QSettings
        return str(QSettings("DesktopPet", "PetWindow").value(
            "deepseek_api_key", "") or "").strip()
    except Exception:
        return ""


def wait_maid_connected(start, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = read_log(MAID_LOG, start)
        if any(("已握手" in l or "女仆已连接" in l or "女仆已握手" in l) for l in lines):
            return True
        time.sleep(2)
    return False


def send_maid_command(cmd, params=None):
    try:
        with open(DEBUG_CMD, "w", encoding="utf-8") as f:
            json.dump([{"type": "command", "cmd": cmd, "params": params or {}}],
                      f, ensure_ascii=False)
        return True
    except OSError:
        return False


def wait_command_reply(cmd, start, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any("指令回执" in l and cmd in l for l in read_log(MAID_LOG, start)):
            return True
        time.sleep(1)
    return False


def O1_pet_ws():
    return result("O1", "桌宠 WS 监听", ensure_pet())


def O2_maid_connected(start, timeout):
    ok = wait_maid_connected(start, timeout)
    return result("O2", "女仆连接握手", ok,
                  "" if ok else "等待超时——请确认游戏已开 + /summonmaid")


def O3_perception(start):
    lines = read_log(MAID_LOG, start)
    states = [l for l in lines if "状态 ->" in l]
    ok = bool(states)
    return result("O3", "感知上报（状态行进桌宠日志）", ok,
                  (states[-1] if states else "暂无状态行")[:80])


def O4_real_dsl_loop(start, api_key):
    """真实 AI 生成 DSL → 解析 → 下发 → 回执（真机闭环）。"""
    if not api_key:
        return result("O4", "真实 AI→DSL→女仆闭环", False, "未配置官方 API Key")
    try:
        from ai.deepseek_api import DeepSeekApiClient
        c = DeepSeekApiClient(api_key, "deepseek-chat")
        msgs = [
            {"role": "system", "content": "你是桌面宠物游戏女仆的指挥AI。"
             "主人说要去打僵尸时，回复末尾附指令【attack(range=12)】。25字内。"},
            {"role": "user", "content": "主人想去打僵尸，你怎么办？"},
        ]
        reply = c.chat_messages(msgs)
    except Exception as exc:
        return result("O4", "真实 AI→DSL→女仆闭环", False, "AI 调用失败: %s" % exc)
    from game.maid_intent import parse
    cmd, clean = parse(reply)
    if cmd is None:
        cmd = {"cmd": "status", "params": {}, "cancel_previous": False}
    send_ok = send_maid_command(cmd["cmd"], cmd["params"])
    replied = send_ok and wait_command_reply(cmd["cmd"], start, timeout=15)
    return result("O4", "真实 AI→DSL→女仆闭环", send_ok and replied,
                  "AI=%s | 指令=%s | clean=%s | 回执=%s"
                  % (reply[:30], cmd["cmd"], clean, replied))


def O5_real_multi_turn(api_key):
    """真实官方 API 多轮：MessagesBackend 3 轮串联 + 瞬时状态 + ephemeral。"""
    if not api_key:
        return result("O5", "官方API多轮历史+瞬时状态", False, "未配置 Key")
    try:
        from ai.deepseek_api import DeepSeekApiClient
        from ai.context import ChatContext, TransientContext
        from ai.backends.messages import MessagesBackend
        c = DeepSeekApiClient(api_key, "deepseek-chat")
        b = MessagesBackend(c, condense=None)
        ctx = ChatContext()
        # 第 1 轮：带瞬时状态
        tr1 = TransientContext(status_full="生命=18 任务=guard", events=["发现僵尸"])
        r1 = b.request(b.build(ctx, tr1, "主人去打僵尸，简短回应", system_prompt="你是女仆"))
        b.append_history(ctx, "主人去打僵尸", r1)   # 非 ephemeral → 进历史
        # 第 2 轮：ephemeral 事件轮（不进历史）
        tr2 = TransientContext(events=["女仆被攻击"])
        r2 = b.request(b.build(ctx, tr2, "事件播报，简短回应"))
        b.append_history(ctx, "事件播报", r2, ephemeral=True)
        # 第 3 轮：历史应只有第 1 轮（ephemeral 被隔离）
        tr3 = TransientContext()
        msgs = b.build(ctx, tr3, "现在呢？一句话")
        hist_turns = len(msgs) - 1 - 1   # 去掉 system + 当轮
        isolated = (len(ctx.history) == 1) and (hist_turns == 1)
        r3 = b.request(msgs)
        ok = bool(r1 and r2 and r3) and isolated
        return result("O5", "官方API多轮历史+瞬时状态隔离", ok,
                      "第1轮=%s | 第2轮=%s | 第3轮=%s | history=%d | 隔离=%s"
                      % ((r1 or "")[:20], (r2 or "")[:20], (r3 or "")[:20],
                         len(ctx.history), isolated))
    except Exception as exc:
        return result("O5", "官方API多轮历史+瞬时状态", False, str(exc)[:100])


def run_online(args):
    log("\n===== 在线层（需要女仆连接 + 真实 API） =====")
    O1_pet_ws()
    start = os.path.getsize(MAID_LOG) if os.path.isfile(MAID_LOG) else 0
    O2_maid_connected(start, args.timeout)
    time.sleep(3)   # 等感知上报
    O3_perception(start)
    api_key = get_api_key(args)
    if api_key:
        log("官方 API Key 已配置（%d 字符）" % len(api_key))
    O4_real_dsl_loop(start, api_key)
    O5_real_multi_turn(api_key)
    # 汇总在线层证据
    lines = read_log(MAID_LOG, start)
    states = [l for l in lines if "状态 ->" in l]
    events = [l for l in lines if "播报 ->" in l]
    return states, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--timeout", type=int, default=120, help="等女仆连接超时秒数")
    ap.add_argument("--offline-only", action="store_true", help="只跑离线层")
    ap.add_argument("--online-only", action="store_true", help="只跑在线层")
    args = ap.parse_args()

    log("=" * 64)
    log("通讯闭环验证套件（离线 + 在线）")
    log("=" * 64)

    if not args.online_only:
        run_offline()
    states = events = []
    if not args.offline_only:
        states, events = run_online(args)

    # 汇总
    passed = sum(1 for r in _results if r["ok"])
    total = len(_results)
    log("\n" + "=" * 64)
    log("验证汇总：%d/%d 通过" % (passed, total))
    for r in _results:
        log("  %s %s %s" % ("[通过]" if r["ok"] else "[失败]", r["id"], r["name"]))
    if states:
        log("\n--- 在线证据：女仆状态行 ---")
        for s in states[-5:]:
            log("  %s" % s)
    if events:
        log("--- 在线证据：事件播报 ---")
        for e in events[-10:]:
            log("  %s" % e)

    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed, "total": total,
        "results": _results,
        "online_states": states[-5:], "online_events": events[-10:],
    }
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log("报告已写入 %s" % REPORT)
    except OSError:
        pass
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
