# -*- coding: utf-8 -*-
"""槽位抽取：从一句话里抽出「物品 / 数量 / 坐标 / 目标生物 / 槽位」。

核心是**物品名消歧**——语音输入几乎不可能逐字正确，
「帮我合成一把稿子」里的「稿子」其实是「镐子」。纯字面匹配必挂，
所以这里用**拼音模糊匹配**：

1. 字面精确（最长优先）—— 覆盖「铁镐」「工作台」
2. 拼音精确（忽略声调、忽略间距）—— 覆盖「稿子→镐子」「合诚→合成」
3. 拼音允许一个音节的增/删/换 —— 覆盖「铁搞子→铁镐」
4. 首字母缩写 —— 覆盖键盘输入的「tg→铁镐」

物品词典由**模组在握手时下发**（中文名 ↔ 物品 id，含 mod 物品、对应当前语言）；
拿不到时降级用本地 wiki 词条，只做「认得出名字」，不产生 id（也就不会误执行）。
"""

import json
import os
import re
from dataclasses import dataclass, field

from nlu import taxonomy

# ---------------------------------------------------------------- 词典

# 常见敌对/被动生物（attack / feed / look 的目标）。
# 不用模组下发——生物名固定且量少，内置更省一次握手。
MOBS = [
    "僵尸", "骷髅", "苦力怕", "爬行者", "蜘蛛", "末影人", "史莱姆", "女巫",
    "溺尸", "尸壳", "烈焰人", "恶魂", "岩浆怪", "凋灵骷髅", "凋灵", "掠夺者",
    "卫道士", "唤魔者", "幻术师", "潜影贝", "守卫者", "远古守卫者", "监守者",
    "猪灵", "疣猪兽", "凋灵骷髅", "猪", "牛", "羊", "鸡", "狼", "猫", "马",
    "驴", "骡", "狐狸", "兔子", "熊猫", "北极熊", "羊驼", "海豚", "鱿鱼",
    "蝙蝠", "蜜蜂", "村民", "流浪商人", "铁傀儡", "雪傀儡", "末影龙",
    "凋灵", "怪", "怪物", "敌对生物", "小怪", "boss",
]

# 生物中文名 → 实体类型 id（供 attack 指定目标；模组侧 Identifier.tryParse 会自动补
# ``minecraft:`` 命名空间）。「怪 / 怪物 / 敌对生物 / 小怪 / boss」不映射到具体实体，
# 保持走模组「最近敌对生物」逻辑。
MOB_IDS = {
    "僵尸": "minecraft:zombie", "骷髅": "minecraft:skeleton",
    "苦力怕": "minecraft:creeper", "爬行者": "minecraft:creeper",
    "蜘蛛": "minecraft:spider", "末影人": "minecraft:enderman",
    "史莱姆": "minecraft:slime", "女巫": "minecraft:witch",
    "溺尸": "minecraft:drowned", "尸壳": "minecraft:husk",
    "烈焰人": "minecraft:blaze", "恶魂": "minecraft:ghast",
    "岩浆怪": "minecraft:magma_cube", "凋灵骷髅": "minecraft:wither_skeleton",
    "凋灵": "minecraft:wither", "掠夺者": "minecraft:pillager",
    "卫道士": "minecraft:vindicator", "唤魔者": "minecraft:evoker",
    "幻术师": "minecraft:illusioner", "潜影贝": "minecraft:shulker",
    "守卫者": "minecraft:guardian", "远古守卫者": "minecraft:elder_guardian",
    "监守者": "minecraft:warden", "猪灵": "minecraft:piglin",
    "疣猪兽": "minecraft:hoglin",
    "猪": "minecraft:pig", "牛": "minecraft:cow", "羊": "minecraft:sheep",
    "鸡": "minecraft:chicken", "狼": "minecraft:wolf", "猫": "minecraft:cat",
    "马": "minecraft:horse", "驴": "minecraft:donkey", "骡": "minecraft:mule",
    "狐狸": "minecraft:fox", "兔子": "minecraft:rabbit",
    "熊猫": "minecraft:panda", "北极熊": "minecraft:polar_bear",
    "羊驼": "minecraft:llama", "海豚": "minecraft:dolphin",
    "鱿鱼": "minecraft:squid", "蝙蝠": "minecraft:bat", "蜜蜂": "minecraft:bee",
    "村民": "minecraft:villager", "流浪商人": "minecraft:wandering_trader",
    "铁傀儡": "minecraft:iron_golem", "雪傀儡": "minecraft:snow_golem",
    "末影龙": "minecraft:ender_dragon",
}

