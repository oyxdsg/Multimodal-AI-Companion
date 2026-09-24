# -*- coding: utf-8 -*-
"""Minecraft 中文 Wiki 本地知识库构建脚本。

抓取 zh.minecraft.wiki 主命名空间全部内容页与重定向表，
清理 wikitext 后写入 SQLite（wiki_data/mcwiki.db），供桌宠离线检索。

用法：
    python wiki_build.py --full        # 全量重建（首次使用）
    python wiki_build.py --update      # 增量更新（仅拉取最近变更的页面）
    python wiki_build.py --limit 50    # 调试：只抓少量页面验证流程
    python wiki_build.py --refresh 进度  # 重新抓取指定页面（清理规则升级后修复入库页）
    python wiki_build.py --refresh-tpl Crafting Smelting  # 按模板批量重抓（合成/烧炼/成就等）
    python wiki_build.py --export      # 导出为可读文本（默认 wiki_data/export/）
                                       # 按分类生成 common/rare/disambig/version/tech.txt、
                                       # redirects.txt 与 _index.txt，可直接检查库内内容

数据库结构：
    pages      (id, title UNIQUE, text, updated)
    redirects  (alias PK, title)                # 重定向别名 -> 目标标题
    pages_fts  FTS5 trigram（external content，触发器自动同步）
    meta       (key, value)                     # 记录上次增量更新时间
"""

import argparse
import html
import os
import re
import sqlite3
import sys
import time

try:
    from curl_cffi import requests as cffi
except ImportError:
    cffi = None  # 离线操作（--classify/--reclean/--export）不依赖 curl_cffi

API = "https://zh.minecraft.wiki/api.php"
_UA = {"User-Agent": "DesktopPet-WikiKB/1.0 (desktop pet game companion)"}
_SLEEP = 0.25          # 请求间隔（限速友好）
DB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "wiki_data")
DB_PATH = os.path.join(DB_DIR, "mcwiki.db")

_session = None


def _http():
    global _session
    if _session is None:
        _session = cffi.Session(impersonate="chrome131", timeout=30)
    return _session


