"""Minecraft 游戏模式：游戏日志增量读取 + 事件提取。

- 日志路径自动检测（默认 .minecraft/logs/latest.log，可在设置中手动指定）
- 按字节偏移增量读取，支持日志轮转

注：早期版本在这里直连 zh.minecraft.wiki 的 MediaWiki API 做在线查询，
2.6.0 起已改为**本地知识库**（`game/wiki_kb.py` + `wiki_data/mcwiki.db`），
在线查询与缓存代码（``wiki_search`` / ``_wiki_fetch``）已于 2026-09-16 删除。
本模块只保留：日志解析 + 检索词提取（`extract_terms` 的固定词表仍是兜底）。
"""

import json
import os
import re

import config

# ---------- 真实 Minecraft 日志句式（精确匹配，避免启动噪音误报） ----------
# 死亡 / 受伤致死
_DEATH_RE = re.compile(
    r"was (?:killed|slain|shot|blown up) by [\w_ ]+?"
    r"|was (?:burned to death|burnt to a crisp|drowned|pricked to death)"
    r"|tried to swim in lava"
    r"|fell (?:from a high place|off a ladder|out of the world)"
    r"|hit the ground too hard"
    r"|blew up"
    r"|squashed by a falling anvil"
    r"|starved to death"
    r"|went off with a bang"
    r"|died",
    re.IGNORECASE,
)
# 成就 / 进度 / 挑战
_ADV_RE = re.compile(
    r"has (?:just )?earned the achievement"
    r"|has made the advancement"
    r"|has completed the challenge"
    r"|has been discovered by the advancement",
    re.IGNORECASE,
)
# 进出世界 / 睡觉等
_JOIN_RE = re.compile(
    r"logged in with entity id"
    r"|logged out"
    r"|joined the game"
    r"|left the game"
    r"|entered the (Nether|End)",
    re.IGNORECASE,
)
# 进度数量加载（新版 1.13+：游戏中获得进度时输出 "Loaded N advancements"）
_ADV_LOAD_RE = re.compile(r"Loaded (\d+) advancements", re.IGNORECASE)
# 提取死亡原因实体（by X）
_KILLED_BY_RE = re.compile(
    r"was (?:killed|slain|shot|blown up) by ([A-Za-z_ ]+?)(?:[\.!\?\[\]]|$)",
    re.IGNORECASE,
)
# 提取成就 / 进度名
_ADV_NAME_RE = re.compile(
    r"(?:advancement|achievement|challenge)\s*\[([^\]]+)\]",
    re.IGNORECASE,
)

# 用于从日志行里提取 wiki 关键词（英文日志）
_WIKI_TERMS = [
    "diamond", "emerald", "nether", "ender", "dragon", "wither",
    "enchant", "ender chest", "beacon", "village", "creeper",
    "zombie", "skeleton", "diamond ore", "ancient debris", "elytra",
    "shulker", "raid", "pillager", "sculk", "warden",
]

# 中文词条（模组数据为中文描述时提取 wiki 搜索词；中文搜索更精准）
_WIKI_TERMS_CN = [
    "末影龙", "凋灵骷髅", "凋灵", "远古残骸", "下界合金", "钻石矿石",
    "绿宝石矿石", "末影箱", "末影珍珠", "末影之眼", "钻石", "绿宝石",
    "信标", "鞘翅", "潜影贝", "循声守卫", "监守者", "苦力怕", "僵尸",
    "骷髅", "掠夺者", "袭击", "幽匿", "村庄", "下界", "末地", "附魔",
    "海底神殿", "林地府邸", "堡垒", "要塞",
]


def find_log_path():
    """自动检测 Minecraft 日志路径；找不到返回 None。"""
    candidates = []
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.append(
            os.path.join(appdata, ".minecraft", "logs", "latest.log"))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# 优先纳入监听的活动日志文件名（当前会话正在写入的日志）
_ACTIVE_LOG_NAMES = ("latest.log", "debug.log", "fml-client-latest.log")


def active_log_files(path, top_n=5):
    """解析要监听的日志文件列表：
    - path 是文件：直接返回（仅 .log，.gz 历史归档不增量监听）
    - path 是目录：按修改时间筛选最新的未压缩 .log，活动日志名优先
    - 返回 [] 表示无可监听日志
    """
    if not path:
        return []
    if os.path.isfile(path):
        return [path] if path.lower().endswith(".log") else []
    if os.path.isdir(path):
        files = []
        try:
            for fn in os.listdir(path):
                p = os.path.join(path, fn)
                if not os.path.isfile(p) or not fn.lower().endswith(".log"):
                    continue
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                files.append((mt, p))
        except OSError:
            return []
        files.sort(reverse=True)          # 按修改时间倒序
        fixed = [p for _, p in files
                 if os.path.basename(p).lower() in _ACTIVE_LOG_NAMES]
        others = [p for _, p in files if p not in fixed]
        return (fixed + others)[:top_n]
    return []


