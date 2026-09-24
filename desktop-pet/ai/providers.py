# -*- coding: utf-8 -*-
"""AI 供应商注册表 —— **单一事实来源**（设计见 DESIGN_AI_PROVIDERS.md §二 / §十）。

本模块只放**数据与纯查询**，两条硬约束：
* **不 import Qt**：`config.py` 顶层要 import 本模块，必须保持无 Qt 依赖。
* **不 import config**：避免循环依赖。

为什么要这张表
--------------
原来把两件正交的事焊成了一个字符串三元组（`("deepseek","deepseek-api","qwen")`）：

* **轴 A · 上下文策略**：`stateless`（客户端组装 messages）↔ `server-session`（服务端记忆）
* **轴 B · 传输协议**：`openai` / `anthropic` / `deepseek-web`

拆开后：轴 A 由能力位 `server_memory` **推导**，轴 B 由 `protocol` **选适配器**。
于是「加一家供应商」= 加一条记录，不再改 if 链。

能力分两级（依据 opencode 实证，见 §十）
----------------------------------------
* **provider 级**：**该端点真的支持哪些链接特性**（流式 / 服务端记忆 / 识图 / 联网 …）
* **model 级**：该模型**自身**是否具备（同一家下 Pro 不支持传图、Flash 支持）

**有效能力 = provider.cap AND model.cap**。两层都要，不能只留一层。

> ⚠️ provider 级能力位**只声明真的实现了的东西**：`attachments` 目前**只有网页版**有
> （它实现了 `upload_image_file`）。官方 API / Claude 的模型数据里写了支持传图，
> 但适配器还没实现 OpenAI 兼容的图像输入（content parts），
> 所以**传输级能力位先不开** —— 否则界面会提示"支持传图"却在发送时报错。

id 命名（规避一次撞车）
----------------------
旧值 `"deepseek"` 原本指**网页版**。曾考虑把网页版改名为 `deepseek`、官方 API 改名
`deepseek-api` —— 但那会让**旧值 `deepseek` 与新值 `deepseek` 语义相反**，
老用户的后端选择会被错读成"需要 API Key 的官方 API"。
因此最终只把**网页版**改名 `deepseek-web`（这个名字以前从未出现过，无歧义），
官方 API 保持 `deepseek-api` 不变。旧值映射见 :data:`LEGACY_IDS`。

模型清单来源
------------
预置模型的 id / 能力 / 上下文 / 价格**取自本机 models.dev 缓存**（`opencode models` 同步下来的
真实数据），不是手编。字段对齐 models.dev，将来要接远端 registry 可无损升级。
"""

from plugin.contracts import (
    PROTO_OPENAI, PROTO_ANTHROPIC, PROTO_DEEPSEEK_WEB, PROTOCOLS,
    CAP_SERVER_MEMORY, CAP_SYSTEM_EACH_TURN, CAP_STREAM, CAP_ATTACHMENTS,
    CAP_THINKING, CAP_SEARCH, CAP_SESSION_STATE, CAP_TTS, CAP_CHAT_STREAM,
    CAP_TOOLS,
    REASONING_KIND_TOGGLE, REASONING_KIND_EFFORT, REASONING_KIND_BUDGET,
    REASONING_TRANSPORT_PARAM, REASONING_TRANSPORT_MODEL,
    REASONING_TRANSPORT_NONE, REASONING_OFF,
    ModelInfo, ProviderProfile, ProviderError, ProviderUnavailable,
)

# ---------------------------------------------------------------- 协议

# （协议 / 能力位 / 思考语义常量与数据类型见 plugin.contracts，此处 import 复用）

# ---------------------------------------------------------------- 注册表

#: 默认供应商。**必须指向"永远在"的内置后端**，不能是可选插件（网页版）。
DEFAULT_PROVIDER = "deepseek-api"

#: 旧 provider id → 规范 id（只读回退，保证升级不丢设置）
LEGACY_IDS = {
    "deepseek": "deepseek-web",   # 旧值指网页版
}