def api(params, retries=5):
    """发起 MediaWiki API 请求并返回 JSON；失败重试（指数退避）。"""
    params = dict(params)
    params.setdefault("format", "json")
    for i in range(retries):
        try:
            resp = _http().get(API, params=params, headers=_UA)
            if resp.status_code == 200:
                return resp.json()
            time.sleep(3 * (i + 1))
        except Exception:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"API 请求失败: {params}")


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def iso_now():
    """当前 UTC 时间，ISO 8601（MediaWiki rcstart 需要的格式）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- 抓取 ----------

def fetch_all_titles(filter_redir="nonredirects"):
    """枚举主命名空间全部页面标题（纯标题请求，轻量、分页可靠）。

    filter_redir: "nonredirects" 内容页 / "redirects" 重定向页。
    """
    titles = []
    cont = None
    while True:
        p = {"action": "query", "list": "allpages",
             "apnamespace": "0", "apfilterredir": filter_redir,
             "aplimit": "max"}
        if cont:
            p["apcontinue"] = cont
        d = api(p)
        titles += [x.get("title", "") for x in
                   d.get("query", {}).get("allpages", [])]
        cont = d.get("continue", {}).get("apcontinue")
        if not cont:
            break
        time.sleep(_SLEEP)
    return [t for t in titles if t]


def fetch_redirects(target_titles):
    """对内容页目标批量查询指向它们的重定向（别名 -> 目标）。

    用 prop=redirects 元数据接口，返回每个目标页被哪些页重定向，
    即「别名 -> 目标」映射。避免拉取全部重定向页 wikitext，请求量小。
    正确处理 rdcontinue 分页：重定向多的目标（如「剑」）不会被截断。
    """
    aliases = []
    for i in range(0, len(target_titles), 50):
        chunk = target_titles[i:i + 50]
        cont = None
        while True:
            p = {"action": "query", "titles": "|".join(chunk),
                 "prop": "redirects", "rdlimit": "max"}
            if cont:
                p["rdcontinue"] = cont
            d = api(p)
            for pid, pg in d.get("query", {}).get("pages", {}).items():
                target = pg.get("title", "").strip()
                if not target:
                    continue
                for r in pg.get("redirects", []) or []:
                    alias = r.get("title", "").strip()
                    if alias:
                        aliases.append((alias, target))
            cont = d.get("continue", {}).get("rdcontinue")
            if not cont:
                break
            time.sleep(_SLEEP)
    return aliases


def fetch_all_content(titles, per_batch=50):
    """按标题批量拉取主命名空间内容，返回 [(标题, wikitext), ...]。

    用 titles 参数（每批 50 个）逐批请求，避免 generator+prop 的分页陷阱。
    """
    rows = []
    for i in range(0, len(titles), per_batch):
        chunk = titles[i:i + per_batch]
        d = api({"action": "query", "titles": "|".join(chunk),
                 "prop": "revisions", "rvprop": "content", "rvslots": "main"})
        for pid, pg in d.get("query", {}).get("pages", {}).items():
            title = pg.get("title", "")
            rev = (pg.get("revisions") or [{}])[0]
            content = (rev.get("slots") or {}).get("main", {}).get("*", "")
            if title:
                rows.append((title, content))
        time.sleep(_SLEEP)
    return rows


def fetch_recent_changes_titles(rcend=""):
    """增量更新：拉取最近变更的主命名空间页面标题（每页最新一版）。

    返回 (标题列表, 最新变更时间戳 ISO8601)。
    rcend 为增量下界：只返回该时间之后（到当前）的变更。
    """
    titles = []
    last_ts = rcend
    cont = None
    while True:
        p = {"action": "query", "list": "recentchanges",
             "rclimit": "max", "rcnamespace": "0",
             "rctype": "new|edit", "rctoponly": "1",
             "rcprop": "title|timestamp"}
        if rcend:
            p["rcend"] = rcend
        if cont:
            p["rccontinue"] = cont
        d = api(p)
        for c in d.get("query", {}).get("recentchanges", []):
            title = c.get("title", "")
            if title:
                titles.append(title)
            ts = c.get("timestamp", "")
            if ts:
                last_ts = ts
        cont = d.get("continue", {}).get("rccontinue")
        if not cont:
            break
        time.sleep(_SLEEP)
    return [t for t in titles if t], last_ts


def fetch_content_by_titles(titles):
    """按标题批量拉取 wikitext（titles 参数最多 50 个/请求），返回 {title: content}。"""
    out = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i:i + 50]
        d = api({"action": "query", "titles": "|".join(chunk),
                 "prop": "revisions", "rvprop": "content", "rvslots": "main"})
        for pid, pg in d.get("query", {}).get("pages", {}).items():
            title = pg.get("title", "")
            rev = (pg.get("revisions") or [{}])[0]
            out[title] = (rev.get("slots") or {}).get("main", {}).get("*", "")
        time.sleep(_SLEEP)
    return out


# ---------- wikitext 清理 ----------

# 无价值小节（整节删除）：对 AI 讲解游戏知识无用
_DROP_SECTIONS = {
    "成就", "进度", "成就与进度", "相关成就", "历史", "历史记录", "更新历史",
    "画廊", "画廊历史", "艺术作品", "注释", "参考", "导航", "你知道吗",
    "音效", "音频", "数据值", "ID", "实体数据", "统计", "参见", "琐事",
    "冷知识", "外部链接", "引用", "来源", "漏洞", "命令格式", "命令",
    "生物群系ID", "本页面或章节", "开发", "你知道吗？", "其他", "相关历史",
}
# 小节标题前缀黑名单（子标题以这些开头也删除）
_DROP_SECTION_PREFIX = ("历史", "画廊", "参考", "注释", "导航", "数据值")


def _strip_section_noise(s):
    """在已清理文本上做补充清理：繁简标记/跨语言/画廊行/孤立行 + 小节过滤。

    按小节标题切块处理：顶层小节整块保留/删除（子节跟随父块），
    无顶层父节的孤儿子节直接删除。对已清理过的文本幂等，可重跑。
    """
    # 繁简转换标记（-{文字}- / -{}-）
    s = s.replace("-{", "").replace("}-", "").replace("{}", "")
    # 命令模板（{{cmd|...}} 内部含大括号，逐行删到行尾）
    s = re.sub(r"{{cmd\|[^\n]*", "", s, flags=re.I)
    # 跨语言链接行（cs:Zombie 等）
    s = re.sub(r"^[a-z]{2,3}:[^\n]*$", "", s, flags=re.M)
    # 画廊图片行（xxx.png|描述）
    s = re.sub(r"^[^=\n]*\.(?:png|jpg|jpeg|gif|webp)\|.*$", "", s,
               flags=re.M | re.I)
    # 孤立链接/分隔行
    s = re.sub(r"^[\[\]|：:\s\-]+$", "", s, flags=re.M)
    # 按小节标题切块
    head_re = re.compile(r"^(={2,})\s*(.*?)\s*=*\s*$")
    lines = s.splitlines()
    blocks = []            # (level, title, lines)
    cur_level, cur_title, cur = 0, None, []
    for line in lines:
        m = head_re.match(line)
        if m:
            blocks.append((cur_level, cur_title, cur))
            cur_level = len(m.group(1))
            cur_title = m.group(2).strip()
            cur = [line]
        else:
            cur.append(line)
    blocks.append((cur_level, cur_title, cur))
    # 过滤：引言保留；顶层按黑名单整块删；子节跟随最近顶层，孤儿删除
    out = []
    has_top = False
    keep_top = False
    for level, title, blines in blocks:
        if level == 0:
            out.extend(blines)
            continue
        if level == 2:
            has_top = True
            keep_top = (title not in _DROP_SECTIONS
                        and not title.startswith(_DROP_SECTION_PREFIX))
            if keep_top:
                out.extend(blines)
            continue
        # 子节：黑名单子节删；无正文（只剩标题行）的孤儿子节删；否则跟随顶层
        body = [l for l in blines if l.strip()]
        if len(body) <= 1:
            continue
        if (title in _DROP_SECTIONS
                or title.startswith(_DROP_SECTION_PREFIX)):
            continue
        if has_top and keep_top:
            out.extend(blines)
    s = "\n".join(out)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------- 已知模板展开（进度列表 / 实体链接等） ----------
# 中文 Minecraft Wiki 的进度列表由 {{Advancements|key=...|require=...}} 模板渲染，
# 直接剥离会把整段进度列表删光（如「进度」页的 adventure.kill_a_mob）。
# 这里把这类模板展开为可读文本，进度中文名/描述/实体名取自游戏 lang 数据
# （Module:NameProvider/releaseJE），网络失败时退化为只保留 key。

_EL_MAP = {
    "je": "Java版", "java": "Java版",
    "be": "基岩版", "bedrock": "基岩版",
    "lce": "原主机版", "3ds": "New Nintendo 3DS版",
    "ee": "教育版", "cn": "中国版",
}

_ADV_LANG = None    # {adv_key: {"title": 中文名, "description": 中文描述}}
_LANG_ZH = None     # {规范化en名: 中文名}（实体/物品/方块的反向查表）
_ACH_LANG = None    # {成就id: {"title": 中文名, "desc": 中文描述}}（基岩版 lang）


def _norm_key(s):
    return re.sub(r"[\s_\-]+", "", (s or "")).lower()


def _fetch_module(title):
    """拉取一个 Module/模板 的源码文本。"""
    try:
        d = api({"action": "query", "titles": title,
                 "prop": "revisions", "rvprop": "content", "rvslots": "main"})
        page = next(iter(d.get("query", {}).get("pages", {}).values()), {})
        return (page.get("revisions") or [{}])[0].get("slots", {}).get("main", {}).get("*", "")
    except Exception:
        return ""


def _parse_lua_lang(text):
    """解析 'return { [ 'key' ] = { "en","zh_cn",... }, ... }' 为 {key: [值...]}。"""
    out = {}
    pat = re.compile(
        r"\[\s*'([^']+)'\s*\]\s*=\s*\{\s*((?:\"(?:\\.|[^\"\\])*\"\s*,?\s*)*)\}",
        re.S)
    for m in pat.finditer(text):
        key = m.group(1)
        vals = re.findall(r"\"((?:\\.|[^\"\\])*)\"", m.group(2))
        if vals:
            out[key] = vals
    return out


def _adv_lang():
    """惰性加载游戏 lang 数据（进度中文名/描述 + 实体物品方块反向表）。"""
    global _ADV_LANG, _LANG_ZH
    if _ADV_LANG is not None:
        return _ADV_LANG
    _ADV_LANG = {}
    _LANG_ZH = {}
    try:
        text = _fetch_module("Module:NameProvider/releaseJE")
        for key, vals in _parse_lua_lang(text).items():
            if len(vals) < 2:
                continue
            en, zh_cn = vals[0], vals[1]
            if key.startswith("advancements."):
                ak = key[len("advancements."):]      # adventure.kill_a_mob.title
                base, _, typ = ak.rpartition(".")
                if typ in ("title", "description"):
                    d = _ADV_LANG.setdefault(base, {})
                    d[typ] = zh_cn
            elif key.startswith(("entity.minecraft.",
                                 "item.minecraft.",
                                 "block.minecraft.")):
                _LANG_ZH[_norm_key(en)] = zh_cn
    except Exception:
        pass
    return _ADV_LANG


def _ach_names():
    """惰性加载基岩版 lang 的成就名/描述（achievement.<id>[.desc]）。

    成就页由 {{Achievements|id=...}} 渲染，id 可能是驼峰（acquireIron）或连字符
    （getting-wood），这里同时登记两种形式以便查表。
    """
    global _ACH_LANG
    if _ACH_LANG is not None:
        return _ACH_LANG
    _ACH_LANG = {}
    try:
        text = _fetch_module("Module:NameProvider/releaseBE")
        for key, vals in _parse_lua_lang(text).items():
            if not key.startswith("achievement.") or len(vals) < 2:
                continue
            rest = key[len("achievement."):]
            if not rest:
                continue
            if rest.endswith(".desc"):
                aid = rest[:-len(".desc")]
                d = _ACH_LANG.setdefault(_norm_key(aid), {})
                d["desc"] = vals[1]
            else:
                aid = rest
                d = _ACH_LANG.setdefault(_norm_key(aid), {})
                d["title"] = vals[1]
    except Exception:
        pass
    return _ACH_LANG


def _ach_title(key):
    """成就 id -> 中文名（尝试驼峰/连字符两种归一，找不到返回原 id）。"""
    d = _ach_names().get(_norm_key(key))
    return (d or {}).get("title") or key


def _adv_title(key):
    return (_adv_lang().get(key) or {}).get("title") or key


def _adv_aliases():
    """把每个进度的中文名与 ID 作为重定向别名指向「进度」页，便于检索命中。"""
    aliases = {}
    for key, d in _adv_lang().items():
        title = (d.get("title") or "").strip()
        if title:
            aliases[title] = "进度"
        aliases[key] = "进度"
    return list(aliases.items())


def _zh_name(target):
    """EntityLink/BlockLink/ItemLink 的英文名 -> 中文名（lang 反向查表）。"""
    _adv_lang()
    return _LANG_ZH.get(_norm_key(target)) or target


def _split_params(rest):
    """解析模板参数段 '|a=1|b|2' -> {'1':'b', '2':'2', 'a':'1'}。

    按顶层 '|' 切分，参数值内的嵌套模板（{{...}} / [[...]]）不被切开。
    """
    args = {}
    parts = []
    cur = []
    depth = 0
    i = 0
    while i < len(rest):
        if rest.startswith("{{", i) or rest.startswith("[[", i):
            depth += 1
            cur.append(rest[i:i + 2])
            i += 2
            continue
        if rest.startswith("}}", i) or rest.startswith("]]", i):
            depth -= 1
            cur.append(rest[i:i + 2])
            i += 2
            continue
        if rest[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(rest[i])
        i += 1
    if cur:
        parts.append("".join(cur))
    pos = 0
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 命名参数：= 出现在本层（任何嵌套 {{...}} / [[...]] 之前）
        eq = part.find("=")
        brace = min([i for i in (part.find("{{"), part.find("[[")) if i >= 0],
                    default=-1)
        if eq > 0 and (brace < 0 or eq < brace):
            k, v = part.split("=", 1)
            args[k.strip()] = v.strip()
        else:
            pos += 1
            args[str(pos)] = part
    return args


def _clean_list_item(s):
    return re.sub(r"^[#*;: \t]+", "", s or "").strip()


def _list_template(rest, sep="、"):
    """展开 flatlist / columns-list 等列表包装模板，保留条目内容。"""
    items = []
    for k, v in _split_params(rest).items():
        if k.isdigit():
            for line in str(v).splitlines():
                line = _clean_list_item(line)
                if line:
                    items.append(line)
    return sep.join(items)


def _adv_entry(params):
    """把 {{Advancements|...}} 参数渲染成一行可读文本。"""
    key = str(params.get("key", "")).strip()
    if not key:
        return ""
    data = _adv_lang().get(key) or {}
    name = data.get("title") or key
    desc = (data.get("description") or "").strip().rstrip("。；， ")
    req = str(params.get("require", "")).strip().rstrip("。；， ")
    parts = [f"进度[{name}]（{key}）"]
    if desc:
        parts.append(f"描述：{desc}")
    if req:
        parts.append(f"需求：{req}")
    up = str(params.get("upstream", "")).strip()
    if up:
        parts.append(f"上游：{up}")
    return "；".join(p for p in parts if p) + "。"


_MAT_SPECIAL = {
    "planks": "木板", "log": "原木", "logs": "原木", "wood": "原木",
    "copper": "铜", "stone": "石头", "sandstone": "砂岩", "wool": "羊毛",
    "concrete": "混凝土", "terracotta": "陶瓦", "brick": "砖",
    "slab": "台阶", "stairs": "楼梯", "fence": "栅栏", "wall": "墙",
    "iron": "铁", "gold": "金", "diamond": "钻石", "emerald": "绿宝石",
    "netherite": "下界合金", "redstone": "红石", "lapis": "青金石",
    "quartz": "石英", "cobblestone": "圆石", "glass": "玻璃",
    "door": "门", "trapdoor": "活板门", "fence gate": "栅栏门",
    "boat": "船", "sign": "告示牌", "bed": "床", "chest": "箱子",
    "barrel": "木桶", "ladder": "梯子", "button": "按钮", "lever": "拉杆",
    "torch": "火把", "pressure plate": "压力板", "candle": "蜡烛",
    "flower pot": "花盆", "armor stand": "盔甲架", "item frame": "物品展示框",
    "map": "地图", "book": "书", "paper": "纸", "arrow": "箭",
    "bow": "弓", "crossbow": "弩", "sword": "剑", "pickaxe": "镐",
    "axe": "斧", "shovel": "锹", "hoe": "锄", "helmet": "头盔",
    "chestplate": "胸甲", "leggings": "护腿", "boots": "靴子",
    "stone tier block": "石头类方块", "stone-tier block": "石头类方块",
    "wooden tool": "木工具", "stone tool": "石工具", "iron tool": "铁工具",
    "golden tool": "金工具", "diamond tool": "钻石工具",
}
_MAT_SPECIAL = {_norm_key(k): v for k, v in _MAT_SPECIAL.items()}


def _mat_zh(name):
    """配方材料英文名 -> 中文名（lang 查表 + 组名特例，兼容 'Any X' / 'Matching X' / {组}）。"""
    name = (name or "").strip().strip("{}").strip()
    if not name:
        return ""
    m = re.match(r"^Any\s+(.+)$", name)
    if m:
        return f"任意{_mat_zh(m.group(1))}"
    if re.match(r"^Match(?:ing)?\s+", name):
        _rest = re.sub(r"^Match(?:ing)?\s+", "", name)
        return f"对应{_mat_zh(_rest)}"
    m = re.match(r"^wood(?:en)?\s+(.+)$", name, re.I)
    if m:
        return f"木{_mat_zh(m.group(1))}"
    zh = _zh_name(name)
    if zh != name:
        return zh
    return _MAT_SPECIAL.get(_norm_key(name)) or name


def _split_multi(v):
    """按顶层 ';' 拆分多配方单元格（{group} 组标记不拆开）。"""
    parts = []
    cur = ""
    depth = 0
    for ch in (v or ""):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        if ch == ";" and depth == 0:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _craft_entry(params):
    """把 {{Crafting|A1=...|Output=...}} 渲染成可读配方（支持 ; 分隔的多配方块）。"""
    if str(params.get("custom", "")).strip() == "1":
        return ""  # 修复/自定义配方（铁砧等另有章节），跳过
    out_raw = (params.get("Output") or params.get("output")
               or params.get("OUTPUT") or "").strip()
    out_qty = 1
    if "," in out_raw and not out_raw.startswith("{"):
        out_name, _, q = out_raw.rpartition(",")
        out_name, q = out_name.strip(), q.strip()
        try:
            out_qty = int(q)
        except ValueError:
            out_name = out_raw
    else:
        out_name = out_raw
    outputs = _split_multi(out_name)

    cells = []
    for row in "ABC":
        for col in "123":
            v = str(params.get(f"{row}{col}", "")).strip()
            if v:
                cells.append((f"{row}{col}", v))
    if not cells and not out_name:
        return ""

    qty_str = f"×{out_qty}" if out_qty > 1 else ""

    def render_one(mats, out):
        s = "合成配方：" + " + ".join(mats)
        if out:
            s += f" → {_mat_zh(out)}{qty_str}"
        return s + "。"

    def mats_from(raw_list):
        cnt = {}
        order = []
        for m in raw_list:
            if not m:
                continue
            if m not in cnt:
                order.append(m)
            cnt[m] = cnt.get(m, 0) + 1
        return [f"{_mat_zh(m)}×{cnt[m]}" for m in order]

    if cells:
        multi = (len(outputs) > 1
                 or any(len(_split_multi(c)) > 1 for _, c in cells))
        if multi:
            n = max(len(outputs), *(len(_split_multi(c)) for _, c in cells))
            lines = []
            for k in range(n):
                raw = []
                for _, c in cells:
                    alts = _split_multi(c)
                    m = alts[k] if k < len(alts) else (alts[-1] if alts else "")
                    if m:
                        raw.append(m)
                out = outputs[k] if k < len(outputs) else ""
                lines.append(render_one(mats_from(raw), out))
            return "\n".join(lines)
        raw = [c for _, c in cells]
        out = outputs[0] if outputs else ""
        return render_one(mats_from(raw), out)

    # 无网格（纯位置参数）
    inp = str(params.get("1", "")).strip()
    if not inp:
        return ""
    return render_one(mats_from(_split_multi(inp)), outputs[0] if outputs else "")


def _smelt_entry(params):
    """把 {{Smelting|输入;输入|输出|经验}} 渲染成可读配方。"""
    inp = str(params.get("1", "")).strip()
    out = str(params.get("2", "")).strip()
    xp = str(params.get("3", "")).strip()
    if not inp or not out:
        return ""
    ins = "、".join(_mat_zh(x.strip()) for x in inp.split(";") if x.strip())
    s = f"烧炼配方：{ins} → {_mat_zh(out)}"
    if xp:
        s += f"（经验{xp}）"
    return s + "。"


def _achievement_entry(params):
    """把 {{Achievements|id=...|require=...}} 渲染成一行可读文本。"""
    aid = str(params.get("id", "")).strip()
    if not aid:
        return ""
    name = _ach_title(aid)
    desc = (_ach_names().get(_norm_key(aid)) or {}).get("desc") or ""
    req = str(params.get("require", "")).strip().rstrip("。；， ")
    parts = [f"成就[{name}]（{aid}）"]
    if desc:
        parts.append(f"描述：{desc}")
    if req:
        parts.append(f"需求：{req}")
    score = str(params.get("score", "")).strip()
    if score:
        parts.append(f"分值：{score}")
    return "；".join(p for p in parts if p) + "。"


def _render_template(inner):
    """把已知模板内容渲染为可读文本；未知模板返回 None（留给后续剥离）。"""
    inner = (inner or "").strip()
    if not inner:
        return None
    m = re.match(r"^\s*([^|\n]+?)(\s*\|.*)?$", inner, re.S)
    if not m:
        return None
    name = m.group(1).strip().lower()
    rest = m.group(2) or ""
    if name == "advancements":
        return _adv_entry(_split_params(rest))
    if name in ("crafting", "crafting grid"):
        return _craft_entry(_split_params(rest))
    if name == "smelting":
        return _smelt_entry(_split_params(rest))
    if name in ("achievements", "achievement"):
        return _achievement_entry(_split_params(rest))
    if name == "advancement name":
        args = _split_params(rest)
        key = str(args.get("1", "")).strip()
        variant = str(args.get("2", "")).strip().lower()
        return _adv_title(key) if variant.startswith("zh") else (key or None)
    if name in ("entitylink", "blocklink", "itemlink"):
        target = str(_split_params(rest).get("1", "")).strip()
        return _zh_name(target)
    if name == "flatlist":
        return _list_template(rest)
    if name in ("columns-list", "columns", "hlist", "plainlist"):
        return _list_template(rest, sep="、")
    if name == "el":
        ed = str(_split_params(rest).get("1", "")).strip().lower()
        return _EL_MAP.get(ed)
    if name in _PARSE_TMPL:
        target = str(_split_params(rest).get("1", "")).strip()
        if target:
            tpl = _PARSE_TMPL[name]
            return _render_parse_invoke(f"{{{{{tpl}|{target}}}}}")
    return None


def _expand_known_templates(s):
    """反复展开已知模板（Advancements 列表 / Advancement name / 实体物品链接 / flatlist / el），
    未知模板保持原样交给后续剥离。"""
    for _ in range(8):
        out = []
        i = 0
        changed = False
        while i < len(s):
            if s[i:i + 2] == "{{":
                j = i + 2
                depth = 1
                while j < len(s) and depth:
                    if s.startswith("{{", j):
                        depth += 1
                        j += 2
                    elif s.startswith("}}", j):
                        depth -= 1
                        j += 2
                    else:
                        j += 1
                inner = s[i + 2:j - 2]
                rep = _render_template(inner)
                if rep is None:
                    out.append(s[i:j])
                else:
                    out.append(rep)
                    changed = True
                i = j
            else:
                out.append(s[i])
                i += 1
        s = "".join(out)
        if not changed:
            break
    # 展开完成后补齐词间分隔（如 flatlist 末尾实体名与后文粘连）
    s = re.sub(r"([\u4e00-\u9fff])(除此以外)", r"\1，\2", s)
    return s


# ---------- 表格转文本 ----------
# 一些数据（生物群系列表、附魔表、状态效果表等）以 wikitext 表格 {|...|} 呈现，
# 过去被整体移除。这里做保守转换：去属性/空单元格/图片列，按「列名：值」渲染并限长。

_TBL_CAP = 1500     # 单表渲染字符上限


def _cell_content(cell):
    """去掉单元格的属性前缀（style=... | 正文），返回正文。"""
    cell = (cell or "").strip()
    if re.match(r"^[\w-]+=\"", cell):
        if "|" in cell:
            cell = cell.split("|", 1)[1].strip()
        else:
            cell = ""
    cell = re.sub(r"\[\[(?:File|文件|Image|图片):[^\]]*\]\]", "", cell)
    cell = re.sub(r"'''|''", "", cell)
    return cell.strip()


def _iter_tables(s):
    """迭代 wikitext 中的 {|...|} 表格（支持嵌套深度），yield (内容, 起点, 终点)。"""
    i = 0
    while True:
        start = s.find("{|", i)
        if start < 0:
            return
        depth = 0
        j = start
        end = -1
        while j < len(s):
            if s.startswith("{|", j):
                depth += 1
                j += 2
            elif s.startswith("|}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    end = j - 2
                    break
            else:
                j += 1
        if end < 0:
            return
        yield s[start + 2:end], start, end
        i = end + 2


def _render_table(content):
    """把表格内容渲染为可读文本：表头配对，每行「列名：值」，去空/图片单元格。"""
    rows = []
    cur = []
    for line in content.split("\n"):
        s = line.strip()
        if not s or s.startswith("|+") or s.startswith("|}"):
            continue
        if s.startswith("|-"):
            if cur:
                rows.append(cur)
            cur = []
            continue
        if s.startswith("!"):
            body = s[1:]
            for c in re.split(r"!!", body):
                cur.append(("h", _cell_content(c)))
        elif s.startswith("|"):
            body = s[1:]
            for c in re.split(r"\|\|", body):
                cur.append(("d", _cell_content(c)))
        else:
            if cur:
                cur[-1] = (cur[-1][0], (cur[-1][1] + " " + s).strip())
    if cur:
        rows.append(cur)

    # 表头 = 第一行全为表头单元格的行
    h_idx = next((i for i, r in enumerate(rows)
                  if r and all(t == "h" for t, _ in r)), None)
    hnames = [c for _, c in rows[h_idx]] if h_idx is not None else []
    out = []
    budget = _TBL_CAP
    for i, r in enumerate(rows):
        if i == h_idx:
            continue
        cells = []
        for k, (typ, c) in enumerate(r):
            if not c:
                continue
            if typ == "h":
                cells.append(c)
            else:
                name = hnames[k] if k < len(hnames) and hnames[k] else ""
                cells.append(f"{name}：{c}" if name else c)
        line = "；".join(cells).strip()
        if not line:
            continue
        if len(line) > budget:
            out.append(line[:budget])
            break
        out.append(line)
        budget -= len(line)
    if not out:
        return ""
    return "[数据表]\n" + "\n".join(out)


def _strip_templates(s):
    """剥离所有剩余 {{...}} 模板（含嵌套，平衡花括号扫描）。

    取代多次正则：深层嵌套（如 {{quote|...{{ref}}...}}）单次扫净。
    """
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i:i + 2] == "{{":
            depth = 1
            j = i + 2
            while j < n and depth:
                if s.startswith("{{", j):
                    depth += 1
                    j += 2
                elif s.startswith("}}", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            i = j
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _convert_tables(s):
    """把页面中的表格替换为可读文本（原逻辑整体移除）。"""
    out = []
    last = 0
    for content, start, end in _iter_tables(s):
        out.append(s[last:start])
        rendered = _render_table(content)
        if rendered:
            out.append(rendered + "\n")
        last = end + 2
    out.append(s[last:])
    return "".join(out)


# ---------- 复杂模板经 MediaWiki parse API 渲染 ----------
# {{LootChestItem|X}}（箱子战利品）与 {{Drop sources|X}}（生物掉落物）由 Lua 数据模块
# 生成，静态解析脆弱，这里直接调用 parse API 渲染成纯文本（进程内缓存，失败回退为空）。

_PARSE_CACHE = {}
_PARSE_CAP = 2500    # 单次 parse 渲染文本上限（Drop sources 等大表截断）
_PARSE_TMPL = {
    "lootchestitem": "LootChestItem",
    "drop sources": "Drop sources",
}


def _html_cell_text(c):
    c = re.sub(r"<br\s*/?>", "，", c)
    c = re.sub(r"<[^>]+>", "", c)
    c = re.sub(r"\s+", " ", c)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        c = c.replace(a, b)
    return html.unescape(c).strip()


def _attr_int(attrs, name):
    m = re.search(name + r"\s*=\s*[\"']?(\d+)", attrs)
    return int(m.group(1)) if m else 1


def _parse_html_rows(tbl):
    """解析 <table> 为二维单元格网格（处理 rowspan/colspan 合并）。"""
    grid = []
    spans = {}    # 列 -> 剩余覆盖行数
    spanval = {}  # 列 -> 合并值
    for row in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", tbl):
        cells = []
        col = 0
        for m in re.finditer(r"<t([dh])([^>]*)>([\s\S]*?)</t\1>", row):
            attrs, content = m.group(2), m.group(3)
            while spans.get(col, 0) > 0:
                cells.append(spanval[col])
                spans[col] = spans.get(col, 0) - 1
                col += 1
            text = _html_cell_text(content)
            rs = _attr_int(attrs, "rowspan")
            cs = _attr_int(attrs, "colspan")
            for _ in range(cs):
                cells.append(text)
                if rs > 1:
                    spans[col] = rs - 1
                    spanval[col] = text
                col += 1
        while spans.get(col, 0) > 0:
            cells.append(spanval[col])
            spans[col] = spans.get(col, 0) - 1
            col += 1
        grid.append(cells)
    return grid


def _html_tables_to_text(html):
    """把 parse API 渲染结果中的 <table> 解析为「列名：值」文本行。

    同名列连续出现（如掉落表的多个抢夺等级列）时折叠为「列名：值1 / 值2」，
    跳过「值==列名」的嵌套表头伪影。
    """
    out = []
    for tbl in re.findall(r"<table[\s\S]*?</table>", html):
        grid = _parse_html_rows(tbl)
        if not grid:
            continue
        header = grid[0]
        lines = []
        for r in grid[1:]:
            if not r or not any(c for c in r if c):
                continue
            parts = []
            run_name, run_vals = None, []
            for i, c in enumerate(r):
                if not c:
                    continue
                name = header[i] if i < len(header) and header[i] else ""
                if c == name:
                    continue
                if name and name == run_name:
                    run_vals.append(c)
                else:
                    if run_name and run_vals:
                        parts.append(f"{run_name}：{' / '.join(run_vals)}")
                    run_name = name
                    run_vals = [c]
            if run_name and run_vals:
                parts.append(f"{run_name}：{' / '.join(run_vals)}")
            line = "；".join(parts)
            if line:
                lines.append(line)
        if lines:
            out.append("[数据表]\n" + "\n".join(lines))
    return "\n\n".join(out)


def _render_parse_invoke(wikitext):
    """用 MediaWiki parse API 渲染 wikitext 片段为纯文本（带缓存，失败重试）。"""
    if wikitext in _PARSE_CACHE:
        return _PARSE_CACHE[wikitext]
    _PARSE_CACHE[wikitext] = ""
    for attempt in range(3):
        try:
            time.sleep(_SLEEP)
            resp = _http().post(API, data={
                "action": "parse", "contentmodel": "wikitext",
                "text": wikitext, "prop": "text",
                "format": "json", "formatversion": "2",
            }, headers={**_UA, "Content-Type": "application/x-www-form-urlencoded"})
            if resp.status_code != 200:
                time.sleep(1 + attempt)
                continue
            j = resp.json()
            if "parse" not in j:
                time.sleep(1 + attempt)
                continue
            html_text = j["parse"]["text"]
            html_text = re.sub(r"<style[\s\S]*?</style>", "", html_text)
            html_text = re.sub(r"<script[\s\S]*?</script>", "", html_text)
            table_txt = _html_tables_to_text(html_text)
            # 表格块替换为渲染文本，剩余 HTML 转为普通文本
            html_text = re.sub(r"<table[\s\S]*?</table>", "{{TABLE}}", html_text)
            txt = re.sub(r"<[^>]+>", " ", html_text)
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n\s*\n+", "\n", txt)
            for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
                txt = txt.replace(a, b)
            txt = html.unescape(txt).replace("{{TABLE}}", "\n" + table_txt).strip()
            if len(txt) > _PARSE_CAP:
                txt = txt[:_PARSE_CAP].rsplit("\n", 1)[0]
            _PARSE_CACHE[wikitext] = txt
            return txt
        except Exception:
            time.sleep(1 + attempt)
    return ""


def clean_wikitext(text):
    """把 wikitext 清理为适合注入 AI 的纯文本。

    基础清理（模板/链接/表格/HTML）+ 补充清理（繁简标记/跨语言/画廊/小节过滤），
    只保留引言段与有价值小节（生成/掉落/行为/变种等）。
    进度列表/合成配方等模板渲染内容会展开保留，数据表格转换为可读文本。
    """
    if not text:
        return ""
    s = text
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)                     # HTML 注释
    # 文件 / 分类 / 语言链接
    s = re.sub(r"\[\[(?:File|文件|Image|图片|Category|分类|模板):[^\]]*\]\]",
               "", s, flags=re.I)
    # 已知模板展开为可读文本（进度列表 / 合成配方 / 箱子战利品等）
    s = _expand_known_templates(s)
    # 其余模板（含深层嵌套）整体剥离
    s = _strip_templates(s)
    # 内部链接 [[目标|显示]] -> 显示 ; [[目标]] -> 目标
    s = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", s)
    # 外部链接
    s = re.sub(r"\[(?:https?://[^\] ]*)(?: ([^\]]*))?\]", r"\1", s)
    # 表格转文本（数据表：生物群系/附魔/状态效果等）
    s = _convert_tables(s)
    # 引用与其余 HTML
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    # 样式标记 / 列表前缀
    s = re.sub(r"'''|''", "", s)
    s = re.sub(r"^[#*;:]+", "", s, flags=re.M)
    # 实体
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                 ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        s = s.replace(a, b)
    return _strip_section_noise(s)


# ---------- 页面分类 ----------

_VERSION_RE = re.compile(
    r"(?:Java版|基岩版|教育版|中国版|携带版|Minecraft Java版)[ 　]*\d"
    r"|^(Alpha|Beta|Infdev|Indev|Classic|Pre-classic)\b"
    r"|^(\d+\.)?\d+(\.\d+)*$"
    r"|^\d{2}w\d{2}[a-z]$"
    r"|(版本记录|开发版本|更新日志|预览版|预发布|发布候选|快照|开发版|版本历史)"
    # —— 版本族页（此前漏判：成百上千个版本页落到 common，污染实体池）——
    # 例：Java版Alpha v1.0.1（「Java版」后紧跟的不是数字 → 旧规则失手）
    r"|^(?:Java版|基岩版|教育版|中国版|携带版)[ ]?"
    r"(?:Alpha|Beta|Infdev|Indev|Classic|Pre-classic|pre-Classic|Realms)"
    r"|^(?:Java版|基岩版|教育版|中国版|携带版)指南(?:/|$)"
    r"|/(?:开发者版本|开发版本)$"
    r"|(?:小)?更新$"
    # 平台版本页（Gear VR / Wii U / Xbox / PlayStation…）
    r"|^(?:Minecraft[：:]?)?(?:Gear VR|Wii U|Xbox|PlayStation|Nintendo Switch|PS[0-9])"
)
_TECH_RE = re.compile(
    r"/(ED|DV|DV2|ID|be|je)$"
    r"|(数据值|实体数据|方块状态|物品组件|世界格式|存档格式|协议版本|语言文件)$"
    r"|^(?:Java版|基岩版|教育版)?(?:标签|存档格式|世界格式|物品组件)(?:/|$)"
)
# 正文技术特征：数据格式/底层结构页（NBT / SNBT / Molang 等）
_TECH_TEXT_HINT = re.compile(r"(树状数据结构|二进制|命名空间ID|序列化|基于表达式)")
# 版本前缀页：以平台/版本名开头的标题一律不是游戏实体。
# 实测 common 里这类共 78 条（Java版 44 + 基岩版 28 + 携带版 3 + 原主机版 2 +
# 教育版 1），全部是版本号、数据值、网络协议、文档、独有/已移除特性 —— 零误伤。
_VERSION_PREFIX_RE = re.compile(
    r"^(?:Java版|基岩版|携带版|教育版|中国版|原主机版|Minecraft[：:]?(?:Java|基岩)版)")
# 上面这批里偏「技术文档」的子集（归 tech，与 version 一样不参与检索）
_TECHISH_RE = re.compile(
    r"(数据值|网络协议|协议|材料|粒子|函数|声音事件|文档|脚本|地物|结构文件|配方文档)")
# 冷门特征：成就/进度/活动/周边/文化/技术性等，检索时优先级低于常用知识
# 注：「音乐」刻意**不**放在这里——标题含「音乐」的可能是真物品（音乐唱片），
#     音乐曲目页的标题多是英文曲名，靠下面的正文特征（_RARE_TEXT_HINT2）识别。
_RARE_RE = re.compile(
    r"(成就|进度|奖杯|历史|年表|里程碑|记录|档案|大事记)"
    r"|(活动|节日|周年|纪念|生日|万圣节|圣诞|新年|愚人节|彩蛋|庆典|聚会)"
    r"|(电影|书籍|周边|服饰|披风|皮肤|纪念品)"
    r"|(启动器|服务器|社区|Mojang|Notch|开发者|作者|材质包|模组|数据包|资源包|存档|语言文件|翻译|无障碍|辅助功能|术语表)"
    r"|(Minecraft Legends|Minecraft Dungeons|Minecraft Earth|Festival|Live|Movie|Comic)"
)


# 正文中的成就/进度提示词：说明该页是「成就/进度」实体页而非导航页
_RARE_TEXT_HINT = re.compile(r"(成就|进度|奖杯|升级|获得条件)")
# 正文特征判定「非游戏实体」页（标题是英文曲名/人名/公司名，靠标题认不出来）：
#   音乐曲目："A Familiar Room是一首由Aaron Cherof创作的音乐。" + "== 播放条件 =="
#   开发方/人员："…是一个…独立游戏开发工作室。" / "是一名音乐制作人"
#   平台版本："…Minecraft原主机版"
_RARE_TEXT_HINT2 = re.compile(
    r"(播放条件|是一首由|创作的音乐|原声带|作曲家|音乐制作人"
    r"|工作室|开发公司|软件工程师|== 雇员 ==|== 生平 =="
    r"|原主机版)")
# 跨版本/跨平台同名成就进度页的特征（如「怪物猎人」正文以 可以指 开头但列举的是进度/成就）
_DISAMBIG_WITH_RARE = re.compile(r"(进度|成就|奖杯)")


def _is_disambig(title, text=""):
    """消歧义页：标题带「消歧义」，或正文以「可以指」开头且无实质知识的导航页。

    例外：正文以「可以指」开头但列举的是成就/进度/奖杯的条目
    （如「怪物猎人」= 击杀怪物的进度），是跨版本同名的实际内容页，不算导航页。
    """
    if not title:
        return False
    if "消歧义" in title or "消歧義" in title:
        return True
    if text and re.match(r"^[（(]?[^）)]*[）)]?.*可以指[:：]", text[:60]):
        if _DISAMBIG_WITH_RARE.search(text):
            return False
        return True
    return False


def classify_title(title, text=None):
    """给页面标题分类：version / tech / common / rare / disambig。

    常用=玩家游戏中常遇到的生物/物品/方块/机制等，优先注入；
    冷门=成就/进度/活动/周边/历史等边缘内容，仅兜底；
    disambig=消歧义导航页（无实际知识，检索排除）；
    version 与 tech 对游戏互动无价值，直接过滤。

    标题认不出来的（英文曲名 / 人名 / 公司名），再靠正文特征兜一层：
    音乐曲目、开发方人员、平台版本页都不属于"游戏实体"，统一降为 rare。
    """
    if not title:
        return "common"
    text = text or ""
    if _is_disambig(title, text):
        return "disambig"
    if _VERSION_PREFIX_RE.match(title):
        # 「Java版数据值/方块ID」这类技术文档页归 tech，其余版本页归 version
        return "tech" if _TECHISH_RE.search(title) else "version"
    if _VERSION_RE.search(title):
        return "version"
    if _TECH_RE.search(title):
        return "tech"
    if text and _TECH_TEXT_HINT.search(text[:200]):
        return "tech"
    if _RARE_RE.search(title):
        return "rare"
    if text and (_RARE_TEXT_HINT.search(text[:120])
                 or _RARE_TEXT_HINT2.search(text[:200])):
        return "rare"
    return "common"


# ---------- 建库 ----------

_SCHEMA = """
DROP TABLE IF EXISTS pages;
DROP TABLE IF EXISTS redirects;
DROP TABLE IF EXISTS pages_fts;
CREATE TABLE pages(id INTEGER PRIMARY KEY, title TEXT UNIQUE NOT NULL,
                   text TEXT, updated TEXT, category TEXT DEFAULT 'common');
