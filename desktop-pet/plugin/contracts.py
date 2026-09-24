# -*- coding: utf-8 -*-
"""插件**公共数据契约** —— 第三方插件只依赖本模块，不 import 主体内部模块。

设计见 ``DESIGN_OPTIONAL.md`` §三 与 ``docs/PLUGIN_API.md``。

本模块收敛「插件需要知道的数据类型与常量」，是**单一事实来源**：
宿主的 ``ai.providers`` / ``core.action_key`` 与此对齐（测试守卫防漂移）。

硬约束：**不 import Qt / config / 任何宿主内部模块**（纯数据 + 标准库）。
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 传输协议

PROTO_OPENAI = "openai"            # OpenAI 兼容 /chat/completions（含绝大多数厂商）
PROTO_ANTHROPIC = "anthropic"      # Anthropic 原生 /v1/messages
PROTO_DEEPSEEK_WEB = "deepseek-web"  # DeepSeek 网页内部接口（服务端记忆）

PROTOCOLS = (PROTO_OPENAI, PROTO_ANTHROPIC, PROTO_DEEPSEEK_WEB)

# ---------------------------------------------------------------- 能力位（provider 级）

CAP_SERVER_MEMORY = "server_memory"        # 服务端保存历史 → 客户端只发当轮
CAP_SYSTEM_EACH_TURN = "system_each_turn"  # system 每轮重发 → 必须用精简人设
CAP_STREAM = "stream"                      # 支持流式
CAP_ATTACHMENTS = "attachments"            # 端点+适配器都实现了传图/文件
CAP_THINKING = "thinking"                  # 端点支持思考控制
CAP_SEARCH = "search"                      # 支持联网搜索
CAP_SESSION_STATE = "session_state"        # 需要持久化 session_id / parent_id
CAP_TTS = "tts"                            # 同一厂商 Key 可用于语音合成
CAP_CHAT_STREAM = "chat_stream"            # 聊天窗口走流式路径（产品决定）
CAP_TOOLS = "tools"                        # 端点+适配器实现了原生 tool_calls

# ---------------------------------------------------------------- 思考语义

REASONING_KIND_TOGGLE = "toggle"           # 纯开关
REASONING_KIND_EFFORT = "effort"           # 档位枚举（low/medium/high…）
REASONING_KIND_BUDGET = "budget_tokens"    # token 预算

REASONING_TRANSPORT_PARAM = "request_param"  # 靠请求体字段
REASONING_TRANSPORT_MODEL = "model_switch"   # 靠换模型
REASONING_TRANSPORT_NONE = ""

#: 关档的统一取值（各家的"关"表达不同，对外统一成 ``off``）
REASONING_OFF = "off"

# ---------------------------------------------------------------- 动作 key 约定

#: 桌宠动作 key 全集（与 ``core.action_key.ActionKey`` 的值一致，测试守卫防漂移）。
#: ``assets`` 插件的 ``actions()`` 返回的 dict 键应取自这里。
ACTION_KEYS = (
    "idle", "front", "back", "side", "sit", "angry", "rise", "turn",
    "think", "happy", "cry", "shy", "surprised", "sleep", "jump", "roll",
    "dance",
)

#: 必备动作 key：即使素材包只给部分动作，也应有这些（否则用静态图兜底）。
REQUIRED_ACTION_KEYS = ("idle",)

# ---------------------------------------------------------------- 异常


class ProviderError(Exception):
    """AI 后端错误的通用基类（插件异常继承它，宿主只认这个基类）。"""


class ProviderUnavailable(ProviderError):
    """所需后端未安装 / 不可用。"""


# ---------------------------------------------------------------- AI 数据类型


@dataclass(frozen=True)
class ModelInfo:
    """模型级记录。默认值即"未知/不支持"，避免漏填时误报能力。"""

    id: str
    label: str = ""
    attachments: bool = False
    reasoning: tuple = ()             # 可用档位；() = 不支持思考；含 "off" 表示可关
    reasoning_kind: str = ""          # toggle / effort / budget_tokens
    reasoning_transport: str = ""     # request_param / model_switch
    reasoning_default: str = ""       # 默认档
    reasoning_map: tuple = ()         # model_switch 用：((variant, model_id), ...)
    reasoning_min: int = 0            # budget_tokens 下限（真实 API 要求 ≥1024）
    reasoning_max: int = 0
    context_limit: int = 0
    output_limit: int = 0
    cost: tuple = ()                  # (input, output, cache_read, cache_write)，0=未知
    output_modalities: tuple = ("text",)   # 含 "audio" 表示模型原生出语音
    audio_voice: str = ""
    note: str = ""

    @property
    def supports_reasoning(self):
        return bool(self.reasoning)

    @property
    def outputs_audio(self):
        return "audio" in self.output_modalities

    def mapped_model(self, variant):
        """model_switch 类：把档位映射成实际模型 id；无映射返回 None。"""
        for v, mid in self.reasoning_map:
            if v == variant:
                return mid
        return None


@dataclass(frozen=True)
class ProviderProfile:
    """供应商记录。``api`` 可空（原生协议自带端点）；``env`` 声明密钥的环境变量名。"""

    id: str
    label: str
    protocol: str
    group: str
    api: str = ""
    env: tuple = ()
    auth: str = "bearer"              # bearer / x-api-key / cookie
    default_model: str = ""
    models: tuple = ()
    caps: frozenset = field(default_factory=frozenset)
    key_hint: str = ""
    note: str = ""
    #: False = 适配器已就绪但设置界面尚未接入
    selectable: bool = True
    #: 端点是否必须由用户填写（自定义供应商）
    api_required: bool = False
    #: 能力是否**已知**（三态里的「未知」态：自定义端点无从预知）
    caps_known: bool = True

    def cap(self, name):
        """该供应商是否具备指定能力位。"""
        return name in self.caps
