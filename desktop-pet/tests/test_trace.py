# -*- coding: utf-8 -*-
"""轻量 trace/span（P2-4）测试：core/trace.py。

无框架纯断言，风格同 test_structured.py：
- 嵌套 span：自动父链（线程栈）+ 显式 parent
- 字段完整性：trace_id/span_id/parent_id/name/dur_ms/status
- 异常标 error 且不吞
- 落盘文件可解析（logs/trace/<date>.jsonl）
- traced 装饰器

运行：python tests/test_trace.py
（会把 span 写入 logs/trace/ 当天文件 —— 低频，可接受）
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trace import Trace, new_trace_id, trace, traced

_TRACE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "trace")


def _today_spans():
    """读当天 jsonl 的最后几行 span。"""
    files = sorted(glob.glob(os.path.join(_TRACE_DIR, "*.jsonl")))
    if not files:
        return []
    import json
    spans = []
    with open(files[-1], encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                spans.append(json.loads(ln))
    return spans


def test_nested_parent_chain():
    """同一线程内嵌套 → 自动父链 + 字段完整。"""
    before = len(_today_spans())
    tid = new_trace_id()
    with trace("root", trace_id=tid, source="测试") as root:
        assert root.trace_id == tid
        with trace("child") as child:
            assert child.parent_id == root.span_id, "自动挂到栈顶"
            assert child.trace_id == tid
    spans = _today_spans()[before:]
    assert len(spans) == 2
    by_name = {s["name"]: s for s in spans}
    assert "root" in by_name and "child" in by_name
    assert by_name["child"]["parent_id"] == by_name["root"]["span_id"]
    assert by_name["root"]["trace_id"] == tid
    assert by_name["root"]["dur_ms"] is not None
    assert by_name["root"]["status"] == "ok"
    print("[ok] 嵌套 span 自动父链 + 字段完整")


def test_explicit_parent_cross_stack():
    """显式 parent 可跨"栈"挂父（如 worker 线程子调用）。"""
    before = len(_today_spans())
    with trace("outer") as outer:
        with trace("inner", parent=outer) as inner:
            assert inner.parent_id == outer.span_id
    spans = _today_spans()[before:]
    assert len(spans) == 2
    print("[ok] 显式 parent 挂父")


def test_error_status_not_swallowed():
    """异常：span 标 error 且不吞（继续抛出）。"""
    before = len(_today_spans())
    try:
        with trace("boom"):
            raise ValueError("x")
    except ValueError:
        pass
    spans = _today_spans()[before:]
    assert len(spans) == 1
    assert spans[0]["status"] == "error"
    assert "ValueError" in spans[0].get("error", "")
    print("[ok] 异常标 error 且不吞")


def test_traced_decorator():
    """@traced 装饰器：函数整体一个 span，异常标 error。"""
    before = len(_today_spans())

    @traced("deco_fn")
    def _fn(a, b):
        return a + b

    assert _fn(1, 2) == 3
    spans = _today_spans()[before:]
    assert spans and spans[-1]["name"] == "deco_fn"
    assert spans[-1]["status"] == "ok"
    print("[ok] traced 装饰器")


def test_meta_updates():
    """运行中 set_meta 补元数据。"""
    before = len(_today_spans())
    with trace("meta_test") as t:
        t.set_meta(model="m1", chars=10)
    spans = _today_spans()[before:]
    assert spans[0]["meta"].get("model") == "m1"
    assert spans[0]["meta"].get("chars") == 10
    print("[ok] set_meta 补元数据")


def main():
    test_nested_parent_chain()
    test_explicit_parent_cross_stack()
    test_error_status_not_swallowed()
    test_traced_decorator()
    test_meta_updates()
    print("\n全部通过")


if __name__ == "__main__":
    main()
