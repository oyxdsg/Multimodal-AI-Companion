#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路真机测试（状态接地 + 前置门控版）。

核心变化
--------
不再「盲打」指令。每条用例先读女仆全量感知，**确认目标真的存在**才发指令：
有猪才打猪、地上有掉落物才收集、主手有东西才收纳、背包有材料才合成。
缺前置的用例**跳过**并告诉你「缺什么」，由你把场景搭好再跑。

流程：``--setup`` 先看哪些用例就绪 → 搭场景 → 逐条：读状态(前置检查) → 发指令 → 落地 → 读状态 diff → 判执行

证据：``logs/maid_snapshot.json``（感知全量）+ ``logs/chat_trace.log`` + ``logs/maid_link.log``。

用法::
    python tools/chat_chain_test.py --setup      # 只读状态，列每条「就绪/缺什么」
    python tools/chat_chain_test.py              # 跑全部（缺前置的自动跳过）
    python tools/chat_chain_test.py --only S4    # 只跑一条
    python tools/chat_chain_test.py --snapshot   # 只看当前状态
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(BASE, "logs")
CHAT_IN = os.path.join(BASE, ".chat_input.json")
STATE_REQ = os.path.join(BASE, ".maid_state.json")
SNAPSHOT = os.path.join(LOGS, "maid_snapshot.json")
PERF = os.path.join(LOGS, "perf.log")
TRACE = os.path.join(LOGS, "chat_trace.log")
MAID = os.path.join(LOGS, "maid_link.log")
CRASH = os.path.join(LOGS, "crash.log")


# 前置条件：用世界视图判断「目标是否真的存在」。
def has_item(v, item_id, n=1):
    return (v.get("inventory") or {}).get(item_id, 0) >= n


def _hostiles(v):
    return v.get("nearby", {}).get("hostiles") or []


def _ground_items(v):
    return v.get("nearby", {}).get("items") or []


# (id, 消息, 前置描述, 前置检查(world_view)->bool, 判据)
CASES = [
    ("S1", "把地上的东西都捡起来", "地上有掉落物", lambda v: bool(_ground_items(v)),
     "背包新增物品 / 地上物品消失"),
    ("S2", "把手上的东西收起来", "女仆主手非空", lambda v: v.get("mainhand") not in (None, "minecraft:air"),
     "主手变 air"),
    ("S3", "换上你的铁镐", "背包里有铁镐", lambda v: has_item(v, "minecraft:iron_pickaxe"),
     "主手变 minecraft:iron_pickaxe"),
    ("S4", "把钻石丢给我", "背包里有钻石", lambda v: has_item(v, "minecraft:diamond"),
     "背包钻石 -1 + 地上出现掉落"),
    ("S5", "帮我合成一把铁镐", "背包有 3 铁锭 + 2 木棍",
     lambda v: has_item(v, "minecraft:iron_ingot", 3) and has_item(v, "minecraft:stick", 2),
     "背包新增 iron_pickaxe"),
    ("S6", "去打最近的怪", "附近有敌对怪", lambda v: bool(_hostiles(v)),
     "任务=战斗 / 敌对怪掉血或消失"),
    ("S7", "守在我身边", "无（女仆须活着）", lambda v: v.get("maid_alive"),
     "任务=护卫 或 女仆靠近主人"),
    ("S8", "到我身边来", "无（女仆须活着）", lambda v: v.get("maid_alive"),
     "女仆向主人移动（距离缩小）"),
    ("S9", "今天天气怎么样呀", "无（闲聊对照）", lambda v: v.get("maid_alive"),
     "状态应无变化"),
]


def size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def tail(p, off):
    try:
        with open(p, "rb") as f:
            f.seek(off)
            raw = f.read()
    except OSError:
        return "(无)\n", off
    return raw.decode("utf-8", "replace"), off + len(raw)


def pet_alive():
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             errors="replace", timeout=15).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if ":21420" in line and "LISTENING" in line:
            return line.split()[-1]
    return None


# ---------------------------------------------------------------------------
# 状态读取与视图
# ---------------------------------------------------------------------------

def read_state(timeout=5.0):
    """请求桌宠转储感知全量（用 mtime 判断更新），失败返回 None。"""
    try:
        before = os.path.getmtime(SNAPSHOT)
    except OSError:
        before = 0.0
    t0 = time.time()
    try:
        with io.open(STATE_REQ, "w", encoding="utf-8") as f:
            json.dump({}, f)
    except OSError:
        return None
    while time.time() - t0 < timeout:
        time.sleep(0.25)
        try:
            if os.path.getmtime(SNAPSHOT) > before and os.path.getsize(SNAPSHOT) > 2:
                with io.open(SNAPSHOT, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, ValueError):
            pass
    return None


