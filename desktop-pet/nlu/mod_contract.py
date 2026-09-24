# -*- coding: utf-8 -*-
"""模组指令契约（SmartMaid 指令层的**单一事实来源**）。

来源：``SmartMaid/src/main/java/.../bridge/MaidAIBridge.java``（指令 → 参数）
与 ``command/MaidTaskCommand.java``（``/maidtasks`` 参数范围）。
对齐基线：SmartMaid ``275bc59``（2026-09-17 女仆战斗系统 C0-C5）。

为什么单独一个模块
------------------
桌宠侧的「训练（意图集 / 语料）」与「执行（下发参数）」都依赖模组指令层。
把参数名 / 类型 / 默认值 / 取值域 / 前置条件集中放在这里，做到：

1. **训练与模组接轨**：意图集、语料里的说法都能映射到真实指令；
2. **下发零漂移**：:func:`clamp` 会自动丢弃模组不认识的参数、把数值夹到合法区间——
   以前「把 ``slot`` 塞给 ``equip``」「发 ``transfer`` 却不给 ``from``」这类对齐错误
   会直接导致真机失败，现在变成结构性不可能；
3. **可回归**：``tests/test_nlu.py`` 用契约校验 taxonomy / router，模组改了就红。

槽位表达式（``transfer`` 的 ``from`` / ``to``，见模组 ``Slots.parse``）
------------------------------------------------------------------
* ``mainhand`` / ``offhand`` / ``head`` / ``chest`` / ``legs`` / ``feet``
* ``inv:<0-40>``（``inv5`` / ``5`` 也宽容接受）；0-8 热键 / 9-35 背包 / 36-39 盔甲 / 40 副手
* ``world:<x>,<y>,<z>`` —— **只能作目标**（放方块 / 丢出）
* ``container:<x>,<y>,<z>[:<slot>|auto]``

契约有 / 意图无的指令
--------------------
* ``pickup`` —— 模组基础指令，NLU 已按 ``DESIGN_NLU.md §11.1`` 规范把它合并进
  ``collect(range=4)``，不再产出 pickup 意图；
* ``craft_check`` —— 桌宠侧干跑查询（不执行、不改世界）。

这两条留在契约里，是为了 **DSL 白名单校验**、**AI 直接下发**，以及让契约与模组指令集
一一对应。``tests/test_nlu.py::test_layer_alignment`` 对它们走豁免名单。
"""

from dataclasses import dataclass, field

# 契约锚点：核对时 SmartMaid 的提交（改了模组指令层就更新这里并重跑测试）
MOD_COMMIT = "275bc59"


@dataclass(frozen=True)
class Param:
    type: str = "int"          # int | bool | item | pos | slot | blocks | entity
    required: bool = False
    default: object = None
    lo: int = None             # 数值下限（含）
    hi: int = None             # 数值上限（含）
    desc: str = ""


@dataclass(frozen=True)
class Command:
    name: str
    params: dict = field(default_factory=dict)
    # 执行前必须已满足的状态；provides 是执行后会建立的状态
    requires: tuple = ()
    provides: tuple = ()
    # 模组**不接受**、只能作为语义提示的槽位（如 place 的 block）
    advisory: tuple = ()
    desc: str = ""
    # 指令分层（与 MaidTaskCommand javadoc 对齐）：
    #   basic      —— 基础指令（原子动作）：move/look/break/place/use/equip/store/drop/pickup/sit/stop
    #                 （``pickup`` 是**契约有 / 意图无**：模组指令仍在，NLU 已合并进 collect）
    #   integrated —— 集成指令（任务级）：attack/guard/feed/mine/farm/build/collect/craft/smelt
    #                 + 桥接组合（transfer/chestopen/chestput/chesttake）
    #   meta       —— 元命令：cancel/status
    #   meta-dryrun—— 桌宠侧查询（不执行）：craft_check
    layer: str = "basic"
    # True = params 原样透传（不做逐参 clamp），由模组侧严格解析。仅 script 用
    raw_params: bool = False


INT = lambda **kw: Param(type="int", **kw)          # noqa: E731
POS = lambda **kw: Param(type="pos", required=True, **kw)   # noqa: E731
ITEM = lambda **kw: Param(type="item", required=True, **kw)  # noqa: E731

