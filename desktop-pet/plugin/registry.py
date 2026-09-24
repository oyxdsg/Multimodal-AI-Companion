# -*- coding: utf-8 -*-
"""扩展点注册表 + 注入给插件的门面实现。

* :class:`HostRegistry` —— 宿主侧的注册表，保存各扩展点的实现，按优先级取用。
* :func:`make_host_api` —— 构造注入给某个插件的 :class:`plugin.api.HostAPI`。

**注册是"暂存后合并"的**：loader 给每个插件一个独立的暂存 registry，
``register()`` 成功后才合并进主注册表（见 :mod:`plugin.loader`），
因此插件中途失败不会污染宿主。
"""

from plugin import api


def _order(entries):
    """按 (优先级降序, 加载顺序升序) 排列。同优先级时先加载者优先。"""
    return [e for _, e in sorted(enumerate(entries),
                                 key=lambda t: (-t[1]["priority"], t[0]))]


class HostRegistry:
    """各扩展点的实现集合。跨插件合并，按优先级取用。"""

    def __init__(self):
        self._ai = []
        self._wiki = []
        self._qa = []
        self._assets = []

    # ---------------- 注册 ----------------

    def add_ai_provider(self, plugin_id, priority, profile, factory=None):
        if profile is None or not getattr(profile, "id", ""):
            raise api.HostAPIError("AI 供应商需要 profile 且 profile.id 非空")
        if not getattr(profile, "protocol", ""):
            raise api.HostAPIError("profile 缺少 protocol 字段：%r" % (profile.id,))
        self._ai.append({
            "plugin_id": plugin_id, "priority": int(priority),
            "profile": profile, "factory": factory,
        })

    def add_wiki_knowledge(self, plugin_id, priority, impl):
        if not callable(getattr(impl, "know", None)):
            raise api.HostAPIError("wiki 实现需提供可调用的 know()")
        self._wiki.append({
            "plugin_id": plugin_id, "priority": int(priority), "impl": impl,
        })

    def add_knowledge_qa(self, plugin_id, priority, impl):
        if not callable(getattr(impl, "ask", None)):
            raise api.HostAPIError("知识问答实现需提供可调用的 ask()")
        self._qa.append({
            "plugin_id": plugin_id, "priority": int(priority), "impl": impl,
        })

    def add_assets(self, plugin_id, priority, impl):
        if not (callable(getattr(impl, "actions", None))
                or callable(getattr(impl, "static_figure", None))):
            raise api.HostAPIError("素材实现需提供 actions() 或 static_figure()")
        self._assets.append({
            "plugin_id": plugin_id, "priority": int(priority), "impl": impl,
        })

    def merge(self, other):
        """把另一个（暂存）注册表合并进来。"""
        self._ai.extend(other._ai)
        self._wiki.extend(other._wiki)
        self._qa.extend(other._qa)
        self._assets.extend(other._assets)

    # ---------------- 查询 ----------------

    def ai_entries(self):
        return _order(self._ai)

    def ai_profiles(self):
        return [e["profile"] for e in _order(self._ai)]

    def ai_profile_ids(self):
        return [p.id for p in self.ai_profiles()]

    def adapter_factory(self, protocol):
        """按协议找适配器工厂（最高优先级、且真的提供了工厂的那个）。"""
        for e in _order(self._ai):
            if e["factory"] is not None and e["profile"].protocol == protocol:
                return e["factory"]
        return None

    def wiki_knowledge(self):
        """取优先级最高的 Wiki 知识实现；无则 None（= 不注入）。"""
        ordered = _order(self._wiki)
        return ordered[0]["impl"] if ordered else None

    def wiki_source(self):
        ordered = _order(self._wiki)
        return ordered[0]["plugin_id"] if ordered else ""

    def knowledge_qa(self):
        """取优先级最高的知识问答实现；无则 None（= 不注入任何检索上下文）。"""
        ordered = _order(self._qa)
        return ordered[0]["impl"] if ordered else None

    def knowledge_qa_source(self):
        ordered = _order(self._qa)
        return ordered[0]["plugin_id"] if ordered else ""

    def assets(self):
        """取优先级最高的素材实现；无则 None。"""
        ordered = _order(self._assets)
        return ordered[0]["impl"] if ordered else None

    def assets_source(self):
        ordered = _order(self._assets)
        return ordered[0]["plugin_id"] if ordered else ""

    def plugin_ids(self):
        """注册表里出现过的插件 id（去重，保持顺序）。"""
        out = []
        for bucket in (self._ai, self._wiki, self._qa, self._assets):
            for e in bucket:
                if e["plugin_id"] and e["plugin_id"] not in out:
                    out.append(e["plugin_id"])
        return out


class _HostAPI:
    """:class:`plugin.api.HostAPI` 的真实实现（注入给插件）。"""

    api_version = api.API_VERSION

    def __init__(self, registry, plugin_id, priority=0, logger=None):
        self._reg = registry
        self._plugin_id = plugin_id
        self._priority = int(priority)
        self._logger = logger

    def add_ai_provider(self, *, profile, adapter_factory=None):
        self._reg.add_ai_provider(self._plugin_id, self._priority,
                                  profile, adapter_factory)

    def add_wiki_knowledge(self, impl):
        self._reg.add_wiki_knowledge(self._plugin_id, self._priority, impl)

    def add_knowledge_qa(self, impl):
        self._reg.add_knowledge_qa(self._plugin_id, self._priority, impl)

    def add_assets(self, impl):
        self._reg.add_assets(self._plugin_id, self._priority, impl)

    def llm(self, messages, *, system=None, purpose="", thinking=False):
        """借一次宿主的 LLM 调用（走宿主已配好的供应商与凭据）。

        `ai.utility` 延迟 import：避免 `plugin` ↔ `ai` 循环依赖。
        失败一律返回 ""（不把异常抛回插件）。
        """
        try:
            from ai.utility import utility_chat
            return utility_chat(messages,
                                purpose=purpose or ("plugin_" + self._plugin_id),
                                system_prompt=system, thinking=thinking)
        except Exception:
            return ""

    def log(self, msg):
        if self._logger is not None:
            self._logger(self._plugin_id, msg)


def make_host_api(registry, plugin_id, priority=0, logger=None):
    return _HostAPI(registry, plugin_id, priority=priority, logger=logger)
