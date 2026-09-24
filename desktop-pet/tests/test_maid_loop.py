# -*- coding: utf-8 -*-
"""通讯闭环测试（阶段一：官方 API 通道 / 双后端 ContextBuilder）。

无框架纯断言，风格同 test_reconstruction.py：
- MessagesBackend：messages 组装、ephemeral 不进历史、滑动窗口 + 提炼触发、提炼失败回退
- SessionBackend：网页版只发当轮 prompt（服务端自动记忆，客户端无历史）
- DeepSeekApiClient：请求体组装（messages/thinking/model）
- 记忆渲染 render_memory

运行：python tests/test_maid_loop.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai.context import (
    ChatContext, Turn, Fact, TransientContext, render_memory,
    CONDENSE_PROMPT,
)
from ai.backends.messages import MessagesBackend
from ai.backends.session import SessionBackend


# ---------- 1. MessagesBackend ----------

class _FakeMsgClient:
    """模拟官方 API / 千问：记录收到的 messages，返回固定回复。"""

    def __init__(self):
        self.calls = []

    def chat_messages(self, messages, thinking=False, model=None):
        self.calls.append({"messages": messages, "thinking": thinking,
                           "model": model})
        return "reply-%d" % len(self.calls)


def _build_backend(fake_client, condense=None):
    return MessagesBackend(fake_client, condense=condense)


def test_messages_build_and_ephemeral():
    """瞬时状态进当轮；ephemeral=True 不写回历史。"""
    fc = _FakeMsgClient()
    b = _build_backend(fc)
    ctx = ChatContext()
    tr = TransientContext(
        status_full="生命=18 任务=guard",
        events=["发现僵尸"],
        replies=["任务完成：attack"],
    )
    msgs = b.build(ctx, tr, "去打僵尸", system_prompt="你是女仆")
    assert msgs[0] == {"role": "system", "content": "你是女仆"}
    assert msgs[-1]["role"] == "user"
    assert "生命=18" in msgs[-1]["content"]
    assert "发现僵尸" in msgs[-1]["content"]
    assert "任务完成" in msgs[-1]["content"]
    assert "去打僵尸" in msgs[-1]["content"]

    # ephemeral：不写回历史
    b.append_history(ctx, "去打僵尸", "好的", ephemeral=True)
    assert len(ctx.history) == 0, "ephemeral 轮次不得进历史"
    # 非 ephemeral：写回
    b.append_history(ctx, "hello", "hi", ephemeral=False)
    assert len(ctx.history) == 1
    assert ctx.history[0] == Turn(user="hello", assistant="hi")
    print("[ok] MessagesBackend 组装 + ephemeral")


def test_messages_window_and_condense():
    """滑动窗口：发给 AI 的历史恒 ≤ WINDOW；超窗触发提炼。"""
    fc = _FakeMsgClient()
    condense_log = []

    def fake_condense(ctx, old_turns):
        condense_log.append(len(old_turns))
        ctx.summary = "提炼了%d轮" % len(old_turns)

    b = _build_backend(fc, condense=fake_condense)
    ctx = ChatContext()
    for i in range(35):
        tr = TransientContext()
        msgs = b.build(ctx, tr, "u%d" % i, system_prompt="SYS")
        # 发送的历史轮数 ≤ WINDOW（去掉 system + 当轮，每轮 2 条消息）
        hist_msgs = msgs[1:-1]
        assert len(hist_msgs) // 2 <= config.HISTORY_WINDOW, \
            (i, len(hist_msgs) // 2)
        b.append_history(ctx, "u%d" % i, "r%d" % i)

    assert len(ctx.history) <= config.CONDENSE_EVERY
    assert condense_log, "应触发至少一次提炼"
    assert ctx.summary.startswith("提炼了")
    print("[ok] MessagesBackend 滑动窗口 + 提炼触发（history≤%d）"
          % config.CONDENSE_EVERY)


def test_messages_condense_failure_rollback():
    """提炼失败：滑出的历史塞回，不丢对话。"""
    fc = _FakeMsgClient()

    def boom(ctx, old):
        raise RuntimeError("网络断")

    b = _build_backend(fc, condense=boom)
    ctx = ChatContext()
    for i in range(40):
        b.append_history(ctx, "u%d" % i, "r%d" % i)
    assert len(ctx.history) == 40, "提炼失败必须回退保留全部历史"
    print("[ok] MessagesBackend 提炼失败回退")


def test_messages_memory_render():
    """长期记忆渲染为 system 消息（恒定小）。"""
    ctx = ChatContext()
    ctx.memory = [Fact("偏好", "喜欢矿石"), Fact("约定", "叫主人")]
    ctx.summary = "关系友好"
    text = render_memory(ctx)
    assert "对话概述：关系友好" in text
    assert "偏好=喜欢矿石" in text
    assert "约定=叫主人" in text
    print("[ok] render_memory")


def test_condense_prompt_shape():
    """提炼 prompt 排除瞬时状态，只给「已有记忆 + 本次滑出对话」。"""
    assert "具体坐标" in CONDENSE_PROMPT
    assert "临时血量" in CONDENSE_PROMPT
    assert "{memory}" in CONDENSE_PROMPT
    assert "{turns}" in CONDENSE_PROMPT
    print("[ok] CONDENSE_PROMPT 约束")


# ---------- 2. SessionBackend ----------

class _FakeSessionClient:
    """模拟网页版 DeepSeekClient：只接收当轮 prompt，返回 (content, thinking)。"""

    def __init__(self):
        self.calls = []
        self.session_id = "s1"
        self.parent_message_id = 10

    def chat(self, prompt, system_prompt=None, model_type=None,
             thinking_enabled=False, search_enabled=False, memory=True,
             ref_file_ids=None):
        self.calls.append({"prompt": prompt, "system": system_prompt,
                           "thinking": thinking_enabled,
                           "search": search_enabled})
        return "网页版回复", ""


def test_session_backend_only_current_prompt():
    """网页版：只发当轮 prompt（含极短状态行），客户端不组历史、不提炼。"""
    fc = _FakeSessionClient()
    b = SessionBackend(fc)
    ctx = ChatContext()
    # 往 ctx.history 塞内容（网页版应无视它——历史在服务端）
    ctx.history.append(Turn(user="旧对话", assistant="旧回复"))
    tr = TransientContext(status_compact="[态] 生命18 任务guard")
    when = b.build(ctx, tr, "去打僵尸", system_prompt="你是女仆")
    # 返回的是当轮 prompt 字符串，不是 messages 数组
    assert isinstance(when, str)
    assert "旧对话" not in when, "网页版不得把本地历史拼进当轮"
    assert "[态] 生命18" in when
    assert "去打僵尸" in when
    content, _ = b.request(when, system_prompt="你是女仆")
    assert content == "网页版回复"
    assert fc.calls[0]["prompt"] == when
    # append_history 是无操作（历史在服务端）
    b.append_history(ctx, "x", "y", ephemeral=False)
    assert len(ctx.history) == 1, "SessionBackend 不维护本地历史"
    print("[ok] SessionBackend 只发当轮 prompt（服务端自动记忆）")


# ---------- 3. DeepSeekApiClient 请求体 ----------

def test_deepseek_api_request_body():
    """官方 API：请求体 = model + messages + stream=false。

    **模型清单已按实机 `/models` 校准**（官方 API 只认 deepseek-flash 与
    deepseek-v4-pro）；思考走 `reasoning_effort` 请求参数，不再是"换模型"。
    """
    from ai.deepseek_api import DeepSeekApiClient, CHAT_MODELS
    from ai import providers as prov

    # 清单单一事实来源 = 注册表
    assert CHAT_MODELS == list(prov.model_ids("deepseek-api"))
    assert CHAT_MODELS == ["deepseek-flash", "deepseek-v4-pro"], CHAT_MODELS

    # 旧 id 在官方 API 上仍可调用但会被**别名**到 deepseek-flash
    # （实机：响应里的 model 字段会变）→ 不该出现在清单，但必须能归一化
    for legacy in ("deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash"):
        assert legacy not in CHAT_MODELS, legacy
        assert prov.resolve_model("deepseek-api", legacy) == "deepseek-flash", legacy

    client = DeepSeekApiClient("sk-test", "deepseek-flash")
    msgs = [{"role": "user", "content": "hi"}]
    assert client.model == "deepseek-flash"
    # 只验证参数组装逻辑（不真实发请求）
    assert "reasoning_effort" not in client._build_body(msgs, False, None, False)
    body = client._build_body(msgs, "high", None, False)
    assert body["reasoning_effort"] == "high", body
    assert body["model"] == "deepseek-flash", body
    # 关思考时整字段省略（实机：省略即不推理）
    assert "reasoning_effort" not in client._build_body(msgs, "off", None, False)
    print("[ok] DeepSeekApiClient 模型清单（实机校准）+ 旧 id 归一化 + reasoning_effort")


# ---------- 4. 女仆 DSL 闭环（阶段二） ----------

def test_dsl_parse():
    """DSL 解析：正常 / 坐标 / ~ / 无括号 / 未知指令 / 缺参 / clamp。"""
    from game.maid_intent import parse, WHITELIST

    r, clean = parse("好的主人！{开心}【attack(range=12)】")
    assert r == {"cmd": "attack", "params": {"range": 12},
                 "cancel_previous": False}
    assert clean == "好的主人！{开心}"

    r, _ = parse("【mine(pos=[100,-60,-50], range=4, count=5, cancel_previous=true)】")
    assert r["params"]["pos"] == ["100", "-60", "-50"]
    assert r["cancel_previous"] is True

    r, _ = parse("【move(pos=[~,~,~])】")
    assert r["params"]["pos"] == ["~", "~", "~"]

    # 无括号指令（stop / status）
    r, clean = parse("【stop】好的")
    assert r["cmd"] == "stop" and r["params"] == {} and clean == "好的"

    # 未知指令 → 丢弃并清除原文
    r, clean = parse("【fly()】你好")
    assert r is None and clean == "你好"

    # 缺必填参数 → 丢弃
    r, _ = parse("【move()】")
    assert r is None

    # 畸形坐标 → 丢弃
    r, _ = parse("【move(pos=[1,2])】")
    assert r is None

    # clamp：未知参数丢弃、数值夹紧；target 现在是合法参数（指定目标实体）
    r, _ = parse("【attack(range=999, target=zombie)】")
    assert r["params"] == {"range": 64, "target": "zombie"}

    # 白名单不含 craft_check（桌宠内部查询）
    assert "craft_check" not in WHITELIST
    print("[ok] DSL 解析")


def test_dsl_script_parse():
    """Atomic Command Protocol：AI 回复夹带【script({...json...})】解析。"""
    from game.maid_intent import parse

    text = ('好的主人，我去砍点橡树 {开心}【script({"name":"砍橡树","vars":{"target_count":4},'
            '"steps":['
            '{"cmd":"inventory","params":{"item":"#axe"},"as":"axe"},'
            '{"if":{"var":"axe.has","op":"eq","val":true},"then":[{"cmd":"equip","params":{"item":"#axe"}}]},'
            '{"cmd":"find","params":{"structure":"tree","produce":"#minecraft:oak_logs",'
            '"range":12,"max_range":32,"expand":4},"as":"tree"},'
            '{"terminate":{"reason":"砍树完成"}}'
            ']})】')
    r, clean = parse(text)
    assert r is not None, "script 应解析成功"
    assert r["cmd"] == "script", r["cmd"]
    assert r["cancel_previous"] is True, "script 默认抢占女仆当前任务"
    assert clean.strip() == "好的主人，我去砍点橡树 {开心}", clean
    steps = r["params"]["steps"]
    assert len(steps) == 4, steps
    assert steps[0]["as"] == "axe"
    assert steps[2]["params"]["produce"] == "#minecraft:oak_logs"
    assert steps[2]["params"]["max_range"] == 32

    # 非法 script（未知指令 fly）→ 丢弃 + 清除原文，不崩溃
    r2, clean2 = parse('好了【script({"steps":[{"cmd":"fly"}]})】')
    assert r2 is None, r2
    assert "script" not in clean2, clean2

    # 空 steps → 丢弃
    r3, _ = parse('【script({"steps":[]})】')
    assert r3 is None, r3

    # 非 JSON → 丢弃
    r4, _ = parse('【script(不是json)】')
    assert r4 is None, r4
    print("[ok] DSL script 解析（JSON/校验/默认抢占/非法丢弃）")


def test_maid_loop_execute_and_inject():
    """MaidLoop：DSL 下发（默认排队）、cancel_previous 透传、回执注入、未连接降级。"""
    from game.maid_loop import MaidLoop

    class FakeLink:
        def __init__(self):
            self.sent = []
            self.connected_flag = True

        @property
        def connected(self):
            return self.connected_flag

        def request(self, cmd, params, cancel_previous=False, timeout=5.0):
            self.sent.append((cmd, params, cancel_previous))
            return {"ok": True, "state": "running", "result": {}}

        def status_line(self, maid=""):
            return "生命=18 任务=guard"

    link = FakeLink()
    loop = MaidLoop(link)

    clean, inject = loop.process_reply("好的{开心}【attack(range=12)】")
    assert clean == "好的{开心}"
    assert "attack" in inject
    assert link.sent == [("attack", {"range": 12}, False)]  # 默认排队

    # cancel_previous 透传
    link.sent = []
    loop.process_reply("【guard(range=10, cancel_previous=true)】")
    assert link.sent[0][2] is True

    # 未连接 → 降级（指令丢弃，无注入，不报错）
    link.connected_flag = False
    clean, inject = loop.process_reply("【stop】好的")
    assert inject == "" and clean == "好的"
    link.connected_flag = True

    # 失败回执 → 注入失败信息
    class FailLink(FakeLink):
        def request(self, cmd, params, cancel_previous=False, timeout=5.0):
            return {"ok": False, "result": {"error": "女仆正忙"}}

    loop2 = MaidLoop(FailLink())
    _, inject = loop2.process_reply("【mine(pos=[1,2,3])】")
    assert "失败" in inject and "正忙" in inject

    # drain_replies 消费
    got = loop2.drain_replies()
    assert got and not loop2.peek_replies()
    print("[ok] MaidLoop 执行 + 回执注入")


# ---------- 5. 女仆状态注入 + AI 决策（阶段三） ----------

def _sample_snap():
    return {
        "self": {"health": 18.0, "max_health": 20.0, "pos": [100, 64, -50],
                 "status": {"task_id": "guard",
                            "target": {"type": "minecraft:zombie"}},
                 "equipment": {"mainhand": {"id": "minecraft:iron_sword"}}},
        "nearby": {"hostiles": [
            {"type": "minecraft:zombie", "dist": 3, "targeting_me": True},
            {"type": "minecraft:skeleton", "dist": 8}]},
        "inventory": {"capabilities": {"has_sword": True, "has_food": True,
                                       "has_pickaxe": False}},
        "owner": {"name": "Steve"},
    }


def test_maid_context_build():
    """快照 → TransientContext：完整/极短两种形态 + 渲染到当轮。"""
    from game.maid_context import build_transient, _compact_status

    tr = build_transient(_sample_snap(), events=["发现僵尸"],
                         replies=["[系统] 任务完成"])
    assert "生命=18.0/20.0" in tr.status_full
    assert "任务=guard" in tr.status_full
    assert "敌对=僵尸(3格，在攻击我)、骷髅(8格)" in tr.status_full
    assert "装备=has_sword、has_food" in tr.status_full
    assert tr.status_compact == "[态] 生命18.0 任务guard"
    assert tr.events == ["发现僵尸"] and tr.replies == ["[系统] 任务完成"]

    msg = tr.render_messages_backend("主人小心！")
    assert "你当前状态" in msg and "发现僵尸" in msg and "任务完成" in msg
    assert "主人小心！" in msg
    print("[ok] 女仆状态注入（完整/极短）")


def test_maid_context_danger():
    """AI 决策判定：低血(<30%) / 被围(3+敌对 10 格内)。"""
    from game.maid_context import evaluate_danger

    # 健康 + 2 敌 → 无危险
    low, surr = evaluate_danger(_sample_snap())
    assert low is False and surr is False

    # 低血
    low, _ = evaluate_danger({"self": {"health": 5.0, "max_health": 20.0}})
    assert low is True
    # 无 max_health 时按绝对阈值 6
    low, _ = evaluate_danger({"self": {"health": 5.0}})
    assert low is True

    # 被围（≤10 格的敌对 ≥3）
    _, surr = evaluate_danger({"self": {"health": 15.0},
                               "nearby": {"hostiles": [
                                   {"dist": 2}, {"dist": 5},
                                   {"dist": 9}, {"dist": 15}]}})
    assert surr is True
    # 远敌不算
    _, surr = evaluate_danger({"self": {"health": 15.0},
                               "nearby": {"hostiles": [
                                   {"dist": 12}, {"dist": 15}, {"dist": 20}]}})
    assert surr is False
    print("[ok] AI 决策判定（低血/被围）")


# ---------- 6. 限流 / 去重 / 排队（阶段四） ----------

def test_ai_throttle():
    """AICallThrottle：30s≤5，超出拒绝；reset 恢复。"""
    from core.ai_throttle import AICallThrottle

    t = AICallThrottle()
    now = 1000.0
    res = [t.allow(now + i) for i in range(6)]
    assert res == [True, True, True, True, True, False], res
    t.reset()
    assert t.allow(now + 1) is True
    print("[ok] AICallThrottle（30s≤5）")


def test_event_dedup():
    """通道去重：同指纹 60s 内去重；任务边界不参与。"""
    from core.event_dedup import EventDedup, is_task_boundary

    d = EventDedup(ttl=60)
    now = 100.0
    assert d.check("女仆被僵尸攻击", now) is True
    assert d.check("女仆被僵尸攻击", now + 10) is False
    assert d.check("女仆被僵尸攻击", now + 70) is True
    assert d.check("女仆发现僵尸", now + 80) is True
    assert is_task_boundary("女仆开始挖矿了") is True
    assert is_task_boundary("女仆被僵尸攻击") is False
    print("[ok] EventDedup（60s 去重 + 任务边界豁免）")


def test_default_cancel_previous_false():
    """AI 指令默认排队（cancel_previous=False）。"""
    from game.maid_loop import MaidLoop

    class FakeLink:
        def __init__(self):
            self.sent = []

        @property
        def connected(self):
            return True

        def request(self, cmd, params, cancel_previous=False, timeout=5.0):
            self.sent.append((cmd, params, cancel_previous))
            return {"ok": True, "state": "running"}

        def status_line(self, maid=""):
            return ""

    link = FakeLink()
    loop = MaidLoop(link)
    loop.process_reply("好的【attack(range=10)】")
    assert link.sent[0][2] is False, "AI 指令默认排队"
    print("[ok] cancel_previous 默认排队")


# ---------- 主入口 ----------

if __name__ == "__main__":
    tests = [
        test_messages_build_and_ephemeral,
        test_messages_window_and_condense,
        test_messages_condense_failure_rollback,
        test_messages_memory_render,
        test_condense_prompt_shape,
        test_session_backend_only_current_prompt,
        test_deepseek_api_request_body,
        test_dsl_parse,
        test_dsl_script_parse,
        test_maid_loop_execute_and_inject,
        test_maid_context_build,
        test_maid_context_danger,
        test_ai_throttle,
        test_event_dedup,
        test_default_cancel_previous_false,
    ]
    for fn in tests:
        fn()
    print("\n全部测试通过（%d 项）" % len(tests))
