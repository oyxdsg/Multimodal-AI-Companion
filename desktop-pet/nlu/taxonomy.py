# -*- coding: utf-8 -*-
"""意图体系（NLU 的「本体」层）。

单一事实来源：意图 id、中文展示名、我们要抽取的语义槽位、对应的女仆指令、能否自动执行。

与模组的关系
------------
意图的 ``cmd`` 必须存在于 :mod:`nlu.mod_contract`（模组指令契约）。但**语义槽位 ≠ 模组参数**：

* ``slots`` 是「我们要从这句话里抽取什么」（语义层）
* 模组实际接受的参数名/类型/取值域在 ``mod_contract.py``（协议层）
* 两者的映射在 ``nlu/router.py`` 的 ``_plan()``：语义槽位 → 模组参数，
  并负责把「模组做不到的语义」用**已有指令组合**补出来（见下）

模组能力差异（重要，决定了哪些语义要组合实现）
----------------------------------------------
============================  ============================================
语义                          实际实现
============================  ============================================
``equip`` 指定槽位（戴头盔）  模组 ``equip`` 只能到主手 → 组合
                              ``transfer(from=inv:<背包装备所在格>, to=head)``
``drop`` 指定物品              模组 ``drop`` 只丢主手 → 组合
                              ``transfer(from=inv:<格>, to=world:<女仆坐标>)``
``transfer`` 只说目标槽位      用背包快照反查该物品所在格补出 ``from``
``chestput`` / ``chesttake``   模组要求容器**已打开** → 自动前置
                              ``chestopen``（模组会在 4 格内定位最近容器）
``attack`` 指定目标类型        模组 ``attack`` 支持 ``target``（实体类型 id）→
                              直接透传（如 target=minecraft:pig 获取食物）
``place`` / ``use`` / ``build``  用**主手**物品，模组不接受物品参数
                              （``block`` / ``item`` 为 advisory）
``chesttake`` 指定物品         模组按容器内格子下标取；我们无法枚举箱内物品
                              → ``item`` 只能作提示，实际取首个非空槽
============================  ============================================

自动执行（``auto``）
-------------------
只有**明确祈使、缺参也能安全降级**的意图才置 True，具体分档见 ``AUTO_POLICY``：
``always``（指令本身够用）/ ``pos``（位置可解析就执行）/ ``abs_pos``（长任务必须给绝对坐标）
/ ``never``（闲聊）。

意图边界规则（必须与训练语料口径一致，否则模型会把相邻意图混判）
--------------------------------------------------------------
**规范已定稿**（2026-09-12 用户拍板，完整版见 ``DESIGN_NLU.md §11.1``）。
下面是本文件的执行版摘要，语料里用 ``synth.DISAMBIG`` 逐对写对抗样本：

=========================  ==========================================
break  挖掉**一个指定方块**（这扇门/这堵墙/这块）
   vs mine   在坐标**附近挖一片/挖矿**（这片/这一带/挖点矿）
build  建**一片/多格**（围一圈/建个房/搭座桥/几格高）
   vs place  **放一个**方块（放个/摆块/铺一块）
look   只是**看**（看看/看向/盯着，人不动）
   vs move   **人过去**（过去/过来/来我这儿/跟上）
equip  **要指定拿什么**（切出镐子/换成剑/拿起弓/穿上靴子）
   vs store  **收拾/收纳类动作**（收起来/归置/装回包里），哪怕提到主手也不点名物品
   vs transfer **点名了具体物品**（把铁镐换到副手）或**两样对调**（胸甲跟靴子换一下）
collect **一片/附近/掉落物** → range 8；**就近/单个/「捡起来」** → range 4
   （``pickup`` 已合并进本意图，不再单独出意图）
   vs store  收的是**身上/手上的**，不是地上的
cancel 取消**任务/命令/指令**（撤了/作废）
   vs stop   **否定当前动作**（别做了/停手）
status 问**女仆自身**（你的背包/装备/血/在干嘛）
   vs chat   闲聊
attack 主动打（干掉/清理）
   vs guard  保护我/防守
smelt  烧/熔炼           vs craft  合成/做/造
chestopen 开箱           vs move   只是过去
=========================  ==========================================

复合句的主意图约定（语料里用 ``synth.COMPOUND`` 固化）
----------------------------------------------------
1. **否定/停止优先**：「别挖了，先把周围的怪清了」→ ``stop``
2. **最终产出优先**：「把箱子里的铁锭拿出来合成铁镐」→ ``craft``
3. **附带动作弱化**：「给我挖点煤，顺便捡捡掉落的」→ ``mine``（「顺便」引导的动作不是主意图）
4. **核心目的优先**：「你过去把那个箱子开了再回来」→ ``chestopen``

> 单标签模型的固有局限：一句话只出一个意图。上面的约定把「复合句怎么判」变成
> 可训练、可回归的规则，而不是让模型自己猜。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    key: str                 # 意图 id（= 女仆指令名，除非 cmd 为 None）
    label: str               # 中文展示名
    slots: tuple = ()        # 我们尝试抽取的**语义**槽位
    cmd: str = None          # 下发的模组指令名；None = 不产生指令
    auto: bool = False       # 是否允许预处理层自动执行
    desc: str = ""
    # 指令分层（与 MaidTaskCommand javadoc 对齐）：
    #   basic      —— 基础指令（原子动作）：move/look/break/place/use/equip/store/drop/sit/stop
    #                 （模组另有 pickup 指令，NLU 已把它合并进 collect，不产出该意图）
    #   integrated —— 集成指令（任务级）：attack/guard/feed/mine/farm/build/collect/craft/smelt
    #                 + 桥接组合（transfer/chestopen/chestput/chesttake）
    #   meta       —— 元命令：cancel/status/chat（不产生任务）
    layer: str = "basic"


ALL = [
    # ---- 集成指令（任务级；与 MaidTaskCommand javadoc 集成段对齐） ----
    Intent("attack", "攻击", ("target", "range"), cmd="attack", auto=True,
           layer="integrated",
           desc="攻击；指定目标类型就打该种生物（如猪/牛/羊，用于获取食物），否则打最近敌对"),
    Intent("guard", "护卫", ("range", "defensive"), cmd="guard", auto=True,
           layer="integrated",
           desc="护卫主人；defensive=只反击"),
    Intent("feed", "喂食", ("target",), cmd="feed", auto=True,
           layer="integrated",
           desc="给主人喂食"),
    Intent("eat", "进食", ("item",), cmd="eat", auto=True,
           layer="integrated",
           desc="女仆**自己**吃东西（回饱食度/食物效果）；给了 item 就吃那个，"
                "否则吃背包里营养最高的食物（与 feed「喂主人」区分）"),
    Intent("mine", "挖矿", ("pos", "block", "range", "count"), cmd="mine", auto=True,
           layer="integrated",
           desc="在坐标附近挖矿（长任务，需要绝对坐标）"),
    Intent("farm", "耕作", ("pos", "range"), cmd="farm", auto=True,
           layer="integrated",
           desc="在坐标附近耕作（长任务，需要绝对坐标）"),
    Intent("collect", "收集", ("range",), cmd="collect", auto=True,
           layer="integrated",
           desc="收集地上的掉落物；半径按语句产出（泛指一片/附近→8，就近/单个/「捡起来」→4）。"
                "原 pickup 意图已合并进来（模组仍有 pickup 指令，但那只是 collect(range=4) 的捷径）"),
    Intent("craft", "合成", ("item", "count"), cmd="craft", auto=True,
           layer="integrated",
           desc="消耗背包材料合成（女仆自动找配方，无需工作台）"),
    Intent("smelt", "烧炼", ("item", "count"), cmd="smelt", auto=True,
           layer="integrated",
           desc="烧炼物品（如铁矿→铁锭）"),
    Intent("build", "建造", ("pos", "height", "block"), cmd="build", auto=True,
           layer="integrated",
           desc="在坐标处建造（长任务，需要绝对坐标；用主手方块）"),
    # 集成（桥接组合；MaidTaskCommand javadoc 未显式列出但模组协议实际支持）
    Intent("transfer", "转移物品", ("item", "from", "to", "count"), cmd="transfer",
           auto=True, layer="integrated",
           desc="槽位之间转移物品（from/to 必填，缺 from 时用物品反查）"),
    Intent("chestopen", "开箱子", ("pos",), cmd="chestopen", auto=True,
           layer="integrated",
           desc="打开容器；没给坐标就用女仆所在位置，模组会自动找最近容器"),
    Intent("chestput", "存箱子", ("item", "count"), cmd="chestput", auto=True,
           layer="integrated",
           desc="把主手物品存进箱子（自动前置 chestopen）"),
    Intent("chesttake", "取箱子", ("item", "slot", "count"), cmd="chesttake",
           auto=True, layer="integrated",
           desc="从箱子取出物品（自动前置 chestopen；按格子取）"),

    # ---- 基础指令（原子动作；与 MaidTaskCommand javadoc 基础段对齐） ----
    Intent("move", "移动", ("pos", "target"), cmd="move", auto=True,
           layer="basic",
           desc="移动到坐标/目标处（需要坐标或目标）"),
    Intent("look", "张望", ("pos", "target"), cmd="look", auto=True,
           layer="basic",
           desc="看向坐标/目标（需要坐标或目标）"),
    Intent("break", "挖除方块", ("pos", "block"), cmd="break", auto=True,
           layer="basic",
           desc="挖掉坐标处的方块（需要坐标）"),
    Intent("place", "放置", ("pos", "block"), cmd="place", auto=True,
           layer="basic",
           desc="在坐标处放置方块（需要坐标；用主手方块）"),
    Intent("use", "使用物品", ("pos", "item"), cmd="use", auto=True,
           layer="basic",
           desc="对坐标使用主手物品（需要坐标）"),
    Intent("equip", "装备", ("item", "slot"), cmd="equip", auto=True,
           layer="basic",
           desc="装备物品；给了槽位就穿戴到该槽位（组合 transfer 实现）"),
    Intent("store", "收纳", (), cmd="store", auto=True,
           layer="basic",
           desc="把主手物品收进背包"),
    Intent("drop", "丢出", ("item", "count"), cmd="drop", auto=True,
           layer="basic",
           desc="丢出物品；指定了物品就丢那个（组合 transfer 实现），否则丢主手"),
    Intent("sit", "坐/站切换", (), cmd="sit", auto=True,
           layer="basic",
           desc="切换坐姿与站姿（是 toggle，不是「坐下」）"),
    Intent("stop", "停下", (), cmd="stop", auto=True,
           layer="basic",
           desc="停止导航并取消当前任务"),

    # ---- 元命令（管理；不产生任务） ----
    Intent("status", "查状态", (), cmd="status", auto=True,
           layer="meta",
           desc="报告女仆当前任务/忙碌状态"),
    Intent("cancel", "取消任务", (), cmd="cancel", auto=True,
           layer="meta",
           desc="只取消当前任务（不停导航，与 stop 有区别）"),
    Intent("chat", "闲聊", (), cmd=None, auto=False,
           layer="meta",
           desc="与女仆聊天、提问、吐槽——不指挥她干活，交给大 AI"),
]

BY_KEY = {it.key: it for it in ALL}
KEYS = [it.key for it in ALL]
LABELS = {it.key: it.label for it in ALL}

# 有物品**宾语**的意图（用于槽位抽取时的「容器词降级」判断）
NEED_ITEM = frozenset({"craft", "smelt", "equip", "drop", "eat",
                       "transfer", "chestput", "chesttake", "use"})
# 硬性要求物品 id 才能执行的意图
ITEM_REQUIRED = frozenset({"craft", "smelt", "equip"})
# 硬性要求坐标的意图（chestopen 缺坐标时可用女仆位置兜底，故不在此列）
NEED_POS = frozenset({"mine", "farm", "build", "place", "break", "use",
                      "move", "look"})
# 有副作用且识别置信度足够时可以自动执行
AUTO_OK = frozenset(it.key for it in ALL if it.auto)

# 自动执行策略（比 AUTO_OK 更细，决定「参数够不够就能动手」）：
#   always  —— 指令本身够用，高置信即执行
#   pos     —— 需要位置；位置可解析（绝对坐标，或相对词换算成主人/女仆坐标）就执行
#   abs_pos —— 需要**绝对坐标**（长任务：挖矿/耕作/建造），只说「我脚下」这类不执行
#   never   —— 永不自动执行
AUTO_POLICY = {
    # mine 无 pos 时可自动探测周围矿物（女仆脚下），故只需 pos 可解析或可兜底
    "mine": "pos", "farm": "abs_pos", "build": "abs_pos",
    "place": "pos", "break": "pos", "use": "pos",
    "move": "pos", "look": "pos",
    "chat": "never",
}


def auto_policy(key):
    return AUTO_POLICY.get(key, "always" if is_auto_ok(key) else "never")


def get(key):
    return BY_KEY.get(key)


def is_auto_ok(key):
    return key in AUTO_OK


def command_intents():
    """所有会产生模组指令的意图 key。"""
    return [it.key for it in ALL if it.cmd]


# ---- 分层（与 MaidTaskCommand javadoc 对齐） ----
BASIC = frozenset(it.key for it in ALL if it.layer == "basic")
INTEGRATED = frozenset(it.key for it in ALL if it.layer == "integrated")
META = frozenset(it.key for it in ALL if it.layer == "meta")


def layer_of(key):
    """意图的指令分层：basic / integrated / meta。"""
    it = BY_KEY.get(key)
    return it.layer if it else None


def is_basic(key):
    return key in BASIC


def is_integrated(key):
    return key in INTEGRATED
