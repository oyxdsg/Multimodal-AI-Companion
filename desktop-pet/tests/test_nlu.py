# -*- coding: utf-8 -*-
"""NLU（本地预处理）回归测试 —— 无框架，纯断言。

    python tests/test_nlu.py

覆盖：
1. 意图体系与模组指令集一致（24 条 + status/cancel/chat）
2. 训练语料合成（无空样本、意图齐全、噪声生效）
3. 特征器（定长编码、PAD、全角归一化）
4. 模型存取（save/load 后概率完全一致）
5. 意图识别质量门槛（关键用例 + 训练报告指标）
6. 槽位抽取：拼音模糊（稿子→镐子）、容器降级、数量绑定、坐标/生物/槽位
7. 路由编排（FakeBridge）：材料够→自动合成、材料不够→回报缺料、未连接→只提示
8. 健壮性：任何异常输入都不得抛出

不依赖 numpy 之外的东西；不需要游戏与网络。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MODEL_DIR = os.path.join(BASE, "nlu", "data")


# ---------------------------------------------------------------- 桩引擎

class StubEngine:
    """固定返回某个意图的引擎，用于**确定性**测试「语义 → 模组指令」的映射。

    计划层的行为不该依赖模型对某个具体句式的判断（模型会随训练变化），
    所以映射类测试注入桩引擎，把「模型质量」与「映射正确性」分开考。
    """

    def __init__(self, intent, conf=0.99):
        self.intent = intent
        self.conf = conf

    def predict(self, text):
        from nlu.intent import Prediction
        return Prediction(self.intent, self.conf,
                          {self.intent: self.conf, "chat": 1.0 - self.conf}, text)

    def decide(self, text):
        return self.predict(text), "auto"


# ---------------------------------------------------------------- 假 Bridge

class FakeBridge:
    """按 nlu.router 的 Bridge 协议实现的假对象。"""

    def __init__(self, connected=True, items=None, inv=None, recipe=None,
                 reply=None, replies=None, slots=None, maid_pos=(10, 64, 10),
                 owner_pos=(12, 64, 14)):
        self._connected = connected
        self._items = items or {}
        self._inv = inv or {}
        self._recipe = recipe
        self._reply = reply if reply is not None else {"ok": True, "state": "running"}
        self._replies = replies or {}
        self._slots = list(slots or [])
        self._maid_pos = maid_pos
        self._owner_pos = owner_pos
        self.commands = []      # [(cmd, params)]
        self.last_cancel_previous = None   # 最近一次指令是否抢占
        self.queries = []
        self.waited = []        # 等过的任务 id
        self.status_calls = 0

    # ---- 只读 ----

    @property
    def connected(self):
        return self._connected

    def item_index(self):
        return self._items

    def inventory(self):
        return self._inv

    def inventory_slots(self):
        return list(self._slots)

    def maid_pos(self):
        return self._maid_pos

    def owner_pos(self):
        return self._owner_pos

    # ---- 指令 ----

    def query_craft(self, item_id, count=1, timeout=3.0):
        self.queries.append((item_id, count))
        return self._recipe

    def query_status(self, timeout=2.0):
        self.status_calls += 1
        return {"task": None, "ai_busy": False}

    def wait_task_done(self, task_id, timeout=4.0):
        self.waited.append(task_id)
        return True

    def send_command(self, cmd, params=None, cancel_previous=True, timeout=6.0):
        self.commands.append((cmd, params))
        self.last_cancel_previous = cancel_previous
        if cmd in self._replies:
            return self._replies[cmd]
        return self._reply

    def speak(self, text, maid=""):
        return True


ITEMS = {
    "镐子": "minecraft:wooden_pickaxe",
    "铁镐": "minecraft:iron_pickaxe",
    "钻石镐": "minecraft:diamond_pickaxe",
    "钻石": "minecraft:diamond",
    "钻石剑": "minecraft:diamond_sword",
    "铁锭": "minecraft:iron_ingot",
    "铁块": "minecraft:iron_block",
    "铁剑": "minecraft:iron_sword",
    "铁头盔": "minecraft:iron_helmet",
    "木棍": "minecraft:stick",
    "木板": "minecraft:oak_planks",
    "箱子": "minecraft:chest",
    "熔炉": "minecraft:furnace",
}

MOD_TASK_COMMANDS = {
    "attack", "guard", "feed", "eat", "mine", "farm", "build", "collect", "craft",
    "smelt", "transfer", "chestopen", "chestput", "chesttake", "move",
    "look", "break", "place", "use", "equip", "store", "drop", "pickup",
    "sit", "stop", "cancel", "status",
}

# 契约有 / 意图无的指令（模组侧仍合法，但 NLU 不产出该意图）：
#   pickup      —— 已按 DESIGN_NLU.md §11.1 规范合并进 collect(range=4)
#   craft_check —— 桌宠侧干跑查询（见下方单独断言）
CONTRACT_ONLY = frozenset({"pickup"})


# ---------------------------------------------------------------- 1. 体系

def test_taxonomy():
    from nlu import taxonomy
    keys = [it.key for it in taxonomy.ALL]
    assert len(keys) == len(set(keys)), "意图 key 必须唯一"
    # 模组 /maidtasks 的指令必须有对应意图，且 cmd 名一致
    # （pickup 例外：已合并进 collect，见 §11.1 规范①）
    cmd_keys = {it.cmd for it in taxonomy.ALL if it.cmd}
    missing = MOD_TASK_COMMANDS - cmd_keys - CONTRACT_ONLY
    assert not missing, "缺少模组指令映射: %s" % missing
    assert "pickup" not in cmd_keys, "pickup 已合并进 collect，不应再作为意图出 key"
    assert taxonomy.get("collect") is not None
    assert taxonomy.is_auto_ok("craft")
    assert not taxonomy.is_auto_ok("chat")
    # 长任务需要绝对坐标；普通位置类可换算
    # mine 例外：不给坐标也能自动探测周围矿物（女仆脚下为中心），故为 pos 策略
    assert taxonomy.auto_policy("mine") == "pos"
    assert taxonomy.auto_policy("farm") == "abs_pos"
    assert taxonomy.auto_policy("place") == "pos"
    assert taxonomy.auto_policy("chat") == "never"
    print("  [1] 意图体系 OK（%d 类）" % len(keys))


def test_mod_contract():
    """taxonomy ↔ mod_contract 必须严格一致（防止桌宠与模组指令层漂移）。"""
    from nlu import mod_contract, taxonomy
    # ① 每个有指令的意图，其指令必须在契约里
    for it in taxonomy.ALL:
        if it.cmd:
            assert mod_contract.get(it.cmd) is not None, \
                "意图 %s 的指令 %s 不在模组契约里" % (it.key, it.cmd)
    # ② 契约里的每条指令都要有意图能产生它
    #    （craft_check 是桌宠内部查询；pickup 已合并进 collect —— 两条都豁免）
    cmd_intents = {it.cmd for it in taxonomy.ALL if it.cmd}
    for name in mod_contract.COMMANDS:
        if name in CONTRACT_ONLY or name == "craft_check":
            continue
        assert name in cmd_intents, "契约里的 %s 没有对应意图" % name
    # 契约里有、意图里没有的必须是**白名单内**的（防漏删/误删）
    assert set(mod_contract.COMMANDS) - cmd_intents == CONTRACT_ONLY | {"craft_check"}
    # ③ 模组 26 条全覆盖
    assert MOD_TASK_COMMANDS <= set(mod_contract.COMMANDS)
    print("  [1b] 模组指令契约 OK（%d 条，锚点 %s）"
          % (len(mod_contract.COMMANDS), mod_contract.MOD_COMMIT))


def test_contract_clamp():
    """clamp 必须丢弃模组不认识的参数、把数值夹进合法区间。"""
    from nlu import mod_contract
    p, dropped = mod_contract.clamp("equip", {"item": "minecraft:iron_helmet",
                                              "slot": "head", "to": "head"})
    assert "slot" in dropped and "to" in dropped, "equip 不该收到 slot/to"
    assert p == {"item": "minecraft:iron_helmet"}
    p, _ = mod_contract.clamp("attack", {"range": 999})
    assert p["range"] == 64, "range 应被夹到上限 64"
    p, _ = mod_contract.clamp("attack", {"range": 0})
    assert p["range"] == 1, "range 应被夹到下限 1"
    assert mod_contract.missing_required("transfer", {"from": "mainhand"}) == ["to"]
    assert mod_contract.missing_required("craft", {"item": "x"}) == []
    assert mod_contract.unmet_requires("chestput", set()) == ["opened_container"]
    assert mod_contract.unmet_requires("chestput", {"opened_container"}) == []
    print("  [1c] 参数裁剪与前置校验 OK")


# ---------------------------------------------------------------- 2. 语料

def test_synth():
    from collections import Counter
    from nlu import synth, taxonomy
    rows = synth.generate(n_per_intent=20, seed=1, noise=0.5)
    assert rows, "语料为空"
    assert all(r["text"].strip() for r in rows), "存在空文本"
    kinds = set(r["intent"] for r in rows)
    assert kinds == set(taxonomy.KEYS), "意图覆盖不全: %s" % (set(taxonomy.KEYS) - kinds)
    tr, va = synth.split(rows, val_ratio=0.2, seed=2)
    assert len(tr) + len(va) == len(rows)
    # 噪声确实在起作用（同音替换会产生字面不同的样本）
    base = synth.generate(n_per_intent=20, seed=1, noise=0.0)
    assert [r["text"] for r in rows] != [r["text"] for r in base]
    print("  [2] 语料合成 OK（%d 条，%d 类）"
          % (len(rows), len(Counter(r['intent'] for r in rows))))


# ---------------------------------------------------------------- 3. 特征

def test_features():
    from nlu import features
    f = features.Featurizer.build(["合成一把镐子", "去打僵尸", "你好呀"], min_count=1)
    ids = f.encode("帮我合成一把稿子")
    assert len(ids) == f.max_len, "编码必须是定长"
    assert 0 in ids, "应包含 PAD"
    assert f.encode("") == [0] * f.max_len, "空串应全 PAD"
    # 全角/标点归一化：全角字母与半角等价
    assert features.normalize("ＡＢＣ，。！") == "abc"
    # 长文本截断后仍定长
    assert len(f.encode("很长的句子" * 100)) == f.max_len
    print("  [3] 特征器 OK（词表 %d）" % f.size)


# ---------------------------------------------------------------- 4/5. 模型

def _load_engine():
    from nlu import intent
    if not intent.available(MODEL_DIR):
        return None
    intent.reset_shared()
    return intent.IntentEngine.load(MODEL_DIR)


def test_model_roundtrip():
    """模型存/取必须完全一致（float16 存储不能改变预测）。"""
    import tempfile
    import numpy as np
    from nlu.model import IntentNet
    net = IntentNet(50, 3, emb=4, hidden=6, seed=1)
    ids = np.asarray([[2, 3, 4, 0, 0], [5, 0, 0, 0, 0]], dtype="int32")
    before = net.proba(ids)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.npz")
        net.save(p)
        back = IntentNet.load(p)
        after = back.proba(ids)
    assert np.allclose(before, after, atol=1e-3), "存取后概率偏差过大"
    assert back.labels == net.labels
    print("  [4] 模型存取 OK")


def test_intent_quality():
    eng = _load_engine()
    if eng is None:
        print("  [5] 跳过（nlu/data 下无模型，请先跑 python -m nlu.train）")
        return
    # 关键用例：用户原例「稿子」必须判成合成
    must = {
        "帮我合成一把稿子": "craft",
        "合诚一个铁镐": "craft",
        "用三个铁锭两根木棍做个铁搞子": "craft",
        "今天天气怎么样": "chat",
        "我好累啊陪我聊聊天": "chat",
        "把那个僵尸打掉": "attack",
        "快把周围的僵尸清了": "attack",
        "烧点铁矿": "smelt",
        "合成一组木板": "craft",
    }
    for text, want in must.items():
        p = eng.predict(text)
        assert p is not None, "推理返回 None: %s" % text
        assert p.intent == want, "「%s」判成 %s（期望 %s，%.3f）" % (
            text, p.intent, want, p.confidence)
    # 闲聊绝不能被当作可自动执行的指令（产品红线）
    for text in ["今天天气怎么样", "我好累啊", "你叫什么名字", "晚安"]:
        p, mode = eng.decide(text)
        assert mode != "auto", "闲聊被误判为可自动执行: %s -> %s" % (text, p.intent)
    print("  [5] 意图识别 OK（%d 个关键用例）" % len(must))


def test_report_gate():
    """训练报告的质量门槛：自动执行档必须准、闲聊误激活必须为 0。"""
    path = os.path.join(MODEL_DIR, "report.json")
    if not os.path.isfile(path):
        print("  [5b] 跳过（无 report.json）")
        return
    with open(path, "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["val_acc"] >= 0.95, "验证集准确率过低: %s" % rep["val_acc"]
    t85 = rep["thresholds"]["0.85"]
    assert t85["accuracy"] >= 0.99, "高置信档准确率过低: %s" % t85["accuracy"]
    assert t85["chat_false_activation"] <= 0.01, \
        "闲聊误激活率过高: %s" % t85["chat_false_activation"]
    print("  [5b] 训练报告门槛 OK（val_acc=%.4f，≥0.85 档准确 %.4f，闲聊误激活 %.2f%%）"
          % (rep["val_acc"], t85["accuracy"],
             t85["chat_false_activation"] * 100))


# ---------------------------------------------------------------- 6. 槽位

def test_slots():
    from nlu import slots
    idx = slots.ItemIndex.from_mod(ITEMS)

    # 拼音模糊：用户原例「稿子」→「镐子」
    got = idx.resolve("帮我合成一把稿子")
    assert got and got[0].name == "镐子", "拼音模糊失败: %s" % [(m.name, m.kind) for m in got]
    assert got[0].kind == "pinyin"
    assert got[0].item_id == "minecraft:wooden_pickaxe"

    # 容器降级：「取箱子里的铁定」宾语是铁锭而不是箱子
    e = slots.extract("取箱子里的铁定", idx, intent="chesttake")
    assert e.get("item") == "铁锭", "容器降级失败: %s" % e.get("item")
    assert "箱子" in (e.get("item_alts") or [])

    # 数量绑定：材料数量不算产出数量
    e = slots.extract("用三个铁锭两根木棍做个铁镐", idx, intent="craft")
    assert e.get("item") == "铁镐", "动词邻接失败: %s" % e.get("item")
    assert not e.get("count"), "材料数量被误当成产出数量: %s" % e.get("count")
    e = slots.extract("六个铁锭合成两个铁块", idx, intent="craft")
    assert e.get("count") == 2, "应取动词后的数量: %s" % e.get("count")

    # 同起点子串消歧：产出物取「更长匹配」（钻石镐 优于 钻石）——
    # 修复：此前「帮我做钻石稿」会错抽成「钻石」，导致 craft 查「钻石」配方失败
    e = slots.extract("帮我做钻石稿", idx, intent="craft")
    assert e.get("item") == "钻石镐", "同起点应取更长匹配: %s" % e.get("item")
    assert e.get("item_id") == "minecraft:diamond_pickaxe", e.get("item_id")
    assert e.get("item_kind") == "pinyin"

    # 量词换算
    assert slots.choose_count("合成一组木板", idx, None)[0] == 64
    assert slots.choose_count("合成半组木板", idx, None)[0] == 32

    # 坐标 / 相对位置 / 生物 / 槽位
    assert slots.parse_pos("在 100 64 -50 挖矿")[0] == (100, 64, -50)
    assert slots.parse_pos("x=1 y=2 z=3 放方块")[0] == (1, 2, 3)
    assert slots.parse_pos("在我脚下放")[0] == "owner", "「我脚下」应为主人参照"
    assert slots.parse_pos("到你脚下")[0] == "maid", "「你脚下」应为女仆参照"
    assert slots.parse_target("把僵尸打掉") == "僵尸"
    assert slots.parse_slot("把铁剑拿在主手") == "mainhand"
    assert slots.parse_slot("把铁头盔戴在头上") == "head"
    # 数值类槽位
    assert slots.parse_range("清理 10 格内的怪") == 10
    assert slots.parse_range("半径 6 攻击") == 6
    assert slots.parse_height("在那边建 3 格高的墙") == 3
    assert slots.parse_defensive("只防守别主动打") is True
    # 「脚下」是位置词，不能污染成装备槽位
    assert slots.parse_slot("在我脚下挖") is None, "「脚下」不该被当成脚部槽位"
    # 槽位只在需要槽位的意图上抽（避免与位置语义交叉）
    e = slots.extract("在我脚下放个方块", idx, intent="place")
    assert "slot" not in e and e.get("pos") == "owner"

    # 降级词典（无 id）不能产生可执行 id
    wiki = slots.ItemIndex({}, extra_names=["镐子", "铁锭"])
    m = wiki.resolve("合成镐子")
    assert m and m[0].item_id is None, "降级词典不应给出物品 id"

    # drop/equip 等无产出动词的意图，也要取「更长的完整名」：
    # 语音「钻石搞」（搞/镐同音）→ 钻石镐(拼音 0.95) 不应被字面命中的「钻石」(1.0) 挤掉
    e = slots.extract("把钻石搞丢给我", idx, intent="drop")
    assert e.get("item") == "钻石镐", "drop 应取更长完整名: %s" % e.get("item")
    assert e.get("item_id") == "minecraft:diamond_pickaxe", e.get("item_id")
    # 多物品时取「最靠前的完整名」
    e = slots.extract("把钻石剑和钻石镐丢给我", idx, intent="drop")
    assert e.get("item") == "钻石剑", "多物品应取最靠前完整名: %s" % e.get("item")
    print("  [6] 槽位抽取 OK")


# ---------------------------------------------------------------- 7. 路由

def _router(bridge, engine=None):
    from nlu.router import NluRouter
    return NluRouter(bridge, engine=engine)


def test_router_insufficient():
    """材料不够：不执行，且回报缺什么、缺多少。"""
    bridge = FakeBridge(
        items=ITEMS,
        recipe={"found": True, "craftable": False, "need": 1,
                "ingredients": [
                    {"options": ["minecraft:iron_ingot"], "need": 3, "have": 1, "ok": False},
                    {"options": ["minecraft:stick"], "need": 2, "have": 0, "ok": False},
                ]})
    res = _router(bridge).preprocess("帮我合成一把铁镐")
    assert res.mode == "hint", "材料不够时不该走 auto: %s" % res.mode
    assert not res.executed, "材料不够却执行了"
    assert not bridge.commands, "材料不够却下发了指令"
    assert "还差" in res.summary, "缺少「还差」明细: %s" % res.summary
    assert "铁锭" in res.summary and "木棍" in res.summary
    assert "3" in res.summary, "应给出缺口数量"
    print("  [7a] 材料不够 → 只回报明细 OK")


def test_router_sufficient():
    """材料齐备：自动合成，并把「已合成」写进给大 AI 的结果。"""
    bridge = FakeBridge(
        items=ITEMS,
        recipe={"found": True, "craftable": True, "need": 1,
                "ingredients": [
                    {"options": ["minecraft:iron_ingot"], "need": 3, "have": 5, "ok": True},
                    {"options": ["minecraft:stick"], "need": 2, "have": 2, "ok": True},
                ]},
        reply={"ok": True, "state": "running"})
    res = _router(bridge).preprocess("帮我合成一把铁镐")
    assert res.executed and res.exec_ok, "材料齐备却未执行: %s" % res.exec_detail
    assert bridge.commands and bridge.commands[0][0] == "craft"
    assert bridge.commands[0][1].get("item") == "minecraft:iron_pickaxe"
    assert "已合成" in res.summary
    assert "成功" in res.summary
    # 原话必须出现在注入文本里（要求：把用户输入也交给大 AI）
    assert "帮我合成一把铁镐" in res.summary
    print("  [7b] 材料齐备 → 自动合成 OK")


def test_router_via_craft():
    """直接材料背包没有、但模组 craft_check 判定可递归合成（via_craft）：
    应自动合成，且注入文本说明材料可由合成补齐。"""
    bridge = FakeBridge(
        items=ITEMS,
        recipe={"found": True, "craftable": True, "need": 1,
                "ingredients": [
                    {"options": ["minecraft:iron_ingot"], "need": 3, "have": 3, "ok": True},
                    {"options": ["minecraft:stick"], "need": 2, "have": 0, "ok": True,
                     "via_craft": True,
                     "craft_plan": ["minecraft:stick×2", "minecraft:oak_planks×2"]},
                ]},
        reply={"ok": True, "state": "running"})
    res = _router(bridge).preprocess("帮我合成一把铁镐")
    assert res.executed and res.exec_ok, "可递归合成却未执行: %s" % res.exec_detail
    assert bridge.commands and bridge.commands[0][0] == "craft"
    assert bridge.commands[0][1].get("item") == "minecraft:iron_pickaxe"
    assert "可经合成补齐" in res.summary, "缺少「可经合成补齐」: %s" % res.summary
    assert "木棍" in res.summary
    print("  [7b2] 材料可递归合成 → 自动合成 + 说明补齐来源 OK")


def test_router_not_connected():
    """女仆未连接：不执行、不报错，只把意图作为提示交给大 AI。"""
    bridge = FakeBridge(connected=False, items=ITEMS)
    res = _router(bridge).preprocess("帮我合成一把铁镐")
    assert not res.executed, "未连接却执行了"
    assert not bridge.commands
    assert res.mode == "hint"
    assert "未连接" in res.summary
    print("  [7c] 未连接 → 只提示 OK")


def test_router_no_recipe():
    bridge = FakeBridge(items=ITEMS, recipe={"found": False, "ingredients": []})
    res = _router(bridge).preprocess("帮我合成一把铁镐")
    assert not res.executed
    assert "没有" in res.summary and "配方" in res.summary
    print("  [7d] 无配方 → 明确回报 OK")


def test_router_chat_passthrough():
    """闲聊必须零干预：不产指令、不生成注入文本。"""
    bridge = FakeBridge(items=ITEMS)
    res = _router(bridge).preprocess("今天天气怎么样呀")
    assert res.mode == "none", "闲聊被处理了: %s" % res.mode
    assert not res.useful, "闲聊不该注入任何内容"
    assert not bridge.commands
    print("  [7e] 闲聊零干预 OK")


# ------------------------------------------------- 7g-7l. 语义 → 模组指令 组合

def test_plan_equip_with_slot():
    """模组 equip 只能到主手 → 指定槽位必须组合 transfer。"""
    bridge = FakeBridge(items=ITEMS,
                        slots=[{"i": 12, "id": "minecraft:iron_helmet", "count": 1}])
    res = _router(bridge, StubEngine("equip")).preprocess("把铁头盔戴在头上")
    assert res.intent == "equip", "意图判成 %s" % res.intent
    assert res.executed, "未执行：%s" % res.exec_detail
    assert bridge.commands == [("transfer", {"from": "inv:12", "to": "head", "count": 1})], \
        bridge.commands
    # 关键：必须用**语义化**告知大 AI「已经做完、做成了什么」——
    # 否则 AI 以为没做成会再发指令（如【equip】撤销穿戴）
    assert "本地程序已自动完成" in res.summary, res.summary
    assert "穿戴到头部" in res.summary, "应语义化描述结果: %s" % res.summary
    assert "不要" in res.summary and "指令" in res.summary, "应明确禁止重复执行: %s" % res.summary
    print("  [7g] 装备到指定槽位 → 组合 transfer + 语义化告知 OK")


def test_plan_drop_named_item():
    """模组 drop 只丢主手 → 指定物品组合 transfer 到**主人身边**（丢女仆脚下会被捡回）。"""
    bridge = FakeBridge(items=ITEMS,
                        slots=[{"i": 3, "id": "minecraft:iron_ingot", "count": 5}],
                        maid_pos=(10, 64, 10), owner_pos=(12, 64, 14))
    res = _router(bridge, StubEngine("drop")).preprocess("把铁锭丢出来")
    assert res.intent == "drop", "意图判成 %s" % res.intent
    assert bridge.commands == [
        ("transfer", {"from": "inv:3", "to": "world:12,64,14", "count": 1})], \
        bridge.commands
    print("  [7h] 丢出指定物品 → 组合 transfer 到主人身边 OK")


def test_plan_transfer_derives_from():
    """transfer 的 from 必填 → 只说目标槽位时用背包快照反查。"""
    bridge = FakeBridge(items=ITEMS,
                        slots=[{"i": 7, "id": "minecraft:iron_sword", "count": 1}])
    res = _router(bridge, StubEngine("transfer")).preprocess("把铁剑放到主手")
    assert res.intent == "transfer", "意图判成 %s" % res.intent
    assert bridge.commands == [
        ("transfer", {"from": "inv:7", "to": "mainhand", "count": 64})], \
        bridge.commands
    print("  [7i] 转移缺 from → 用物品反查补出 OK")


def test_plan_chest_needs_opener():
    """chestput/chesttake 依赖已打开的容器 → 必须自动前置并等 chestopen 跑完。"""
    bridge = FakeBridge(items=ITEMS,
                        slots=[{"i": 5, "id": "minecraft:iron_ingot", "count": 3}])
    res = _router(bridge, StubEngine("chestput")).preprocess("把铁锭放进箱子")
    cmds = [c for c, _ in bridge.commands]
    assert cmds and cmds[0] == "chestopen", "缺少 chestopen 前置: %s" % cmds
    assert "chestput" in cmds, "没下发 chestput: %s" % cmds
    assert bridge.waited and bridge.waited[0] == "chestopen", \
        "chestput 之前必须等 chestopen 真正跑完（异步任务）"
    assert res.executed, res.exec_detail
    print("  [7j] 存箱子 → 自动前置并等待 chestopen OK")


def test_plan_relative_pos():
    """相对位置必须换算成模组能吃的形态（主人坐标 / ~）。"""
    bridge = FakeBridge(items=ITEMS, owner_pos=(100, 64, -50),
                        maid_pos=(10, 64, 10))
    res = _router(bridge, StubEngine("place")).preprocess("在我脚下放个方块")
    assert res.intent == "place", "意图判成 %s" % res.intent
    assert bridge.commands, "没执行：%s" % res.exec_detail
    cmd, params = bridge.commands[0]
    assert cmd == "place" and params["pos"] == [100, 64, -50], bridge.commands
    # 主人坐标拿不到时不能瞎放
    b2 = FakeBridge(items=ITEMS, owner_pos=None)
    r2 = _router(b2, StubEngine("place")).preprocess("在我脚下放个方块")
    assert not b2.commands, "拿不到主人坐标却执行了"
    assert "坐标" in r2.summary
    print("  [7k] 相对位置换算 OK")


def test_plan_long_task_needs_abs_pos():
    """长任务（耕作/建造）只说相对位置不执行，给绝对坐标才执行；
    mine 例外：不给坐标也能以女仆脚下为中心自动探测周围矿物。"""
    # farm 仍要求绝对坐标（相对位置不执行）
    b1 = FakeBridge(items=ITEMS, owner_pos=(100, 64, -50))
    r1 = _router(b1, StubEngine("farm")).preprocess("在我脚下种地")
    assert not b1.commands, "长任务不该因相对位置就执行"
    assert "长任务" in r1.summary
    b2 = FakeBridge(items=ITEMS)
    r2 = _router(b2, StubEngine("mine")).preprocess("在 100 64 -50 挖点矿")
    assert b2.commands and b2.commands[0][0] == "mine", b2.commands
    assert b2.commands[0][1]["pos"] == [100, 64, -50], b2.commands
    # mine 不给坐标：不发 pos，模组自动探测周围矿物
    b3 = FakeBridge(items=ITEMS)
    r3 = _router(b3, StubEngine("mine")).preprocess("帮我挖点矿")
    assert b3.commands and b3.commands[0][0] == "mine", b3.commands
    assert "pos" not in b3.commands[0][1], "mine 无坐标时不应带 pos: %s" % b3.commands
    print("  [7l] 长任务坐标策略 OK（mine 支持无坐标自动探测）")


def test_router_never_raises():
    """预处理层绝不能因任何输入抛异常（拖垮聊天）。"""
    bridge = FakeBridge(items=ITEMS)

    class Boom(FakeBridge):
        def query_craft(self, *a, **k):
            raise RuntimeError("boom")

        def item_index(self):
            raise RuntimeError("boom")

    router = _router(bridge)
    for bad in ["", "   ", "?!@#$%^&*()", "a" * 5000, "\n\t", "😀😀", "，，，"]:
        r = router.preprocess(bad)
        assert r is not None
    r = _router(Boom()).preprocess("帮我合成一把铁镐")
    assert r is not None and not r.executed
    print("  [7f] 健壮性 OK")


def test_router_user_command_preempts():
    """用户显式指令默认抢占（cancel_previous=True），优先于女仆当前 AI 任务。

    例：女仆正低血守护（guard，AI 决策），用户说「把钻石镐丢给我」——
    用户指令应抢占 guard，而不是被「正忙」拒绝（否则看着像任务失败）。
    """
    bridge = FakeBridge(items=ITEMS,
                        slots=[{"i": 5, "id": "minecraft:diamond_pickaxe"}])
    res = _router(bridge).preprocess("把钻石镐丢给我")
    assert res.executed and res.exec_ok, "用户指令应执行: %s" % res.exec_detail
    assert bridge.commands and bridge.commands[0][0] == "transfer"
    assert bridge.last_cancel_previous is True, "用户指令应抢占女仆任务"
    print("  [7m] 用户指令默认抢占 OK")


def test_plan_attack_target():
    """attack 指定目标：具体生物 → 传 target 实体 id；泛称「怪」→ 不传，走敌对逻辑。"""
    bridge = FakeBridge(items=ITEMS)
    res = _router(bridge, StubEngine("attack")).preprocess("杀死你旁边这头猪")
    assert res.executed, "应执行: %s" % res.exec_detail
    assert bridge.commands and bridge.commands[0][0] == "attack", bridge.commands
    assert bridge.commands[0][1].get("target") == "minecraft:pig", bridge.commands

    b2 = FakeBridge(items=ITEMS)
    r2 = _router(b2, StubEngine("attack")).preprocess("打怪")
    assert r2.executed, "泛称也应执行（打最近敌对）: %s" % r2.exec_detail
    assert b2.commands and "target" not in b2.commands[0][1], b2.commands
    print("  [7n] attack 指定目标 OK")


# ---------------------------------------------------------------- 8. WS 链路

def test_maid_link_roundtrip():
    """用假 WS 客户端模拟模组，验证 MaidLink 新增链路。

    覆盖：handshake → item_index 下发 → 感知快照 → 背包汇总 → 指令/等回执 → 干跑查询。
    """
    try:
        import websockets
    except ImportError:
        print("  [8] 跳过（未安装 websockets）")
        return
    import asyncio
    import json
    import threading
    import time

    from game.maid_link import MaidLink

    PORT = 21499
    link = MaidLink(port=PORT)
    if not link.start():
        print("  [8] 跳过（端口 %d 不可用）" % PORT)
        return

    result = {"error": None, "ack": None}

    async def client():
        async with websockets.connect("ws://127.0.0.1:%d" % PORT) as ws:
            await ws.send(json.dumps({"type": "hello", "mod": "fake",
                                      "maids": [{"id": "m1", "name": "女仆"}]}))
            result["ack"] = json.loads(await ws.recv())
            await ws.send(json.dumps({
                "type": "item_index", "count": 2,
                "items": {"铁镐": "minecraft:iron_pickaxe",
                          "木棍": "minecraft:stick"}}))
            await ws.send(json.dumps({
                "type": "perception", "maid": "m1", "seq": 1, "tick": 1, "full": True,
                "snapshot": {
                    "self": {"pos": [10.5, 64.0, 10.5], "health": 20.0},
                    "owner": {"name": "Steve", "pos": [12, 64, 14]},
                    "inventory": {"slots": [
                        {"i": 0, "id": "minecraft:iron_ingot", "count": 5},
                        {"i": 1, "id": "minecraft:iron_ingot", "count": 2},
                        {"i": 2, "id": "minecraft:stick", "count": 1}]}}}))
            deadline = time.time() + 5
            while time.time() < deadline:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.0))
                except asyncio.TimeoutError:
                    continue
                if msg.get("type") == "command":
                    await ws.send(json.dumps({
                        "type": "command_result", "maid": "m1",
                        "reply": {"id": msg["id"], "ok": True, "state": "done",
                                  "step": "checked",
                                  "result": {"found": True, "craftable": True,
                                             "need": 1, "ingredients": []}}}))
                    result["cmd"] = msg
                    return

    def run_client():
        try:
            asyncio.run(client())
        except Exception as exc:  # pragma: no cover
            result["error"] = exc

    try:
        th = threading.Thread(target=run_client, daemon=True)
        th.start()
        # 等连接建立（最多 3s）
        deadline = time.time() + 3
        while not link.connected and time.time() < deadline:
            time.sleep(0.05)
        assert link.connected, "假模组未能连上 MaidLink"

        # 等 item_index / perception 到位
        deadline = time.time() + 3
        while (not link.item_index() or not link.inventory_counts()) \
                and time.time() < deadline:
            time.sleep(0.05)
        idx = link.item_index()
        assert idx.get("铁镐") == "minecraft:iron_pickaxe", "item_index 未落地: %s" % idx
        inv = link.inventory_counts()
        assert inv.get("minecraft:iron_ingot") == 7, "背包汇总错误: %s" % inv
        slots = link.inventory_slots()
        assert [s["i"] for s in slots] == [0, 1, 2], "背包槽位明细错误: %s" % slots
        assert slots[0]["id"] == "minecraft:iron_ingot"
        assert link.maid_pos() == (10, 64, 10), "女仆坐标错误: %s" % (link.maid_pos(),)
        assert link.owner_pos() == (12, 64, 14), "主人坐标错误: %s" % (link.owner_pos(),)

        # 下发指令并等回执（在另一个线程里做客户端响应）
        reply = link.query_craft("minecraft:iron_pickaxe", 1, timeout=4.0)
        th.join(timeout=3)
        assert result["error"] is None, "假模组异常: %s" % result["error"]
        assert reply is not None, "未收到 craft_check 回执（等回执链路失败）"
        assert reply.get("found") is True
        assert reply.get("craftable") is True
        assert result.get("cmd", {}).get("cmd") == "craft_check"
    finally:
        link.stop()
    print("  [8] WS 链路（物品索引 / 背包汇总 / 等回执）OK")


# ---------------------------------------------------------------- 9. 泄漏守卫

def test_no_leak():
    """训练语料必须与人工测试集零重合（极短命令豁免，见 tools/check_leak.py）。

    这道守卫是为了防一个真实踩过的坑：把测试句抄进训练语料，
    人工测试集准确率从 89.9% 虚报到 98.1%，产品毫无改善。
    """
    from nlu import hard_cases, synth
    path = os.path.join(BASE, "nlu", "data", "manual_test.json")
    if not os.path.isfile(path):
        print("  [9] 跳过（无 manual_test.json）")
        return
    with open(path, "r", encoding="utf-8") as f:
        tests = [c["text"].strip() for c in json.load(f) if c.get("text")]
    train = []
    for v in hard_cases.DISAMBIG.values():
        train += list(v)
    train += [t for t, _ in hard_cases.COMPOUND]
    for v in synth.TEMPLATES.values():
        train += list(v)
    cand = sorted({t.strip() for t in train if t and len(t.strip()) > 3})
    tset = set(tests)
    exact = [s for s in cand if s in tset]
    assert not exact, "训练语料与测试集逐字重合（数据泄漏）: %s" % exact
    near = []
    for s in cand:
        for t in tset:
            if len(t) >= 8 and s[:8] == t[:8]:
                near.append((s, t))
                break
    assert not near, "训练语料与测试集 8 字前缀重合（数据泄漏）: %s" % near
    print("  [9] 泄漏守卫 OK（%d 条候选，零重合）" % len(cand))


# ---------------------------------------------------------------- 10. 分层对账

# MaidTaskCommand javadoc 的权威分层（行 51-67）
MAID_INTEGRATED = frozenset({
    "attack", "guard", "feed", "eat", "mine", "farm", "build",
    "collect", "craft", "smelt",
})
MAID_BASIC = frozenset({
    "move", "look", "break", "place", "use",
    "equip", "store", "drop", "pickup", "sit", "stop",
})
MAID_META = frozenset({"cancel", "status"})


def test_layer_alignment():
    """taxonomy 与 mod_contract 的 layer 字段必须与 MaidTaskCommand javadoc 一致。

    防止「基础指令 vs 集成指令」概念在 taxonomy / mod_contract / router 之间漂移。
    """
    from nlu import taxonomy, mod_contract

    # ① taxonomy.layer 与 MaidTaskCommand javadoc 三层完全对齐
    for it in taxonomy.ALL:
        if it.cmd is None:
            continue  # chat 不产生指令，跳过
        if it.cmd in MAID_INTEGRATED:
            assert it.layer == "integrated", \
                "意图 %s 应是 integrated（MaidTaskCommand 集成段），实为 %s" % (it.key, it.layer)
        elif it.cmd in MAID_BASIC:
            assert it.layer == "basic", \
                "意图 %s 应是 basic（MaidTaskCommand 基础段），实为 %s" % (it.key, it.layer)
        elif it.cmd in MAID_META:
            assert it.layer == "meta", \
                "意图 %s 应是 meta，实为 %s" % (it.key, it.layer)
        else:
            # 桥接组合（transfer/chestopen/chestput/chesttake）—— javadoc 未显式列出，
            # 归到 integrated（组合多个基础原子 transfer）
            assert it.layer == "integrated", \
                "桥接指令 %s 应归 integrated（组合实现），实为 %s" % (it.cmd, it.layer)

    # ② mod_contract.layer 与 taxonomy.layer 必须一致（chat 例外：意图有、契约无）
    for it in taxonomy.ALL:
        if not it.cmd:
            continue
        c = mod_contract.get(it.cmd)
        assert c is not None, "意图 %s 的指令 %s 不在契约里" % (it.key, it.cmd)
        assert c.layer == it.layer, \
            "taxonomy 与 mod_contract 的 layer 不一致：%s（taxonomy=%s, contract=%s）" \
            % (it.cmd, it.layer, c.layer)

    # ③ craft_check 是桌宠侧 dryrun，不在 taxonomy（不是意图），在契约里是 meta-dryrun
    assert "craft_check" not in taxonomy.BY_KEY, "craft_check 不该是意图"
    assert mod_contract.layer_of("craft_check") == "meta-dryrun"

    # ④ MaidTaskCommand 的集成段（9）与基础段（11）必须在两份表里都齐
    #    （基础段的 pickup 是「契约有 / 意图无」，用 CONTRACT_ONLY 扣掉）
    cmd_intents = {it.cmd for it in taxonomy.ALL if it.cmd}
    assert MAID_INTEGRATED <= cmd_intents, "集成指令缺失: %s" % (MAID_INTEGRATED - cmd_intents)
    assert (MAID_BASIC - CONTRACT_ONLY) <= cmd_intents, \
        "基础指令缺失: %s" % (MAID_BASIC - CONTRACT_ONLY - cmd_intents)
    assert MAID_META <= cmd_intents, "元命令缺失: %s" % (MAID_META - cmd_intents)
    assert MAID_INTEGRATED <= mod_contract.LAYER_INTEGRATED
    assert MAID_BASIC <= mod_contract.LAYER_BASIC          # 契约侧仍要保留 pickup
    assert MAID_META <= mod_contract.LAYER_META
    assert CONTRACT_ONLY <= (set(mod_contract.COMMANDS) - cmd_intents), \
        "CONTRACT_ONLY 里的指令应当只在契约里: %s" % CONTRACT_ONLY

    # ⑤ router 分层派发覆盖所有会产生指令的意图
    # （不产生指令的 chat / craft_check 不进 _plan）
    for it in taxonomy.ALL:
        if not it.cmd:
            continue
        layer = taxonomy.layer_of(it.key)
        assert layer in ("basic", "integrated", "meta"), \
            "意图 %s 的 layer %s 不在三层里" % (it.key, layer)

    print("  [10] 分层对账 OK（basic=%d / integrated=%d / meta=%d / dryrun=%d）"
          % (len(mod_contract.LAYER_BASIC), len(mod_contract.LAYER_INTEGRATED),
             len(mod_contract.LAYER_META), len(mod_contract.LAYER_META_DRYRUN)))

    # ⑥ collect 半径规则（§11.1 规范①）：一片/附近 → 8，就近/单个 → 4
    from nlu import slots as _slots
    assert _slots.collect_radius("收集附近的东西") == 8
    assert _slots.collect_radius("把这一片掉落全扫进包") == 8
    assert _slots.collect_radius("把这个捡起来") == 4
    assert _slots.collect_radius("把地上那个盾拾起来") == 4
    assert _slots.extract("把这个捡起来", intent="collect").get("range") == 4
    assert _slots.extract("收集掉落物", intent="collect").get("range") == 8
    print("  [10b] collect 半径规则 OK（一片→8 / 就近→4）")


def test_script_contract():
    """Atomic Command Protocol：script 契约 + validate_script / script_clamp。"""
    from nlu import mod_contract

    # SCRIPT_COMMANDS 独立于 COMMANDS（不与意图对账）
    assert "script" in mod_contract.SCRIPT_COMMANDS
    assert "harvest" in mod_contract.SCRIPT_COMMANDS
    assert "inventory" in mod_contract.SCRIPT_COMMANDS
    assert "find" in mod_contract.SCRIPT_COMMANDS
    assert "script" not in mod_contract.COMMANDS, "script 不应进意图契约（避免破坏对账）"
    assert "harvest" not in mod_contract.COMMANDS

    # validate_script：合法脚本通过 + 清洗；未知指令拒绝
    ok, err = mod_contract.validate_script({
        "name": "砍树", "vars": {"target_count": 4},
        "steps": [
            {"cmd": "inventory", "params": {"item": "#axe"}, "as": "axe"},
            {"if": {"var": "axe.has", "op": "eq", "val": True},
             "then": [{"cmd": "equip", "params": {"item": "#axe"}}]},
            {"cmd": "find", "params": {"structure": "tree", "produce": "#minecraft:oak_logs",
                                       "range": 12, "max_range": 32, "expand": 4}, "as": "tree"},
            {"terminate": {"reason": "x"}},
        ],
    })
    assert ok is not None and err is None, err
    assert ok["steps"][2]["params"]["max_range"] == 32

    bad, err2 = mod_contract.validate_script({"steps": [{"cmd": "fly"}]})
    assert bad is None and err2, err2
    bad2, _ = mod_contract.validate_script({"steps": []})
    assert bad2 is None
    # loop 递归校验
    ok3, err3 = mod_contract.validate_script({
        "steps": [{"loop": {"while": {"var": "i", "op": "lt", "val": 3},
                            "body": [{"cmd": "assign", "params": {"x": 1}}]}}]})
    assert ok3 is None and err3, err3  # assign 是控制原语不是 cmd 指令，但缺 cmd 也算非法
    ok4, err4 = mod_contract.validate_script({
        "steps": [{"loop": {"while": {"var": "i", "op": "lt", "val": 3},
                            "body": [{"assign": "i", "expr": "$i + 1"}]}}]})
    assert ok4 is not None and err4 is None, err4
    print("  [11] script 契约 OK（SCRIPT_COMMANDS=%d，validate/递归/拒绝）"
          % len(mod_contract.SCRIPT_COMMANDS))


# ---------------------------------------------------------------- 入口

def main():
    print("NLU 回归测试")
    tests = [
        test_taxonomy, test_mod_contract, test_contract_clamp,
        test_synth, test_features, test_model_roundtrip,
        test_intent_quality, test_report_gate, test_slots,
        test_router_insufficient, test_router_sufficient,
        test_router_not_connected, test_router_no_recipe,
        test_router_chat_passthrough,
        test_plan_equip_with_slot, test_plan_drop_named_item,
        test_plan_transfer_derives_from, test_plan_chest_needs_opener,
        test_plan_relative_pos, test_plan_long_task_needs_abs_pos,
        test_router_never_raises,
        test_router_user_command_preempts,
        test_plan_attack_target,
        test_no_leak,
        test_layer_alignment,
        test_maid_link_roundtrip,
        test_script_contract,
    ]
    for fn in tests:
        fn()
    print("全部通过")


if __name__ == "__main__":
    main()