_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{2}):(\d{2})\]")


def _line_ts(line):
    """解析日志行首的 [HH:MM:SS] 时间戳为秒数；无时间戳返回 None。"""
    m = _TS_RE.match(line)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def _is_newer(ts, last_ts):
    """行时间戳 ts 是否比 last_ts 新（时间戳增量判断，不处理跨天）。"""
    return last_ts is None or ts > last_ts


def read_events_increment(path, last_pos, last_ts):
    """增量读取日志并返回其中的游戏事件。

    只读取自字节偏移 last_pos 之后的新增内容，再按时间戳过滤：
    仅保留时间戳晚于 last_ts 的行（时间戳增量，日志轮转/重写也不怕）。
    返回 (事件行列表, 新字节偏移, 新最后时间戳)。
    """
    try:
        size = os.path.getsize(path)
        if size < last_pos:          # 日志轮转（文件被重写变小）
            last_pos = 0
            last_ts = None
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(last_pos)
            new_text = f.read()
        new_pos = size
    except OSError:
        return [], last_pos, last_ts

    new_last = last_ts
    ts_lines = []
    for line in new_text.splitlines():
        ts = _line_ts(line)
        if ts is None:               # 无时间戳的多行内容（堆栈等）跳过
            continue
        if _is_newer(ts, last_ts):
            ts_lines.append(line)
            if new_last is None or ts > new_last:
                new_last = ts
    if not ts_lines:
        return [], new_pos, new_last
    events = parse_events("\n".join(ts_lines))
    return events, new_pos, new_last


def parse_events(text):
    """从日志文本提取真实游戏事件行（死亡 / 成就 / 进度 / 进出世界等）。

    使用精确正则匹配 Minecraft 的日志句式，避免把启动噪音误判为事件。
    """
    lines = []
    seen = set()
    for line in text.splitlines():
        if (_DEATH_RE.search(line) or _ADV_RE.search(line)
                or _JOIN_RE.search(line) or _ADV_LOAD_RE.search(line)):
            s = line.strip()
            if s and s not in seen:
                seen.add(s)
                lines.append(s)
    return lines


def extract_player(events):
    """从事件行提取玩家（主人）的游戏名字；提取不到返回空串。

    日志事件行形如 "[12:34:56] ... Steve was killed by Zombie"，
    去掉时间戳/日志前缀后取行首英文名（1-16 字符，避免介词误判）。
    """
    for line in events or []:
        s = re.sub(r"\[[^\]]*\]", "", (line or "").strip())
        s = re.sub(r"^\W+", "", s)   # 去掉行首的冒号/空格等非单词字符
        m = re.match(r"^([A-Za-z0-9_]{1,16})\b", s)
        if m:
            return m.group(1)
    return ""


def extract_terms(events):
    """从事件行提取用于本地知识库检索的关键词，返回去重列表。

    这只是**兜底词表**：主力来源是模组事件的 highlights（`game_handler.
    _extract_win_targets`）与本地库反向提取（`wiki_kb.match_terms`）。
    """
    joined = " ".join(events)
    # 死亡原因实体：was killed/slain by X
    m = _KILLED_BY_RE.search(joined)
    if m:
        return [m.group(1).strip().lower().replace(" ", "_")]
    # 成就 / 进度名
    m = _ADV_NAME_RE.search(joined)
    if m:
        return [m.group(1).strip().lower().replace(" ", "_")]
    # 兜底：已知词条
    joined_low = joined.lower()
    found = [t for t in _WIKI_TERMS if t in joined_low]
    if found:
        return found
    # 中文词条（模组数据为中文描述），按长度降序优先具体词条
    found_cn = [t for t in _WIKI_TERMS_CN if t in joined]
    found_cn.sort(key=len, reverse=True)
    return found_cn or []


def filter_advancements(events, last_adv=None):
    """按进度数量变化过滤事件行（新版日志 "Loaded N advancements"）：
    - N 达到全量阈值（游戏启动加载）忽略
    - 首次游戏内进度作为基线，不触发
    - 数量增长的行保留（表示玩家获得新进度）
    返回 (保留的事件行列表, 新的 last_adv)。"""
    kept = []
    new_last = last_adv
    for line in events:
        m = _ADV_LOAD_RE.search(line)
        if not m:
            kept.append(line)
            continue
        n = int(m.group(1))
        if n >= config.GAME_ADV_FULL_LIMIT:
            continue
        if new_last is None:
            new_last = n
            continue
        if n > new_last:
            new_last = n
            kept.append(line)
    return kept, new_last