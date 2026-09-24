# -*- coding: utf-8 -*-
"""工具调用原生化（P1-1）测试：schema 导出 + 适配器 tool_calls 解析 + 能力位。

无框架纯断言，风格同 test_providers.py：

- T1 schema 导出与 COMMANDS 逐条对齐（名称集 / 必填参数 / 类型 / 排除 craft_check）
- T2 OpenAI 适配器：请求体带 tools，响应 tool_calls 解析成完整 message
- T3 Anthropic 适配器：tool_use 块转 OpenAI 风格 tool_calls，请求体转自家格式
- T4 能力位：CAP_TOOLS 只加在支持原生 tools 的供应商

运行：python tests/test_tool_calls.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai import providers as prov
from ai.protocols.base import ProtocolAdapter
from nlu import mod_contract as mc


def test_schema_alignment():
    """T1：schema 名称集合与 COMMANDS 对齐（除 craft_check），必填/类型正确。"""
    names = {c["function"]["name"] for c in mc.to_openai_tools()}
    contract_names = {n for n, c in mc.COMMANDS.items()
                      if c.layer != "meta-dryrun"}
    assert names == contract_names, \
        "tools 名称集与契约不一致：缺 %s 多 %s" % (
            contract_names - names, names - contract_names)
    # craft_check（meta-dryrun）不得下发
    assert "craft_check" not in names
    # 逐条参数对齐
    tools = {t["function"]["name"]: t["function"] for t in mc.to_openai_tools()}
    for name, c in mc.COMMANDS.items():
        if c.layer == "meta-dryrun":
            continue
        fn = tools[name]
        assert fn["description"] == c.desc
        params = fn["parameters"]
        props = params.get("properties") or {}
        assert set(props) == set(c.params), (name, set(props), set(c.params))
        req = params.get("required") or []
        want_req = [k for k, p in c.params.items() if p.required]
        assert sorted(req) == sorted(want_req), (name, req, want_req)
        for pname, p in c.params.items():
            s = props[pname]
            if p.type == "int":
                assert s["type"] == "integer"
                if p.lo is not None:
                    assert s["minimum"] == p.lo
                if p.hi is not None:
                    assert s["maximum"] == p.hi
            elif p.type == "bool":
                assert s["type"] == "boolean"
            else:
                assert s["type"] == "string"
    # Anthropic 格式
    an = mc.to_anthropic_tools()
    assert {t["name"] for t in an} == names
    assert all("input_schema" in t and "description" in t for t in an)
    print("[T1] schema 与 COMMANDS 逐条对齐（%d 个工具，不含 craft_check）"
          % len(names))


def test_openai_chat_message_tool_calls():
    """T2：OpenAI 适配器解析 tool_calls 为完整 message。"""
    from ai.protocols.openai_chat import OpenAIChatAdapter

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {
                "role": "assistant",
                "content": "好的，我去打猪",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "attack",
                                 "arguments": '{"range": 12}'},
                }],
            }}]}

    a = OpenAIChatAdapter(api_key="k", base_url="http://x", model="m")
    a._session = type("S", (), {"post": lambda self, url, json, headers:
        _FakeResp()})()
    msg = a.chat_message([{"role": "user", "content": "打猪"}],
                         tools=mc.to_openai_tools())
    assert msg["content"] == "好的，我去打猪"
    assert msg["tool_calls"] and msg["tool_calls"][0]["function"]["name"] == "attack"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"])["range"] == 12
    print("[T2] OpenAI chat_message 解析 tool_calls OK")


def test_openai_chat_message_no_tools():
    """T2b：未传 tools 时退化为纯文本 message。"""
    from ai.protocols.openai_chat import OpenAIChatAdapter

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "纯文本"}}]}

    a = OpenAIChatAdapter(api_key="k", base_url="http://x", model="m")
    a._session = type("S", (), {"post": lambda self, url, json, headers:
        _FakeResp()})()
    msg = a.chat_message([{"role": "user", "content": "hi"}])
    assert msg["content"] == "纯文本" and msg["tool_calls"] is None
    print("[T2b] OpenAI 无 tools 退化纯文本 OK")


def test_anthropic_chat_message_tool_use():
    """T3：Anthropic tool_use 块转 OpenAI 风格 tool_calls。"""
    from ai.protocols.anthropic_messages import AnthropicAdapter

    class _FakeResp:
        status_code = 200

        def json(self):
            return {"content": [
                {"type": "text", "text": "去挖矿"},
                {"type": "tool_use", "id": "tu_1", "name": "mine",
                 "input": {"range": 12, "count": 8}},
            ]}

    captured = {}

    class _S:
        def post(self, url, json=None, headers=None):
            captured["body"] = json
            return _FakeResp()

    a = AnthropicAdapter(api_key="k", base_url="http://x", model="m")
    a._session = _S()
    msg = a.chat_message([{"role": "user", "content": "挖矿"}],
                         tools=mc.to_openai_tools())
    # 请求体 tools 是 Anthropic 格式
    assert captured["body"]["tools"][0]["name"] == "cancel"
    assert "input_schema" in captured["body"]["tools"][0]
    # 响应转 OpenAI 风格
    assert msg["content"] == "去挖矿"
    assert msg["tool_calls"][0]["function"]["name"] == "mine"
    args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
    assert args["range"] == 12
    print("[T3] Anthropic tool_use → OpenAI tool_calls OK")


def test_supports_tools_caps():
    """T4：CAP_TOOLS 能力位只加在支持原生 tools 的供应商。"""
    for pid in ("deepseek-api", "qwen", "anthropic", "custom"):
        assert prov.supports(pid, "", "tools"), pid
    # deepseek-web 是占位/插件协议，不含（Cookie 会话不支持 tool_calls）
    assert not prov.supports("deepseek-web", "", "tools")
    print("[T4] CAP_TOOLS 能力位 OK")


def test_base_adapter_default_no_tools():
    """T1b：基类默认不支持 tools（真实实现须覆写 supports_tools）。"""
    assert not ProtocolAdapter.supports_tools(ProtocolAdapter())
    print("[T1b] 基类 supports_tools 默认 False OK")


def main():
    test_schema_alignment()
    test_openai_chat_message_tool_calls()
    test_openai_chat_message_no_tools()
    test_anthropic_chat_message_tool_use()
    test_supports_tools_caps()
    test_base_adapter_default_no_tools()
    print("\n全部通过")


if __name__ == "__main__":
    main()