COMMANDS = {
    # ---------- 元命令（管理；不产生任务） ----------
    "cancel": Command("cancel", desc="取消当前任务（不动导航）", layer="meta"),
    "status": Command("status", desc="查询当前任务与忙碌状态", layer="meta"),
    "craft_check": Command("craft_check", {
        "item": ITEM(desc="目标物品 id"),
        "count": INT(default=1, lo=1, hi=64),
    }, desc="干跑查询配方与材料（不消耗材料，桌宠侧新增）", layer="meta-dryrun"),

    # ---------- 集成指令（任务级） ----------
    "attack": Command("attack", {
        "range": INT(default=12, lo=1, hi=64),
        "target": Param(type="entity",
                        desc="实体类型 id（如 minecraft:pig）；不填=最近敌对生物"),
    }, layer="integrated",
        desc="攻击：不填 target 打附近最近敌对生物；填 target 打最近的该种生物"
             "（如 target=minecraft:pig 获取食物）"),
    "guard": Command("guard", {
        "range": INT(default=16, lo=1, hi=64),
        "defensive": Param(type="bool", default=False,
                           desc="只反击不主动出击（仅 JSON 支持，/maidtasks 未暴露）"),
    }, layer="integrated", desc="护卫主人"),
    "feed": Command("feed", desc="给主人喂食", layer="integrated"),
    "eat": Command("eat", {
        "item": Param(type="item", desc="要吃的物品 id；不填=吃背包里营养最高的食物"),
    }, layer="integrated",
        desc="女仆**自己**进食（回饱食度 / 触发食物效果；不填 item 就吃营养最高的食物）"),
    "mine": Command("mine", {
        "pos": Param(type="pos", desc="挖掘中心坐标（可选）；不给则女仆以自己脚下为中心自动探测周围矿物"),
        "range": INT(default=12, lo=1, hi=24),
        "count": INT(default=8, lo=1, hi=256),
    }, layer="integrated", desc="挖矿：给 pos 按坐标挖，不给就自动探测周围矿物"),
    "farm": Command("farm", {
        "pos": POS(),
        "range": INT(default=4, lo=1, hi=16),
    }, layer="integrated", desc="在坐标附近耕作"),
    "collect": Command("collect", {
        "range": INT(default=8, lo=1, hi=32),
    }, advisory=("target",), layer="integrated",
        desc="收集地上掉落物；range 由语句产出（泛指一片/附近→8，就近/单个→4）"),
    "craft": Command("craft", {
        "item": ITEM(desc="目标物品 id"),
        "count": INT(default=1, lo=1, hi=64),
    }, provides=("craft_result",), layer="integrated",
        desc="消耗背包材料合成（自动找配方，无需工作台）"),
    "smelt": Command("smelt", {
        "item": ITEM(),
        "count": INT(default=1, lo=1, hi=64),
    }, layer="integrated", desc="烧炼物品"),
    "build": Command("build", {
        "pos": POS(),
        "height": INT(default=1, lo=1, hi=64, desc="自 pos 向上堆叠的格数"),
        "blocks": Param(type="blocks",
                        desc="坐标数组 [[x,y,z],...]；给了它就忽略 height"),
    }, advisory=("block",), layer="integrated", desc="建造（用主手方块；block 仅作语义提示）"),
    # 集成（桥接组合；MaidTaskCommand javadoc 未显式列出但模组协议实际支持）
    "transfer": Command("transfer", {
        "from": Param(type="slot", required=True),
        "to": Param(type="slot", required=True),
        "count": INT(default=64, lo=1, hi=64),
    }, layer="integrated",
        desc="槽位之间转移物品（from/to 必填，见模块文档的槽位表达式）"),
    "chestopen": Command("chestopen", {
        "pos": POS(desc="提示位置；模组会在 4 格内自动定位最近的容器"),
    }, provides=("opened_container",), layer="integrated",
        desc="走到容器前并打开（记录当前打开的箱子）"),
    "chestput": Command("chestput", {
        "count": INT(default=64, lo=1, hi=64),
    }, requires=("opened_container",), layer="integrated",
        desc="把主手物品存进**已打开**的容器"),
    "chesttake": Command("chesttake", {
        "slot": INT(default=-1, lo=0, hi=255, desc="-1 = auto 取首个非空槽"),
        "count": INT(default=1, lo=1, hi=64),
    }, requires=("opened_container",), advisory=("item",), layer="integrated",
        desc="从**已打开**的容器取出物品（slot 是容器内格子下标，不是物品）"),

    # ---------- 基础指令（原子动作） ----------
    # 契约有 / 意图无：模组指令仍合法（/maidtasks pickup、AI DSL 都能用），
    # 但 NLU 已按 §11.1 规范把它合并进 collect(range=4)，不再产出 pickup 意图。
    "pickup": Command("pickup", {
        "range": INT(default=4, lo=1, hi=32),
    }, layer="basic",
        desc="拾取附近物品（与 collect 同实现、半径更小；NLU 已合并进 collect，此条仅供 DSL/契约校验）"),
    "move": Command("move", {
        "pos": POS(),
    }, advisory=("target",), layer="basic", desc="移动到坐标/目标身边"),
    "look": Command("look", {
        "pos": POS(),
    }, advisory=("target",), layer="basic", desc="看向坐标位置"),
    "break": Command("break", {
        "pos": POS(),
    }, advisory=("block",), layer="basic", desc="挖掉 pos 处的方块"),
    "place": Command("place", {
        "pos": POS(),
    }, advisory=("block",), layer="basic",
        desc="在 pos 放置方块（用**主手**方块；模组不接受 block 参数）"),
    "use": Command("use", {
        "pos": POS(),
    }, advisory=("item",), layer="basic",
        desc="对 pos 使用**主手**物品（模组不接受 item 参数）"),
    "equip": Command("equip", {
        "item": ITEM(desc="要装备的物品 id"),
    }, layer="basic",
        desc="把物品装备到**主手**（模组不接受目标槽位；要穿戴到其它槽位用 transfer）"),
    "store": Command("store", desc="把主手物品收进背包", layer="basic"),
    "drop": Command("drop", {
        "count": INT(default=1, lo=1, hi=64),
    }, advisory=("item",), layer="basic",
        desc="丢出**主手**物品（模组不接受 item 参数）"),
    "sit": Command("sit", desc="**切换**坐/站（不是单纯坐下）", layer="basic"),
    "stop": Command("stop", desc="停止导航并取消当前任务", layer="basic"),
}

