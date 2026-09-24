# -*- coding: utf-8 -*-
"""结构化输出（P1-2）测试：ai/structured.py + 记忆提炼接入重试。

无框架纯断言，风格同 test_maid_loop.py：
- extract_json：整段 / 代码块包裹 / 前后缀夹带 / 非法 → 错误信息
- retry_structured：首轮失败 → 重试轮回灌纠正提示 → 成功；次数耗尽返回 None
- condense_context 接入：mock `_condense_call` 首轮非法、重试轮合法 → 记忆正常提炼

运行：python tests/test_structured.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from ai import structured
from ai.structured import extract_json, retry_structured


def test_extract_json_plain():
    obj, err = extract_json('{"key": "值", "n": 3}')
    assert err == "", err
    assert obj == {"key": "值", "n": 3}
    print("[ok] extract_json 整段 JSON")


def test_extract_json_code_fence():
    obj, err = extract_json('```json\n{"facts": [], "summary": "s"}\n```')
    assert err == "" and obj == {"facts": [], "summary": "s"}
    print("[ok] extract_json markdown 代码块包裹")


def test_extract_json_with_prefix_suffix():
    text = '好的，结果如下：\n{"facts": [{"key": "a", "value": "1"}], "summary": "ss"}\n以上'
    obj, err = extract_json(text)
    assert err == "", err
    assert obj["summary"] == "ss" and obj["facts"][0]["key"] == "a"
    print("[ok] extract_json 前后缀夹带")


def test_extract_json_invalid():
    obj, err = extract_json("这不是 JSON")
    assert obj is None and "JSON" in err
    obj, err = extract_json("")
    assert obj is None and "为空" in err
    obj, err = extract_json(None)
    assert obj is None
    print("[ok] extract_json 非法输入返回错误信息")


def test_retry_structured_success_on_second():
    calls = []

    def fetch(note):
        calls.append(note)
        if not note:
            return "非法文本"
        return '{"summary": "ok"}'

    obj, raw, attempts = retry_structured(fetch, extract_json, retries=1)
    assert obj == {"summary": "ok"}, obj
    assert attempts == 2
    assert len(calls) == 2
    assert "更正" in calls[1], "重试轮必须携带纠正提示"
    print("[ok] retry_structured 首轮失败 → 重试成功")


def test_retry_structured_exhausted():
    calls = []

    def fetch(note):
        calls.append(note)
        return "还是不对"

    obj, raw, attempts = retry_structured(fetch, extract_json, retries=2)
    assert obj is None
    assert attempts == 3
    assert raw == "还是不对"
    print("[ok] retry_structured 次数耗尽返回 None")


def test_retry_zero_disables():
    calls = []

    def fetch(note):
        calls.append(note)
        return "非法"

    obj, _raw, attempts = retry_structured(fetch, extract_json, retries=0)
    assert obj is None and attempts == 1 and len(calls) == 1
    print("[ok] retry_structured retries=0 只问一次")


def test_condense_retry_end_to_end():
    """记忆提炼：mock 首轮非法 JSON、重试轮合法 → ctx 正常更新，历史不丢。"""
    import ai.memory as mem
    from ai.context import ChatContext, Turn

    replies = iter(["不是 JSON", '{"facts": [{"key": "K", "value": "V"}],'
                            ' "summary": "关系概述"}'])
    mem._condense_call = lambda msgs: next(replies)
    orig = config.STRUCTURED_RETRY
    config.STRUCTURED_RETRY = 1
    try:
        ctx = ChatContext()
        old = [Turn(user="你好", assistant="嗨")]
        mem.condense_context(ctx, old)
        assert ctx.summary == "关系概述", ctx.summary
        assert any(f.key == "K" for f in ctx.memory)
    finally:
        config.STRUCTURED_RETRY = orig
    print("[ok] condense_context 重试后正常提炼（不静默丢记忆）")


def main():
    test_extract_json_plain()
    test_extract_json_code_fence()
    test_extract_json_with_prefix_suffix()
    test_extract_json_invalid()
    test_retry_structured_success_on_second()
    test_retry_structured_exhausted()
    test_retry_zero_disables()
    test_condense_retry_end_to_end()
    print("\n全部通过")


if __name__ == "__main__":
    main()