def world_view(payload):
    if not payload:
        return {"connected": False, "read_ok": False}
    snap = payload.get("snapshot") or {}
    self_ = snap.get("self") or {}
    status = self_.get("status") or {}
    equip = self_.get("equipment") or {}
    owner = snap.get("owner") or {}
    nearby = snap.get("nearby") or {}
    inv = snap.get("inventory") or {}

    def pos(v):
        v = v or []
        return [round(x) for x in v[:3] if isinstance(x, (int, float))]

    nearby_summary = {}
    nearby_types = set()
    if isinstance(nearby, dict):
        for k in ("hostiles", "animals", "players", "items"):
            lst = nearby.get(k) or []
            if lst:
                nearby_summary[k] = sorted(
                    "%s@%.0f" % (str(e.get("type") or k), float(e.get("dist") or 0))
                    for e in lst if isinstance(e, dict))
                nearby_types.update(str(e.get("type") or k) for e in lst if isinstance(e, dict))
    elif isinstance(nearby, list):
        nearby_summary["entities"] = sorted(
            "%s@%.0f" % (str(e.get("type") or "?"), float(e.get("dist") or 0))
            for e in nearby if isinstance(e, dict))
        nearby_types.update(str(e.get("type") or "?") for e in nearby if isinstance(e, dict))

    items = {}
    slots = inv.get("slots") if isinstance(inv, dict) else (inv if isinstance(inv, list) else [])
    for s in slots:
        if isinstance(s, dict) and s.get("id") and s.get("id") != "minecraft:air":
            items[str(s["id"])] = items.get(str(s["id"]), 0) + int(s.get("count") or 0)

    hp = self_.get("health")
    return {
        "connected": bool(payload.get("connected")),
        "read_ok": True,
        "maid_alive": bool(hp is not None and hp > 0),
        "maid_pos": pos(self_.get("pos")),
        "task": status.get("task_id") or ("busy" if status.get("ai_busy") else "idle"),
        "ai_busy": status.get("ai_busy"),
        "target": (status.get("target") or {}).get("type"),
        "mainhand": (equip.get("mainhand") or {}).get("id"),
        "health": hp,
        "owner": owner.get("name"),
        "owner_pos": pos(owner.get("pos")),
        "owner_dist": owner.get("dist"),
        "nearby": nearby_summary,
        "nearby_types": sorted(nearby_types),
        "inventory": {k: items[k] for k in sorted(items)},
    }


def diff(a, b):
    a, b = a or {}, b or {}
    out = []
    for k in sorted(set(a) | set(b)):
        if json.dumps(a.get(k), sort_keys=True) != json.dumps(b.get(k), sort_keys=True):
            out.append((k, a.get(k), b.get(k)))
    return out


def pretty_state(v):
    """一行摘要，便于日志/汇报。"""
    if not v.get("read_ok"):
        return "读取失败"
    if not v.get("maid_alive"):
        return "女仆已死亡(血0)"
    return ("pos=%s hp=%s hand=%s task=%s 周边=%s 背包%d种"
            % (v.get("maid_pos"), v.get("health"), v.get("mainhand"),
               v.get("task"), v.get("nearby") or "{}", len(v.get("inventory") or {})))


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

# 判定只看「指令真正驱动的字段」：任务 / 主手 / 背包 / 忙碌。
# 位置、朝向、周边实体、心跳每帧都在变（怪自己会走动、进出感知半径），不能当证据。
BEHAVIOR = ("task", "ai_busy", "mainhand", "inventory")