#: 旧模型 id → 现役 id（**只读归一化**）。
#: 实机取证：`deepseek-chat` / `deepseek-reasoner` / `deepseek-v4-flash` 都仍能调用，
#: 但服务端会把它们**别名**到 `deepseek-flash`（响应里的 `model` 字段会变）。
#: 不归一化的话，perf 日志会记下"跑的是 deepseek-chat"，而实际跑的是别的模型 ——
#: 排查问题时这是有毒的。注意 `deepseek-reasoner` 别名到的是 **flash 而非 pro**。
LEGACY_MODEL_ALIASES = {
    "deepseek-api": {
        "deepseek-chat": "deepseek-flash",
        "deepseek-reasoner": "deepseek-flash",
        "deepseek-v4-flash": "deepseek-flash",
    },
}


def resolve_model(pid, mid):
    """把历史模型 id 归一化成现役 id（读设置与发请求都走这里）。"""
    return LEGACY_MODEL_ALIASES.get(canonical(pid), {}).get(mid, mid)


# ModelInfo / ProviderProfile 见 plugin.contracts（本文件顶部已 import）。


#: DeepSeek 官方 API 的思考档位。**实机取证**：两个模型对非法取值都返回
#: 422 并回读合法集 —— ``reasoning_effort: expected one of none, minimal, low,
#: medium, high, xhigh, max``（deepseek-flash 与 deepseek-v4-pro 集合相同）。
#: 内部统一用 "off" 表示关，发请求时**整字段省略**（实测省略即不推理：
#: 无该字段时 usage 里没有 reasoning_tokens 且 prompt_tokens 5，
#: 带上后变 31 并出现 reasoning_tokens）。
_DS_EFFORT = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# ---------- 内置供应商 ----------