# 槽位词 → 模组槽位规范（见 mod_contract 的槽位表达式）
# 注意：**不要**放「脚下」——它是位置词（见 _MAID_WORDS），放进来会让
# 「在我脚下放方块」被误读成「装备到脚部」。脚部槽位用「靴子/鞋子/脚上」。
SLOT_WORDS = {
    "主手": "mainhand", "手上": "mainhand", "手里": "mainhand", "手": "mainhand",
    "副手": "offhand", "左手": "offhand",
    "头盔": "head", "头部": "head", "头上": "head",
    "胸甲": "chest", "护胸": "chest", "身体": "chest",
    "护腿": "legs", "腿": "legs", "裤腿": "legs",
    "靴子": "feet", "鞋子": "feet", "脚上": "feet",
    "背包": "inv", "包里": "inv", "身上": "inv",
}

# 只有这些意图才谈得上「目标槽位」（避免与位置/容器语义交叉污染）
SLOT_INTENTS = frozenset({"equip", "transfer"})

# 数量词
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100}
# 量词别名（一组 = 64，MC 一组上限）
_STACK_WORDS = {"组": 64, "半组": 32, "打": 12, "沓": 64}

# 位置：x y z / x=1 y=2 z=3 / 1,2,3
_POS_RE = re.compile(
    r"[xX]?\s*=?\s*(-?\d+)\s*[,\s]+[yY]?\s*=?\s*(-?\d+)\s*[,\s]+[zZ]?\s*=?\s*(-?\d+)")
# 相对位置：分「主人参照」与「女仆参照」两类。
# 模组只吃绝对坐标，所以这里只给语义标签，由 router 用感知里的坐标换算：
#   owner → 主人坐标（感知 owner.pos）；maid → 女仆自身（模组坐标支持 "~"）
_OWNER_WORDS = ["我脚下", "我这儿", "我这里", "我身边", "我旁边", "我这边",
                "我指的地方", "主人脚下", "到我这儿", "到我这里", "到我这",
                "过来", "来我这儿", "跟上我"]
_MAID_WORDS = ["你脚下", "你那儿", "你这里", "你身边", "你旁边", "你这边",
               "脚底下", "脚底", "这里", "这儿", "这边", "那儿",
               "那边", "前面", "后边", "左边", "右边", "上面", "下面",
               "旁边", "脚下", "身边", "鼠标指的地方", "刚才那个地方"]

# 数值类槽位（范围 / 高度 / 防御姿态）
_RANGE_RE = re.compile(r"(?:半径|范围|距离)\s*(\d+)|(\d+)\s*格(?:以内|内|范围)")
_HEIGHT_RE = re.compile(r"(\d+)\s*格高|高\s*(\d+)\s*格|(\d+)\s*格的?(?:高|墙|柱子)")
_DEFENSIVE_WORDS = ("只防守", "只反击", "别主动", "不主动", "防守反击",
                    "不要主动打", "只挡")

_CLEAN_RE = re.compile(r"[\s，。！？、,.!?；;：:\"'“”‘’()（）\[\]【】]+")


def clean(text):
    return _CLEAN_RE.sub("", text or "")


def _cn_number(s):
    """解析中文数字（支持 十六 / 三十二 / 六十四 / 一百 / 二十三）。"""
    if not s:
        return None
    if s in _STACK_WORDS:
        return _STACK_WORDS[s]
    total, section, num = 0, 0, 0
    seen = False
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
            seen = True
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            section += (num or 1) * unit
            num = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + section + num


# ---------------------------------------------------------------- 拼音

_PY = None
_PY_TRIED = False
_PY_TABLE = None
_PY_TABLE_TRIED = False


def _pinyin_mod():
    global _PY, _PY_TRIED
    if _PY_TRIED:
        return _PY
    _PY_TRIED = True
    try:
        import pypinyin
        _PY = pypinyin
    except Exception:
        _PY = None
    return _PY


