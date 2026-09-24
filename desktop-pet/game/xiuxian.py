# -*- coding: utf-8 -*-
"""凡人修仙游戏桥接（方案 C：playwright 直接读浏览器状态）。

两种运行模式：

1. 子进程模式（`python xiuxian.py --bridge`）—— 由桌宠主进程拉起：
   - 确保游戏服务器（node server.js）在 3000 端口监听（未监听则用 games/xiuxian 启动）
   - playwright 拉起系统 Edge（headless=False，隔离临时 profile）打开游戏页
   - 每 5 秒轮询 localStorage 存档 + 页面日志 DOM，做快照 diff
   - 把事件以 JSONL 追加写入 logs/xiuxian_events.jsonl，供主进程增量读取
   - 主进程不加载 playwright，浏览器只归子进程管（参照 ai/login.py 的设计）

2. 主进程模式（`import xiuxian`）—— 桌宠主进程：
   - start_bridge()：以子进程启动桥接（返回 Popen，可 stop_bridge() 结束）
   - read_events()：增量读取 events.jsonl（与 deskpet.read_window 同思路）

事件 JSONL 每行一个对象：
  {"ts": 毫秒, "type": "...", "title": "...", "detail": "...", "state": {...}}

type 取值：
  start            开局（存档从无到有）
  event            页面日志新增（DOM 日志，聚合叙事）
  advance          境界提升（currentStage 变化）
  ending           结局达成（存档消失 + 页面出现结局面板）
  death            死亡 / 寿元耗尽（存档消失 + 陨落面板）
"""

import json
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# 常量（与 config.py 保持一致；此处独立，避免主进程加载 config 的 UI 依赖）
# ---------------------------------------------------------------------------
# 项目根目录（本文件在 game/ 子包，上溯两级）
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GAME_DIR = os.path.join(_PROJ_ROOT, "games", "xiuxian")
GAME_PORT = 3000
SAVE_KEY = "fanren_xiuxian_save"
EVENTS_FILE = os.path.join(_PROJ_ROOT, "logs", "xiuxian_events.jsonl")
BRIDGE_LOG = os.path.join(_PROJ_ROOT, "logs", "xiuxian_bridge.log")
# 桥接子进程心跳文件：每轮轮询 touch 一次，供主进程判断存活（避免重复拉起）
BRIDGE_ALIVE_FILE = os.path.join(_PROJ_ROOT, "logs", "xiuxian_bridge.alive")

POLL_INTERVAL = 5        # 读存档间隔（秒）
LOG_POLL_INTERVAL = 10   # 抓页面日志间隔（秒）
STATE_FIELDS = ["currentStage", "age", "maxAge", "difficulty", "world",
                "cultivation", "health", "spirit", "weaponDao", "formation",
                "alchemy", "karma", "reputation", "spiritStones", "daoHeart"]
STAGE_NAMES = {"qi": "炼气", "base": "筑基", "core": "结丹",
               "infant": "元婴", "god": "化神"}


def _log(msg):
    try:
        with open(BRIDGE_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _emit(ev):
    """追加写入一个事件。"""
    try:
        os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 主进程辅助（不加载 playwright）
# ---------------------------------------------------------------------------

def default_game_dir():
    return DEFAULT_GAME_DIR


def events_path():
    return EVENTS_FILE


def bridge_log_path():
    return BRIDGE_LOG


def is_bridge_running(ttl=45):
    """桥接子进程最近 ttl 秒内是否在心跳（存在且存活的可靠判据）。"""
    try:
        return os.path.isfile(BRIDGE_ALIVE_FILE) and (
            time.time() - os.path.getmtime(BRIDGE_ALIVE_FILE) < ttl)
    except Exception:
        return False


def server_http_ok(port=GAME_PORT, timeout=2.0):
    """检查游戏服务器 HTTP 是否可用。"""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def is_bridge_running(ttl=45):
    """桥接子进程最近 ttl 秒内是否在心跳（存在且存活的可靠判据）。"""
    try:
        return os.path.isfile(BRIDGE_ALIVE_FILE) and (
            time.time() - os.path.getmtime(BRIDGE_ALIVE_FILE) < ttl)
    except Exception:
        return False


def start_bridge(game_dir=None):
    """以子进程启动桥接，返回 QProcess（失败返回 None）。"""
    from PySide6.QtCore import QProcess
    proc = QProcess()
    proc.setProcessChannelMode(QProcess.ForwardedChannels)
    args = [os.path.abspath(__file__), "--bridge"]
    if game_dir:
        args += ["--game-dir", game_dir]
    proc.start(sys.executable, args)
    return proc


def read_events(path, last_pos):
    """增量读取事件文件，返回 (事件列表, 新字节偏移)。文件变小则回开头。"""
    try:
        size = os.path.getsize(path)
        if size < last_pos:
            last_pos = 0
        with open(path, "r", encoding="utf-8") as f:
            f.seek(last_pos)
            text = f.read()
        evs = []
        for line in text.splitlines():
            line = line.strip().lstrip("\ufeff")   # 兼容 UTF-8 BOM
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict):
                evs.append(obj)
        return evs, size
    except OSError:
        return [], last_pos