def run_case(cid, msg, need, pre, criterion, outdir, quiet=False):
    pre_payload = None
    for _ in range(2):
        pre_payload = read_state()
        if pre_payload:
            break
        time.sleep(1.0)
    pre_view = world_view(pre_payload)

    if not pre_view.get("read_ok"):
        verdict = "[注意] 状态读取失败"
    elif not pre_view.get("maid_alive"):
        verdict = "💀 女仆已死亡，跳过"
    elif not pre(pre_view):
        verdict = "⏭️ 前置不满足：需要「%s」" % need
    else:
        o = {p: size(p) for p in (PERF, TRACE, MAID, CRASH)}
        t0 = time.time()
        with io.open(CHAT_IN, "w", encoding="utf-8") as f:
            json.dump({"text": msg}, f, ensure_ascii=False)
        consumed = None
        while time.time() - t0 < 20:
            if not os.path.isfile(CHAT_IN):
                consumed = time.time() - t0
                break
            time.sleep(0.3)
        ai_done = False
        while time.time() - t0 < 45:
            time.sleep(0.6)
            chunk, _ = tail(TRACE, o[TRACE])
            if "AI   |" in chunk:
                ai_done = True
                break
        time.sleep(7)                        # 等女仆落地
        post_view = world_view(read_state())
        perf_new, _ = tail(PERF, o[PERF])
        trace_new, _ = tail(TRACE, o[TRACE])
        maid_new, _ = tail(MAID, o[MAID])
        crash_new, _ = tail(CRASH, o[CRASH])

        dv = diff(pre_view, post_view)
        state_changed = any(k in BEHAVIOR for k, _a, _b in dv)
        task_events = [l for l in (maid_new or "").splitlines()
                       if ("播报" in l or "指令回执" in l)]

        if not post_view.get("read_ok"):
            verdict = "[注意] 指令后状态读取失败"
        elif not post_view.get("maid_alive"):
            verdict = "💀 执行中女仆死亡"
        elif state_changed or task_events:
            verdict = "[通过] 已执行（行为字段有变化）"
        else:
            verdict = "[失败] 未见可观测状态变化"

        with io.open(os.path.join(outdir, cid + ".txt"), "w", encoding="utf-8") as f:
            f.write("CASE      : %s\nMESSAGE   : %s\nNEED      : %s\nCRITERION : %s\n"
                    % (cid, msg, need, criterion))
            f.write("consumed  : %s s   ai=%s   elapsed %.1fs\n"
                    % (consumed, ai_done, time.time() - t0))
            f.write("VERDICT   : %s\n\n" % verdict)
            f.write("-- 前 --\n%s\n" % json.dumps(pre_view, ensure_ascii=False))
            f.write("\n-- 后 --\n%s\n" % json.dumps(post_view, ensure_ascii=False))
            f.write("\n-- diff --\n")
            for k, a, b in dv:
                f.write("  %s%-12s %s → %s\n" % ("★" if k in BEHAVIOR else " ", k, a, b))
            if not dv:
                f.write("  （无变化）\n")
            f.write("\n-- trace --\n" + (trace_new or "(无)\n"))
            f.write("\n-- maid_link(回执/播报) --\n"
                    + ("\n".join(task_events) + "\n" if task_events else "(无)\n"))
            f.write("\n-- perf --\n" + (perf_new or "(无)\n"))
            f.write("\n-- crash --\n" + (crash_new or "(无)\n"))

    if not quiet:
        print("  %-4s %s" % (cid, verdict))
    return {"id": cid, "verdict": verdict}


def setup_report():
    payload = read_state(timeout=6)
    v = world_view(payload)
    if not v.get("read_ok"):
        print("[失败] 读不到感知快照")
        return 1
    print("当前状态：%s" % pretty_state(v))
    print("周边：%s" % json.dumps(v.get("nearby"), ensure_ascii=False))
    print("背包：%s" % json.dumps(v.get("inventory"), ensure_ascii=False))
    print()
    print("%-4s %-22s %-28s %s" % ("ID", "消息", "前置", "就绪?"))
    print("-" * 80)
    for cid, msg, need, pre, _c in CASES:
        if not v.get("maid_alive"):
            ok = "💀女仆死"
        else:
            ok = "[通过]" if pre(v) else "[失败] 需搭场景"
        print("%-4s %-22s %-28s %s" % (cid, msg, need, ok))
    return 0


def snapshot_now():
    payload = read_state(timeout=6)
    if not payload:
        print("[失败] 读不到感知快照")
        return 1
    print(json.dumps(world_view(payload), ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="桌宠全链路真机测试（状态接地+前置门控）")
    ap.add_argument("--only", default="", help="只跑指定编号，逗号分隔")
    ap.add_argument("--out", default=os.path.join(BASE, "logs", "chain_test"))
    ap.add_argument("--setup", action="store_true", help="只看哪些用例就绪、缺什么")
    ap.add_argument("--snapshot", action="store_true", help="只看当前状态")
    ap.add_argument("--list", action="store_true", help="只列用例")
    args = ap.parse_args()

    if args.list:
        for cid, msg, need, _p, crit in CASES:
            print("%-4s %-22s 前置: %-20s 判据: %s" % (cid, msg, need, crit))
        return 0
    if args.snapshot:
        return snapshot_now()
    if args.setup:
        return setup_report()

    pid = pet_alive()
    if not pid:
        print("[失败] 21420 没有监听 —— 桌宠没在跑", file=sys.stderr)
        return 2
    print("桌宠在跑（PID=%s）" % pid)

    want = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    todo = [c for c in CASES if not want or c[0] in want]
    os.makedirs(args.out, exist_ok=True)

    results = []
    for cid, msg, need, pre, crit in todo:
        print("· %s %s" % (cid, msg))
        try:
            results.append(run_case(cid, msg, need, pre, crit, args.out))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("  %s 异常: %r" % (cid, e))
        time.sleep(2.0)

    from collections import Counter
    print("\n完成 %d 条：" % len(results))
    for v, n in Counter(r["verdict"] for r in results).most_common():
        print("  %-40s %d" % (v, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
