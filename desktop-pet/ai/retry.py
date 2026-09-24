# -*- coding: utf-8 -*-
"""统一重试 / 退避策略（P3-2，借鉴 LangChain 的受控重试 / fallbacks 思路）。

把散落各 client 的「失败重试几次」收敛成两个工具：

* :func:`with_retry` —— **异常驱动**装饰器（适合抛异常的网络调用）。
* :func:`retry_call`  —— **值驱动**重试（适合返回 `None` 表示失败的调用，
  如 qwen TTS：`synth_sentence` 被限流返回 `None`）。

两者的共同约定：**永久错误不重试**（由调用方在 `retry_on` / `should_retry` 里表达），
瞬时故障指数退避重试；重试次数有界（默认 1 次额外尝试），绝不无限等。

用法（值驱动，qwen TTS 即此模式）::

    def _attempt():
        resp = session.post(...)
        if resp.status_code == 200 and pcm:
            return (pcm, False)          # 成功
        if resp.status_code not in RETRY_STATUS:
            return (None, True)          # 永久错误：不再重试
        return (None, False)             # 瞬时故障：可重试

    pcm, _ = retry_call(_attempt, attempts=2, delay=0.6,
                        should_retry=lambda r: not r[1] and r[0] is None)
"""
import functools
import time


class RetryExhausted(Exception):
    """重试次数耗尽（with_retry 最后一次异常仍抛原异常，不抛这个）。"""


def with_retry(attempts=3, base_delay=0.5, max_delay=4.0, backoff=2.0,
               retry_on=None):
    """异常驱动重试装饰器（指数退避）。

    :param attempts: 总尝试次数（含首次）。
    :param base_delay: 首次重试前等待秒数。
    :param max_delay: 退避上限。
    :param backoff: 每次退避翻倍系数。
    :param retry_on: ``callable(异常) -> bool``；None = 全部异常都可重试。
        返回 False 的异常**立即抛出**（视为永久错误）。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            delay = base_delay
            last = None
            for i in range(attempts):
                try:
                    return fn(*a, **kw)
                except Exception as e:          # noqa: BLE001 - 由 retry_on 过滤
                    last = e
                    if i + 1 >= attempts:
                        break
                    if retry_on is not None and not retry_on(e):
                        break
                    time.sleep(delay)
                    delay = min(delay * backoff, max_delay)
            raise last if last is not None else RetryExhausted()
        return wrap
    return deco


def retry_call(fn, attempts=2, delay=0.6, should_retry=None):
    """值驱动重试：调用 ``fn()``，按 ``should_retry`` 决定是否再来一次。

    :param fn: ``() -> result``。
    :param attempts: 总尝试次数（含首次）。
    :param delay: 每次重试前等待秒数（固定间隔）。
    :param should_retry: ``callable(result) -> bool``；None = 不重试（一次即止）。
        ``fn`` 抛异常视为可重试（除非 ``should_retry`` 对异常返回 False ——
        此时立即重抛）。**重试耗尽后返回最后一次结果**（不抛）。
    :return: 最后一次 ``fn()`` 的结果（成功 / 重试耗尽 / 永久错误时的当前结果）。
    """
    result = None
    for i in range(attempts):
        try:
            result = fn()
        except Exception as e:                    # noqa: BLE001
            if i + 1 >= attempts:
                raise
            if should_retry is not None and not should_retry(e):
                raise
        else:
            if should_retry is None or not should_retry(result):
                return result
        if i + 1 < attempts:
            time.sleep(delay)
    return result