# ---------------------------------------------------------------------------
# 状态读取与 diff（纯函数，便于独立测试）
# ---------------------------------------------------------------------------

def _extract_player(save):
    """从存档 engine.player 提取玩家对象与摘要。"""
    engine = save.get("engine") or {}
    player = engine.get("player") or save.get("player") or {}
    summary = {}
    for k in STATE_FIELDS:
        v = player.get(k)
        if isinstance(v, (int, float)):
            summary[k] = v
    summary["currentStage"] = summary.get("currentStage") or "qi"
    flags = player.get("flags") or []
    if isinstance(flags, str):
        flags = [flags]
    elif isinstance(flags, (list, tuple, set)):
        flags = list(flags)
    summary["flags"] = flags
    ending = player.get("ending") or None
    if isinstance(ending, dict):
        summary["ending"] = {
            "name": ending.get("name"),
            "desc": ending.get("desc"),
        }
    return player, summary


def _stage_name(key):
    return STAGE_NAMES.get(key, key or "")


def _read_save(page):
    try:
        raw = page.evaluate(f"localStorage.getItem('{SAVE_KEY}')")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:
        return None


def _read_logs(page):
    """抓页面日志区文本（class 含 log 的元素 + body 尾部）。"""
    try:
        return page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('[class*=log]').forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (t && !out.includes(t)) out.push(t);
                });
                const tail = (document.body.innerText || '')
                    .split('\\n').map(s => s.trim()).filter(Boolean).slice(-40);
                return {dom: out.slice(0, 60), tail: tail};
            }"""
        ) or {"dom": [], "tail": []}
    except Exception:
        return {"dom": [], "tail": []}


def _read_ending_panel(page):
    """从页面 DOM 检测结局 / 死亡面板，返回 (type, title, detail) 或 None。"""
    try:
        text = page.evaluate("document.body.innerText || ''")
    except Exception:
        return None
    if not text:
        return None
    if "达成" in text and ("结局" in text or "飞升" in text or "证道" in text):
        return "ending", "结局达成", text[:500]
    if "陨落" in text and "修仙之路" in text:
        return "death", "修士陨落", "修士陨落，修仙之路到此为止"
    if "坐化" in text and "寿元耗尽" in text:
        return "death", "寿元耗尽", "大限已至，寿元耗尽，坐化归尘"
    return None


def new_ctx():
    """初始化跨轮状态。"""
    return {
        "last_raw": None,
        "last_save_state": None,
        "last_logs_key": "",
        "last_log_report": 0.0,
    }


def poll_once(page, ctx, now=None, force_log=False):
    """单轮轮询 diff，返回待写入的事件列表。ctx 保存跨轮状态。"""
    evs = []
    now = now if now is not None else time.time()
    raw = _read_save(page)

    if raw:
        if ctx["last_raw"] is None:
            _player, summary = _extract_player(raw)
            st = _stage_name(summary.get("currentStage"))
            evs.append({
                "ts": int(time.time() * 1000),
                "type": "start",
                "title": "修仙之路开启",
                "detail": (
                    f"玩家开始了新的修仙旅程：{st}期 · "
                    f"难度 {summary.get('difficulty', 'normal')} · "
                    f"世界 {summary.get('world', 'mortal')} · "
                    f"寿元上限 {summary.get('maxAge', '?')} 岁"),
                "state": summary,
            })
            _log(f"开局 {st}期")
        elif raw != ctx["last_raw"]:
            _player, summary = _extract_player(raw)
            prev = ctx["last_save_state"] or {}
            old_stage = prev.get("currentStage")
            new_stage = summary.get("currentStage")
            if new_stage and old_stage and new_stage != old_stage:
                evs.append({
                    "ts": int(time.time() * 1000),
                    "type": "advance",
                    "title": f"突破成功，踏入{_stage_name(new_stage)}期",
                    "detail": (
                        f"{_stage_name(old_stage)}期 → {_stage_name(new_stage)}期，"
                        f"寿元上限提升至 {summary.get('maxAge', '?')} 岁"),
                    "state": summary,
                })
                _log(f"晋级 {old_stage}→{new_stage}")
            ctx["last_save_state"] = summary
    else:
        if ctx["last_raw"] is not None:
            panel = _read_ending_panel(page)
            if panel:
                etype, title, detail = panel
                evs.append({
                    "ts": int(time.time() * 1000),
                    "type": etype,
                    "title": title,
                    "detail": detail,
                    "state": ctx["last_save_state"] or {},
                })
                _log(title)
            else:
                evs.append({
                    "ts": int(time.time() * 1000),
                    "type": "death",
                    "title": "游戏结束",
                    "detail": "存档已清除（游戏结束）",
                    "state": ctx["last_save_state"] or {},
                })
                _log("存档消失")
            ctx["last_save_state"] = None
    ctx["last_raw"] = raw

    # 页面日志 diff（聚合叙事，节流上报）
    if force_log or now - ctx["last_log_report"] >= LOG_POLL_INTERVAL:
        ctx["last_log_report"] = now
        logs = _read_logs(page)
        key = "\n".join(logs.get("dom", [])) or "\n".join(logs.get("tail", []))
        if key and key != ctx["last_logs_key"]:
            ctx["last_logs_key"] = key
            lines = []
            for line in reversed(logs.get("tail", [])):
                line = (line or "").strip()
                if not line:
                    continue
                if line in lines:
                    continue
                lines.append(line)
                if len(lines) >= 4:
                    break
            lines.reverse()
            if lines and ctx["last_save_state"]:
                evs.append({
                    "ts": int(time.time() * 1000),
                    "type": "event",
                    "title": "历事",
                    "detail": "；".join(lines),
                    "state": ctx["last_save_state"],
                })
                _log("日志变化")
    return evs


# ---------------------------------------------------------------------------
# 子进程桥接
# ---------------------------------------------------------------------------

def _start_game_server(game_dir):
    """若 3000 未监听，用 games/xiuxian 启动 node server.js。返回 True 表示就绪。"""
    if server_http_ok():
        _log("游戏服务器已在 3000 端口运行")
        return True
    if not os.path.isdir(game_dir):
        _log(f"游戏目录不存在: {game_dir}")
        return False
    node = os.environ.get("NODE_BIN") or "node"
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            [node, "server.js"],
            cwd=game_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _log(f"已启动游戏服务器 pid={proc.pid}")
    except Exception as e:
        _log(f"启动游戏服务器失败: {e}")
        return False
    for _ in range(60):   # 最长等 30s
        if server_http_ok():
            return True
        time.sleep(0.5)
    _log("游戏服务器 30 秒内未就绪")
    return False


def _open_game_page():
    """playwright 拉起系统 Edge 打开游戏页，返回 (pw, browser, page)。失败返回 None。"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            channel="msedge",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{GAME_PORT}/", timeout=60000)
        page.wait_for_timeout(3000)
        _log("已打开游戏页面")
        return pw, browser, page
    except Exception as e:
        _log(f"打开浏览器失败: {e}")
        try:
            pw.stop()
        except Exception:
            pass
        return None