def _pinyin_table():
    """离线拼音表（nlu/data/pinyin.json）；这是**首选**来源。

    用表而不是 pypinyin，是为了让「训练期」和「运行期」的拼音完全一致，
    同时桌宠运行时零额外依赖（表只有 ~30KB）。
    """
    global _PY_TABLE, _PY_TABLE_TRIED
    if _PY_TABLE_TRIED:
        return _PY_TABLE
    _PY_TABLE_TRIED = True
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "pinyin.json")
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            _PY_TABLE = json.load(f)
    except Exception:
        _PY_TABLE = {}
    return _PY_TABLE


def syllables(text):
    """转成无声调拼音音节列表。

    优先级：离线拼音表 → pypinyin（未生成表时）→ 原字本身。
    非汉字（数字/字母）保留原样并小写，这样「铁镐3」这类也能对齐。
    """
    text = clean(text)
    if not text:
        return []
    table = _pinyin_table()
    mod = None
    out = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            py = table.get(ch)
            if py is None:
                if mod is None:
                    mod = _pinyin_mod() or False
                if mod:
                    try:
                        got = mod.lazy_pinyin(ch)
                        py = got[0] if got else None
                    except Exception:
                        py = None
            out.append(py if py else ch)
        else:
            out.append(ch.lower())
    return out


def initials(text):
    """首字母串（用于「tg → 铁镐」这类缩写输入）。"""
    return "".join(s[0] for s in syllables(text) if s)


# ---------------------------------------------------------------- 匹配

@dataclass
class Match:
    name: str                # 词典里的中文名
    item_id: str             # minecraft:xxx（本地降级词典时为 None）
    score: float             # 1.0 字面 / 0.95 拼音 / 0.8 容错 / 0.7 缩写
    kind: str                # exact / pinyin / near / initial
    span: tuple = (0, 0)     # 在原文中的位置（字面命中时有意义）


class ItemIndex:
    """物品词典 + 模糊匹配器。构建时预计算拼音，查询 O(词典规模)。"""

    def __init__(self, mapping=None, extra_names=()):
        self.map = dict(mapping or {})          # name → item_id
        for n in extra_names:
            self.map.setdefault(n, None)
        self._names = sorted(self.map.keys(), key=len, reverse=True)
        self._py = {}                           # name → [syllables]
        self._init = {}
        for n in self._names:
            self._py[n] = syllables(n)
            self._init[n] = initials(n)
        self._maxname = len(self._names[0]) if self._names else 0

    # ---- 构建 ----

    @classmethod
    def from_mod(cls, items):
        """items 形如 {"铁镐": "minecraft:iron_pickaxe", ...}。"""
        return cls({k: v for k, v in (items or {}).items() if k})

    @classmethod
    def from_wiki(cls, db_path=None):
        """降级词典：只取名字（无 id），用于「认得出但执行不了」的场景。"""
        import os
        import sqlite3
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "wiki_data", "mcwiki.db")
        names = []
        if os.path.isfile(db_path):
            try:
                conn = sqlite3.connect(db_path)
                rows = conn.execute(
                    "SELECT title FROM pages WHERE category='common' "
                    "AND length(title) BETWEEN 2 AND 6")
                names = [r[0] for r in rows]
                rows = conn.execute(
                    "SELECT alias FROM redirects WHERE length(alias) BETWEEN 2 AND 6")
                names += [r[0] for r in rows]
                conn.close()
            except Exception:
                names = []
        return cls({}, extra_names=set(names))

    def __len__(self):
        return len(self.map)

    @property
    def has_ids(self):
        return any(v for v in self.map.values())

    def name_at(self, text):
        """返回作为 text 前缀的最长物品名（用于判断数量绑在哪个物品上）。"""
        t = clean(text or "")
        for n in self._names:      # 已按长度降序
            if t.startswith(n):
                return n
        return None

    # ---- 查询 ----

    def resolve(self, text, limit=8, allow_near=True):
        """返回按「分数 / 命中长度 / 位置」降序的候选（可能为空）。

        精确与拼音候选**合并**而非短路——否则「取箱子里的铁定」里精确命中的
        「箱子」会把真正要操作的「铁锭」（拼音命中）挤掉。
        """
        plain = clean(text or "")
        if not plain or not self._names:
            return []
        found = {}

        def add(m):
            old = found.get(m.name)
            if old is None or m.score > old.score:
                found[m.name] = m

        # ① 字面精确
        for n in self._names:
            i = plain.find(n)
            if i >= 0:
                add(Match(n, self.map.get(n), 1.0, "exact", (i, i + len(n))))

        # ② 拼音精确（忽略声调），与字面结果合并
        tsyl = syllables(plain)
        if tsyl:
            for n in self._names:
                if n in found:
                    continue
                ns = self._py.get(n) or []
                if len(ns) < 2:
                    continue
                k = len(ns)
                for i in range(len(tsyl) - k + 1):
                    if tsyl[i:i + k] == ns:
                        add(Match(n, self.map.get(n), 0.95, "pinyin", (i, i + k)))
                        break

        # ③ 兜底：允许一个音节增/删/换（「铁搞子」→「铁镐」）
        if not found and allow_near and tsyl:
            for n in self._names:
                ns = self._py.get(n) or []
                k = len(ns)
                if k < 2:
                    continue
                for i in range(max(0, len(tsyl) - k)):
                    if _one_edit_contained(ns, tsyl[i:i + k + 1]):
                        add(Match(n, self.map.get(n), 0.8, "near", (i, i + k + 1)))
                        break

        # ④ 兜底：首字母缩写（键盘输入「tg」→「铁镐」）
        if not found:
            tin = initials(plain)
            if 2 <= len(tin) <= 8:
                for n in self._names:
                    if (self._init.get(n) or "") and self._init[n] == tin:
                        add(Match(n, self.map.get(n), 0.7, "initial",
                                  (0, len(plain))))

        out = sorted(found.values(),
                     key=lambda m: (-m.score, -(m.span[1] - m.span[0]), -m.span[0]))
        return out[:limit]


