# -*- coding: utf-8 -*-
"""AI 供应商注册表 / 协议适配器 / 单一事实来源测试（DESIGN_AI_PROVIDERS.md §五）。

无框架纯断言，风格同 test_chat_unit.py：

- V1 注册表完整性（id 唯一 / 协议合法 / 默认模型属于模型表）
- V2 **能力位等价性**：三个内置后端的 `is_messages_backend` 与改造前逐一相同
- V3 旧 id 归一化（`deepseek` → `deepseek-web`，且**语义不得反过来**）
- V4 凭据命名空间 + 老键只读回退（用假 QSettings，**不碰真实配置**）
- V5 跨协议请求体 golden（OpenAI / Anthropic 思考与 temperature 互斥 / DeepSeek 换模型）
- V6 无硬编码守卫（`ai/` 下除 credentials_store 外不得再出现旧键字面量）
- V7 版本铁律（config.VERSION == CHANGELOG 顶部）

运行：python tests/test_providers.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import ai.client as client_mod
from ai import credentials_store as creds
from ai import providers as prov

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fail(msg):
    raise AssertionError(msg)


# ---------------- V1 注册表完整性 ----------------

def test_registry_integrity():
    ids = [p.id for p in prov.profiles()]
    assert len(ids) == len(set(ids)), "供应商 id 必须唯一：%s" % ids
    for p in prov.profiles():
        assert p.protocol in prov.PROTOCOLS, (p.id, p.protocol)
        assert p.label, p.id
        assert p.group, p.id
        if p.models:
            mid = {m.id for m in p.models}
            assert p.default_model in mid, \
                "%s 的 default_model=%r 不在模型表 %s" % (p.id, p.default_model, mid)
        for m in p.models:
            if m.reasoning_kind:
                assert m.reasoning_kind in (
                    prov.REASONING_KIND_TOGGLE,
                    prov.REASONING_KIND_EFFORT,
                    prov.REASONING_KIND_BUDGET), (p.id, m.id, m.reasoning_kind)
            if m.reasoning:
                assert m.reasoning_default in m.reasoning, (p.id, m.id)
        # 默认不能把没接入 UI 的供应商算作可选之外的东西
        assert isinstance(p.selectable, bool)
    assert prov.DEFAULT_PROVIDER in prov.provider_ids()
    print("  [V1] 注册表完整性 OK（%d 家供应商）" % len(ids))


# ---------------- V2 能力位等价性 ----------------

def test_capability_equivalence():
    """P0 的验收核心：三个内置后端的新旧判定必须**逐一相同**。

    旧实现：`name in ("deepseek-api", "qwen")`
    """
    old = {"deepseek": False, "deepseek-api": True, "qwen": True}
    for name, want in old.items():
        got = config.is_messages_backend(name)
        assert got is want, "is_messages_backend(%r) 应为 %s，实际 %s" % (name, want, got)
    # 新 id 也必须一致
    assert config.is_messages_backend("deepseek-web") is False
    # 与能力位定义一致（判据是能力不是厂名）
    for pid in prov.provider_ids():
        assert config.is_messages_backend(pid) == (
            not prov.cap(pid, prov.CAP_SERVER_MEMORY)), pid
    # 精简人设分流：只有「system 每轮重发」的后端才用 *full* 之外的精简版
    assert prov.needs_system_each_turn("deepseek-web") is False
    assert prov.needs_system_each_turn("deepseek-api") is True
    assert prov.needs_system_each_turn("qwen") is True
    print("  [V2] 能力位等价性 OK（三个内置后端与改造前逐一相同）")


# ---------------- V3 旧 id 归一化（防语义反转） ----------------

def test_legacy_id_normalization():
    """`deepseek` 历史上指**网页版**。改造中一度打算把它当成官方 API 的新名字，
    那会让老用户的后端被错读（语义反转 180°）。这里把它钉死。
    """
    assert prov.canonical("deepseek") == "deepseek-web"
    assert prov.canonical("deepseek-web") == "deepseek-web"
    assert prov.canonical("deepseek-api") == "deepseek-api"   # 不改名
    assert prov.canonical("qwen") == "qwen"
    assert prov.canonical("") == prov.DEFAULT_PROVIDER
    assert prov.canonical(None) == prov.DEFAULT_PROVIDER
    # 未知 id 原样返回（自定义供应商）
    assert prov.canonical("my-local-llm") == "my-local-llm"
    # 旧 id 的能力必须与新 id 一致（否则老用户行为会变）
    assert prov.cap("deepseek", prov.CAP_SERVER_MEMORY) is True
    assert prov.is_messages_backend("deepseek") is False
    print("  [V3] 旧 id 归一化 OK（deepseek → deepseek-web，语义未反转）")


# ---------------- V4 凭据命名空间 + 老键回退 ----------------

class _FakeStore:
    """内存版 QSettings（绝不动真实配置）。"""

    def __init__(self, init=None):
        self.d = dict(init or {})

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):
        self.d[key] = value

    def remove(self, key):
        self.d.pop(key, None)


def test_credentials_namespace_and_legacy():
    saved = creds._STORE
    try:
        # ① 只存在旧键 → 全部能读出来（升级不丢 Key）
        creds._STORE = _FakeStore({
            "ai_backend": "deepseek",
            "qwen_api_key": "K-QWEN",
            "qwen_model": "qwen-plus",
            "deepseek_api_key": "K-DS",
            "deepseek_api_model": "deepseek-v4-pro",
            "ai_session_id": "SID-1",
            "ai_parent_id": 4242,
        })
        assert creds.provider() == "deepseek-web", creds.provider()
        assert client_mod.backend_name() == "deepseek-web"
        assert client_mod.load_qwen_key() == "K-QWEN"
        assert client_mod.load_qwen_model() == "qwen-plus"
        assert client_mod.load_deepseek_api_key() == "K-DS"
        assert client_mod.load_deepseek_api_model() == "deepseek-v4-pro"
        assert client_mod.load_thread_state() == ("SID-1", 4242)

        # ①b 旧**模型** id 在读取时就被归一化（实机：服务端会把它别名到
        # deepseek-flash，不归一化则 perf 日志会记下与实际不符的模型名）
        _keep = creds._STORE
        creds._STORE = _FakeStore({"deepseek_api_model": "deepseek-reasoner"})
        assert creds.get_model("deepseek-api") == "deepseek-flash", \
            creds.get_model("deepseek-api")
        assert creds._STORE.d.get("deepseek_api_model") == "deepseek-reasoner", \
            "归一化只发生在读，原始键不得被改写"
        creds._STORE = _keep

        # ② 写入落到命名空间新键，且不破坏旧键（可回滚）
        creds.set_key("qwen", "K-NEW")
        assert creds._STORE.d.get("ai/qwen/key") == "K-NEW"
        assert creds._STORE.d.get("qwen_api_key") == "K-QWEN"
        assert client_mod.load_qwen_key() == "K-NEW"
        creds.set_model("qwen", "qwen-turbo")
        assert client_mod.load_qwen_model() == "qwen-turbo"

        # ③ set_provider 会同步写老键，方便回滚到旧版本
        creds.set_provider("deepseek-api")
        assert creds._STORE.d.get("ai_provider") == "deepseek-api"
        assert creds._STORE.d.get("ai_backend") == "deepseek-api"
        creds.set_provider("deepseek-web")
        assert creds._STORE.d.get("ai_backend") == "deepseek", \
            "网页版在旧版本里叫 deepseek，回写必须用旧值"

        # ④ 新键优先于旧键
        creds._STORE = _FakeStore({"ai/qwen/key": "NEW", "qwen_api_key": "OLD"})
        assert client_mod.load_qwen_key() == "NEW"

        # ⑤ 端点：用户覆盖优先，其次注册表默认
        creds._STORE = _FakeStore({})
        assert creds.get_base_url("deepseek-api") == prov.profile("deepseek-api").api
        creds.set_base_url("deepseek-api", "https://proxy.local/v1/")
        assert creds.get_base_url("deepseek-api") == "https://proxy.local/v1/"
    finally:
        creds._STORE = saved
    print("  [V4] 凭据命名空间 + 老键回退 OK（升级不丢 Key，新键优先）")


# ---------------- V5 跨协议请求体 golden ----------------

def test_request_body_golden():
    from ai.protocols.openai_chat import OpenAIChatAdapter
    from ai.protocols.anthropic_messages import AnthropicAdapter
    from ai.deepseek_api import DeepSeekApiClient

    msgs = [{"role": "user", "content": "hi"}]

    # OpenAI 兼容：model + messages + stream
    a = OpenAIChatAdapter("k", "https://x.test", "m1")
    body = a._build_body(msgs, False, None, False)
    assert body == {"model": "m1", "messages": msgs, "stream": False}, body
    assert a._build_body(msgs, False, None, True)["stream"] is True
    assert a._url() == "https://x.test/chat/completions", a._url()
    assert a._headers()["Authorization"] == "Bearer k"
    # 方言位：换 path 即可支持 /responses 之类
    a2 = OpenAIChatAdapter("k", "https://x.test", "m1", chat_path="/responses")
    assert a2._url() == "https://x.test/responses"

    # Anthropic：system 抽到顶层 + max_tokens 必填 + 不带 temperature
    msgs2 = [{"role": "system", "content": "SYS"},
             {"role": "user", "content": "hi"}]
    an = AnthropicAdapter("k", "https://api.anthropic.com", "claude-x")
    b2 = an._build_body(msgs2, False, None, False)
    assert b2["system"] == "SYS", b2
    assert b2["messages"] == [{"role": "user", "content": "hi"}], b2
    assert b2["max_tokens"] > 0, b2
    assert "temperature" not in b2, b2
    assert "thinking" not in b2, b2
    assert an._headers()["x-api-key"] == "k"
    assert an._headers()["anthropic-version"], an._headers()
    # 开思考 → 有 thinking、**绝无 temperature**、max_tokens 留出预算
    b3 = an._build_body(msgs2, "high", None, False)
    assert b3["thinking"] == {"type": "enabled", "budget_tokens": 8192}, b3
    assert "temperature" not in b3, b3
    assert b3["max_tokens"] >= 8192 + 1024, b3
    # off 等同于不开
    assert "thinking" not in an._build_body(msgs2, "off", None, False)
    # 预算型模型：UI 直接给 token 数 → 原样用，且有下限兜底
    b4 = an._build_body(msgs2, "2000", None, False)
    assert b4["thinking"]["budget_tokens"] == 2000, b4
    b5 = an._build_body(msgs2, "10", None, False)
    assert b5["thinking"]["budget_tokens"] == 1024, b5

    # DeepSeek：思考是**请求参数**（reasoning_effort），**不再换模型**
    ds = DeepSeekApiClient("sk-test", "deepseek-flash")
    assert "reasoning_effort" not in ds._build_body(msgs, False, None, False)
    assert ds._build_body(msgs, "high", None, False)["reasoning_effort"] == "high"
    # 模型不因思考而变
    assert ds._resolve_model(True, None) == "deepseek-flash"
    assert ds._build_body(msgs, "max", None, False)["model"] == "deepseek-flash"
    # 关思考 = 整字段省略（实机：省略即不推理）
    assert "reasoning_effort" not in ds._build_body(msgs, "off", None, False)
    print("  [V5] 跨协议请求体 golden OK（含 Anthropic thinking×temperature 互斥）")


# ---------------- V6 无硬编码守卫 ----------------

#: 旧键字面量：除 credentials_store.py（回退映射的唯一归属地）外不得出现
_LEGACY_LITERALS = ("qwen_api_key", "deepseek_api_key", "deepseek_api_model",
                    "qwen_model", "ai_backend")


def test_no_hardcoded_legacy_keys():
    offenders = []
    ai_dir = os.path.join(BASE, "ai")
    for root, _dirs, files in os.walk(ai_dir):
        for fn in files:
            if not fn.endswith(".py") or fn == "credentials_store.py":
                continue
            path = os.path.join(root, fn)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            for name in _LEGACY_LITERALS:
                # 只揪**字符串字面量**（文档里用反引号提到旧键名是允许的）
                if ('"%s"' % name) in text or ("'%s'" % name) in text:
                    offenders.append("%s → %s" % (
                        os.path.relpath(path, BASE), name))
    assert not offenders, "旧键字面量泄漏到 ai/ 其他地方：%s" % offenders
    print("  [V6] 无硬编码守卫 OK（旧键字面量只在 credentials_store.py）")


# ---------------- V7 版本铁律 ----------------

def test_version_iron_rule():
    """`config.VERSION` 必须等于 CHANGELOG 顶部版本号（项目铁律，曾被破坏三次）。"""
    with open(os.path.join(BASE, "CHANGELOG.md"), "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^##\s+(\d+\.\d+\.\d+)", text, re.M)
    assert m, "CHANGELOG 顶部找不到版本小节"
    top = m.group(1)
    m2 = re.search(r"当前版本：\*\*(\d+\.\d+\.\d+)\*\*", text)
    assert m2, "CHANGELOG 找不到「当前版本」行"
    cur = m2.group(1)
    assert top == cur, "CHANGELOG 顶部小节 %s 与「当前版本」%s 不一致" % (top, cur)
    assert config.VERSION == top, \
        "版本铁律破了：config.VERSION=%s，CHANGELOG 顶部=%s" % (config.VERSION, top)
    print("  [V7] 版本铁律 OK（config.VERSION = CHANGELOG 顶部 = %s）" % top)


# ---------------- V8 认证/端点组装（防误配） ----------------

def test_adapter_factory_guards():
    from ai.protocols import make_adapter, adapter_class
    # 网页版不能走协议工厂（它是 Cookie 会话链路）
    try:
        make_adapter("deepseek-web")
        _fail("deepseek-web 不该能由协议工厂构造")
    except ValueError:
        pass
    assert adapter_class(prov.PROTO_OPENAI).__name__ == "OpenAIChatAdapter"
    assert adapter_class(prov.PROTO_ANTHROPIC).__name__ == "AnthropicAdapter"
    try:
        adapter_class("不存在的协议")
        _fail("未知协议应当报错")
    except ValueError:
        pass
    # 思考控件三态：不支持 → kind 为空
    kind, values, default, _note = prov.reasoning_ui("qwen", "qwen-turbo")
    assert kind == "" and values == (), (kind, values)
    kind2, values2, default2, _n2 = prov.reasoning_ui("deepseek-api",
                                                      "deepseek-v4-pro")
    assert kind2 == prov.REASONING_KIND_EFFORT, kind2
    assert default2 == "off" and "off" in values2, (default2, values2)
    print("  [V8] 适配器工厂 / 思考控件三态 OK")


# ---------------- V9 流式 SSE 解析（两个协议各来一遍） ----------------

class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, pieces):
        self._pieces = pieces          # list[bytes]，刻意按"分片"喂

    def iter_content(self, chunk_size=None):
        for p in self._pieces:
            yield p


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.posts = []

    def post(self, url, json=None, headers=None, stream=False):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return self._resp


def test_stream_sse_parsing():
    from ai.protocols.base import KIND_DONE, KIND_TEXT, KIND_THINK
    from ai.protocols.openai_chat import OpenAIChatAdapter
    from ai.protocols.anthropic_messages import AnthropicAdapter

    # ---- OpenAI 兼容：正文与 reasoning_content 分流，[DONE] 收尾 ----
    oa = OpenAIChatAdapter("k", "https://x.test", "m")
    oa._session = _FakeSession(_FakeResp([
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"\xe6\x83\xb3"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"\xe5\xa5"}}]}\n',
        b'data: [DONE]\n',
    ]))
    evs = list(oa.stream_events([{"role": "user", "content": "hi"}]))
    kinds = [(e.kind, e.text) for e in evs]
    assert (KIND_TEXT, "你") in kinds, kinds
    assert any(k == KIND_THINK for k, _ in kinds), kinds
    assert evs[-1].kind == KIND_DONE, evs[-1]
    # 便捷包装只给正文（思考不得混进去）
    oa._session = _FakeSession(_FakeResp([
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"\xe6\x83\xb3"}}]}\n',
        b'data: [DONE]\n',
    ]))
    assert "".join(oa.stream([{"role": "user", "content": "hi"}])) == "你"

    # ---- Anthropic：content_block_delta / message_stop，且行被切成碎片也要能拼回 ----
    an = AnthropicAdapter("k", "https://api.anthropic.com", "claude-x")
    an._session = _FakeSession(_FakeResp([
        b"event: content_block_delta\n",              # 事件名行（应被忽略）
        b'data: {"type":"content_block_delta","delta":',   # 刻意断在 JSON 中间
        b'{"type":"text_delta","text":"\xe5\x97\xa8"}}\n',
        b'data: {"type":"content_block_delta","delta":'
        b'{"type":"thinking_delta","thinking":"\xe5\x94\x94"}}\n',
        b'data: {"type":"message_stop"}\n',
    ]))
    evs2 = list(an.stream_events([{"role": "system", "content": "S"},
                                 {"role": "user", "content": "hi"}]))
    kinds2 = [(e.kind, e.text) for e in evs2]
    assert (KIND_TEXT, "嗨") in kinds2, kinds2
    assert any(k == KIND_THINK for k, _ in kinds2), kinds2
    assert evs2[-1].kind == KIND_DONE, evs2[-1]
    # 请求体确实带上了顶层 system 与 max_tokens
    body = an._session.posts[0]["json"]
    assert body["system"] == "S" and body["max_tokens"] > 0, body
    assert an._session.posts[0]["url"].endswith("/v1/messages")
    print("  [V9] 流式 SSE 解析 OK（OpenAI / Anthropic，含跨分片行拼接）")


def test_late_phases_registry_surface():
    """P7 / P8 / P9 / P10 落在注册表上的那些"面子"。

    这些是界面与后续阶段直接依赖的判定，钉住它们比钉 UI 更划算。
    """
    # P7：自定义端点与 Anthropic 已放开可选；自定义项必须自己填端点
    assert "custom" in prov.selectable_ids()
    assert "anthropic" in prov.selectable_ids()
    assert prov.needs_base_url("custom") is True
    assert prov.needs_base_url("deepseek-api") is False

    # 传图门控只看 **provider 级**（适配器真的实现了才开）：
    # 模型数据说支持，但 OpenAI 兼容 / Anthropic 的图像输入还没实现 → 不能开，
    # 否则界面提示"支持传图"却在发送时报错。
    assert prov.supports_attachments("deepseek-web") is True
    assert prov.supports_attachments("deepseek-api") is False
    assert prov.supports_attachments("anthropic") is False
    # 而模型级 attachments 仍如实声明（供将来实现 content parts 用）
    assert prov.model("deepseek-api", "deepseek-flash").attachments is True
    assert prov.model("deepseek-api", "deepseek-v4-pro").attachments is False

    # P10：思考控件三态 —— 同一家不同模型语义不同
    k1, v1, _d1, _n1 = prov.reasoning_ui("deepseek-api", "deepseek-v4-pro")
    assert k1 == prov.REASONING_KIND_EFFORT, k1
    # 实机 422 回读的权威取值集（两个模型相同）
    assert v1 == ("off", "minimal", "low", "medium", "high", "xhigh", "max"), v1
    k2, _v2, _d2, _n2 = prov.reasoning_ui("anthropic", "claude-sonnet-4-5")
    assert k2 == prov.REASONING_KIND_BUDGET, k2
    assert prov.model("anthropic", "claude-sonnet-4-5").reasoning_min == 1024
    k3, _v3, _d3, _n3 = prov.reasoning_ui("qwen", "qwen-turbo")
    assert k3 == "", k3
    # 思考关必须是一个合法档位，UI 才敢把它当默认
    _k, vals, default, _n = prov.reasoning_ui("deepseek-api", "deepseek-flash")
    assert default == prov.REASONING_OFF and default in vals, (default, vals)

    # P9：原生音频声明位（当前没有任何模型声明，故 TTS 不该被抑制）
    assert prov.model("qwen", "qwen-turbo").outputs_audio is False
    assert prov.supports("qwen", "qwen-turbo", "audio") is False

    # P8：limit / cost 文本（价格未知时不硬编数字）
    assert "1M" in prov.limit_text("deepseek-api", "deepseek-flash")
    assert prov.cost_text("deepseek-api", "deepseek-flash")
    assert prov.cost_text("custom", "whatever") == ""
    print("  [V10] P7/P8/P9/P10 注册表面子 OK（可选性/传图门控/思考三态/音频位/成本）")


def main():
    print("=" * 62)
    print("AI 供应商注册表与协议适配器测试")
    print("=" * 62)
    test_registry_integrity()
    test_capability_equivalence()
    test_legacy_id_normalization()
    test_credentials_namespace_and_legacy()
    test_request_body_golden()
    test_no_hardcoded_legacy_keys()
    test_adapter_factory_guards()
    test_stream_sse_parsing()
    test_late_phases_registry_surface()
    test_version_iron_rule()
    print("\n全部通过")


if __name__ == "__main__":
    main()