def _touch_alive():
    """写桥接心跳文件，证明子进程存活。"""
    try:
        with open(BRIDGE_ALIVE_FILE, "a") as f:
            os.utime(BRIDGE_ALIVE_FILE, None)
    except Exception:
        pass


def bridge_main(game_dir=None):
    """桥接子进程主流程。"""
    game_dir = game_dir or DEFAULT_GAME_DIR
    os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
    _log(f"桥接启动 game_dir={game_dir}")
    # 一启动就写心跳，覆盖「启服务器 + 开浏览器」的启动期，
    # 避免主进程在此期间重复拉起（见 pet/window._xiuxian_tick 的冷却）
    _touch_alive()

    if not _start_game_server(game_dir):
        _log("游戏服务器不可用，桥接退出")
        return 1

    opened = _open_game_page()
    if not opened:
        _log("无法打开游戏页面，桥接退出")
        return 1
    pw, browser, page = opened

    ctx = new_ctx()
    try:
        while True:
            for ev in poll_once(page, ctx):
                _emit(ev)
            _touch_alive()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log(f"桥接异常: {e}")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        try:
            os.remove(BRIDGE_ALIVE_FILE)
        except Exception:
            pass
        _log("桥接退出")
    return 0


def main():
    if "--bridge" in sys.argv:
        game_dir = None
        if "--game-dir" in sys.argv:
            i = sys.argv.index("--game-dir")
            if i + 1 < len(sys.argv):
                game_dir = sys.argv[i + 1]
        sys.exit(bridge_main(game_dir))
    else:
        print("xiuxian.py：凡人修仙游戏桥接（方案 C）")
        print("  --bridge            以子进程运行桥接（桌宠主进程调用）")
        print("  --game-dir <path>   指定游戏目录（默认 games/xiuxian）")


if __name__ == "__main__":
    main()