def _one_edit_contained(needle, window):
    """needle 是否是 window 删掉一个音节后的结果（长度差 1）。"""
    if len(window) - len(needle) != 1:
        return False
    for skip in range(len(window)):
        if window[:skip] + window[skip + 1:] == needle:
            return True
    return False


# ---------------------------------------------------------------- 其它槽位

def parse_count(text):
    """返回 (数量, 原始表达)。识别「三个 / 16个 / 一组 / 半组 / 64」等。"""
    t = clean(text or "")
    if not t:
        return None, None
    # 阿拉伯数字
    m = re.search(r"(\d+)\s*个?", t)
    if m:
        return int(m.group(1)), m.group(0)
    # 一组 / 半组
    for w, v in (("半组", 32), ("两组", 128), ("一组", 64), ("一打", 12)):
        if w in t:
            return v, w
    # 中文数字 + 量词
    m = re.search(r"([零一二两三四五六七八九十百]+)\s*(把|个|件|块|根|条|张|支|组|片|颗|瓶|桶)?", t)
    if m:
        v = _cn_number(m.group(1))
        if v:
            return v, m.group(0)
    return None, None


# 量词（要求出现量词或阿拉伯数字，避免把「一起/一定」当成数量）
_MEASURE = "把个件块根条张支组片颗瓶桶打沓堆"
# 量词换算：MC 里一组 = 64、一打 = 12
_MEASURE_MULT = {"组": 64, "打": 12, "沓": 64}
_CN_NUM = "零一二两三四五六七八九十百半"
_COUNT_TOKEN_RE = re.compile(
    r"(\d+|[零一二两三四五六七八九十百半]+)\s*(?:([" + _MEASURE + r"]))?")


def iter_counts(text):
    """产出 (数量, 原始表达, 起始, 结束)。只认「数字」或「中文数词+量词」。"""
    t = clean(text or "")
    for m in _COUNT_TOKEN_RE.finditer(t):
        raw, measure = m.group(1), m.group(2)
        if not raw.isdigit() and not measure:
            continue          # 中文数词但无量词 → 多半是「一起」「一定」这类词
        if raw == "半":
            value = 0.5
        elif raw.isdigit():
            value = int(raw)
        else:
            value = _cn_number(raw)
        if not value:
            continue
        value = int(value * _MEASURE_MULT.get(measure or "", 1))
        if value <= 0:
            continue
        yield value, m.group(0), m.start(), m.end()


