# -*- coding: utf-8 -*-
"""knowledge-qa 扩展点 + HostAPI.llm 测试（A+B 落地的伴随守卫）。

无框架纯断言，风格同 test_plugins.py。

- K1 knowledge-qa 注册与参数校验（缺 ask() 被拒）
- K2 同扩展点取优先级最高者；同优先级先加载者胜
- K3 未装插件时宿主零行为变化（qa_context → ""）
- K4 插件抛异常被隔离（绝不影响对话）
- K5 HostAPI.llm：失败返回 ""、成功透传、默认 purpose 带插件 id
- K6 真实 agentic-rag 插件：被发现并注册 wiki-knowledge + knowledge-qa
- K7 agentic-rag 缺索引时 know() / ask() 返回 ""（优雅降级）
- K8 --doctor 报出 knowledge-qa

运行：python tests/test_knowledge_qa.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugin import api
from plugin import host as H
from plugin.loader import load_all
from plugin.registry import HostRegistry, make_host_api

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGINS = os.path.join(_REPO, "plugins")


def _fail(msg):
    raise AssertionError(msg)


def _write_plugin(root, pid, types_, register_src, *, priority=0):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    man = {"id": pid, "name": pid, "version": "0.0.1", "api_version": api.API_VERSION,
           "type": list(types_), "entry": "plugin:register", "priority": priority}
    with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(register_src)
    return d


_QA_PLUGIN = '''
class _Qa:
    def __init__(self, tag):
        self.tag = tag
    def ask(self, question, *, session_key="", max_chars=1200):
        return "%s:%s" % (self.tag, question)


def register(host):
    host.add_knowledge_qa(_Qa("%s"))
'''


# ---------------- K1 注册与参数校验 ----------------

def test_register_validation():
    reg = HostRegistry()
    reg.add_knowledge_qa("p", 0, type("Q", (), {"ask": lambda self, q, **k: "x"})())
    assert reg.knowledge_qa() is not None

    for bad in (object(), type("NoAsk", (), {"know": lambda self, *a, **k: ""})()):
        try:
            reg.add_knowledge_qa("p", 0, bad)
            _fail("缺 ask() 的实现应被拒绝")
        except api.HostAPIError:
            pass
    print("  [K1] knowledge-qa 注册与参数校验 OK")


# ---------------- K2 优先级 ----------------

def test_priority():
    reg = HostRegistry()
    low = type("Q", (), {"ask": lambda self, q, **k: "low"})()
    high = type("Q", (), {"ask": lambda self, q, **k: "high"})()
    first = type("Q", (), {"ask": lambda self, q, **k: "first"})()
    reg.add_knowledge_qa("low", 1, low)
    reg.add_knowledge_qa("high", 99, high)
    assert reg.knowledge_qa() is high
    assert reg.knowledge_qa_source() == "high"
    # 同优先级：先加载者胜（与 _order 的既有语义一致）
    reg2 = HostRegistry()
    reg2.add_knowledge_qa("a", 5, first)
    reg2.add_knowledge_qa("b", 5, low)
    assert reg2.knowledge_qa() is first
    print("  [K2] knowledge-qa 优先级 OK")


# ---------------- K3 零行为变化 ----------------

def test_no_plugin_noop():
    saved = H._registry
    try:
        H._registry = HostRegistry()          # 假装一个插件都没装
        assert H.knowledge_qa() is None
        assert H.qa_context("下界合金锭怎么合成") == ""
    finally:
        H._registry = saved
        H.reset()
    print("  [K3] 未装插件时宿主零行为变化 OK")


# ---------------- K4 异常隔离 ----------------

def test_exception_isolated():
    def _boom(self, q, **k):
        raise RuntimeError("插件内部炸了")

    saved = H._registry
    try:
        reg = HostRegistry()
        reg.add_knowledge_qa("boom", 0, type("Q", (), {"ask": _boom})())
        H._registry = reg
        assert H.qa_context("随便问问") == ""   # 抛异常 → 返回空，不冒泡
    finally:
        H._registry = saved
        H.reset()
    print("  [K4] 插件异常被隔离（不影响对话）OK")


# ---------------- K5 HostAPI.llm ----------------

def test_host_llm():
    import ai.utility as U
    h = make_host_api(HostRegistry(), "unit-test")
    orig = U.utility_chat
    seen = {}
    try:
        def _boom(*a, **k):
            raise RuntimeError("没有可用后端")
        U.utility_chat = _boom
        assert h.llm([{"role": "user", "content": "hi"}]) == ""   # 失败 → ""，不抛

        def _ok(messages, purpose="", system_prompt=None, thinking=False):
            seen["purpose"] = purpose
            seen["system"] = system_prompt
            return "OK"
        U.utility_chat = _ok
        out = h.llm([{"role": "user", "content": "hi"}], system="你是助手")
        assert out == "OK"
        assert seen["purpose"] == "plugin_unit-test"    # 默认按插件 id 分组
        assert seen["system"] == "你是助手"
    finally:
        U.utility_chat = orig
    print("  [K5] HostAPI.llm 失败兜底 / 透传 / purpose OK")


# ---------------- K6 真实插件被发现 ----------------

def test_real_plugin_discovered():
    res = load_all(plugin_dirs=[_PLUGINS], use_entry_points=False)
    ids = [m.id for m in res.plugins]
    if "agentic-rag" not in ids:
        _fail("agentic-rag 未被发现：%r（errors=%r）" % (ids, res.errors))
    reg = res.registry
    wiki = reg.wiki_knowledge()
    qa = reg.knowledge_qa()
    assert wiki is not None and qa is not None
    assert callable(getattr(wiki, "know", None))
    assert callable(getattr(qa, "ask", None))
    print("  [K6] agentic-rag 注册 wiki-knowledge + knowledge-qa OK")


# ---------------- K7 缺索引优雅降级 ----------------

def test_degradation_without_index():
    res = load_all(plugin_dirs=[_PLUGINS], use_entry_points=False)
    impl = res.registry.knowledge_qa()
    if impl is None:
        _fail("agentic-rag 未注册")
    data_ready = getattr(impl, "data_ready", lambda: False)()
    # 无论有没有索引，都必须"不抛异常"
    a = impl.know("[击杀] 猪 x1", session_key="s1")
    b = impl.ask("下界合金锭怎么合成")
    assert isinstance(a, str) and isinstance(b, str)
    if not data_ready:
        assert a == "" and b == "", "缺索引时应返回空串（=不注入）"
        print("  [K7] agentic-rag 缺索引优雅降级（返回空串，不抛）OK")
    else:
        print("  [K7] agentic-rag 索引已就绪（跳过空串断言，仅验证不抛）OK")


# ---------------- K8 doctor ----------------

def test_doctor_mentions_qa():
    from plugin.doctor import run_doctor
    lines = []
    rc = run_doctor(out=lines.append)
    text = "\n".join(lines)
    assert rc == 0
    for kw in ("knowledge-qa", "wiki-knowledge", "ai-provider", "assets"):
        assert kw in text, kw
    print("  [K8] --doctor 报出 knowledge-qa OK")


def main():
    print("=" * 62)
    print("knowledge-qa 扩展点 + HostAPI.llm 测试（A+B）")
    print("=" * 62)
    test_register_validation()
    test_priority()
    test_no_plugin_noop()
    test_exception_isolated()
    test_host_llm()
    test_real_plugin_discovered()
    test_degradation_without_index()
    test_doctor_mentions_qa()
    print("\n全部通过")


if __name__ == "__main__":
    main()
