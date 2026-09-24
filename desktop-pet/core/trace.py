# -*- coding: utf-8 -*-
"""轻量 trace/span 可观测性（P2-4，借鉴 LangChain callbacks / OTel 的树形 span）。

不接云端，**本地落盘** `logs/trace/<date>.jsonl`（每行一个 span，UTF-8）。
用途：把「一次用户消息 → NLU → 主 AI → 工具往返 → 提炼 → TTS」的**父子耗时**
串起来，排查"为什么这次没执行指令 / 为什么慢"。

用法（上下文管理器，自动建立父子链）::

    from core.trace import trace, new_trace_id
    tid = new_trace_id()
    with trace("chat", trace_id=tid, source="聊天") as t:
        with trace("nlu", parent=t):
            ...
        with trace("ai", parent=t):
            ...

线程模型：
- 用**线程局部栈**自动挂父子（同一线程内嵌套即父子），多线程互不串扰；
- 显式传 `parent=t` 可跨线程补父链（如 worker 线程里的子调用）。
- 异常不吞：span 标 `status="error"` 后照样向外抛。
"""
import datetime
import json
import os
import threading
import time
import uuid

_LOCK = threading.Lock()
_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "trace")
_tls = threading.local()


def _now():
    return time.time()


def _ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def new_trace_id():
    """一次「对话/请求」的唯一 trace id。"""
    return uuid.uuid4().hex[:12]


def _stack():
    s = getattr(_tls, "stack", None)
    if s is None:
        s = _tls.stack = []
    return s


class Trace:
    """一个 span（上下文管理器）。进入记开始、退出记结束并落盘。"""

    def __init__(self, name, *, parent=None, trace_id=None, **meta):
        self.name = name
        self.trace_id = trace_id
        self.span_id = uuid.uuid4().hex[:12]
        self.parent_id = parent.span_id if parent is not None else None
        self.meta = dict(meta)
        self.start = None
        self.end = None
        self.status = "ok"
        self.error = ""

    def __enter__(self):
        self.start = _now()
        st = _stack()
        # 显式 parent 优先；否则挂到当前线程栈顶（自动父子）
        if self.parent_id is None and st:
            self.parent_id = st[-1].span_id
        # trace_id 继承父链（须在确定父链后，不能在 __init__ 里）
        if self.trace_id is None:
            self.trace_id = (st[-1].trace_id if (self.parent_id and st)
                             else new_trace_id())
        st.append(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.end = _now()
        st = _stack()
        if st and st[-1] is self:
            st.pop()
        if exc_type is not None:
            self.status = "error"
            self.error = "%s: %s" % (exc_type.__name__, exc)
        _emit(self)
        return False  # 不吞异常

    def set_meta(self, **kw):
        """运行中补元数据（如 AI 调用后补模型/字数）。"""
        self.meta.update(kw)

    @property
    def dur_ms(self):
        if self.start is not None and self.end is not None:
            return int((self.end - self.start) * 1000)
        return None


def trace(name, **kw):
    """便捷入口：返回一个 `Trace` 上下文管理器。"""
    return Trace(name, **kw)


def traced(name, **meta):
    """装饰器：给一个函数整体包一个 span（自动继承调用方父链）。"""
    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrap(*a, **kw):
            with Trace(name, **meta):
                return fn(*a, **kw)
        return wrap
    return deco


def _emit(t):
    """写一行 span 到当天 jsonl（低频，直接追加写即可）。"""
    try:
        os.makedirs(_DIR, exist_ok=True)
        path = os.path.join(_DIR, _ts()[:10] + ".jsonl")
        rec = {
            "trace_id": t.trace_id,
            "span_id": t.span_id,
            "parent_id": t.parent_id,
            "name": t.name,
            "start_ts": _ts(),
            "dur_ms": t.dur_ms,
            "status": t.status,
            "meta": t.meta,
        }
        if t.error:
            rec["error"] = t.error
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
