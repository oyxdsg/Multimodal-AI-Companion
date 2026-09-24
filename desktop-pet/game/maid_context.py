# -*- coding: utf-8 -*-
"""女仆感知快照 → AI 上下文（TransientContext）+ 危险判定。

把 maid_link 的合并快照（perception）转成两种形态的瞬时状态：
- ``status_full``    完整摘要（MessagesBackend / 官方 API / 千问 用）
- ``status_compact`` 极短一行（SessionBackend / 网页版 用，会被服务端记住所以尽量短）

另提供 AI 主动决策判定：``evaluate_danger``（低血 / 被围），
供 maid_handler 每秒检查 → 命中才调 AI 决策。

快照结构（模组 EntitySense / SelfSense 输出，经 merge_snapshot 合并）::

    snap = {
      "self":    {"health": 18.0, "max_health": 20.0, "pos": [x,y,z],
                  "status": {"task_id": "guard", "target": {"type": "zombie"}},
                  "equipment": {"mainhand": {"id": "minecraft:iron_sword"}}},
      "nearby":  {"hostiles": [{"type": "zombie", "dist": 3, "targeting_me": true}, ...],
                  "animals": [...], "players": [...], "items": [...]},
      "inventory": {"slots": [...], "free_slots": N,
                    "capabilities": {"has_sword": true, "has_food": true, ...}},
      "owner":   {"name": "...", "pos": [x,y,z], "dist": N},
      "env":     {"biome": "...", "is_day": true},
    }
"""

from ai.context import TransientContext

# 危险判定阈值（可被 config 覆盖）
LOW_HEALTH_RATIO = 0.30      # 生命低于 30% 视为低血
SURROUNDED_COUNT = 3         # 敌对生物 ≥3
SURROUNDED_DIST = 10         # 且都在 10 格内


def build_transient(snap, events=None, replies=None):
    """从快照 + 事件 + 回执生成 TransientContext。

    :param snap: maid_link 的合并快照 dict（可空）
    :param events: 最近事件中文行（list[str]）
    :param replies: 最近指令回执（list[str]，[系统] 前缀）
    """
    snap = snap or {}
    return TransientContext(
        status_full=_full_status(snap),
        status_compact=_compact_status(snap),
        events=list(events or [])[:3],
        replies=list(replies or [])[:3],
    )


def _full_status(snap):
    """完整中文状态（~100-250 token，MessagesBackend 用）。"""
    self_ = snap.get("self") or {}
    status = self_.get("status") or {}
    equip = self_.get("equipment") or {}
    nearby = snap.get("nearby") or {}
    inv = snap.get("inventory") or {}

    parts = []
    hp = self_.get("health")
    mhp = self_.get("max_health")
    if hp is not None:
        parts.append("生命=%s%s" % (hp, "/%s" % mhp if mhp else ""))
    task = status.get("task_id")
    parts.append("任务=%s" % (task if task else "待命"))
    tgt = (status.get("target") or {}).get("type")
    if tgt:
        parts.append("目标=%s" % tgt.split(":")[-1])
    mainhand = (equip.get("mainhand") or {}).get("id")
    if mainhand and mainhand != "minecraft:air":
        parts.append("手持=%s" % mainhand.split(":")[-1])
    pos = self_.get("pos")
    if isinstance(pos, (list, tuple)) and len(pos) >= 3:
        try:
            parts.append("坐标=(%d,%d,%d)" % tuple(int(round(v)) for v in pos[:3]))
        except (TypeError, ValueError):
            pass

    hostiles = nearby.get("hostiles") or []
    if hostiles:
        descs = []
        for h in hostiles[:5]:
            if not isinstance(h, dict):
                continue
            from game.maid_link import _short_name
            nm = _short_name(h.get("type"))
            d = h.get("dist")
            tail = "(%d格%s)" % (d, "，在攻击我" if h.get("targeting_me") else "") \
                if d is not None else ""
            descs.append(nm + tail)
        if descs:
            parts.append("敌对=" + "、".join(descs))

    cap = inv.get("capabilities") or {}
    have = [k for k, v in cap.items() if v and isinstance(v, bool)]
    if have:
        parts.append("装备=" + "、".join(have))

    owner = snap.get("owner") or {}
    if owner.get("name"):
        parts.append("主人=%s" % owner["name"])

    if not parts:
        return ""
    return "你当前状态：" + "，".join(parts)


def _compact_status(snap):
    """极短状态行（~20 token，SessionBackend 用，会被服务端记住）。"""
    self_ = snap.get("self") or {}
    status = self_.get("status") or {}
    parts = []
    hp = self_.get("health")
    if hp is not None:
        parts.append("生命%s" % hp)
    task = status.get("task_id")
    parts.append("任务%s" % (task if task else "待命"))
    return "[态] " + " ".join(parts)


def evaluate_danger(snap):
    """AI 主动决策判定：返回 (低血?, 被围?)。只在命中时让 AI 介入。

    决策 3 边界（仅两种，后续可扩）：
    - low_health：生命 < 30%（有 max_health 时按比例，否则按绝对值 6）
    - surrounded：nearby.hostiles 中距离 ≤10 格的敌对 ≥3
    """
    snap = snap or {}
    self_ = snap.get("self") or {}
    low_health = False
    hp = self_.get("health")
    mhp = self_.get("max_health")
    if hp is not None:
        if mhp:
            low_health = hp / float(mhp) < LOW_HEALTH_RATIO
        else:
            low_health = hp < 6.0

    surrounded = False
    nearby = snap.get("nearby") or {}
    hostiles = nearby.get("hostiles") or []
    close = [h for h in hostiles
             if isinstance(h, dict) and h.get("dist") is not None
             and float(h["dist"]) <= SURROUNDED_DIST]
    surrounded = len(close) >= SURROUNDED_COUNT
    return low_health, surrounded
