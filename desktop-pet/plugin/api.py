# -*- coding: utf-8 -*-
"""插件宿主 · **公共契约**（唯一对外接口）。

设计见 ``DESIGN_OPTIONAL.md`` §三。三条硬约束：

* **插件只依赖本模块**：插件不 import 主体内部模块（``pet`` / ``ai`` / ``game`` …），
  只实现这里的 Protocol，并从宿主注入的 :class:`HostAPI` 拿到注册入口。
* **宿主不认插件名**：宿主只认**扩展点类型**与接口，不认 ``deepseek-web`` 之类的名字。
* **接口只增不改**：新增要素以可选方法 / 字段追加，保证旧插件不碎。

生命周期：宿主发现插件 → 调用其入口 ``register(host)``（host 为 :class:`HostAPI`）
→ 插件通过 ``host.add_*`` 注册实现 → 宿主按**扩展点 + 优先级**选取。
"""

from typing import Protocol, runtime_checkable

#: 宿主插件 API 版本。插件在 ``plugin.json`` 里声明 ``api_version``，宿主校验。
#: 主版本不等即拒绝加载；接口只增不改。
API_VERSION = 1

# ---------------------------------------------------------------- 扩展点类型

TYPE_AI_PROVIDER = "ai-provider"        # AI 后端（供应商 + 协议适配器）
TYPE_WIKI_KNOWLEDGE = "wiki-knowledge"  # 游戏模式知识注入（整块可插拔）
TYPE_KNOWLEDGE_QA = "knowledge-qa"      # 主动知识问答（用户提问 → 检索 → 注入上下文）
TYPE_ASSETS = "assets"                  # 动画素材 / 角色形象
TYPE_TTS = "tts"                        # 预留：语音合成
TYPE_STT = "stt"                        # 预留：语音识别

#: 宿主当前认识的扩展点类型。未知类型会被忽略（不报错），保证前向兼容。
TYPES = (TYPE_AI_PROVIDER, TYPE_WIKI_KNOWLEDGE, TYPE_KNOWLEDGE_QA,
         TYPE_ASSETS, TYPE_TTS, TYPE_STT)

# ---------------------------------------------------------------- 异常


class PluginError(Exception):
    """插件相关错误基类（加载 / 校验 / 注册）。"""


class ManifestError(PluginError):
    """``plugin.json`` 缺失或非法。"""


class HostAPIError(PluginError):
    """插件调用宿主 API 出错（参数不符、扩展点不支持等）。"""


# ---------------------------------------------------------------- 扩展点实现契约


@runtime_checkable
class WikiKnowledgePlugin(Protocol):
    """Wiki 知识扩展点：把游戏事件文本换成"可注入 prompt 的知识"。

    宿主**完全不知道 wiki 的存在**，只做「给事件文本 + 预算，换注入文本」。
    拔掉插件时宿主返回 None（不注入），由大模型凭自身知识作答 —— 这是一等模式。
    """

    def know(self, events_text, *, session_key="", max_chars=480, hints=()):
        """返回注入文本；返回 ``None`` / ``""`` 表示不注入。

        ``hints``：宿主提供的额外检索词（如模组高亮词），插件可选用。
        """
        ...


@runtime_checkable
class KnowledgeQAPlugin(Protocol):
    """主动知识问答扩展点：把「用户的自然语言问题」换成「可注入的检索上下文」。

    与 :class:`WikiKnowledgePlugin` 的分工：

    * ``wiki-knowledge``：宿主喂**游戏事件文本**，插件回知识块 —— 被动注入、事件驱动
    * ``knowledge-qa``：宿主喂**用户提问**，插件回相关上下文 —— 主动检索、提问驱动

    **插件不生成最终回答**：回答始终由主对话 AI 完成，这样人格与口吻保持一致；
    插件内部可用 :meth:`HostAPI.llm` 自行做多步检索 / 重排 / 压缩（Agentic）。
    拔掉插件时宿主不注入任何东西，行为与现在完全一致。
    """

    def ask(self, question, *, session_key="", max_chars=1200):
        """返回可注入 prompt 的知识上下文；返回 ``None`` / ``""`` 表示无可用知识。"""
        ...


@runtime_checkable
class AssetPlugin(Protocol):
    """素材扩展点：提供动作帧与（可选）静态兜底图。

    可以只提供**部分**动作 key；宿主用内置静态图打底，插件按优先级覆盖。
    """

    def actions(self):
        """返回 ``{动作key: [帧, ...]}``；返回 ``None`` 表示不提供动作。"""
        ...

    def static_figure(self):
        """返回静态兜底图路径；无则 ``None``（用宿主默认）。"""
        ...


@runtime_checkable
class HostAPI(Protocol):
    """宿主注入给插件的门面。**插件只应使用这里的方法**。

    实现见 :class:`plugin.registry._HostAPI`；此处为插件侧的类型契约。
    注册是"暂存后合并"的：``register()`` 中途抛异常时该插件已做的注册会被丢弃。
    """

    api_version: int

    def add_ai_provider(self, *, profile, adapter_factory=None):
        """注册一个 AI 供应商。

        * ``profile``：供应商记录（字段对齐 ``ai.providers.ProviderProfile``）。
        * ``adapter_factory``：可选，``factory(provider_id, profile) -> ProtocolAdapter``；
          缺省时按 ``profile.protocol`` 走宿主内置协议适配器。
        """
        ...

    def add_wiki_knowledge(self, impl):
        """注册 Wiki 知识实现（需实现 :class:`WikiKnowledgePlugin`）。"""
        ...

    def add_knowledge_qa(self, impl):
        """注册主动知识问答实现（需实现 :class:`KnowledgeQAPlugin`）。"""
        ...

    def add_assets(self, impl):
        """注册素材实现（需实现 :class:`AssetPlugin`）。"""
        ...

    def llm(self, messages, *, system=None, purpose="", thinking=False):
        """**向宿主借一次 LLM 调用**（走宿主已配好的供应商与凭据）。

        插件**不要**自己读宿主凭据、也不要 import ``ai.*`` —— 需要模型能力时用它。

        * ``messages``：OpenAI 风格 ``[{"role", "content"}, ...]``
        * ``system``：角色提示词（可选）
        * ``purpose``：会话分组键。网页版后端下按 purpose 复用**独立持久会话**，
          不会污染主对话历史；留空按插件 id 分组
        * ``thinking``：是否要求思考
        * 返回文本；**失败返回 ``""``**（插件自行兜底，不要把异常抛回宿主）

        这是**辅助调用**通道，跟随「辅助调用后端」设置（未设置时跟随主对话后端）。
        """
        ...

    def log(self, msg):
        """写一行插件日志（宿主会加插件 id 前缀并落 ``logs/plugin.log``）。"""
        ...
