# -*- coding: utf-8 -*-
"""桌宠 AI 链路单元测试（L0）。

无框架纯断言，风格同 test_maid_loop.py。覆盖全链路测试方案 §四 L0 缺口：
- U1  _ChatWorker._prepare_prompt 组装顺序（模式声明/女仆上下文/NLU 前置）
- U2  _apply_dsl 三分支（有 loop / 无 loop / 异常）
- U3  _upload_images 降级（全失败 / 部分失败 / 无图）
- U4  make_client 路由（网页版 Cookie 会话 / OpenAI 兼容适配器 / 旧 id 归一化）
- U5  PetMessageQueue 调度（优先级 / 相邻去重 / 上限 / 忙时不派发）
- U6  _run_plain 全流程（AI 回复含 DSL → 下发 → 回执注入 → finished）
- U7  NLU 已执行 → 抑制 AI 冲突 DSL
- U8  NLU 未执行 → AI 兜底 DSL 正常
- U9  流式剥离守卫 TagStripper（P3：未闭合不外泄 / 跨片补齐 / `~` 不碎成可朗读片段）

运行：python tests/test_chat_unit.py
"""

import re
import sys
import os
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai.chat import _ChatWorker
import ai.client as client_mod
from core.message_queue import PetMessageQueue
from game.maid_loop import MaidLoop


# ---------------- 公共 Fake ----------------

class _FakeClient:
    """模拟非流式后端（网页版/官方 API）：记录调用，返回固定回复元组。"""

    def __init__(self, reply="好的", uploads=None):
        self.reply = reply
        self.calls = []
        self.uploads = uploads or {}   # path -> file_id；带异常则 raise

    def chat(self, prompt, **kw):
        self.calls.append((prompt, kw))
        return self.reply, ""   # (content, thinking) 与真实后端一致

    def chat_message(self, messages, thinking=False, model=None, tools=None):
        """与真实适配器一致（P1-1）：返回无 tool_calls 的纯文本 message。"""
        self.calls.append((messages, {"thinking": thinking,
                                      "tools": tools is not None}))
        return {"role": "assistant", "content": self.reply, "tool_calls": None}

    def upload_image_file(self, path):
        v = self.uploads.get(path)
        if isinstance(v, Exception):
            raise v
        return v


class _FakeLink:
    """模拟女仆 WS 链路：connected + request + status_line。"""

    def __init__(self):
        self.sent = []
        self.connected = True

    def request(self, cmd, params, cancel_previous=False, timeout=5.0):
        self.sent.append((cmd, params, cancel_previous))
        return {"ok": True, "state": "running", "result": {}}

    def status_line(self, maid=""):
        return "生命=18 任务=guard"


def _opts():
    return {
        "ai_mode": 1,
        "prompt": "你是女仆",
        "model": config.AI_MODEL_TYPE,
        "thinking": False,
        "search": False,
        "memory": True,
        "qwen_model": "qwen-turbo",
    }


def _nlupre(text):
    return "【本地预处理】意图：attack（置信度 0.99）"


# ---------------- U1 prompt 组装 ----------------

