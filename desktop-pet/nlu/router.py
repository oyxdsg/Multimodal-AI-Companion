# -*- coding: utf-8 -*-
"""意图路由与执行编排：把「意图 + 语义槽位」按**模组指令契约**展开成可执行步骤。

数据流
------
::

    用户原话 ──► 意图模型 ──► 槽位抽取 ──► _plan()：语义 → 模组指令序列
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
                 能生成合法指令 → 逐步执行                  缺参/模组做不到 → 只提示
                        │                                           │
               （craft 走 dry-run 前置：查配方查背包）              （不产生任何动作）
                        ▼                                           ▼
              ┌────────────── 结构化「预处理结果」 ──────────────┐
              │  用户原话 + 意图/置信度 + 槽位 + 执行步骤         │
              │  + 是否已执行 + 配方/背包明细（材料不够时）        │
              └───────────────────────┬──────────────────────────┘
                                      ▼
                                  注入大 AI 上下文

三条「语义 → 指令」的组合规则（模组做不到，用已有指令补出来）
------------------------------------------------------------
1. **chestput / chesttake**：模组要求容器已打开 → 自动前置 ``chestopen``，
   并**等它真正跑完**（模组是异步任务，回执只说「已受理」）
2. **equip 指定槽位 / drop 指定物品**：模组这两个指令只作用于主手 →
   组合 ``transfer``（用背包快照反查物品所在格拼出 ``inv:<n>``）
3. **transfer 只说目标**：用背包快照反查 ``from``

Bridge 协议（鸭子类型，生产环境由 ``pet/handlers/nlu_bridge.py`` 提供）
------------------------------------------------------------------
``connected`` / ``item_index()`` / ``inventory()`` / ``inventory_slots()``
``maid_pos()`` / ``owner_pos()`` / ``query_craft()`` / ``query_status()``
``wait_task_done()`` / ``send_command()``
"""

import time
from dataclasses import dataclass, field

from nlu import intent as intent_mod
from nlu import mod_contract
from nlu import slots as slots_mod
from nlu import taxonomy

# 会产生模组指令的意图
_GAME_INTENTS = frozenset(it.key for it in taxonomy.ALL if it.cmd)
# 建立「容器已打开」状态的指令
_OPENER = "chestopen"
# 依赖容器已打开的意图
_NEEDS_OPENER = ("chestput", "chesttake")

# 执行步骤的中文名（注入文本里给大 AI 看）
_STEP_LABEL = {
    "chestopen": "打开箱子", "transfer": "转移物品", "equip": "装备到主手",
}


@dataclass
class PreprocessResult:
    text: str = ""
    intent: str = ""
    label: str = ""
    confidence: float = 0.0
    mode: str = "none"            # auto / hint / none
    slots: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)   # [{"cmd","params"}]
    executed: bool = False        # 是否真的下发了指令并成功
    exec_ok: bool = False
    exec_detail: str = ""         # 面向用户的一句话
    summary: str = ""             # 注入大 AI 的结构化文本

    @property
    def handled(self):
        return self.mode == "auto" and self.executed

    @property
    def useful(self):
        return self.mode in ("auto", "hint") and bool(self.summary)


