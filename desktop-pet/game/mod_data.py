"""桌宠模组数据读取：读取 .minecraft/deskpet/ 下按分钟分片的 JSONL 事件窗口。

Fabric 模组每 20 秒把聚合后的窗口事件写入独立目录 deskpet/（不与游戏 logs/ 混用），
文件名按分钟（YYYYMMDD-HHMM.jsonl），每行一个 JSON 窗口摘要。
桌宠端按文件 + 字节偏移增量读取，跳过不完整行。
"""

import json
import os

# 普通活动事件的最小重要性（低于它忽略）
IMPORTANCE_ORDER = {"LOW": 0, "NORMAL": 1, "CRITICAL": 2}


def deskpet_dir_from_log(log_path):
    """根据已配置的游戏日志路径推导模组数据目录。
    日志在 <mc>/.minecraft/logs/... → <mc>/.minecraft/deskpet/。"""
    if not log_path:
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, ".minecraft", "deskpet") if appdata else ""
    p = log_path
    if os.path.isfile(p):
        p = os.path.dirname(p)
    if os.path.basename(p).lower() == "logs":
        p = os.path.dirname(p)
    return os.path.join(p, "deskpet")


def window_files(directory):
    """返回目录下所有 .jsonl 文件（含 maid/ 子目录），路径相对 directory，按名称排序。

    maid/ 子目录是智能女仆模组（smartmaid）的写入区，与 deskpet-mod 的顶层文件隔离；
    返回值保持相对路径，调用方 os.path.join(desk_dir, fn) 对两种情况都成立。
    """
    if not directory or not os.path.isdir(directory):
        return []
    names = []
    for sub in ("", "maid"):
        target = os.path.join(directory, sub) if sub else directory
        try:
            for f in os.listdir(target):
                if f.lower().endswith(".jsonl"):
                    names.append(os.path.join(sub, f) if sub else f)
        except OSError:
            pass
    return sorted(names)


def read_window(path, last_pos):
    """增量读取单个窗口文件，返回 (窗口字典列表, 新字节偏移)。
    文件被清理/重写（变小）时自动回到开头重读；坏行跳过下轮再读。"""
    try:
        size = os.path.getsize(path)
        if size < last_pos:
            last_pos = 0
        with open(path, "r", encoding="utf-8") as f:
            f.seek(last_pos)
            text = f.read()
        windows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                windows.append(obj)
        return windows, size
    except OSError:
        return [], last_pos


def window_importance(win):
    """窗口重要性：CRITICAL / NORMAL / LOW，缺省按 LOW。"""
    imp = (win or {}).get("importance", "LOW")
    return imp if imp in IMPORTANCE_ORDER else "LOW"


def read_building_package(directory):
    """读取建筑感知包（deskpet/buildings/latest.json），返回 dict 或 None。

    感知包由建筑识别系统每次 20s 窗口 / 休眠时覆盖写入，
    含分类 / 尺寸 / 墙 / 房间 / 形态 / 材质 / 物件等特征，及 window 窗口序号。
    """
    if not directory:
        return None
    path = os.path.join(directory, "buildings", "latest.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def highlights_to_text(win):
    """把窗口的 highlights 事件列表转成中文描述行。返回 [行, ...]。"""
    lines = []
    for h in (win or {}).get("highlights", []) or []:
        if not isinstance(h, dict):
            continue
        t = (h.get("type") or "").lower()
        player = h.get("player") or ""
        detail = (h.get("detail") or "").strip()
        text = (h.get("text") or "").strip()
        target = (h.get("target") or "").strip()
        if t == "chat":
            lines.append(f"[聊天] {player}: {text}" if text else "")
        elif t == "broadcast":
            # 服务器广播消息（多人）：target 为分类（kill/death/item_gain/chat），detail 为完整文本
            label = {"kill": "击杀", "death": "死亡",
                     "item_gain": "获得", "chat": "消息"}.get(
                (h.get("target") or "chat").lower(), h.get("target") or "消息")
            body = text if text else detail
            if body:
                prefix = f"[广播·{label}] "
                if player:
                    lines.append(f"{prefix}{player}: {body}")
                else:
                    lines.append(f"{prefix}{body}")
        elif t == "death":
            lines.append(f"[死亡] {player} {detail}" if detail else f"[死亡] {player}")
        elif t == "advancement":
            lines.append(f"[进度] {player} {detail}" if detail else f"[进度] {player}")
        elif t == "dimension":
            lines.append(f"[维度] {player} {detail}" if detail else f"[维度] {player}")
        elif t == "kill":
            lines.append(f"[击杀] {player} 击杀 {target}" if target else f"[击杀] {player}")
        elif t == "item_gain":
            how = f"（{detail}）" if detail else ""
            cnt = h.get("count") or 1
            count_str = f"×{cnt}" if int(cnt) > 1 else ""
            lines.append(f"[获得] {player} {target}{count_str}{how}"
                         if target else f"[获得] {player}")
        elif t == "damage":
            cnt = h.get("count") or 1
            count_str = f"×{cnt}" if int(cnt) > 1 else ""
            lines.append(f"[受伤] {player} 被 {target} 伤害{count_str}"
                         if target else f"[受伤] {player}")
        elif t == "summary":
            lines.append(f"[概况] {detail}" if detail else "")
        elif t in ("break", "place", "attack", "use_item", "move", "join", "leave"):
            label = {"break": "破坏", "place": "放置", "attack": "攻击",
                     "use_item": "使用", "move": "移动",
                     "join": "加入", "leave": "离开"}.get(t, t)
            parts = [f"[{label}] {player}"]
            if target:
                parts.append(target)
            if detail:
                parts.append(detail)
            lines.append(" ".join(parts))
        elif detail:
            lines.append(f"[{t}] {detail}")
    return [l for l in lines if l]