# 会建立 opened_container 状态、可作前置的指令
PROVIDES_OPENED_CONTAINER = ("chestopen",)

# ---------------------------------------------------------------- 脚本指令集
# Atomic Command Protocol（SmartMaid DESIGN_ATOMIC_PROTOCOL.md）：
# AI 一次性规划的多步指令流 script，其内部可用的指令集合。
# **不进 COMMANDS**（不与 NLU 意图对账），由 script 校验单独使用。

# script 内部可用的普通指令 = 契约全部层（basic/integrated/meta）+ 查询类
_SCRIPT_COMMON = {n for n, c in COMMANDS.items()} | {"harvest"}

# 查询类指令（无副作用，返回结构化 result，仅供 script 内部）
SCRIPT_QUERY = {
    "inventory": Command("inventory", {
        "item": Param(type="item", desc="物品 id/#tag；缺省=全列非空槽"),
    }, desc="查询背包（has/count/slot/items[]）"),
    "block_at": Command("block_at", {
        "pos": POS(desc="目标方块坐标"),
    }, desc="查询方块（block/hardness/breakable/tool_required）"),
    "find": Command("find", {
        "structure": Param(type="str", default="tree", desc="结构类型：tree（树）等"),
        "produce": Param(type="str", desc="目标产物 id/#tag/wood家族（如 #minecraft:oak_logs）"),
        "range": INT(default=12, lo=1, hi=32),
        "max_range": INT(default=32, lo=1, hi=64),
        "expand": INT(default=4, lo=1, hi=32),
    }, desc="结构查询：按产物过滤，范围递增(12→…→32)，返回 found/pos/remaining"),
    "find_entity": Command("find_entity", {
        "type": Param(type="entity", desc="实体类型 id"),
        "range": INT(default=16, lo=1, hi=64),
    }, desc="查最近匹配实体（found/uuid/type/pos/dist）"),
    "find_item": Command("find_item", {
        "item": Param(type="item", desc="物品 id/#tag；缺省=任意掉落物"),
        "range": INT(default=8, lo=1, hi=32),
    }, desc="查最近掉落物（found/pos/count）"),
    "distance": Command("distance", {
        "pos": POS(),
    }, desc="到坐标的水平距离（dist）"),
    "harvest": Command("harvest", {
        "pos": POS(desc="结构位置（find 的 pos）"),
        "produce": Param(type="str", desc="只收割命中产物（如 #minecraft:oak_logs）"),
        "count": INT(default=0, lo=0, hi=64, desc="目标数量；0=直到产物耗尽"),
    }, desc="按产物过滤收割结构（只挖命中方块）"),
}