class NluRouter:
    """无状态（除 bridge 引用）的路由器，可被多线程复用。"""

    def __init__(self, bridge, engine=None, threshold=None):
        self.bridge = bridge
        self._engine = engine
        self.threshold = threshold

    # ---------------- 基础 ----------------

    @property
    def engine(self):
        if self._engine is None:
            self._engine = intent_mod.shared()
        return self._engine

    def _connected(self):
        try:
            return bool(self.bridge.connected)
        except Exception:
            return False

    def item_index(self):
        """带缓存的物品词典：模组优先，缺省降级 wiki 词条。"""
        bridge = self.bridge
        items = {}
        try:
            items = bridge.item_index() or {}
        except Exception:
            items = {}
        if items:
            if getattr(self, "_idx_src", None) != ("mod", len(items)):
                self._idx = slots_mod.ItemIndex.from_mod(items)
                self._idx_src = ("mod", len(items))
                self._idx_rev = {v: k for k, v in items.items() if v}
            return self._idx
        if getattr(self, "_idx_src", None) != ("wiki",):
            self._idx = slots_mod.ItemIndex.from_wiki()
            self._idx_src = ("wiki",)
            self._idx_rev = {}
        return self._idx

    def name_of(self, item_id):
        """物品 id → 中文名（反向查）；词典里没有时退化为去掉命名空间的 id。"""
        self.item_index()
        name = (getattr(self, "_idx_rev", {}) or {}).get(item_id)
        return name or self._suffix(item_id)

    def _suffix(self, item_id):
        return (item_id or "").split(":")[-1]

    # ---------------- 感知数据 ----------------

    def _backpack_slot_of(self, item_id):
        """反查物品在背包里的槽位号（优先热键+背包 0-35）。找不到返回 None。"""
        if not item_id:
            return None
        try:
            slots = self.bridge.inventory_slots() or []
        except Exception:
            slots = []
        best = None
        for s in slots:
            if s.get("id") != item_id:
                continue
            idx = int(s.get("i", -1))
            if idx < 0:
                continue
            if idx <= 35:
                return idx          # 热键/背包优先
            if best is None:
                best = idx
        return best

    def _inventory_ready(self):
        """女仆背包感知是否就绪（有槽位数据）。WS 刚连 / 感知未到首帧时为 False。"""
        try:
            return bool(self.bridge.inventory_slots())
        except Exception:
            return False

    def _slots_snapshot(self):
        """诊断用：当前背包槽位快照 [(id, i), ...]。"""
        try:
            return [(s.get("id"), s.get("i"))
                    for s in (self.bridge.inventory_slots() or [])]
        except Exception:
            return []

    def _dbg(self, *parts):
        """诊断日志（stderr，pet_console.err.log）。"""
        try:
            import sys
            print("[NLU-DIAG] " + " ".join(str(p) for p in parts),
                  file=sys.stderr, flush=True)
        except Exception:
            pass

    def _maid_pos(self):
        try:
            return self.bridge.maid_pos()
        except Exception:
            return None

    def _owner_pos(self):
        try:
            return self.bridge.owner_pos()
        except Exception:
            return None

    def _pos_param(self, res, allow_maid_fallback=False):
        """语义位置 → 模组坐标参数。返回 ``(pos, 失败原因)``。

        模组只吃**绝对坐标**（或 ``"~"`` 相对女仆）。所以相对词要在这里换算：
        ``owner`` → 感知里的主人坐标；``maid`` → ``"~"``。
        """
        pos = res.slots.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            return [int(v) for v in pos], ""
        if pos == "owner":
            op = self._owner_pos()
            if op is not None:
                return [int(v) for v in op], ""
            return None, "拿不到你的坐标（模组未上报主人位置）"
        if pos == "maid":
            return ["~", "~", "~"], ""
        if pos is None and allow_maid_fallback:
            # 用户没给位置 → 让女仆在**自己脚下**找（chestopen 会在 4 格内定位容器）
            return ["~", "~", "~"], ""
        return None, "没听出坐标位置"

    # ---------------- 主入口 ----------------

    def preprocess(self, text):
        """对一段用户输入做预处理。永不抛异常。"""
        try:
            return self._preprocess(text)
        except Exception as exc:  # 预处理层绝不能拖垮聊天
            return PreprocessResult(text=text or "", mode="none",
                                    exec_detail="预处理异常（已忽略）: %s" % exc)

    def _preprocess(self, text):
        text = (text or "").strip()
        res = PreprocessResult(text=text)
        if not text:
            return res

        pred, mode = self.engine.decide(text) if self.engine else (None, "none")
        if pred is None or mode == "none":
            return res

        res.intent = pred.intent
        res.label = pred.label
        res.confidence = round(pred.confidence, 4)
        res.mode = mode

        res.slots = slots_mod.extract(text, index=self.item_index(),
                                     intent=pred.intent)

        if pred.intent == "chat":
            res.mode = "none"
            return res

        if mode == "hint":
            res.summary = self._hint_summary(res, pred)
            return res

        if pred.intent in _GAME_INTENTS and not self._connected():
            return self._blocked(res, pred, "女仆未连接，无法执行（需要先启动游戏并让女仆上线）")

        if res.intent == "craft":
            self._run_craft(res)
        else:
            steps, reason = self._plan(res)
            if reason:
                return self._blocked(res, pred, reason)
            res.steps = [{"cmd": c, "params": p} for c, p in steps]
            self._execute_steps(steps, res)
            if res.mode != "auto":
                res.summary = self._generic_summary(res)

        res.mode = "auto" if res.executed else "hint"
        if res.mode == "auto":
            res.summary = res.summary or self._generic_summary(res)
        return res

    def _blocked(self, res, pred, reason):
        """不能执行：降级为「只提示」，但把已知信息带给大 AI。"""
        res.mode = "hint"
        res.exec_detail = reason
        res.summary = self._hint_summary(res, pred, reason)
        return res

    # ---------------- 计划：语义 → 模组指令 ----------------
    #
    # 按指令分层显式派发（与 MaidTaskCommand javadoc 一致）：
    #   basic      —— 单原子动作（move/look/break/place/use/equip/store/drop/sit/stop）
    #                 equip/drop 指定槽位/物品时**组合 transfer**（属于 basic 的增强）
    #   integrated —— 任务级（attack/guard/feed/mine/farm/build/collect/craft/smelt
    #                 + 桥接 transfer/chestopen/chestput/chesttake）
    #                 容器类自动前置 chestopen；transfer 只说目标时反查 from
    #   meta       —— cancel/status（无参数）
    #
    # 集成失败**不回退**到基础原子直接调，降级为 hint 告诉大 AI 缺什么。

    def _plan(self, res):
        """返回 ``(steps, 不能执行的原因)``；``steps`` 为 ``[(cmd, params)]``。"""
        layer = taxonomy.layer_of(res.intent)
        if layer == "integrated":
            return self._plan_integrated(res)
        if layer == "basic":
            return self._plan_basic(res)
        return self._plan_meta(res)

    def _plan_basic(self, res):
        """基础层（原子动作）。equip/drop 指定槽位/物品时组合 transfer。"""
        it = res.intent
        s = res.slots

        # ② 装备：模组 equip 只能到主手 → 指定槽位时组合 transfer
        if it == "equip":
            item_id = s.get("item_id")
            if not item_id:
                return [], "没听出要装备哪个物品"
            slot = s.get("slot")
            if slot and slot != "mainhand":
                idx = self._backpack_slot_of(item_id)
                if idx is None:
                    self._dbg("equip 反查失败",
                              "item_id=%r slot=%r item=%r" % (item_id, slot, s.get("item")),
                              "slots=%r" % self._slots_snapshot())
                    if not self._inventory_ready():
                        return [], "女仆背包感知尚未就绪，无法确认物品（请稍后重试）"
                    return [], "背包里没有《%s》" % (s.get("item") or item_id)
                return [("transfer", {"from": "inv:%d" % idx,
                                      "to": slot, "count": 1})], ""
            return [("equip", {"item": item_id})], ""

        # ③ 丢出：模组 drop 只丢主手 → 指定物品时组合 transfer 到地面
        if it == "drop":
            count = int(s.get("count") or 1)
            item_id = s.get("item_id")
            if not item_id:
                return [("drop", {"count": count})], ""
            idx = self._backpack_slot_of(item_id)
            if idx is None:
                self._dbg("drop 反查失败",
                          "item_id=%r item=%r" % (item_id, s.get("item")),
                          "slots=%r" % self._slots_snapshot())
                if not self._inventory_ready():
                    return [], "女仆背包感知尚未就绪，无法确认物品（请稍后重试）"
                return [], "背包里没有《%s》" % (s.get("item") or item_id)
            pos = self._owner_pos() or self._maid_pos()
            if pos is None:
                return [], "拿不到坐标（感知数据缺失）"
            # 丢到**主人身边**而不是女仆脚下：否则会立刻被女仆的拾取逻辑收进背包（等于没丢）
            return [("transfer", {"from": "inv:%d" % idx,
                                   "to": "world:%d,%d,%d" % tuple(pos),
                                   "count": count})], ""

        # 其余基础指令（break/place/use/store/move/look/sit/stop）→ 通用契约填充
        return self._fill_params(res, it)

    def _plan_integrated(self, res):
        """集成层（任务级）。容器类前置 chestopen；transfer 反查 from。"""
        it = res.intent
        s = res.slots
        steps = []

        # ① 容器类：模组要求容器已打开 → 自动前置 chestopen
        if it in _NEEDS_OPENER:
            pos, why = self._pos_param(res, allow_maid_fallback=True)
            if why:
                return [], why
            steps.append((_OPENER, {"pos": pos}))

        # ④ 转移：from/to 必填 → 只说目标时用物品反查 from
        if it == "transfer":
            to = s.get("to") or s.get("slot")
            frm = s.get("from")
            if not to:
                return [], "没说清要移到哪个槽位"
            if not frm and s.get("item_id"):
                idx = self._backpack_slot_of(s["item_id"])
                if idx is not None:
                    frm = "inv:%d" % idx
            if not frm:
                return [], ("没说清从哪个槽位移（或背包里没有《%s》）"
                            % (s.get("item") or "该物品"))
            steps.append(("transfer", {"from": frm, "to": to,
                                       "count": int(s.get("count") or 64)}))
            return steps, ""

        # 其余集成指令（attack/guard/feed/mine/farm/build/collect/craft/smelt
        # /chestopen/chestput/chesttake）→ 通用契约填充
        main_steps, reason = self._fill_params(res, it)
        if reason:
            return [], reason
        return steps + main_steps, ""

    def _plan_meta(self, res):
        """元命令（cancel/status/chat）→ 无参数；chat 不产生指令。"""
        it = res.intent
        if it == "chat":
            return [], ""
        return [(it, {})], ""

    def _fill_params(self, res, it):
        """通用：按契约声明的参数名填充。basic 与 integrated 共用。"""
        s = res.slots
        names = mod_contract.param_names(it)
        if not names and not mod_contract.get(it):
            return [], "本地没有「%s」的可执行指令" % res.label
        params = {}
        if "item" in names and s.get("item_id"):
            params["item"] = s["item_id"]
        if "count" in names and s.get("count"):
            params["count"] = int(s["count"])
        if "range" in names and s.get("range"):
            params["range"] = int(s["range"])
        if "defensive" in names and s.get("defensive"):
            params["defensive"] = True
        if "height" in names and s.get("height"):
            params["height"] = int(s["height"])
        if "target" in names and s.get("target_id"):
            # attack 指定目标生物（如 target=minecraft:pig）；未识别出具体实体则不传，
            # 走模组「最近敌对生物」逻辑（「打怪」这类泛称）
            params["target"] = s["target_id"]
        if "slot" in names and it == "chesttake":
            # 模组的 slot 是**箱内格子下标**；我们无法枚举箱内物品 → auto 取首个非空
            params["slot"] = -1
        if "pos" in names:
            # 长任务（挖矿/耕作/建造）必须给绝对坐标：只说「我脚下」这类
            # 相对位置，判断成本太高、副作用太大，交给大 AI 追问更稳。
            if taxonomy.auto_policy(it) == "abs_pos" \
                    and not isinstance(s.get("pos"), (list, tuple)):
                return [], ("「%s」是长任务，需要明确坐标（例如「在 100 64 -50 挖矿」），"
                            "只说相对位置不执行" % res.label)
            if it == "mine" and s.get("pos") is None:
                # mine 无坐标：不发 pos，模组以女仆脚下为中心自动探测周围矿物
                pos = None
            else:
                pos, why = self._pos_param(res, allow_maid_fallback=(it == _OPENER))
                if why:
                    return [], why
            if pos is not None:
                params["pos"] = pos
        miss = mod_contract.missing_required(it, params)
        if miss:
            return [], "缺少必需参数：" + "、".join(miss)
        params, _dropped = mod_contract.clamp(it, params)
        return [(it, params)], ""

    # ---------------- 执行 ----------------

    def _send(self, cmd, params, timeout=6.0):
        """下发用户显式指令：默认抢占（cancel_previous=True）。

        用户主动要求应优先于女仆当前 AI 任务（如低血守护 guard）——
        否则「丢给我钻石剑」会被正在跑的 guard 以「正忙」拒绝，用户看着像失败。
        AI 兜底 DSL 仍走排队（见 MaidLoop），只有用户显式指令抢占。
        """
        try:
            return self.bridge.send_command(cmd, params, timeout=timeout,
                                            cancel_previous=True)
        except Exception:
            return None

    def _wait_task_done(self, task_id, timeout=4.0):
        try:
            return self.bridge.wait_task_done(task_id, timeout=timeout)
        except Exception:
            return False

    @staticmethod
    def _err_of(reply, tail):
        if reply is None:
            return "女仆没有回应"
        return (reply.get("result") or {}).get("error") or tail

    def _step_label(self, cmd):
        return _STEP_LABEL.get(cmd) or taxonomy.LABELS.get(cmd, cmd)

    def _execute_steps(self, steps, res):
        """顺序执行；任一步失败即停（后面的步骤依赖前面的状态）。"""
        details = []
        ok_all = True
        for cmd, params in steps:
            reply = self._send(cmd, params)
            if not (reply and reply.get("ok")):
                ok_all = False
                details.append("%s 失败：%s"
                               % (self._step_label(cmd), self._err_of(reply, "被拒绝")))
                break
            if cmd == _OPENER:
                # chestopen 是异步任务，必须等它真跑完（否则 chestput 必失败）
                if not self._wait_task_done(cmd, timeout=4.0):
                    ok_all = False
                    details.append("打开箱子超时（没等到容器打开）")
                    break
                details.append("已打开箱子")
                continue
            details.append("已执行「%s」" % self._step_label(cmd))

        acts = [c for c, _ in steps]
        res.executed = ok_all and bool([a for a in acts if a != _OPENER])
        res.exec_ok = ok_all
        res.exec_detail = "；".join(details) if details else "未产生任何动作"
        return ok_all

    # ---------------- craft 专用链路 ----------------

    def _run_craft(self, res):
        item_id = res.slots.get("item_id")
        if not item_id:
            res.mode = "hint"
            res.exec_detail = "没听出要合成哪个物品"
            res.summary = self._hint_summary(res, None, res.exec_detail)
            return
        count = int(res.slots.get("count") or 1)
        name = res.slots.get("item") or self.name_of(item_id)

        t0 = time.time()
        try:
            report = self.bridge.query_craft(item_id, count, timeout=3.0)
        except Exception as exc:
            report = None
            res.exec_detail = "配方查询失败：%s" % exc
        res.slots["recipe_ms"] = int((time.time() - t0) * 1000)

        if not report:
            res.mode = "hint"
            res.exec_detail = res.exec_detail or "配方查询无响应"
            res.summary = self._hint_summary(res, None, res.exec_detail)
            return

        res.steps = [{"cmd": "craft_check",
                      "params": {"item": item_id, "count": count}}]

        if not report.get("found"):
            res.mode = "hint"
            res.exec_detail = "没有「%s」的合成配方" % name
            res.slots["recipe"] = None
            res.summary = self._craft_summary(res, report, crafted=False,
                                              detail=res.exec_detail)
            return

        res.slots["recipe"] = report.get("ingredients") or []
        if [i for i in res.slots["recipe"] if not i.get("ok")]:
            res.mode = "hint"
            res.exec_detail = "材料不够，没有合成"
            res.summary = self._craft_summary(res, report, crafted=False,
                                              detail=res.exec_detail)
            return

        params, _dropped = mod_contract.clamp(
            "craft", {"item": item_id, "count": count})
        res.steps.append({"cmd": "craft", "params": params})
        reply = self._send("craft", params)
        if reply and reply.get("ok"):
            res.executed = True
            res.exec_ok = True
            res.mode = "auto"
            res.exec_detail = "已合成 %s ×%d" % (name, count)
        else:
            res.mode = "hint"
            res.exec_detail = "合成没能完成：%s" % self._err_of(reply, "女仆没有回应")
        res.summary = self._craft_summary(res, report, crafted=res.executed,
                                          detail=res.exec_detail)

    # ---------------- 结构化文本（给大 AI） ----------------

    def _conf_line(self, res):
        return "意图：%s（%s，置信度 %.2f）" % (res.label, res.intent, res.confidence)

    def _slot_line(self, res):
        s = res.slots
        parts = []
        if s.get("item"):
            tail = ""
            if s.get("item_kind") in ("pinyin", "near", "initial"):
                tail = "（语音模糊匹配，原文非字面一致）"
            ident = (" → %s" % s["item_id"]) if s.get("item_id") else "（未取到 id）"
            parts.append("物品：《%s》%s%s" % (s["item"], ident, tail))
        if s.get("count"):
            parts.append("数量：%s（%d）" % (s.get("count_expr") or s["count"], s["count"]))
        if s.get("pos"):
            show = s.get("pos_expr") or s.get("pos")
            parts.append("位置：%s" % (show,))
        if s.get("target"):
            adv = "（仅语义参考）" if res.intent in ("attack", "collect", "move", "look") else ""
            parts.append("目标：%s%s" % (s["target"], adv))
        if s.get("slot"):
            parts.append("槽位：%s" % s["slot"])
        if s.get("range"):
            parts.append("范围：%d 格" % s["range"])
        if s.get("height"):
            parts.append("高度：%d 格" % s["height"])
        if s.get("defensive"):
            parts.append("姿态：只防守不主动")
        return "槽位：" + "；".join(parts) if parts else ""

    def _steps_line(self, res):
        if not res.steps:
            return ""
        parts = []
        for st in res.steps:
            cmd = st.get("cmd")
            prm = st.get("params") or {}
            if prm:
                parts.append("%s(%s)" % (cmd, " ".join(
                    "%s=%s" % (k, v) for k, v in prm.items())))
            else:
                parts.append(cmd)
        return "指令：" + " → ".join(parts)

    def _describe_executed(self, steps):
        """把已执行的指令步骤转成 AI 能懂的**语义化**描述。

        关键：NLU 执行后必须让大 AI 明确知道「用户要求的事已经做完、
        做成了什么」，否则 AI 会以为没做成、自作主张再发指令（如
        「穿金胸甲」NLU 已 transfer 穿上，AI 若不知情会再回【equip】
        把胸甲拿回主手）。这里用自然语言描述结果，而不是程序式
        「转移物品」。
        """
        parts = []
        for st in steps or []:
            cmd = st.get("cmd")
            params = st.get("params") or {}
            if cmd == "transfer":
                to = str(params.get("to") or "")
                slot_name = {"chest": "胸部", "head": "头部",
                             "legs": "腿部", "feet": "脚部"}
                if to in slot_name:
                    parts.append("穿戴到%s" % slot_name[to])
                elif to == "mainhand":
                    parts.append("换到主手")
                elif to == "offhand":
                    parts.append("换到副手")
                elif to.startswith("world"):
                    parts.append("丢到地面")
                elif to.startswith("container"):
                    parts.append("放入箱子")
                else:
                    parts.append("转移物品")
            elif cmd == "equip":
                parts.append("装备到主手")
            elif cmd == "drop":
                parts.append("丢出物品")
            elif cmd == "craft":
                parts.append("合成%s" % self.name_of(params.get("item", "")))
            elif cmd == "smelt":
                parts.append("烧炼%s" % self.name_of(params.get("item", "")))
            elif cmd == "chestopen":
                parts.append("打开箱子")
            else:
                parts.append(_STEP_LABEL.get(cmd)
                             or taxonomy.LABELS.get(cmd, cmd))
        return "、".join(parts) if parts else "完成"

    def _render_ingredient(self, ing):
        opts = ing.get("options") or []
        if len(opts) > 1:
            names = " / ".join(self.name_of(o) for o in opts[:3])
            if len(opts) > 3:
                names += " 等%d种" % len(opts)
            label = "任意(%s)" % names
        elif opts:
            label = self.name_of(opts[0])
        else:
            label = "未知材料"
        need, have = ing.get("need", 0), ing.get("have", 0)
        if ing.get("via_craft"):
            plan = ing.get("craft_plan") or []
            extra = "可经合成补齐"
            if plan:
                names = "、".join(self._craft_plan_name(p) for p in plan[:3])
                extra += "（先合成 %s）" % names
            mark = extra
        elif ing.get("ok"):
            mark = "充足"
        else:
            mark = "缺%d" % max(0, need - have)
        return "%s ×%d（现有 %d，%s）" % (label, need, have, mark)

    def _craft_plan_name(self, entry):
        """把模组返回的合成计划条目（如 minecraft:stick×2）转成中文名×数量。"""
        item_id, _, n = entry.partition("\u00d7")
        return "%s×%s" % (self.name_of(item_id), n)

    def _craft_summary(self, res, report, crafted, detail):
        lines = ["【本地预处理】用户原话：「%s」" % res.text,
                 self._conf_line(res)]
        sl = self._slot_line(res)
        if sl:
            lines.append(sl)
        ings = report.get("ingredients") or []
        if report.get("found") and ings:
            lines.append("配方：%s" % " + ".join(self._render_ingredient(i) for i in ings))
        elif not report.get("found"):
            lines.append("配方：本地/游戏内都没有该物品的合成配方")
        lines.append("执行：%s%s" % ("成功 " if crafted else "失败 ", detail or ""))
        if not crafted and ings:
            miss = [i for i in ings if not i.get("ok")]
            if miss:
                names = []
                for i in miss:
                    opts = i.get("options") or []
                    nm = self.name_of(opts[0]) if opts else "材料"
                    names.append("%s 缺 %d" % (nm, max(0, i.get("need", 0) - i.get("have", 0))))
                lines.append("还差：" + "、".join(names))
        lines.append("（以上为本地程序的事实结果，请据此回应用户，不要重复报技能名）")
        return "\n".join(lines)

    def _generic_summary(self, res):
        lines = ["【本地预处理】用户原话：「%s」" % res.text,
                 self._conf_line(res)]
        sl = self._slot_line(res)
        if sl:
            lines.append(sl)
        st = self._steps_line(res)
        if st:
            lines.append(st)
        if res.executed:
            done = self._describe_executed(res.steps)
            lines.append("本地程序已自动完成：%s" % done)
            lines.append("（用户要求的事已经做完，请直接以女仆口吻回应，"
                         "不要再重复执行、不要发送任何指令）")
        else:
            lines.append("执行：失败 %s" % (res.exec_detail or "未执行"))
            lines.append("（以上为本地程序的事实结果，请据此回应用户）")
        return "\n".join(lines)

    def _hint_summary(self, res, pred, reason=""):
        lines = ["【本地预处理】用户原话：「%s」" % res.text,
                 self._conf_line(res)]
        sl = self._slot_line(res)
        if sl:
            lines.append(sl)
        if reason:
            lines.append("未执行原因：%s" % reason)
        lines.append("（本地程序**没有**执行任何动作，请你自己判断是否/如何回应）")
        return "\n".join(lines)
