# -*- coding: utf-8 -*-
"""宿主运行时：全局插件注册表的惰性加载与便捷查询。

主流程（宠物窗口 / 动画 / 游戏联动…）通过本模块拿扩展点实现，
**不需要知道插件如何加载**。首次调用惰性 :func:`plugin.loader.load_all` 并缓存；
加载器本身出错也返回空注册表，保证宿主照常运行。
"""

from plugin.loader import load_all
from plugin.registry import HostRegistry

_registry = None


def disabled_ids():
    """用户停用的插件 id（QSettings `plugins/disabled`，逗号分隔）。"""
    try:
        from PySide6.QtCore import QSettings
        raw = QSettings("DesktopPet", "PetWindow").value("plugins/disabled", "")
        if not raw:
            return ()
        if isinstance(raw, (list, tuple)):
            return tuple(x for x in raw if x)
        return tuple(x for x in str(raw).split(",") if x)
    except Exception:
        return ()


def registry():
    """全局插件注册表（首次调用时加载并缓存）。"""
    global _registry
    if _registry is None:
        try:
            _registry = load_all(disabled=disabled_ids()).registry
        except Exception:
            _registry = HostRegistry()
    return _registry


def reset():
    """清空缓存，下次调用重新加载（测试用）。"""
    global _registry
    _registry = None


def static_figure():
    """当前 ``assets`` 扩展点提供的静态兜底图路径；无则 ``None``。"""
    impl = registry().assets()
    if impl is None:
        return None
    try:
        return impl.static_figure()
    except Exception:
        return None


def knowledge_qa():
    """当前 ``knowledge-qa`` 扩展点实现；无则 ``None``。"""
    return registry().knowledge_qa()


def qa_context(question, *, session_key="", max_chars=1200):
    """向 ``knowledge-qa`` 扩展点要一段检索上下文；未装插件或出错时返回 ``""``。

    宿主侧统一兜底：**插件抛异常绝不影响对话**（与 :func:`static_figure` 同款策略）。
    """
    impl = registry().knowledge_qa()
    if impl is None:
        return ""
    try:
        out = impl.ask(question, session_key=session_key, max_chars=max_chars)
        return (out or "").strip()
    except Exception:
        return ""