# script 指令本身（params 是 {steps, vars, fail, max_steps, name}，原样透传）
SCRIPT_COMMANDS = {
    "script": Command("script", {}, raw_params=True,
                      desc="AI 一次性规划的多步指令流（steps 数组 + 变量 + if/loop/terminate）"),
    **SCRIPT_QUERY,
}

# 脚本内允许的普通指令（契约全部层）∪ 查询类 ∪ script 本身
SCRIPT_ALLOWED = _SCRIPT_COMMON | set(SCRIPT_QUERY) | {"script"}


def script_clamp(cmd, params):
    """脚本内部指令的参数清洗：查询/采集类按各自契约 clamp；script 原样透传。"""
    c = SCRIPT_COMMANDS.get(cmd) or COMMANDS.get(cmd)
    if c is None:
        return (params or {}), sorted((params or {}).keys())
    if c.raw_params:
        return (params or {}), []
    return _clamp_with_spec(c, params)


def validate_script(script_params):
    """校验 script params 结构：steps 数组 + 每步指令/参数合法（含 if/loop 递归）。

    :return: ``(清洗后的 script_params | None, 错误信息 | None)``
    """
    if not isinstance(script_params, dict):
        return None, "script params 必须是对象"
    raw_steps = script_params.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, "script 缺少非空 steps"
    steps, err = _validate_steps(raw_steps)
    if err:
        return None, err
    out = dict(script_params)
    out["steps"] = steps
    return out, None


def _validate_steps(steps):
    cleaned = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return None, "steps[%d] 必须是对象" % i
        if "if" in step:
            step = dict(step)
            for key in ("then", "else"):
                if key in step and isinstance(step[key], list):
                    sub, err = _validate_steps(step[key])
                    if err:
                        return None, err
                    step[key] = sub
            cleaned.append(step)
            continue
        if "loop" in step and isinstance(step["loop"], dict):
            loop = dict(step["loop"])
            if isinstance(loop.get("body"), list):
                sub, err = _validate_steps(loop["body"])
                if err:
                    return None, err
                loop["body"] = sub
            step = dict(step)
            step["loop"] = loop
            cleaned.append(step)
            continue
        if "assign" in step or "terminate" in step:
            cleaned.append(step)
            continue
        cmd = step.get("cmd")
        if not cmd or cmd not in SCRIPT_ALLOWED:
            return None, "steps[%d] 未知指令: %s" % (i, cmd)
        sp = step.get("params") or {}
        sp, _dropped = script_clamp(cmd, sp)
        step = dict(step)
        step["params"] = sp
        cleaned.append(step)
    return cleaned, None

# 分层集合（与 MaidTaskCommand javadoc 对齐）
LAYER_BASIC = frozenset(n for n, c in COMMANDS.items() if c.layer == "basic")
LAYER_INTEGRATED = frozenset(n for n, c in COMMANDS.items() if c.layer == "integrated")
LAYER_META = frozenset(n for n, c in COMMANDS.items() if c.layer == "meta")
LAYER_META_DRYRUN = frozenset(n for n, c in COMMANDS.items() if c.layer == "meta-dryrun")


def layer_of(cmd):
    """指令的分层：basic / integrated / meta / meta-dryrun。"""
    c = COMMANDS.get(cmd)
    return c.layer if c else None


def get(cmd):
    return COMMANDS.get(cmd)


def param_names(cmd):
    c = COMMANDS.get(cmd)
    return set(c.params) if c else set()


def accepts(cmd, name):
    return name in param_names(cmd)


def clamp(cmd, params):
    """把参数裁剪成模组合法的形态。

    * 丢弃模组未声明的参数（避免对齐漂移导致的静默失败/报错）
    * 数值按 ``lo`` / ``hi`` 夹紧，非整数丢弃
    * 缺省值补全（``required`` 的参数**不补**，缺了就是缺了，调用方负责拦）

    :return: (干净参数, 被丢弃的参数名列表)
    """
    c = COMMANDS.get(cmd)
    if c is None:
        return {}, sorted((params or {}).keys())
    if c.raw_params:
        return (params or {}), []
    return _clamp_with_spec(c, params)