def test_prepare_prompt_order():
    """模式声明/女仆上下文/NLU 前置的组装顺序与内容。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    # 先产生一条回执（[系统] 行）
    loop.process_reply("好的【attack(range=10)】")
    opts = _opts()
    opts["backend"] = "deepseek"   # 网页版才注入模式声明
    worker = _ChatWorker(_FakeClient(), "去打僵尸", opts,
                         preprocess=_nlupre, maid_loop=loop)
    prompt = worker._prepare_prompt()

    assert "现在切换到 1、日常" in prompt, "缺模式声明"
    assert "[态] 生命=18 任务=guard" in prompt, "缺女仆实时状态"
    assert "[系统] 我的任务已受理：attack" in prompt, "缺指令回执注入"
    assert "【本地预处理】" in prompt, "缺 NLU 前置"
    assert "去打僵尸" in prompt, "缺用户消息"
    # 顺序：NLU 前置最前 → 女仆上下文 → 模式声明 → 用户消息
    idx = [prompt.index(s) for s in
           ("【本地预处理】", "[态]", "现在切换到", "去打僵尸")]
    assert idx == sorted(idx), "组装顺序错: %s" % prompt
    print("  [U1] prompt 组装顺序 OK")


# ---------------- U2 _apply_dsl 三分支 ----------------

def test_apply_dsl_with_loop():
    link = _FakeLink()
    loop = MaidLoop(link)
    worker = _ChatWorker(_FakeClient(), "x", _opts(), maid_loop=loop)
    clean, inject = worker._apply_dsl("好的{开心}【attack(range=12)】")
    assert clean == "好的{开心}", clean
    assert "attack" in inject
    assert link.sent == [("attack", {"range": 12}, False)], "默认应排队"
    print("  [U2a] 有 loop → 下发 + 回执 OK")


def test_apply_dsl_without_loop():
    worker = _ChatWorker(_FakeClient(), "x", _opts(), maid_loop=None)
    content = "好的【attack(range=1)】"
    clean, inject = worker._apply_dsl(content)
    assert clean == content and inject == ""
    print("  [U2b] 无 loop → 原样返回 OK")


def test_apply_dsl_exception():
    class BoomLoop:
        def process_reply(self, text):
            raise RuntimeError("boom")
    worker = _ChatWorker(_FakeClient(), "x", _opts(), maid_loop=BoomLoop())
    content = "好的【attack(range=1)】"
    clean, inject = worker._apply_dsl(content)
    assert clean == content and inject == ""
    print("  [U2c] loop 异常 → 原样不抛 OK")


# ---------------- U3 图片上传降级 ----------------

def test_upload_images_all_fail():
    got = []
    client = _FakeClient(uploads={"a.png": RuntimeError("上传失败"),
                                  "b.png": RuntimeError("上传失败")})
    worker = _ChatWorker(client, "x", _opts(), images=["a.png", "b.png"])
    worker.finished.connect(lambda text, err: got.append((text, err)))
    r = worker._upload_images()
    assert r is None, "全失败应返回 None"
    assert got and got[0][1] is True, "应发错误信号"
    assert "上传失败" in got[0][0]
    print("  [U3a] 全失败 → 错误信号 OK")


def test_upload_images_partial_fail():
    client = _FakeClient(uploads={"a.png": RuntimeError("x"),
                                  "b.png": "file-b"})
    worker = _ChatWorker(client, "x", _opts(), images=["a.png", "b.png"])
    r = worker._upload_images()
    assert r == ["file-b"], "部分失败应返回成功部分"
    assert worker.status_hint and "1 张图片上传失败" in worker.status_hint
    print("  [U3b] 部分失败 → 跳过失败项 OK")


def test_upload_images_none():
    worker = _ChatWorker(_FakeClient(), "x", _opts(), images=[])
    assert worker._upload_images() == []
    print("  [U3c] 无图 → 空 OK")


# ---------------- U4 make_client 路由（注册表 + 协议适配器） ----------------

class _Rec:
    def __init__(self, kind):
        self.kind = kind


def test_make_client_router():
    """网页版经**扩展点工厂**造；其余由协议工厂造 —— 断言按 `provider_id` /
    `protocol` / `base_url`，不依赖 `ai.deepseek` 模块是否存在。
    """
    from ai import providers as prov
    import plugin.host as host

    saved_bn = client_mod.backend_name
    orig_reg = host.registry
    orig_cache = getattr(host, "_registry", None)

    class _FakeReg:
        def adapter_factory(self, protocol):
            if protocol == prov.PROTO_DEEPSEEK_WEB:
                return lambda pid, prof: _Rec("deepseek-web")
            return None

    host._registry = None
    host.registry = lambda: _FakeReg()
    try:
        # qwen → OpenAI 兼容适配器
        client_mod.backend_name = lambda: "qwen"
        c1 = client_mod.make_client()
        assert c1.provider_id == "qwen", c1.provider_id
        assert c1.protocol == prov.PROTO_OPENAI, c1.protocol
        assert c1.base_url == prov.profile("qwen").api, c1.base_url

        # deepseek-api → OpenAI 兼容适配器
        client_mod.backend_name = lambda: "deepseek-api"
        c2 = client_mod.make_client()
        assert c2.provider_id == "deepseek-api", c2.provider_id
        assert c2.base_url == prov.profile("deepseek-api").api, c2.base_url

        if not prov.exists("deepseek-web"):
            print("  [U4] make_client 路由（无网页版插件：仅 OpenAI 兼容）OK")
            return

        # 网页版：经扩展点工厂造（Cookie 会话客户端由插件提供）
        client_mod.backend_name = lambda: "deepseek-web"
        assert client_mod.make_client().kind == "deepseek-web"

        # 旧 id 归一化：`deepseek` 是网页版的历史名字，**不能**被当官方 API
        client_mod.backend_name = lambda: "deepseek"
        assert client_mod.make_client().kind == "deepseek-web"
    finally:
        client_mod.backend_name = saved_bn
        host.registry = orig_reg
        host._registry = orig_cache
    print("  [U4] make_client 路由（网页版 / OpenAI 兼容 / 旧 id 归一化）OK")


# ---------------- U7 流式剥离守卫（P3） ----------------

def test_stream_strip_guards():
    """[P3 守卫] TagStripper 的关键性质 + 缺陷复现防护（DESIGN_AI_PROVIDERS.md §11.2）。

    * 缺陷 1：未闭合标签原先会外泄到显示层（玩家看到 "好的{开" 再消失）
    * 缺陷 2：原实现"先切句再剥离"，而切句正则把 `~` 当句末终止符，
      偏偏 DSL 相对坐标就用 `~` → `【move(pos=~,~,~)】` 被切成 4 段、
      每段缺 `】` 剥离失败 → **逐段朗读指令**
    """
    from ai.stream_strip import (KIND_ACTION, KIND_DSL, TagStripper,
                                 split_speakable, strip_tags_incremental)

    # ① 未闭合的标签不得外泄
    s = TagStripper()
    safe, tags = s.feed("好的{开")
    assert safe == "好的", safe
    assert tags == [], tags
    assert s.pending == 2, s.pending   # 被压住的是 "{开" 两个字符

    # ② 跨 chunk 的动作标签补齐后整体吞掉
    safe2, tags2 = s.feed("心}呢~")
    assert safe2 == "呢~", safe2
    assert [t.inner for t in tags2] == ["开心"], tags2
    assert tags2[0].kind == KIND_ACTION

    # ③ ★核心守卫：DSL 的 `~` 相对坐标不得被切成可朗读碎片
    text = "好嘞【move(pos=~,~,~)】马上到~"
    s2 = TagStripper()
    safe3, tags3 = s2.feed(text)
    assert [t.inner for t in tags3] == ["move(pos=~,~,~)"], tags3
    assert tags3[0].kind == KIND_DSL
    assert "【" not in safe3 and "move" not in safe3, safe3
    complete, tail = split_speakable(safe3)
    for seg in complete + [tail]:
        assert "【" not in seg and "move" not in seg, seg
    # 反证：旧的"先切句再剥离"确实会切出含指令的片段
    naive = [p for p in re.split(r"(?<=[。！？!?…~；;])", text) if p.strip()]
    assert any("move" in p for p in naive), "旧实现应当可复现该缺陷"

    # ④ 逐字符喂（最坏分片）也不得漏
    s3 = TagStripper()
    got, gtags = "", []
    for ch in "嗯{害羞}【attack(range=10)】好":
        a, b = s3.feed(ch)
        got += a
        gtags += b
    assert got == "嗯好", got
    assert [t.inner for t in gtags] == ["害羞", "attack(range=10)"], gtags

    # ⑤ flush 放行残余（防止"最后一个标签之后的正文"被吞）
    s4 = TagStripper()
    a4, _ = s4.feed("正文【未闭合")
    assert a4 == "正文", a4
    rest, _ = s4.flush()
    assert rest == "【未闭合", rest

    # ⑥ 超时兜底：卡住超过阈值就按普通文本放行，不能把界面冻住
    clock = [0.0]
    s5 = TagStripper(clock=lambda: clock[0])
    a5, _ = s5.feed("文本【abc")
    assert a5 == "文本", a5
    clock[0] = 1.0
    a6, _ = s5.feed("")
    assert a6 == "【abc", a6

    # ⑦ 一次性剥离与非流式语义一致（旧式 [] 标签也认）
    safe7, tags7 = strip_tags_incremental("嗨[开心]呀")
    assert safe7 == "嗨呀" and [x.inner for x in tags7] == ["开心"], (safe7, tags7)

    print("  [U9] 流式剥离守卫（未闭合不外泄 / 跨片补齐 / ~ 不碎 / flush / 超时）OK")


# ---------------- U5 PetMessageQueue ----------------

class _Pet:
    def __init__(self):
        self.rendered = []
        self.idle = True

    def _render_idle(self):
        return self.idle

    def _render_message(self, text, error=False, speak=False):
        self.rendered.append((text, error, speak))


def test_message_queue_priority():
    pet = _Pet()
    q = PetMessageQueue(pet)
    q.post("低优先级", kind="chatline", priority=10)
    q.post("高优先级", kind="ai_reply", priority=30)
    q.post("中优先级", kind="ai_reply", priority=20)
    assert q.pending_count() == 3
    q._maybe_dispatch()
    assert pet.rendered == [("高优先级", False, False)], \
        "应优先派发最高优先级: %s" % (pet.rendered,)
    print("  [U5a] 优先级插队 OK")


def test_message_queue_dedup_and_busy():
    pet = _Pet()
    q = PetMessageQueue(pet)
    q.post("重复", kind="ai_reply", priority=30)
    q.post("重复", kind="ai_reply", priority=30)   # 相邻同文本 → 丢弃
    q.post("重复", kind="ai_reply", priority=30, force=True)   # force 跳过去重
    assert q.pending_count() == 2, "相邻去重失效"

    pet.idle = False
    before = q.pending_count()
    q._maybe_dispatch()
    assert q.pending_count() == before, "渲染忙时不应派发"
    pet.idle = True
    q._maybe_dispatch()
    assert pet.rendered, "空闲后应派发"
    print("  [U5b] 相邻去重 + 忙时不派发 OK")


def test_message_queue_cap():
    pet = _Pet()
    q = PetMessageQueue(pet)
    # 填满 + 1 条最低优先级，验证最低优先级被挤出
    for i in range(config.MSG_QUEUE_MAX + 3):
        q.post("msg%d" % i, kind="ai_reply",
               priority=10 + (i % 50))
    assert q.pending_count() <= config.MSG_QUEUE_MAX
    lowest = [i for i in range(config.MSG_QUEUE_MAX + 3)
              if "msg%d" % i not in [p[2][0] for p in q._items]]
    assert len(lowest) >= 3, "上限应丢最低优先级"
    print("  [U5c] 队列上限丢最低优先级 OK")


# ---------------- U6 _run_plain 全流程 ----------------

def test_run_plain_full_flow():
    link = _FakeLink()
    loop = MaidLoop(link)
    client = _FakeClient(reply="好的主人！{开心}【attack(range=12)】")
    got = []
    opts = _opts()
    opts["backend"] = "deepseek"   # 网页版全流程（含模式声明）
    worker = _ChatWorker(client, "去打僵尸", opts,
                         preprocess=_nlupre, maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()

    assert got and got[0][1] is False, "正常回复不应标错误"
    assert got[0][0] == "好的主人！{开心}", "DSL 应被剥离: %s" % got
    assert "attack" in worker.dsl_inject, "回执应写入 dsl_inject"
    assert link.sent == [("attack", {"range": 12}, False)], "应下发 attack"
    assert client.calls, "应调用后端"
    # 调用时传了模式声明 + NLU 前置后的完整 prompt
    assert "现在切换到 1、日常" in client.calls[0][0]
    assert "【本地预处理】" in client.calls[0][0]
    print("  [U6] _run_plain 全流程 OK")


def test_prepare_prompt_api_backend_no_decl():
    """官方 API / 千问后端：user 消息不再塞「现在切换到 x、模式名」。

    该后端无状态、system 每轮重发，当前模式写进 system（见 config.get_api_prompt /
    MessagesBackend.build），user 消息里重复声明只会污染 history 并被反复重放。
    """
    opts = _opts()
    opts["backend"] = "deepseek-api"
    worker = _ChatWorker(_FakeClient(), "去打僵尸", opts)
    prompt = worker._prepare_prompt()
    assert "现在切换到" not in prompt, "API 后端不应在 user 消息里塞模式声明"
    assert prompt == "去打僵尸", prompt
    print("  [U1b] API 后端不注入模式声明 OK")


def test_system_prompt_by_backend():
    """system 按后端分流：API / 千问用精简人设，网页版用全长人设。"""
    api_opts = _opts()
    api_opts["backend"] = "deepseek-api"
    api_opts["persona"] = "maid"
    api_opts["pet_name"] = "大肥鱼"
    api_worker = _ChatWorker(_FakeClient(), "x", api_opts)
    sys_api = api_worker._system_prompt()
    assert "【当前模式】" in sys_api, "API 版 system 应带当前模式"
    assert "模式1（日常模式）" in sys_api, sys_api[:200]

    web_opts = _opts()
    web_opts["backend"] = "deepseek"
    web_worker = _ChatWorker(_FakeClient(), "x", web_opts)
    assert web_worker._system_prompt() == web_opts["prompt"], "网页版应原样用 opts['prompt']"

    # 精简版确实比全长版短（省 token）
    full = config.load_system_prompt("maid", "大肥鱼", backend="deepseek")
    lite = config.load_system_prompt("maid", "大肥鱼", backend="deepseek-api")
    assert len(lite) < len(full) * 0.8, (len(lite), len(full))
    assert "女仆指令" in lite, "精简版必须保留女仆指令（AI-DSL 闭环）"
    print("  [U1c] system 按后端分流 OK（全长 %d 字 → 精简 %d 字）" % (len(full), len(lite)))


def test_api_prompt_global_maid_and_modes():
    """精简人设的「功能契约」回归：全域女仆指令 + 模式差异。

    这两点都是 2.20.0 精修时**特意加回**的（此前精简把「女仆指令」写成游戏模式限定、
    并且没有模式差异说明）。后续再压 token 时，很容易顺手删掉这些"看起来像废话"的句子，
    但它们是功能性的 —— 删了 AI 在非游戏模式下就感知不到女仆、工作模式就又开始撒娇。
    所以这里逐条钉住。
    """
    for persona, others in (("maid", "白饭"), ("mikoto", "甜食")):
        lite = config.get_api_prompt(persona=persona, mode_id=1)
        # 1) 女仆指令必须全域可用（不再写「游戏模式」限定）
        assert "女仆指令" in lite
        assert "全域可用" in lite, "%s：女仆指令应标明全域可用" % persona
        assert "不限游戏模式" in lite, "%s：应显式解除游戏模式限定" % persona
        # 2) 必须给出跨模式指令范例
        assert "女仆指令范例" in lite, "%s：应有指令范例" % persona
        assert "【status】" in lite and "【attack(range=10)】" in lite
        # 3) 模式差异段
        assert "模式差异" in lite, "%s：应有模式差异段" % persona
        for tag in ("模式1", "模式2", "模式3"):
            assert tag in lite, "%s：模式差异缺 %s" % (persona, tag)
        # 4) 工作模式明确不许带人设梗（each persona 的招牌梗）
        assert others in lite, "%s：招牌梗 %s 应保留在设定里" % (persona, others)
        # 5) 模式差异落在 system 的模式段里（get_api_prompt 会拼上当前模式）
        m2 = config.get_api_prompt(persona=persona, mode_id=2)
        assert "模式2（工作模式）" in m2
        assert "工作模式" in m2

    # 6) 模式段随 mode_id 变化（同一人设，不同模式 system 不同）。
    #    注意：人设正文里本来就有一段通用「模式差异」会提到"游戏模式"，
    #    所以这里只比【当前模式】之后的模式段，不拿整篇比。
    a = config.get_api_prompt(persona="maid", mode_id=1)
    b = config.get_api_prompt(persona="maid", mode_id=3)
    seg_a = a.split("【当前模式】", 1)[1]
    seg_b = b.split("【当前模式】", 1)[1]
    assert seg_a != seg_b, "不同模式的 system 模式段应不同"
    assert "当前处于模式1（日常模式）" in seg_a
    assert "当前处于模式3（游戏模式）" in seg_b
    assert "游戏模式" not in seg_a, "日常模式的模式段不应出现游戏模式规则"

    # 7) get_api_prompt 必须恒取精简版（不受"当前连的后端"影响）
    lite_direct = config.load_system_prompt("maid", backend=True)
    assert "女仆指令" in lite_direct and len(lite_direct) < 6000
    full_web = config.load_system_prompt("maid", backend="deepseek")
    assert len(lite_direct) < len(full_web), (len(lite_direct), len(full_web))
    print("  [U1d] 精简人设：全域女仆指令 + 模式差异 OK")


def test_prompt_min_style():
    """极简版（标签式）人设：token 最省，但功能契约完整（输出格式 + 指令清单）。

    守卫 2.28.0 的新档位：
    * 极简档确实比精简档再省一大截（省 token）；
    * 极简版必须仍带完整指令清单与参数（AI 不知道指令就没法用，这是硬功能）；
    * 档位校验：网页版只允许 full/min，无状态后端只允许 slim/min；
    * 名字 / 动作词替换对标签式写法（NAME / 「/」分隔动作词）同样生效。
    """
    # 1) 极简档确实比精简档短（省 token）
    slim = config.get_api_prompt("maid", 1, "大肥鱼", style="slim")
    mini = config.get_api_prompt("maid", 1, "大肥鱼", style="min")
    assert len(mini) < len(slim) * 0.5, (len(mini), len(slim))
    # 2) 极简版带标签头 + 名字行
    assert "[PERSONA_LOAD]" in mini
    assert "NAME 大肥鱼" in mini
    # 3) 功能契约：输出格式硬规则 + **完整指令清单与用法**
    assert "{动作}" in mini, "极简版必须保留 {动作} 规则（驱动动画）"
    for cmd in ("attack", "move", "mine", "build", "equip", "chestopen",
                "craft", "smelt", "feed", "status", "cancel"):
        assert cmd in mini, "极简版漏了指令 %s" % cmd
    assert "minecraft:pig" in mini, "极简版必须给 target 用法（英文实体 id）"
    assert "attack" in mini and "guard" in mini, "极简版必须保留自动战斗规则用词"
    # 4) 模式段随 mode_id（get_api_prompt 仍拼当前模式）
    m3 = config.get_api_prompt("maid", 3, "大肥鱼", style="min")
    assert "当前处于模式3（游戏模式）" in m3
    # 5) 名字替换：改宠物名后极简版 NAME 行跟着变
    renamed = config.load_system_prompt("maid", "小鱼", backend="deepseek-api",
                                        style="min")
    assert "NAME 小鱼" in renamed, renamed[:200]
    # 6) 档位校验：网页版不允许 slim，无状态不允许 full；min 两类都可用
    assert config.sanitize_prompt_style("slim", "deepseek") == "full"
    assert config.sanitize_prompt_style("full", "deepseek-api") == "slim"
    assert config.sanitize_prompt_style("min", "deepseek") == "min"
    assert config.sanitize_prompt_style("min", "deepseek-api") == "min"
    # 7) 人格文件都有极简版（mikoto 也接上）
    mikoto_min = config.get_api_prompt("mikoto", 1, "御坂美琴", style="min")
    assert "[PERSONA_LOAD]" in mikoto_min and "NAME 御坂美琴" in mikoto_min
    print("  [U1e] 极简人设（标签式，token 最省 + 功能契约完整）OK "
          "(精简 %d 字 → 极简 %d 字)" % (len(slim), len(mini)))


def test_prompt_min_matrix():
    """极简档组合矩阵：人格 × 后端 × 档位 → 文件选择与关键内容正确。

    覆盖面：maid / mikoto 两个人格 × 网页版 / 官方 API × full / slim / min 三档，
    防止「只给 maid 接上极简、mikoto 或某后端回退错了」这类接线问题。
    """
    cases = [
        # (persona, backend, style, 应含关键标记)
        ("maid",  "deepseek",     "full", "情绪理论"),    # 网页版全长
        ("maid",  "deepseek",     "min",  "[PERSONA_LOAD]"),
        ("maid",  "deepseek-api", "slim", "女仆指令"),     # API 精简
        ("maid",  "deepseek-api", "min",  "[PERSONA_LOAD]"),
        ("mikoto", "deepseek",    "min",  "[PERSONA_LOAD]"),
        ("mikoto", "deepseek-api", "slim", "正义感"),
        ("mikoto", "deepseek-api", "min",  "[ANGER]"),    # 御坂美琴极简保留爆粗分级
    ]
    for persona, backend, style, mark in cases:
        t = config.load_system_prompt(persona, persona, backend=backend, style=style)
        assert mark in t, "%s/%s/%s: 缺标记「%s」" % (persona, backend, style, mark)
        if style == "min":
            assert "NAME " + persona in t, "极简档 NAME 行应带人格默认名"

    # 网页版极简走 load_system_prompt，模式声明走 user 消息，不应自带【当前模式】
    web_min = config.load_system_prompt("maid", "大肥鱼", backend="deepseek",
                                        style="min")
    assert "【当前模式】" not in web_min, "网页版极简不应自带【当前模式】节"
    print("  [U1f] 极简档组合矩阵（maid/mikoto × web/api × full/slim/min）OK")


def test_prompt_min_instruction_parity():
    """极简档指令清单必须与精简版**逐条对齐**（AI 不知道指令就没法用）。

    精简版是功能基准（26 条指令全在），极简版若漏一条，AI 在对应任务上就
    只能瞎编 —— 这里把两档的指令名集合直接比较，缺了就立刻报。
    """
    cmds = ["attack", "guard", "stop", "move", "look", "pickup", "collect",
            "mine", "farm", "build", "place", "break", "use", "equip", "store",
            "drop", "transfer", "chestopen", "chestput", "chesttake", "craft",
            "smelt", "feed", "sit", "status", "cancel"]
    for persona in ("maid", "mikoto"):
        slim = config.get_api_prompt(persona, 3, "大肥鱼", style="slim")
        mini = config.get_api_prompt(persona, 3, "大肥鱼", style="min")
        missing = [c for c in cmds if c not in mini]
        assert not missing, "%s 极简版漏了指令: %s" % (persona, missing)
        # 参数用法（英文实体 id / 坐标）必须保留
        assert "minecraft:pig" in mini, "%s 极简版缺 target 用法" % persona
    print("  [U1g] 极简档指令清单与精简版逐条对齐 OK（26 条 × 2 人格）")


def test_prompt_min_skin_and_name():
    """极简档的皮肤动作词（/ 分隔）与 NAME 名字替换同样生效。"""
    saved = (config.SKIN_ACTION_TAGS, config.SKIN_PROMPT,
             config.SKIN_DEFAULT_NAME)
    config.SKIN_ACTION_TAGS = {"待机": "idle", "思考": "think"}
    config.SKIN_PROMPT = None
    try:
        t = config.load_system_prompt("maid", "小鱼", backend="deepseek-api",
                                      style="min")
        assert "NAME 小鱼" in t, t[:200]
        assert "待机/思考" in t, "皮肤动作词替换应作用于 / 分隔的极简写法"
    finally:
        (config.SKIN_ACTION_TAGS, config.SKIN_PROMPT,
         config.SKIN_DEFAULT_NAME) = saved
    print("  [U1h] 极简档皮肤动作词 + NAME 名字替换 OK")


def test_prompt_min_modes():
    """极简档 × 工作/日常模式：模式段随 mode_id 变化，且模式规则完整。"""
    for mode_id, tag in ((2, "工作"), (1, "日常")):
        sys = config.get_api_prompt("maid", mode_id, "大肥鱼", style="min")
        assert "当前处于模式%d（%s模式）" % (mode_id, tag) in sys, sys[:200]
        assert "40字" in sys or "日常" in sys, sys[:200]
    # 日常 vs 游戏：模式段应不同
    a = config.get_api_prompt("maid", 1, "大肥鱼", style="min")
    b = config.get_api_prompt("maid", 3, "大肥鱼", style="min")
    assert a.split("【当前模式】", 1)[1] != b.split("【当前模式】", 1)[1]
    print("  [U1i] 极简档模式段（工作/日常/游戏互异）OK")


def test_worker_prompt_style_routing():
    """_ChatWorker 按 opts['prompt_style'] 路由：min → 极简 system，slim → 精简。"""
    def mk(style):
        opts = _opts()
        opts["backend"] = "deepseek-api"
        opts["persona"] = "maid"
        opts["pet_name"] = "大肥鱼"
        opts["prompt_style"] = style
        return _ChatWorker(_FakeClient(), "x", opts)

    sys_min = mk("min")._system_prompt()
    assert "[PERSONA_LOAD]" in sys_min, "传 prompt_style=min 应出极简 system"
    assert "【当前模式】" in sys_min
    sys_slim = mk("slim")._system_prompt()
    assert "女仆指令" in sys_slim and "[PERSONA_LOAD]" not in sys_slim
    print("  [U1j] _ChatWorker 极简/精简路由 OK")


def test_run_plain_suppress_dsl_when_nlu_executed():
    """NLU 已本地执行 → AI 回复的 DSL 只剥离文本、不下发（避免冲突撤销执行）。

    例：「穿金胸甲」NLU 已 transfer 穿上，AI 若再回【equip】会把胸甲拿回主手。
    """
    link = _FakeLink()
    loop = MaidLoop(link)
    client = _FakeClient(reply="好的主人！{开心}【equip(item=minecraft:golden_chestplate)】")
    got = []
    opts = _opts()
    opts["backend"] = "deepseek"   # 网页版：测 DSL 文本通道（非原生 tools）
    worker = _ChatWorker(client, "穿金胸甲", opts,
                         preprocess=lambda t: ("【本地预处理】已执行 transfer", True),
                         maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert got and got[0][0] == "好的主人！{开心}", "DSL 应被剥离: %s" % got
    assert not link.sent, "NLU 已执行不应再下发 AI DSL: %s" % link.sent
    print("  [U7] NLU 已执行 → 抑制 AI 冲突 DSL OK")


def test_run_plain_dsl_allowed_when_nlu_not_executed():
    """NLU 未执行（preprocess 返回 str / hint）→ AI DSL 正常下发（AI 兜底）。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    client = _FakeClient(reply="好的，我来！【attack(range=10)】")
    got = []
    opts = _opts()
    opts["backend"] = "deepseek"   # 网页版：测 DSL 文本通道（非原生 tools）
    worker = _ChatWorker(client, "去打僵尸", opts,
                         preprocess=lambda t: "【本地预处理】hint",  # str：不抑制
                         maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert link.sent == [("attack", {"range": 10}, False)], "AI 兜底应正常下发"
    print("  [U8] NLU 未执行 → AI 兜底 DSL 正常 OK")


# ---------------- U11 双通道（P1-1 原生 tool_calls） ----------------

class _FakeToolClient(_FakeClient):
    """支持原生 tool_calls 的后端：chat_message 返回结构化工具调用。"""

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def chat_message(self, messages, thinking=False, model=None, tools=None):
        self.calls.append((messages, {"thinking": thinking,
                                      "tools": tools is not None}))
        return self.msg


def test_native_tools_enabled():
    """双通道开关：maid_loop 在线 + CAP_TOOLS + 非流式 才走原生工具。"""
    # deepseek-api：CAP_TOOLS + 非流式 → 开
    opts = _opts()
    opts["backend"] = "deepseek-api"
    worker = _ChatWorker(_FakeClient(), "x", opts,
                         maid_loop=MaidLoop(_FakeLink()))
    assert worker._native_tools_enabled() is True
    # 网页版：无 CAP_TOOLS → 关
    opts2 = _opts()
    opts2["backend"] = "deepseek"
    worker2 = _ChatWorker(_FakeClient(), "x", opts2,
                          maid_loop=MaidLoop(_FakeLink()))
    assert worker2._native_tools_enabled() is False
    # 无女仆 → 关
    worker3 = _ChatWorker(_FakeClient(), "x", _opts())
    assert worker3._native_tools_enabled() is False
    print("  [U11a] 双通道开关（CAP_TOOLS + 非流式 + 女仆在线）OK")


def test_run_plain_native_tool_calls():
    """原生通道全流程：模型返回 tool_calls → 下发女仆 + 回执注入。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    msg = {"role": "assistant", "content": "去挖矿",
           "tool_calls": [{
               "id": "c1", "type": "function",
               "function": {"name": "mine",
                            "arguments": '{"range": 12, "count": 8}'}}]}
    client = _FakeToolClient(msg)
    got = []
    opts = _opts()
    opts["backend"] = "deepseek-api"
    worker = _ChatWorker(client, "去挖矿", opts, maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert got and got[0][1] is False
    assert got[0][0] == "去挖矿", "正文应保留"
    assert link.sent == [("mine", {"range": 12, "count": 8}, False)]
    assert "mine" in worker.dsl_inject, "回执应写入 dsl_inject"
    assert client.calls, "应调用 chat_message"
    assert client.calls[0][1]["tools"] is True, "应传 tools schema"
    print("  [U11b] 原生 tool_calls 全流程 OK（下发 mine + 回执注入）")


def test_run_plain_native_no_tools_fallback():
    """原生通道：模型只回文本（无 tool_calls）→ 正文照常，无下发。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    msg = {"role": "assistant", "content": "好的，没找到目标",
           "tool_calls": None}
    client = _FakeToolClient(msg)
    got = []
    opts = _opts()
    opts["backend"] = "deepseek-api"
    worker = _ChatWorker(client, "看一下周围", opts, maid_loop=loop)
    worker.finished.connect(lambda text, err: got.append((text, err)))
    worker.run()
    assert got[0][0] == "好的，没找到目标"
    assert link.sent == [], "无 tool_calls 不应下发"
    print("  [U11c] 原生通道无工具 → 纯文本回复 OK")


def test_apply_native_tools_invalid_skipped():
    """非法/越权 tool_calls 应被静默跳过（白名单 + JSON + 必填校验）。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    worker = _ChatWorker(_FakeClient(), "x", _opts(), maid_loop=loop)
    calls = [
        {"function": {"name": "not_a_command", "arguments": "{}"}},
        {"function": {"name": "craft", "arguments": "bad json"}},
        {"function": {"name": "craft_check", "arguments": "{}"}},
        {"function": {"name": "transfer",
                      "arguments": '{"count": 5}'}},   # 缺 from/to 必填
    ]
    inj = worker._apply_native_tools(calls)
    assert inj == "", "全部应被跳过"
    assert link.sent == [], "不应下发任何指令"
    print("  [U11d] 非法 tool_calls 静默跳过 OK")


def test_apply_native_tools_nlu_suppress():
    """NLU 已本地执行时抑制 AI 工具调用（与 DSL 通道口径一致）。"""
    link = _FakeLink()
    loop = MaidLoop(link)
    worker = _ChatWorker(_FakeClient(), "x", _opts(), maid_loop=loop)
    worker._nlu_executed = True
    calls = [{"function": {"name": "equip",
                           "arguments": '{"item": "diamond_sword"}'}}]
    inj = worker._apply_native_tools(calls)
    assert inj == "", "NLU 已执行 → 抑制"
    assert link.sent == []
    print("  [U11e] NLU 已执行 → 抑制原生工具调用 OK")


def main():
    test_prepare_prompt_order()
    test_prepare_prompt_api_backend_no_decl()
    test_system_prompt_by_backend()
    test_api_prompt_global_maid_and_modes()
    test_prompt_min_style()
    test_prompt_min_matrix()
    test_prompt_min_instruction_parity()
    test_prompt_min_skin_and_name()
    test_prompt_min_modes()
    test_worker_prompt_style_routing()
    test_apply_dsl_with_loop()
    test_apply_dsl_without_loop()
    test_apply_dsl_exception()
    test_upload_images_all_fail()
    test_upload_images_partial_fail()
    test_upload_images_none()
    test_make_client_router()
    test_stream_strip_guards()
    test_message_queue_priority()
    test_message_queue_dedup_and_busy()
    test_message_queue_cap()
    test_run_plain_full_flow()
    test_run_plain_suppress_dsl_when_nlu_executed()
    test_run_plain_dsl_allowed_when_nlu_not_executed()
    test_native_tools_enabled()
    test_run_plain_native_tool_calls()
    test_run_plain_native_no_tools_fallback()
    test_apply_native_tools_invalid_skipped()
    test_apply_native_tools_nlu_suppress()
    print("\n全部通过")


if __name__ == "__main__":
    main()