def choose_count(text, index, target):
    """挑出「产出数量」，跳过绑定在**材料**上的数量。

    中文常把材料数量写在一起：「用三个铁锭两根木棍做个铁镐」里
    「三个/两根」是材料的量，产出是 1。判据：数量后面紧跟着一个
    **不是目标物品**的物品名 → 该数量属于材料，跳过。
    """
    for value, expr, start, end in iter_counts(text):
        if target is not None and start <= target.span[0] < end:
            continue
        if index is not None and target is not None:
            tail = clean(text)[end:end + 6]
            bound = index.name_at(tail)
            if bound and bound != target.name:
                continue
        return value, expr
    return None, None


def parse_pos(text):
    """解析位置。

    返回 ``(值, 原始表达)``，其中「值」是：

    * ``(x, y, z)`` 绝对坐标
    * ``"owner"``   主人参照（我脚下 / 过来我这儿）
    * ``"maid"``    女仆参照（你脚下 / 这里 / 旁边）

    模组只接受绝对坐标，相对标签由 router 用感知坐标换算。
    """
    t = text or ""
    m = _POS_RE.search(t)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0)
    for w in sorted(_OWNER_WORDS, key=len, reverse=True):
        if w in t:
            return "owner", w
    for w in sorted(_MAID_WORDS, key=len, reverse=True):
        if w in t:
            return "maid", w
    return None, None


def parse_range(text):
    """解析作用范围（半径格数）。"""
    t = clean(text or "")
    m = _RANGE_RE.search(t)
    if not m:
        return None
    for g in m.groups():
        if g:
            return int(g)
    return None


def parse_height(text):
    """解析高度（建造用，格数）。"""
    t = clean(text or "")
    m = _HEIGHT_RE.search(t)
    if not m:
        return None
    for g in m.groups():
        if g:
            return int(g)
    return None


# ---- collect 半径规则（DESIGN_NLU.md §11.1 规范①） ----
# 判定顺序：出现「一片/附近/掉落物」这类**范围词** → 8；
# 否则出现「就近/单个/捡起来」这类**单目标词** → 4；
# 两者都没有（「捡东西」这种）→ 8（即模组 collect 的默认值，等价于不传 range）。
# 注意「地上 / 撒 / 散」这类**位置词不进范围词表** —— 它们不表达规模，
# 「把地上那个盾拾起来」应当是 4 而不是 8（这是第一版规则踩过的坑）。
_COLLECT_FAR = ("一片", "附近", "周围", "四周", "遍地", "一带", "统统", "一并",
                "掉落物", "都", "全部", "一堆", "扫", "归拢")
_COLLECT_NEAR = ("捡起来", "拾起来", "捡一下", "拾一下", "捡了", "拾了",
                 "这个", "那个", "这块", "那块", "这枚", "那枚", "就近",
                 "脚边", "手边", "身边", "顺手", "弯腰", "单独的")


def collect_radius(text):
    """collect 的半径：范围词 → 8（默认）；单目标词 → 4。"""
    t = clean(text or "")
    if any(w in t for w in _COLLECT_FAR):
        return 8
    return 4 if any(w in t for w in _COLLECT_NEAR) else 8


def parse_defensive(text):
    """是否要求「只防守不主动」姿态。"""
    t = clean(text or "")
    return any(w in t for w in _DEFENSIVE_WORDS)


def parse_target(text):
    """识别目标生物（最长优先）。"""
    t = clean(text or "")
    if not t:
        return None
    for m in sorted(MOBS, key=len, reverse=True):
        if m in t:
            return m
    return None


def parse_slot(text):
    """识别槽位词。"""
    t = clean(text or "")
    if not t:
        return None
    for w in sorted(SLOT_WORDS, key=len, reverse=True):
        if w in t:
            return SLOT_WORDS[w]
    return None


# ---------------------------------------------------------------- 角色感知挑选

# 容器/功能方块：在「操作物品」的意图里，它们通常是**地点**而不是宾语
# （「取箱子里的铁锭」中宾语是铁锭，不是箱子）
CONTAINER_WORDS = frozenset({
    "箱子", "大箱子", "木桶", "熔炉", "高炉", "烟熏炉", "工作台", "铁砧",
    "发射器", "投掷器", "漏斗", "潜影盒", "营火", "酿造台", "织布机",
    "切石机", "锻造台", "制图台", "讲台", "钟", "床",
})