CREATE TABLE redirects(alias TEXT PRIMARY KEY, title TEXT);
CREATE VIRTUAL TABLE pages_fts USING fts5(
    title, body, content='pages', content_rowid='id', tokenize='trigram');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, title, body)
        VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.text);
    INSERT INTO pages_fts(rowid, title, body)
        VALUES (new.id, new.title, new.text);
END;
"""


def write_db(rows, redirects, full=True):
    """rows: [(title, text), ...]；redirects: [(alias, title), ...]。"""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        if full:
            c.executescript(_SCHEMA)
            c.executemany("INSERT OR REPLACE INTO pages(title, text, updated, category) "
                          "VALUES (?,?,?,?)",
                          [(t, tx, now_str(), classify_title(t, tx)) for t, tx in rows])
            c.executemany("INSERT OR REPLACE INTO redirects(alias, title) "
                          "VALUES (?,?)",
                          list(redirects) + _adv_aliases())
        else:
            c.executemany("INSERT OR REPLACE INTO pages(title, text, updated, category) "
                          "VALUES (?,?,?,?)",
                          [(t, tx, now_str(), classify_title(t, tx)) for t, tx in rows])
        conn.commit()
        n = c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        r = c.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
        print(f"数据库已更新：内容页 {n}，重定向 {r}")
    finally:
        conn.close()


def fetch_tpl_users(template):
    """返回主命名空间中使用指定模板的全部页面标题（transcludedin）。"""
    titles = []
    d = api({"action": "query", "titles": template,
             "prop": "transcludedin", "tinamespace": "0", "tilimit": "max"})
    page = next(iter(d.get("query", {}).get("pages", {}).values()), {})
    titles = [p.get("title", "") for p in page.get("transcludedin", [])]
    return [t for t in titles if t]


def cmd_refresh_tpl(templates):
    """按模板批量重新抓取所有使用该模板的页面（如 --refresh-tpl Crafting Smelting）。

    用于清理规则升级后，把依赖模板渲染的内容（合成/烧炼/成就等）重新入库。
    """
    templates = [t.strip() for t in (templates or []) if t.strip()]
    if not templates:
        print("用法：python wiki_build.py --refresh-tpl Crafting Smelting Achievements")
        return
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建（python wiki_build.py）")
        return
    titles = []
    for tpl in templates:
        name = tpl if tpl.startswith("Template:") else f"Template:{tpl}"
        try:
            got = fetch_tpl_users(name)
        except RuntimeError:
            print(f"  查询 {name} 失败，跳过")
            continue
        print(f"  {name}: {len(got)} 页")
        titles += got
    titles = list(dict.fromkeys(titles))
    if not titles:
        print("未找到使用这些模板的页面")
        return
    # 只刷新已入库的页（避免把模板页/新页误插）
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {r[0] for r in conn.execute("SELECT title FROM pages").fetchall()}
    finally:
        conn.close()
    titles = [t for t in titles if t in existing]
    print(f"需重抓：{len(titles)} 页（已入库）")
    cmd_refresh(titles)


_REDIRECT_RE = re.compile(r"^#\s*REDIRECT\s*\[\[([^\]|#]+)")


def cmd_refresh(titles):
    """重新抓取并清理指定页面（清理规则升级后修复已入库页面，如「进度」）。

    例：python wiki_build.py --refresh 进度 成就
    """
    titles = [t.strip() for t in (titles or []) if t.strip()]
    if not titles:
        print("用法：python wiki_build.py --refresh 标题1 [标题2 ...]")
        return
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建（python wiki_build.py）")
        return
    print(f"重新抓取 {len(titles)} 个页面…")
    contents = fetch_content_by_titles(titles)
    fetched = [(t, contents.get(t, "")) for t in titles if t in contents]
    if not fetched:
        print("未抓到任何页面")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        for t, wikitext in fetched:
            m = _REDIRECT_RE.match(wikitext.strip())
            if m:
                target = m.group(1).strip()
                c.execute("DELETE FROM pages WHERE title=?", (t,))
                c.execute("INSERT OR REPLACE INTO redirects(alias, title) "
                          "VALUES (?,?)", (t, target))
                print(f"  {t} -> 重定向到 {target}")
                continue
            tx = clean_wikitext(wikitext)
            c.execute("INSERT OR REPLACE INTO pages(title, text, updated, category) "
                      "VALUES (?,?,?,?)",
                      (t, tx, now_str(), classify_title(t, tx)))
        conn.commit()
        c.executemany("INSERT OR REPLACE INTO redirects(alias, title) "
                      "VALUES (?,?)", _adv_aliases())
        conn.commit()
    finally:
        conn.close()
    print(f"刷新完成：{len(fetched)} 页")


def cmd_export(out_dir=None):
    """把本地知识库导出为人类可读的纯文本（按分类分文件 + 索引 + 重定向）。

    默认导出到 wiki_data/export/，生成 common/rare/disambig/version/tech.txt、
    redirects.txt 与 _index.txt，方便直接检查库内内容。
    """
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建（python wiki_build.py）")
        return
    out_dir = out_dir or os.path.join(DB_DIR, "export")
    os.makedirs(out_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        rows = c.execute(
            "SELECT title, text, updated, category FROM pages "
            "ORDER BY category, title").fetchall()
        idx = []
        files = {}
        for title, text, updated, category in rows:
            fn = f"{category}.txt"
            if fn not in files:
                files[fn] = open(os.path.join(out_dir, fn),
                                 "w", encoding="utf-8")
            f = files[fn]
            f.write("=" * 60 + "\n")
            f.write(f"标题：{title}\n")
            f.write(f"分类：{category} | 更新时间：{updated or '-'}\n")
            f.write("=" * 60 + "\n")
            f.write((text or "(无内容)").strip() + "\n\n")
            idx.append(f"{category}\t{title}")
        for f in files.values():
            f.close()
        with open(os.path.join(out_dir, "redirects.txt"),
                  "w", encoding="utf-8") as f:
            f.write("别名 -> 目标标题\n")
            f.write("=" * 40 + "\n")
            for alias, title in c.execute(
                    "SELECT alias, title FROM redirects ORDER BY alias"):
                f.write(f"{alias}\t->\t{title}\n")
        with open(os.path.join(out_dir, "_index.txt"),
                  "w", encoding="utf-8") as f:
            f.write("分类\t标题\n")
            f.write("=" * 40 + "\n")
            f.write("\n".join(idx) + "\n")
        stat = {}
        for title, text, updated, category in rows:
            stat[category] = stat.get(category, 0) + 1
        print(f"导出完成：{out_dir}")
        for cat, n in sorted(stat.items()):
            print(f"  {cat}.txt：{n} 页")
        print(f"  redirects.txt：{c.execute('SELECT COUNT(*) FROM redirects').fetchone()[0]} 条别名")
        print(f"  _index.txt：{len(idx)} 行索引")
    finally:
        conn.close()


def cmd_reclean():
    """对已有数据库重新清理文本（不重新抓取），自动同步 FTS 索引。

    清理规则升级后对现有库执行，无需重新抓取 wikitext。
    """
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        rows = c.execute("SELECT id, text FROM pages").fetchall()
        updated = 0
        for pid, text in rows:
            new = clean_wikitext(text)
            if new != text:
                c.execute("UPDATE pages SET text=? WHERE id=?", (new, pid))
                updated += 1
        conn.commit()
        # 统计
        n = c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        avg = c.execute("SELECT AVG(length(text)) FROM pages").fetchone()[0]
        print(f"重新清理完成：更新 {updated} / {n} 页，平均每页 {avg:.0f} 字符")
    finally:
        conn.close()


def cmd_classify():
    """对已有数据库补分类（不重新抓取）：加 category 列并填充。"""
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        cols = [r[1] for r in c.execute("PRAGMA table_info(pages)").fetchall()]
        if "category" not in cols:
            c.execute("ALTER TABLE pages ADD COLUMN category TEXT DEFAULT 'content'")
        rows = c.execute("SELECT id, title, text FROM pages").fetchall()
        for pid, title, text in rows:
            c.execute("UPDATE pages SET category=? WHERE id=?",
                      (classify_title(title, text), pid))
        conn.commit()
        stat = {}
        for cat, cnt in c.execute("SELECT category, COUNT(*) FROM pages "
                                  "GROUP BY category").fetchall():
            stat[cat] = cnt
        print(f"分类完成：{stat}")
    finally:
        conn.close()


def read_meta(key):
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""


def write_meta(key, value):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
                     (key, value))
        conn.commit()
    finally:
        conn.close()


# ---------- 命令 ----------

def cmd_full(max_pages=None, include_redirects=True):
    print("步骤 1/3：抓取主命名空间内容页…")
    titles = fetch_all_titles("nonredirects")
    if max_pages:
        titles = titles[:max_pages]
    print(f"  内容页标题：{len(titles)}")
    contents = fetch_all_content(titles)
    rows = [(t, clean_wikitext(c)) for t, c in contents]
    print(f"  抓取页面：{len(rows)}")

    redirects = []
    if include_redirects:
        print("步骤 2/3：抓取重定向表…")
        redirects = fetch_redirects(titles)
        print(f"  重定向条目：{len(redirects)}")

    print("步骤 3/3：写入 SQLite…")
    write_db(rows, redirects, full=True)
    # 记录增量更新基准：下次 --update 只拉取该时刻之后的变更
    write_meta("last_rc_ts", iso_now())
    print("全量构建完成")


def cmd_update():
    if not os.path.isfile(DB_PATH):
        print("本地知识库不存在，先执行全量构建（python wiki_build.py）")
        return
    last_ts = read_meta("last_rc_ts")
    if not last_ts:
        print("无增量更新基准，请先全量构建（python wiki_build.py）")
        return
    print(f"拉取最近变更（截止时间：{last_ts}）…")
    titles, _ = fetch_recent_changes_titles(last_ts)
    print(f"  变更页面：{len(titles)}")
    if not titles:
        print("无更新")
        return
    contents = fetch_content_by_titles(titles)
    rows = [(t, clean_wikitext(contents.get(t, ""))) for t in titles]
    write_db(rows, [], full=False)
    # 下次增量以本次结束时刻为下界，避免遗漏
    write_meta("last_rc_ts", iso_now())
    print("增量更新完成")


def main():
    ap = argparse.ArgumentParser(description="构建 Minecraft 中文 Wiki 本地知识库")
    ap.add_argument("--update", action="store_true", help="增量更新")
    ap.add_argument("--classify", action="store_true",
                    help="对已有数据库补分类（不重新抓取）")
    ap.add_argument("--reclean", action="store_true",
                    help="对已有数据库重新清理文本（不重新抓取）")
    ap.add_argument("--export", nargs="?", const="", metavar="DIR",
                    help="导出数据库为可读文本（默认 wiki_data/export/，可指定目录）")
    ap.add_argument("--refresh", nargs="+", metavar="TITLE",
                    help="重新抓取并清理指定页面（如：--refresh 进度）")
    ap.add_argument("--refresh-tpl", nargs="+", metavar="TPL",
                    help="按模板批量重抓所有使用它的页面（如：--refresh-tpl Crafting Smelting）")
    ap.add_argument("--limit", type=int, default=0,
                    help="调试：只抓前 N 页（默认全量）")
    ap.add_argument("--no-redirects", action="store_true",
                    help="跳过重定向表（仅内容页）")
    args = ap.parse_args()

    if args.export is not None:
        cmd_export(args.export or None)
    elif args.refresh_tpl:
        cmd_refresh_tpl(args.refresh_tpl)
    elif args.refresh:
        cmd_refresh(args.refresh)
    elif args.reclean:
        cmd_reclean()
    elif args.classify:
        cmd_classify()
    elif args.update:
        cmd_update()
    else:
        cmd_full(args.limit or None, not args.no_redirects)


if __name__ == "__main__":
    sys.exit(main())