_PROFILES = (
    ProviderProfile(
        id="deepseek-api",
        label="DeepSeek（官方 API）",
        protocol=PROTO_OPENAI,
        group="官方",
        api="https://api.deepseek.com",
        env=("DEEPSEEK_API_KEY",),
        default_model="deepseek-flash",
        # 模型清单**实机取自** `GET https://api.deepseek.com/models`（只返回这两个）。
        # 历史上写过的 deepseek-v4-flash / deepseek-v4-flash-vision-exp 在官方 API 上
        # 根本不存在（那是百炼侧的名字）—— 照抄 models.dev 会得到一个"选了就报错"的清单。
        models=(
            ModelInfo(
                id="deepseek-flash", label="V4.1 Flash（快·便宜·默认）",
                attachments=True,
                reasoning=_DS_EFFORT, reasoning_kind=REASONING_KIND_EFFORT,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                context_limit=1000000, output_limit=384000,
                cost=(0.15, 0.6, 0.003, 0.0),
            ),
            ModelInfo(
                id="deepseek-v4-pro", label="V4 Pro（强）",
                attachments=False,
                reasoning=_DS_EFFORT, reasoning_kind=REASONING_KIND_EFFORT,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                context_limit=1000000, output_limit=384000,
                cost=(0.435, 0.87, 0.003625, 0.0),
            ),
        ),
        caps=frozenset({CAP_SYSTEM_EACH_TURN, CAP_STREAM, CAP_THINKING,
                        CAP_TOOLS}),
        key_hint="platform.deepseek.com 创建",
        note="无状态：system 每轮重发，故用精简人设。思考是请求参数（reasoning_effort），"
             "不是换模型。旧名 deepseek-chat / deepseek-reasoner / deepseek-v4-flash "
             "仍可调用但会被服务端别名到 deepseek-flash",
    ),
    ProviderProfile(
        id="qwen",
        label="千问（官方 API，流式+语音）",
        protocol=PROTO_OPENAI,
        group="官方",
        api="https://dashscope.aliyuncs.com/compatible-mode/v1",
        env=("DASHSCOPE_API_KEY",),
        default_model="qwen-turbo",
        models=(
            ModelInfo(id="qwen-turbo", label="qwen-turbo（快）",
                      context_limit=1000000, output_limit=8192),
            ModelInfo(id="qwen-plus", label="qwen-plus（强）",
                      context_limit=1000000, output_limit=32768),
            # ---- P9：Omni 系列——**模型自己出语音**（不额外调 TTS）----
            # 实机取证：① 只有 `stream=true` 才有音频（非流式**不返回 audio 字段，
            # 但 usage 里照样有 audio_tokens 被计费**，是个真坑）；
            # ② 音频是 base64 裸 PCM16 @24kHz 单声道，落在 delta.audio.data；
            # ③ 音色集**按模型族不同**（见 voice/tts_catalog.py 的 omni 表）。
            ModelInfo(id="qwen3-omni-flash", label="Omni Flash（可原生出语音）",
                      context_limit=1000000, output_limit=32768,
                      output_modalities=("text", "audio"),
                      audio_voice="Cherry"),
            ModelInfo(id="qwen-omni-turbo", label="Omni Turbo（原生语音·音色少）",
                      context_limit=1000000, output_limit=32768,
                      output_modalities=("text", "audio"),
                      audio_voice="Chelsie",
                      note="实测只有 4 个音色"),
            ModelInfo(id="qwen3.5-omni-flash", label="3.5 Omni Flash（原生语音·音色最多）",
                      context_limit=1000000, output_limit=32768,
                      output_modalities=("text", "audio"),
                      audio_voice="Tina"),
        ),
        caps=frozenset({CAP_SYSTEM_EACH_TURN, CAP_STREAM, CAP_TTS,
                        CAP_CHAT_STREAM, CAP_TOOLS}),
        key_hint="阿里云百炼控制台创建（语音合成也用它）",
        note="流式；与 qwen3-tts 共用 Key。模型 id 在 models.dev 上仍然有效",
    ),
    ProviderProfile(
        id="anthropic",
        label="Anthropic Claude",
        protocol=PROTO_ANTHROPIC,
        group="官方",
        api="https://api.anthropic.com",
        env=("ANTHROPIC_API_KEY",),
        auth="x-api-key",
        default_model="claude-opus-4-8",
        # 模型清单与思考形态取自 models.dev：**同一家不同模型的思考语义不同**
        # （Opus 4.8 是 effort 且 temperature=false；Sonnet 4.5 只有 budget_tokens）
        models=(
            ModelInfo(
                id="claude-opus-4-8", label="Opus 4.8（最强）",
                attachments=True,
                reasoning=("off", "low", "medium", "high", "xhigh", "max"),
                reasoning_kind=REASONING_KIND_EFFORT,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                context_limit=1000000, output_limit=128000,
                cost=(5, 25, 0.5, 6.25),
                note="temperature=false",
            ),
            ModelInfo(
                id="claude-opus-5", label="Opus 5",
                attachments=True,
                reasoning=("off", "low", "medium", "high", "xhigh", "max"),
                reasoning_kind=REASONING_KIND_EFFORT,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                context_limit=1000000, output_limit=128000,
                cost=(5, 25, 0.5, 6.25),
            ),
            ModelInfo(
                id="claude-sonnet-4-6", label="Sonnet 4.6（均衡）",
                attachments=True,
                reasoning=("off", "low", "medium", "high", "max"),
                reasoning_kind=REASONING_KIND_EFFORT,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                context_limit=1000000, output_limit=128000,
                cost=(3, 15, 0.3, 3.75),
            ),
            ModelInfo(
                id="claude-sonnet-4-5", label="Sonnet 4.5（仅 token 预算）",
                attachments=True,
                reasoning=("off", "budget"),   # 该模型只提供 budget_tokens
                reasoning_kind=REASONING_KIND_BUDGET,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                reasoning_min=1024, reasoning_max=64000,
                context_limit=1000000, output_limit=64000,
                cost=(3, 15, 0.3, 3.75),
                note="思考只能用 token 预算表达，没有 effort 档位",
            ),
            ModelInfo(
                id="claude-haiku-4-5", label="Haiku 4.5（快·便宜）",
                attachments=True,
                reasoning=("off", "budget"),
                reasoning_kind=REASONING_KIND_BUDGET,
                reasoning_transport=REASONING_TRANSPORT_PARAM,
                reasoning_default=REASONING_OFF,
                reasoning_min=1024, reasoning_max=64000,
                context_limit=200000, output_limit=64000,
                cost=(1, 5, 0.1, 1.25),
            ),
        ),
        # 注意：**不含 CAP_ATTACHMENTS** —— 模型数据说支持传图，但适配器还没实现
        # Anthropic 的图像内容块，传输级能力位不能先开（否则提示支持却报错）
        caps=frozenset({CAP_SYSTEM_EACH_TURN, CAP_STREAM, CAP_THINKING,
                        CAP_TOOLS}),
        key_hint="console.anthropic.com 创建",
        note="system 是顶层字段；max_tokens 必填；thinking 与 temperature 互斥",
    ),
    ProviderProfile(
        id="custom",
        label="自定义（OpenAI 兼容）",
        protocol=PROTO_OPENAI,
        group="自定义",
        api="",
        default_model="",
        models=(),
        caps=frozenset({CAP_SYSTEM_EACH_TURN, CAP_STREAM, CAP_TOOLS}),
        key_hint="填任意 OpenAI 兼容端点：Ollama / vLLM / LM Studio / 聚合平台…",
        note="能力未知，界面按「未验证」呈现（三态见 §11.4）；模型名手填",
        api_required=True,
        caps_known=False,
    ),
)