# 「产出物」动词：宾语通常在动词**之后**，且是离动词最近的那个候选
_CRAFT_VERBS = ("合成", "制作", "制造", "做", "造", "打", "合", "弄", "制")
_SMELT_VERBS = ("烧炼", "熔炼", "烧", "熔", "炼")


def pick_target(cands, text, intent=None):
    """从候选里挑出真正的「宾语」。

    两条领域规则（可解释、可单测）：

    1. **容器词降级**：``NEED_ITEM`` 类意图里，若存在非容器候选，
       容器候选（箱子/熔炉…）被降为备选——它们是地点不是宾语。
    2. **动词邻接**：合成/烧炼类意图里，取「位于最后一个产出动词之后、
       离它最近」的候选——中文祈使句的宾语一般紧跟动词
       （「用三个铁锭两根木棍做个**铁镐**」里，宾语是铁镐而非铁锭）。
    """
    if not cands:
        return None
    pool = list(cands)
    if intent in taxonomy.NEED_ITEM:
        non_container = [c for c in pool if c.name not in CONTAINER_WORDS]
        if non_container:
            pool = non_container

    verbs = _CRAFT_VERBS if intent == "craft" else (
        _SMELT_VERBS if intent == "smelt" else ())
    if verbs:
        plain = clean(text or "")
        vpos = max((plain.rfind(v) for v in verbs), default=-1)
        if vpos >= 0:
            after = [c for c in pool if c.span[0] >= vpos]
            if after:
                # 同起点优先「更长匹配」（钻石镐 优于 钻石——产出物语义更完整），
                # 再按分数降序（同长时 exact 优先于拼音）。
                # 例：「帮我做钻石稿」→ 钻石镐(3,6) vs 钻石(3,5)，选钻石镐。
                return min(after, key=lambda c: (
                    c.span[0] - vpos,
                    -(c.span[1] - c.span[0]),
                    -c.score))
    # 无产出动词（如 drop/equip/transfer）：优先「更长的完整匹配」，再按分数，
    # 最后取更靠前的出现位置。
    # 关键修复：语音/错字「钻石搞」（搞/镐同音）时，「钻石镐」靠拼音命中(0.95)、
    # 「钻石」靠字面命中(1.0)——若只比分数会错选「钻石」；这里按匹配长度优先，
    # 正确选到「钻石镐」。
    return max(pool, key=lambda c: (
        c.span[1] - c.span[0],            # 匹配更长的完整名优先
        c.score,                          # 同长再比分数
        -c.span[0]))                      # 再取更靠前出现的


def extract(text, index=None, intent=None):
    """一站式抽槽：返回 dict（只放非空项）。"""
    out = {}
    cands = index.resolve(text) if index is not None else []
    target = pick_target(cands, text, intent) if cands else None
    if target is not None:
        out["item"] = target.name
        out["item_id"] = target.item_id
        out["item_score"] = round(target.score, 3)
        out["item_kind"] = target.kind
        alts = [c.name for c in cands if c.name != target.name]
        if alts:
            out["item_alts"] = alts
    n, expr = choose_count(text, index, target)
    if n:
        out["count"] = n
        out["count_expr"] = expr
    pos, pexpr = parse_pos(text)
    if pos is not None:
        out["pos"] = pos
        out["pos_expr"] = pexpr
    rng = parse_range(text)
    if rng:
        out["range"] = rng
    elif intent == "collect":
        # §11.1 规范①：collect 的半径由语句产出（一片/附近→8，就近/单个→4）
        out["range"] = collect_radius(text)
    hgt = parse_height(text)
    if hgt:
        out["height"] = hgt
    if intent == "guard" and parse_defensive(text):
        out["defensive"] = True
    tgt = parse_target(text)
    if tgt:
        out["target"] = tgt
        tid = MOB_IDS.get(tgt)
        if tid:
            out["target_id"] = tid
    if intent is None or intent in SLOT_INTENTS:
        slot = parse_slot(text)
        if slot:
            out["slot"] = slot
    return out