def _clamp_with_spec(c, params):
    """按单个指令契约裁剪参数（clamp / script_clamp 共用）。"""
    out, dropped = {}, []
    for key, val in (params or {}).items():
        spec = c.params.get(key)
        if spec is None or val is None:
            dropped.append(key)
            continue
        if spec.type == "int":
            # 变量/算术表达式（script 内部，如 "$target_count - $collected"）
            # 原样透传，由模组 ScriptRef 在执行时解析——不能 int() 硬转丢弃
            if isinstance(val, str) and ("$" in val or any(op in val for op in "+-")):
                out[key] = val
                continue
            try:
                v = int(val)
            except (TypeError, ValueError):
                dropped.append(key)
                continue
            if spec.lo is not None:
                v = max(spec.lo, v)
            if spec.hi is not None:
                v = min(spec.hi, v)
            out[key] = v
            continue
        if spec.type == "bool":
            out[key] = bool(val)
            continue
        # item / pos / slot / blocks / str / entity：原样透传（pos 的相对坐标转换见 router）
        out[key] = val
    return out, dropped


def missing_required(cmd, params):
    c = COMMANDS.get(cmd)
    if c is None:
        return ["> 未知指令: %s" % cmd]
    have = params or {}
    return [k for k, spec in c.params.items()
            if spec.required and not have.get(k)]


def unmet_requires(cmd, available):
    """``available`` 是当前已满足的运行时状态集合（如 {"opened_container"}）。"""
    c = COMMANDS.get(cmd)
    if c is None:
        return ["> 未知指令: %s" % cmd]
    return [r for r in c.requires if r not in (available or ())]


def as_dict():
    """导出为纯数据（供测试/文档/前端使用）。"""
    out = {}
    for name, c in COMMANDS.items():
        out[name] = {
            "params": {k: {"type": v.type, "required": v.required,
                           "default": v.default, "lo": v.lo, "hi": v.hi}
                       for k, v in c.params.items()},
            "requires": list(c.requires),
            "provides": list(c.provides),
            "advisory": list(c.advisory),
            "layer": c.layer,
            "desc": c.desc,
        }
    return out


# ---------------------------------------------------------------- 工具 schema 导出（P1-1）

def _param_schema(p):
    """单个参数 → JSON Schema（OpenAI / Anthropic 共用；int 带 min/max）。"""
    if p.type == "int":
        s = {"type": "integer"}
        if p.lo is not None:
            s["minimum"] = p.lo
        if p.hi is not None:
            s["maximum"] = p.hi
    elif p.type == "bool":
        s = {"type": "boolean"}
    elif p.type == "pos":
        s = {"type": "string",
             "description": "坐标 [x,y,z]；x/y/z 可为整数或 ~（相对坐标）"}
    elif p.type == "slot":
        s = {"type": "string",
             "description": "槽位：mainhand / offhand / head / chest / legs / feet "
                            "/ inv:<0-40> / world:<x,y,z> / container:<x,y,z>[:槽位|auto]"}
    elif p.type == "blocks":
        s = {"type": "string", "description": "坐标数组 [[x,y,z], ...]"}
    else:  # item / entity / str
        s = {"type": "string"}
    desc = (s.get("description") or "") + p.desc
    if p.default is not None:
        desc = (desc + "；默认 %s" % p.default).strip("；")
    if desc:
        s["description"] = desc
    return s


def _tools_common():
    """(name, desc, properties, required) 迭代；排除 meta-dryrun（桌宠内部查询不下发）。"""
    for name, c in COMMANDS.items():
        if c.layer == "meta-dryrun":
            continue
        props = {}
        required = []
        for pname, p in c.params.items():
            props[pname] = _param_schema(p)
            if p.required:
                required.append(pname)
        yield name, c.desc, props, required


def to_openai_tools():
    """导出为 OpenAI `function tools` 列表（P1-1，原生 tool_calls 的 schema 源）。

    与 `clamp` / `validate_script` 共用同一份 `COMMANDS` 契约，**不手写第二份事实**；
    模型生成的参数仍会经 `clamp` 夹紧/丢弃，这里只负责让模型知道「能调什么、参数长啥样」。
    """
    tools = []
    for name, desc, props, required in _tools_common():
        parameters = {"type": "object", "properties": props}
        if required:
            parameters["required"] = required
        tools.append({
            "type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": parameters},
        })
    return tools


def to_anthropic_tools():
    """导出为 Anthropic `tools` 列表（`/v1/messages` 的 tool 形态）。"""
    tools = []
    for name, desc, props, required in _tools_common():
        input_schema = {"type": "object", "properties": props}
        if required:
            input_schema["required"] = required
        tools.append({"name": name, "description": desc,
                      "input_schema": input_schema})
    return tools