PROFILES = {p.id: p for p in _PROFILES}

#: 由**可选插件**提供的供应商 id（未安装时按"未安装"占位，不静默当 OpenAI 兼容）。
#: 网页版（deepseek-web）由 `deskpet-plugin-deepseek-web` 提供。
_OPTIONAL_PROVIDER_IDS = frozenset({"deepseek-web"})


# ProviderError / ProviderUnavailable 见 plugin.contracts（本文件顶部已 import）。


# ---------------------------------------------------------------- 查询（纯函数）

def canonical(pid):
    """把旧 id / 空值归一化成规范 id。未收录的 id 原样返回（自定义供应商）。"""
    if not pid:
        return DEFAULT_PROVIDER
    pid = str(pid)
    return LEGACY_IDS.get(pid, pid)


def builtin_profiles():
    """**内置（静态）**供应商，按内置顺序。

    插件注册时用这个（而非 `profiles()`），避免 `profiles() → 插件加载 → 注册`
    的递归。
    """
    return tuple(_PROFILES)


def _plugin_profiles():
    """插件注册的供应商（如网页版）。插件系统不可用时为空。"""
    try:
        from plugin import host as _ph
        return tuple(_ph.registry().ai_profiles())
    except Exception:
        return ()


def profiles():
    """全部**可用**供应商 = 内置 + 插件注册（去重，内置优先）。"""
    out = list(_PROFILES)
    seen = {p.id for p in out}
    for p in _plugin_profiles():
        if p.id not in seen:
            out.append(p)
            seen.add(p.id)
    return tuple(out)


def profile(pid):
    """取供应商记录；未收录时返回占位（不抛异常，便于容错）。

    * 用户在 `custom` 里填的任意 id → 按 OpenAI 兼容 + 能力未知处理；
    * 曾是可选插件（如网页版）但现在没装 → 返回"未安装"占位（`selectable=False`）。
    """
    pid = canonical(pid)
    for p in profiles():
        if p.id == pid:
            return p
    if pid in _OPTIONAL_PROVIDER_IDS:
        return ProviderProfile(
            id=pid, label=pid, protocol=PROTO_OPENAI, group="未安装",
            caps=frozenset(), selectable=False,
            note="该后端为可选插件，未安装（缺 deskpet-plugin-%s）" % pid,
        )
    return ProviderProfile(
        id=pid, label=pid, protocol=PROTO_OPENAI, group="自定义",
        caps=frozenset({CAP_SYSTEM_EACH_TURN, CAP_STREAM}),
        api_required=True, caps_known=False,
        note="未在注册表内，按 OpenAI 兼容处理，能力未验证",
    )


def exists(pid):
    return any(p.id == canonical(pid) for p in profiles())


def provider_ids():
    """全部可用 id（内置 + 插件）。"""
    return tuple(p.id for p in profiles())


def selectable_ids():
    """设置界面可选的后端 id（`selectable=False` 的不在其中）。"""
    return tuple(p.id for p in profiles() if p.selectable)


def caps(pid):
    return profile(pid).caps


def cap(pid, name):
    return name in profile(pid).caps


def is_messages_backend(pid):
    """走 MessagesBackend（无状态、客户端组装历史）？

    判据是**能力**（没有 server_memory）而不是厂商名 —— 这正是原来
    `MESSAGES_BACKENDS = ("deepseek-api", "qwen")` 那张硬编码表要解决的问题。
    """
    return not cap(pid, CAP_SERVER_MEMORY)


