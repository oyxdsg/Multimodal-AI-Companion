# -*- coding: utf-8 -*-
"""统一重试/退避（P3-2）测试：ai/retry.py。

无框架纯断言，风格同 test_structured.py：
- with_retry：瞬时失败重试成功 / 次数耗尽重抛原异常 / retry_on 过滤永久错误
- retry_call：值驱动成功 / 重试耗尽返回末次结果 / should_retry 判定永久错误立即停
- 退避时序（注入假 sleep）

运行：python tests/test_retry.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.retry import retry_call, with_retry


def test_with_retry_success():
    """前两次抛瞬时异常，第三次成功。"""
    calls = []

    @with_retry(attempts=3, base_delay=0, retry_on=lambda e: "瞬时" in str(e))
    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("瞬时错误")
        return "ok"

    assert fn() == "ok"
    assert len(calls) == 3
    print("[ok] with_retry 瞬时失败重试成功")


def test_with_retry_exhausted_reraises():
    """次数耗尽：重抛最后一次异常（不吞）。"""
    calls = []

    @with_retry(attempts=2, base_delay=0)
    def fn():
        calls.append(1)
        raise ValueError("始终失败")

    try:
        fn()
        assert False, "应抛出"
    except ValueError:
        pass
    assert len(calls) == 2
    print("[ok] with_retry 耗尽重抛原异常")


def test_with_retry_permanent_error():
    """retry_on 返回 False（永久错误）→ 立即抛，不再重试。"""
    calls = []

    @with_retry(attempts=3, base_delay=0,
                retry_on=lambda e: not isinstance(e, KeyError))
    def fn():
        calls.append(1)
        raise KeyError("永久错误")

    try:
        fn()
        assert False
    except KeyError:
        pass
    assert len(calls) == 1, "永久错误不应重试"
    print("[ok] with_retry 永久错误立即停止")


def test_retry_call_value_driven():
    """值驱动：返回 None 重试，成功返回 pcm。"""
    calls = []

    def _attempt():
        calls.append(1)
        return ("pcm", False) if len(calls) >= 3 else (None, False)

    r = retry_call(_attempt, attempts=3, delay=0,
                   should_retry=lambda r: not r[1] and r[0] is None)
    assert r[0] == "pcm"
    assert len(calls) == 3
    print("[ok] retry_call 值驱动成功")


def test_retry_call_permanent_stops():
    """fatal=True（永久错误）→ should_retry False → 立即停。"""
    calls = []

    def _attempt():
        calls.append(1)
        return (None, True)

    r = retry_call(_attempt, attempts=5, delay=0,
                   should_retry=lambda r: not r[1] and r[0] is None)
    assert r == (None, True)
    assert len(calls) == 1, "永久错误应立即停"
    print("[ok] retry_call 永久错误立即停")


def test_retry_call_exhausted_returns_last():
    """重试耗尽：返回最后一次结果（不抛）。"""
    calls = []

    def _attempt():
        calls.append(1)
        return (None, False)   # 永远可重试但不成功

    r = retry_call(_attempt, attempts=3, delay=0,
                   should_retry=lambda r: not r[1] and r[0] is None)
    assert r == (None, False)
    assert len(calls) == 3
    print("[ok] retry_call 耗尽返回末次结果")


def test_backoff_delay_applied():
    """指数退避：注入假 time.sleep 验证延迟递增。"""
    sleeps = []
    _orig = time.sleep
    time.sleep = lambda s: sleeps.append(s)
    try:
        calls = []

        @with_retry(attempts=3, base_delay=0.5, max_delay=4.0)
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("x")
            return 1

        fn()
    finally:
        time.sleep = _orig
    assert sleeps == [0.5, 1.0], sleeps   # 0.5 → 1.0（×2）
    print("[ok] 指数退避时序（%s）" % sleeps)


def main():
    test_with_retry_success()
    test_with_retry_exhausted_reraises()
    test_with_retry_permanent_error()
    test_retry_call_value_driven()
    test_retry_call_permanent_stops()
    test_retry_call_exhausted_returns_last()
    test_backoff_delay_applied()
    print("\n全部通过")


if __name__ == "__main__":
    main()