def needs_system_each_turn(pid):
    """system 是否每轮重发（决定用精简人设还是全长人设）。"""
    return cap(pid, CAP_SYSTEM_EACH_TURN)


def chat_streams(pid=None):
    """聊天窗口对本供应商是否走流式路径（产品决定，见 CAP_CHAT_STREAM）。

    `pid` 缺省时读当前供应商；读不到（无 Qt / 未配置）时返回 False。
    """
    if pid is None:
        try:
            from ai.credentials_store import provider as _cur
            pid = _cur()
        except Exception:
            return False
    return cap(pid, CAP_CHAT_STREAM)


def supports_attachments(pid=None):
    """端点+适配器是否**真的**能吃图片（chat.py 的传图门控用这个）。"""
    if pid is None:
        try:
            from ai.credentials_store import provider as _cur
            pid = _cur()
        except Exception:
            return False
    return cap(pid, CAP_ATTACHMENTS)


def needs_base_url(pid):
    """该供应商是否必须由用户提供端点（自定义项）。"""
    return profile(pid).api_required


def caps_known(pid):
    """能力是否已知。False → 界面按「未验证」呈现（§11.4 的第三态）。"""
    return profile(pid).caps_known


def model_ids(pid):
    return tuple(m.id for m in profile(pid).models)


def default_model(pid):
    p = profile(pid)
    return p.default_model or (p.models[0].id if p.models else "")


def model(pid, mid):
    """取模型记录。未收录时**合成**一条：能力只继承 provider 级的附件能力。"""
    p = profile(pid)
    mid = str(mid or "")
    for m in p.models:
        if m.id == mid:
            return m
    return ModelInfo(id=mid, label=mid, attachments=cap(pid, CAP_ATTACHMENTS))


def models(pid):
    return profile(pid).models


def supports(pid, mid, name):
    """**有效能力 = provider.cap AND model.cap**（两级都要看）。"""
    p = profile(pid)
    if name == "attachments":
        return p.cap(CAP_ATTACHMENTS) and model(pid, mid).attachments
    if name == "reasoning":
        return p.cap(CAP_THINKING) and model(pid, mid).supports_reasoning
    if name == "audio":
        return model(pid, mid).outputs_audio
    if name == "stream":
        return p.cap(CAP_STREAM)
    if name == "search":
        return p.cap(CAP_SEARCH)
    if name == "tts":
        return p.cap(CAP_TTS)
    if name == "tools":
        return p.cap(CAP_TOOLS)
    return p.cap(name)


def reasoning_ui(pid, mid):
    """给设置界面用的思考控件描述（三态见 §11.4）。

    返回 ``(kind, values, default, note)``：
    * ``kind`` 为空字符串表示**不支持**（界面应隐藏或灰掉）
    * ``values`` 含 ``"off"`` 时默认是关；不含则默认为第一个
    """
    if not cap(pid, CAP_THINKING):
        return "", (), "", "该供应商不支持思考控制"
    m = model(pid, mid)
    if not m.supports_reasoning:
        return "", (), "", "该模型不支持思考"
    values = tuple(m.reasoning)
    default = m.reasoning_default or values[0]
    return m.reasoning_kind, values, default, m.note


def cost_text(pid, mid):
    """价格一行文本（元/百万 token），未知返回 ""。"""
    m = model(pid, mid)
    if not m.cost:
        return ""
    inp, out = m.cost[0], m.cost[1]
    if not inp and not out:
        return ""
    return "输入 %.3g / 输出 %.3g（每百万 token）" % (inp, out)


def limit_text(pid, mid):
    """上下文一行文本，未知返回 ""。"""
    m = model(pid, mid)
    if not m.context_limit:
        return ""
    if m.output_limit:
        return "上下文 %s / 输出 %s" % (_fmt_k(m.context_limit),
                                       _fmt_k(m.output_limit))
    return "上下文 %s" % _fmt_k(m.context_limit)


def _fmt_k(n):
    if n >= 1000000:
        v = n / 1000000.0
        return ("%gM" % v)
    if n >= 1000:
        return "%gk" % (n / 1000.0)
    return str(n)
