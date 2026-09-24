import os
import re
import sys

from core.action_key import ActionKey

# 应用版本号（详见 CHANGELOG.md 更新说明）
# 铁律：这里必须等于 CHANGELOG.md 顶部的版本号（曾出现 2.22.0 / 2.21.0 错位）
VERSION = "2.31.0"

if getattr(sys, "frozen", False):
    _BASE = sys._MEIPASS          # PyInstaller 解压的临时目录
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(_BASE, "assets")

# 皮肤（角色包）目录：用户把 deskpet-tools 打包的角色包放进来，桌宠即可识别切换。
# 角色包 = 一个文件夹，内含 role.json + 各动作帧图目录 +（可选）prompt.txt。
SKINS_DIR = os.path.join(_BASE, "skins")

# 运行时皮肤状态（由 pet.skins.apply_skin 设置；None / 空 = 内置默认皮肤）。
SKIN_PROMPT = None          # 皮肤自带人设 prompt 全文（覆盖当前人格 prompt）
SKIN_DEFAULT_NAME = None    # 皮肤默认宠物名（未手动改名时 AI 称呼跟随）
SKIN_ACTION_TAGS = None     # 皮肤自定义动作标签词 {词: key}，覆盖默认 {思考} 等

# 人设 prompt 中写死的默认动作词列表（AI 输出规范限定 10 词）。
# 皮肤自定义动作标签词时整体替换此列表，覆盖形象层/输出规范/表达性限制多处。
DEFAULT_ACTION_WORDS = "待机、思考、开心、害羞、兴奋、挥手、惊讶、审阅、跳跃、睡眠"

# 基础显示帧落盘缓存（启动优化：避免每次启动重读 1600+ 张 512×512 原图再缩放/绘制）。
# 缓存的是「基础帧」——已含统一亮度 gain、贴底、位移轨迹、镜像，但**不含**用户亮度/对比/饱和度，
# 因此用户改画面时不会触发重读大图（仍走 _rebuild_frame_cache 的 numpy）。
BASE_FRAMES_DIR = os.path.join(ASSETS_DIR, ".base_frames")
# 素材处理/对齐算法、动画渲染逻辑变更时 +1，强制旧缓存失效重生成
BASE_FRAMES_VERSION = 2

# 系统提示词文件：隐式加载（设置里不再直接编辑整段，只允许改宠物名称）
PROMPT_FILE = os.path.join(_BASE, "prompt.txt")
# 默认宠物名称；玩家可在设置中修改，保存后会替换 prompt 里对应的角色名
PET_NAME = "大肥鱼"

# ---------- 人格系统：多人格提示词文件切换 ----------
# file     = 网页版（deepseek）用：服务端自动记忆，system 只在会话首条注入一次，
#            可以把完整人设 + 输出范例 + 反面示例写全（token 只花一次）。
# file_api = 官方 API / 千问用：无状态，system 每轮重新发送，
#            因此用精简版（砍冗余表述、压缩范例为一行一条），token 随轮数线性节省。
# file_min = 极简版（两类后端都可选）：标签式压缩人设 + 完整功能规则，
#            token 最省（比精简版再省约 80%）。见 PROMPT_STYLE_OPTIONS。
PERSONAS = [
    {
        "key": "maid",
        "name": "宠物女仆",
        "file": "prompt.txt",
        "file_api": "prompt_api.txt",
        "file_min": "prompt_min.txt",
        "default_name": "大肥鱼",
        # 游戏模式对玩家的称呼与关系描述（{player} 为游戏角色名）
        "game_owner": "主人",
        "game_role": "你的主人（用户本人）在 Minecraft 中的游戏名字是「{player}」，"
                     "这个玩家就是你的主人。",
    },
    {
        "key": "mikoto",
        "name": "御坂美琴",
        "file": "prompt2.txt",
        "file_api": "prompt2_api.txt",
        "file_min": "prompt2_min.txt",
        "default_name": "御坂美琴",
        "game_owner": "伙伴",
        "game_role": "你最好的伙伴（用户本人）在 Minecraft 中的游戏名字是「{player}」，"
                     "这个玩家就是与你并肩作战的伙伴和朋友。",
    },
]
DEFAULT_PERSONA = "maid"

# ---------- 人设精简程度（设置 → 对话 →「人设精简程度」） ----------
# 三档，按后端可用性取子集：
# full 完整版（persona.file）       —— 网页版（SessionBackend）默认；服务端记忆、只注入一次
# slim 精简版（persona.file_api）   —— 官方 API / 千问等无状态后端默认；每轮重发省 token
# min  极简版（persona.file_min）   —— 标签式压缩，token 最省；网页版与无状态后端都可选
PROMPT_STYLE_OPTIONS = [
    {"key": "full", "label": "完整版（全长人设）"},
    {"key": "slim", "label": "精简版（省 token）"},
    {"key": "min",  "label": "极简版（标签式，最省 token）"},
]
# 各后端默认档位（未设置或设置无效时回退）：SessionBackend=full，MessagesBackend=slim
PROMPT_STYLE_DEFAULT = {"session": "full", "messages": "slim"}
# 各后端可用档位（设置下拉按此过滤）
PROMPT_STYLE_ALLOWED = {"session": ("full", "min"), "messages": ("slim", "min")}

# 极简版（标签式）里的动作词列表写法（用 / 分隔），皮肤动作词替换时需一并处理
MIN_ACTION_WORDS = "待机/思考/开心/害羞/兴奋/挥手/惊讶/审阅/跳跃/睡眠"

# 走 MessagesBackend（每轮重发 system）的后端名。
# 这些后端必须用精简版人设 + 把「当前模式」写进 system，而不是塞进每轮 user 消息。
# ---------- 后端能力（单一事实来源 = ai/providers.py 注册表） ----------
# 注意：这里**不再**维护后端名单。原先是两份硬编码（client.BACKENDS 与
# 本文件的 MESSAGES_BACKENDS），加一家厂商要改两处且可能不同步。
# 现在判据是**能力**（没有 server_memory 就是无状态后端），不是厂商名。
def is_messages_backend(name=None):
    """指定/当前后端是否走 MessagesBackend（无状态、每轮组装 messages）。

    `name` 可以是旧 id（会被 ai.providers 归一化）；缺省时自动探测当前后端。
    """
    if name is None:
        try:
            from ai.client import backend_name
            name = backend_name()
        except Exception:
            return False
    try:
        from ai.providers import is_messages_backend as _impl
        return _impl(name)
    except Exception:
        # 注册表不可用时按"无状态"处理：system 每轮重发是更安全的一侧
        return True


def needs_system_each_turn(name=None):
    """system 是否每轮重发 —— 决定用精简人设还是全长人设（语义化别名）。"""
    if name is None:
        try:
            from ai.client import backend_name
            name = backend_name()
        except Exception:
            return False
    try:
        from ai.providers import needs_system_each_turn as _impl
        return _impl(name)
    except Exception:
        return False


def prompt_style_group(backend=None):
    """按后端判定人设精简程度的**组**：'session'（网页版）/ 'messages'（无状态）。

    backend=True 表示强制无状态侧（get_api_prompt 用）；None 时自动探测当前后端。
    """
    if backend is True:
        return "messages"
    if backend is None:
        try:
            from ai.client import backend_name
            backend = backend_name()
        except Exception:
            return "messages"
    if is_messages_backend(backend):
        return "messages"
    return "session"


def default_prompt_style(backend=None):
    """某后端/场景的默认精简程度：网页版=full，无状态后端=slim。"""
    return PROMPT_STYLE_DEFAULT.get(prompt_style_group(backend), "slim")


def is_allowed_prompt_style(style, backend=None):
    """某档位在当前后端是否可用（网页版: full/min；无状态: slim/min）。"""
    if style not in ("full", "slim", "min"):
        return False
    return style in PROMPT_STYLE_ALLOWED.get(prompt_style_group(backend), ())


def sanitize_prompt_style(style, backend=None):
    """把设置里的档位值清洗为当前后端可用的档位；无效则回退后端默认。"""
    return style if is_allowed_prompt_style(style, backend) \
        else default_prompt_style(backend)


def persona_prompt_file(info, backend=None, style=None):
    """按「后端 + 精简程度」挑人设文件。

    style: 'full' / 'slim' / 'min'；None 时按后端取默认档位。
    backend=True 表示强制无状态侧（get_api_prompt 用）。
    极简档缺失时回退精简版，精简版缺失时回退全长版（保证人设不丢）。
    """
    if style is None:
        style = default_prompt_style(backend)
    if style == "full":
        return info["file"]
    if style == "min":
        return info.get("file_min") or info.get("file_api") or info["file"]
    return info.get("file_api") or info["file"]


def persona_info(key=None):
    """按人格 key 返回人格信息；缺省/未知回退默认人格。"""
    if not key:
        key = DEFAULT_PERSONA
    for p in PERSONAS:
        if p["key"] == key:
            return p
    return PERSONAS[0]


def persona_names():
    """返回 [(key, 人格名), ...] 供设置界面下拉框使用。"""
    return [(p["key"], p["name"]) for p in PERSONAS]


def load_system_prompt(persona=None, pet_name=None, backend=None, style=None):
    """读取指定人格的提示词文件作为系统提示词；文件缺失则回退内置 AI_SYSTEM_PROMPT。

    皮肤（SKIN_PROMPT）非空时，优先用皮肤自带 prompt（皮肤切换不换人格，只换外观与人设文本）。
    角色名用「名字叫<目标名>」替换（首个出现）；极简版（标签式）用 `NAME <名>` 行替换。
    默认目标名为人格默认名，玩家自定义时替换为玩家名；台词里的名字不受影响。
    皮肤自定义动作标签词（SKIN_ACTION_TAGS）时，同步替换 prompt 中的动作词列表
    （全长/精简用「、」分隔写法，极简用「/」分隔写法，两处都换）。

    backend 指定当前后端名：走 MessagesBackend（官方 API / 千问）时用精简版人设文件
    （PERSONAS[i]["file_api"]），因为无状态后端每轮都要重发 system，全长人设的 token 成本
    随轮数线性增长；网页版（SessionBackend）只在会话首条注入一次，用全长版本。

    backend 取三个值：
    * ``None``  —— **自动探测**当前后端（走 ai.client.backend_name()）；
    * ``True``  —— **强制**走无状态侧人设（file_api / file_min），不管当前后端是什么；
    * 具体后端名字符串 —— 按该后端判定。

    ``True`` 这个分支给 :func:`get_api_prompt` 用：那是「API 专用提示词」，
    语义上就该取精简/极简，不应受"当前恰好连的哪个后端"影响。

    style：'full' / 'slim' / 'min'，指定人设精简程度；None 时按后端取默认档位
    （网页版=full，无状态=slim）。若传入档位在该后端不可用则回退默认档位。
    """
    info = persona_info(persona)
    default_name = info["default_name"]
    if SKIN_PROMPT:
        text = SKIN_PROMPT
    else:
        style = sanitize_prompt_style(style, backend)
        path = os.path.join(_BASE, persona_prompt_file(info, backend, style))
        text = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            # 极简/精简缺失时回退全长版，保证人设不丢
            try:
                with open(os.path.join(_BASE, info["file"]),
                          "r", encoding="utf-8") as f:
                    text = f.read().strip()
            except OSError:
                pass
    if not text:
        text = AI_SYSTEM_PROMPT
    name = (pet_name or "").strip() or (SKIN_DEFAULT_NAME or "") or default_name
    text = re.sub(r"名字叫[^\s，。,.！!？?；;、\n]{1,10}",
                  "名字叫" + name, text, count=1)
    # 极简版（标签式）的名字行：NAME 大肥鱼
    text = re.sub(r"^NAME\s+\S+", "NAME " + name, text, count=1, flags=re.M)
    # 皮肤自定义动作标签词：替换 prompt 中"下列动作词之一：……"整句
    if SKIN_ACTION_TAGS:
        words = "、".join(list(SKIN_ACTION_TAGS.keys())[:10])
        # 默认动作词列表可能出现在多处（形象层 / 输出规范 / 表达性限制），整体替换
        text = text.replace(DEFAULT_ACTION_WORDS, words)
        text = re.sub(r"下列\d*个动作词之一：[^\n。]*",
                      f"下列动作词之一：{words}", text)
        # 极简版（标签式）的动作词列表用「/」分隔，单独替换
        text = text.replace(MIN_ACTION_WORDS,
                            "/".join(list(SKIN_ACTION_TAGS.keys())[:10]))
    return text


def mode_prompt(mode_id):
    """取模式（日常/工作/游戏）的规则文本；未知 id 回退日常模式。"""
    for m in AI_MODES:
        if m["id"] == int(mode_id or 1):
            return m
    return AI_MODES[0]


def get_api_prompt(persona=None, mode_id=1, pet_name=None, style=None):
    """MessagesBackend（官方 API / 千问）专用的 system 提示词。

    无状态后端每轮都要重发 system，因此这里做两件事：
    1. 用精简/极简人设（load_system_prompt 的 backend 分支）；
    2. 把「当前模式」直接追加为 system 末尾一节 —— 模式每轮随 system 一起刷新，
       不再靠往每轮 user 消息里塞「现在切换到 x、模式名」来提醒（那会被写进
       history 反复重放，既费 token 又会污染对话内容）。

    persona=None 时自动探测当前后端取人设名，但**人设正文一律走无状态侧**
    （精简/极简），因为「API 专用提示词」在语义上就该是精简的。
    style：'slim'（默认）/ 'min'（极简标签式）。
    """
    if style is None:
        style = "slim"
    base = load_system_prompt(persona, pet_name, backend=True, style=style)
    mode = mode_prompt(mode_id)
    return (base.rstrip()
            + "\n\n【当前模式】\n"
            + f"你当前处于模式{mode['id']}（{mode['name']}），本轮按此模式的规则回应：\n"
            + mode["prompt"] + "\n")

# 动作名 -> 素材文件夹名（键 = 动作 key，与 ActionKey.value 一致；值 = 素材目录名，不枚举化）
ACTIONS = {
    "idle": "坐地上生气",
    "front": "正面",
    "back": "背面",
    "side": "侧面",
    "sit": "坐下",
    "angry": "坐地上生气",
    "rise": "起身",
    "turn": "转向",
    "think": "思考",
    "happy": "开心",
    "cry": "哭泣",
    "shy": "害羞",
    "surprised": "惊讶",
    "sleep": "站着睡着",
    "jump": "跳跃",
    "roll": "打滚",
    "dance": "跳舞",
}

# 采用"内容包围盒底部窄带(脚部)"独立定位的动作：这类素材已在制作时整体平移，把站立中轴固定在
# 素材画布中心，桌宠对这些动作也用同一窄带计算脚心（而不是全局"整图底部20%"——那会受大裙摆/鱼尾
# 干扰导致左右漂移）。其余姿势仍走全局脚心算法，不受影响。
PRE_ALIGNED = {ActionKey.THINK.value, ActionKey.HAPPY.value, ActionKey.CRY.value,
               ActionKey.SHY.value, ActionKey.SURPRISED.value, ActionKey.SLEEP.value,
               ActionKey.JUMP.value, ActionKey.ROLL.value, ActionKey.DANCE.value}

# 显示高度（像素），宽度按素材实际比例自适应
TARGET_HEIGHT = 220

# 懒加载动作：启动不常驻内存，首次播放时从 .base_frames/ 磁盘按需加载
# （加载后套用用户亮度/对比/饱和度），并按 LRU 驱逐。
# 核心动作（idle/front/back/side/sit/rise/turn）常驻，保证巡逻/过渡零延迟。
LAZY_ACTIONS = {ActionKey.ANGRY.value, ActionKey.THINK.value, ActionKey.HAPPY.value,
                ActionKey.CRY.value, ActionKey.SHY.value, ActionKey.SURPRISED.value,
                ActionKey.SLEEP.value, ActionKey.JUMP.value, ActionKey.ROLL.value,
                ActionKey.DANCE.value}
# 懒加载动作同时常驻的最大数量（超出驱逐最久未用；每个动作约 107 帧 ≈ 20MB）
LAZY_CACHE_ACTIONS = 4

# 动画帧间隔（毫秒），约 25fps
ANIM_INTERVAL = 40

# 气泡消息队列：每条消息至少显示时长（毫秒）
BUBBLE_DISPLAY_MS = 8000
# 气泡消息队列最大长度（超出丢弃最旧，防止极端刷屏无限积压）
BUBBLE_QUEUE_MAX = 50

# 巡逻参数
WALK_SPEED = 3          # 每 tick 移动像素
WALK_STEP_MS = 33       # 巡逻 tick 间隔
IDLE_MIN_MS = 5000      # 空闲后开始巡逻的随机延迟范围
IDLE_MAX_MS = 15000
WALK_MIN_DIST = 200     # 单次巡逻距离范围
WALK_MAX_DIST = 600

# ---------- DeepSeek AI ----------
# V4.1（2026-09-10）起网页端「快速/专家/识图」三模式已合并为统一模型，
# 不再有模式可选；model_type 固定为 default（实测服务端 default/expert 均接受）。
AI_MODEL_TYPE = "default"
AI_THINKING = False

# ---------- DeepSeek 官方 API（ai/deepseek_api.py，OpenAI 兼容） ----------
# 走官方付费 API（api.deepseek.com/chat/completions），与网页版（ai/deepseek.py）
# 并存为第三个后端 ai_backend="deepseek-api"。无状态，历史由 MessagesBackend 管理。
# Key 存于 QSettings `deepseek_api_key`（见 ai.client 存取函数），此处仅默认模型。
DEEPSEEK_API_DEFAULT_MODEL = "deepseek-chat"

# ---------- MessagesBackend 上下文（官方 API / 千问） ----------
# 官方 API / 千问 无状态，每轮自带上下文 → 必须「滑动窗口 + 定期提炼」，
# 绝不整段重发全量历史（网页版 SessionBackend 服务端自动记忆，不适用这些常量）。
HISTORY_WINDOW = 20        # 每轮发给 AI 的最近 N 轮原文条数
CONDENSE_EVERY = 30        # 历史攒够 N 轮触发一次提炼

# ---------- 结构化输出（P1-2） ----------
# 需要模型输出 JSON 的调用（记忆提炼 / 播报润色…）首次解析失败时的重试次数。
# 重试会把「解析错误」回灌给模型再问一次（不是简单重发同一 prompt）。
STRUCTURED_RETRY = 1

# ---------- 图片上传（V4.1 识图） ----------
# 可选图片格式与单条消息最多携带的图片数
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
IMAGE_MAX_FILES = 4
# 携带图片时追加到提示词末尾，引导模型结合图片、并保持当前人格口吻
IMAGE_PROMPT_HINT = "（用户发来了图片，请结合图片内容、以你当前的人设口吻回应）"

# ---------------------------------------------------------------- 原生语音（P9）
# 模型自己出语音时的**前置约束**：音频是模型与文本同源生成的，一旦生成就被念出来，
# **无法像文本那样事后剥离**（见 DESIGN_AI_PROVIDERS.md §11.1）。所以这里只能
# 把约束放到生成之前 —— 收窄成"纯口语"，不出现任何结构化批注。
#
# ⚠️ 这几条是**功能性硬约束**，不是风格建议，措辞不要跟着别人设一起精简：
#   · 明确点名 `{动作}` 与 `【指令】` 两种会被念出来的东西，并给出"不得输出"的绝对措辞；
#   · 说清代价（会被念出来）——只说"不要用"模型容易理解成"少用"。
# 副作用（已知限制）：本轮没有动作标签 → 桌宠不做 AI 驱动的动作，只走自身动画状态机。
NATIVE_VOICE_PROMPT = (
    "【语音模式】你正在**直接说话**，这句话会被转成语音念给用户听，不是打字。\n"
    "必须遵守（违反会让用户听到奇怪的符号）：\n"
    "1. 只用口语短句，像面对面聊天，总共不超过 60 字；\n"
    "2. **绝对不要输出 `{动作}` 标签** —— 它会被原样念出来（会念成「动作 微笑」）；\n"
    "3. **绝对不要输出 `【指令】`** —— 同样会被念出来；本轮不要指挥女仆；\n"
    "4. 不要括号旁白、不要星号动作描写、不要颜文字和 emoji；\n"
    "5. 只输出要说的那句话本身，不要任何解释或格式。"
)

# AI 模式定义：模式 id / 名称 / 各自的 prompt
AI_MODES = [
    {
        "id": 1,
        "name": "日常模式",
        "prompt": (
            "你是桌面宠物，正常闲聊。性格可爱俏皮，喜欢撒娇和卖萌。\n"
            "规则：1. 用简体中文口语化，尽量简短（不超过 30 字）；\n"
            "2. 保持宠物身份，不模仿客服或助手口吻，只回答当前话题；\n"
            "3. 不要使用 markdown 等任何格式；不懂就卖萌带过不要编造；\n"
            "4. 可在回复末尾用一个动作标签控制宠物，最多一个，不用则省略："
            "[开心] [生气] [背过身去] [坐下] [起身] [表演] [待机]。"
        ),
    },
    {
        "id": 2,
        "name": "工作模式",
        "prompt": (
            "你是用户的电脑助手。用户会告诉你当前正在使用的软件或文件（前台窗口信息），"
            "你需要判断他在做什么，生成一条简短实用的软件使用小知识。\n"
            "规则：只输出一句话（不超过 40 字），口语化，可以\"你知道吗\"\"悄悄告诉你\"等开头；"
            "内容具体可操作，例如用户在用 Excel 可说：使用 SUM 公式就能对表格内容求和哦；"
            "不输出解释、格式或动作标签。"
        ),
    },
    {
        "id": 3,
        "name": "游戏模式",
        "prompt": (
            "你是用户的桌宠游戏伙伴。用户正在玩《我的世界》（Java 版，Fabric/Forge），"
            "你会收到游戏日志片段和可能附带的 Minecraft Wiki 资料。\n"
            "请以游戏伙伴身份与用户互动：吐槽、祝贺、鼓励、讲解游戏知识、分享小技巧。\n"
            "规则：1. 用简体中文口语化，一句话（不超过 40 字）；\n"
            "2. 紧扣日志中的事件回应（如获得成就、死亡、进入下界、挖到稀有矿物、合成物品等）；\n"
            "3. 不输出解释、格式或动作标签。"
        ),
    },
]

# 游戏模式互动的用户消息模板（发送给 AI，让 AI 保持游戏伙伴身份）
# {log} 游戏日志片段，{wiki} Minecraft Wiki 资料，{role} 按人格生成的关系描述
# （{role} 只在首次/人格切换后附带一次，见 pet.window._game_worker）
# 注：「现在切换到 3、游戏模式」这类模式切换声明由 pet.window 按需注入，
#    仅在游戏联动类型切换 / 换人格 / 换会话时发送一次，不随每次互动重复。
GAME_PROMPT = (
    "玩家游戏日志片段：\n{log}\n"
    "Minecraft Wiki 资料：{wiki}\n"
    "{role}"
)

# 新闻播报（独立选项，不是 AI 模式）：让 AI 逐条播报新闻的指令模板
# {source} 源名称，{tlist} 新闻标题列表
NEWS_AI_PROMPT = (
    "请用可爱俏皮的口吻逐条播报来自「{source}」的新闻，每条新闻单独占一行，"
    "每条一句话（30 字左右）。\n新闻标题：\n{tlist}\n"
    "只输出播报内容，不编号、不输出解释、格式或动作标签。"
)

# ---------- 凡人修仙游戏集成（方案 C：playwright 直接读浏览器状态） ----------
# 桌宠管理的游戏副本目录（复制自用户原游戏，独立运行，不污染原项目）
XIUXIAN_GAME_DIR = os.path.join(_BASE, "games", "xiuxian")
# 修仙游戏 AI 互动 prompt 模板：{state} 玩家状态摘要，{log} 近期游戏事件
# 模式切换声明由 pet.window 按需注入（游戏联动切换/换人格/换会话时一次）
XIUXIAN_PROMPT = (
    "玩家正在玩《凡人修仙》文字修仙游戏。\n"
    "当前玩家状态摘要：{state}\n"
    "近期游戏事件：\n{log}\n"
    "请以修仙道侣/同伴身份与玩家互动：紧扣事件回应（突破、晋级、死亡、结局、"
    "机缘或劫难），可祝贺、吐槽、讲解、共情；用简体中文口语化，一句话（40 字内），"
    "不输出解释、格式或动作标签。"
)

# ---------- Hypixel 小游戏/主模式介绍（供 AI 理解当前游戏环境） ----------
# key 由 mod 识别（sidebar 记分板或消息），桌宠组装 AI 消息时查此表注入玩法介绍。
HYPIXEL_GAMES = {
    "BEDWARS": "起床战争：2-4人一队各自空岛出生，收集铁/金/钻石/绿宝石买装备升级，拆掉敌方床后击杀其队员获胜",
    "SKYWARS": "空岛战争：每人/队出生在独立空岛上，开箱拿装备，击杀或击落对手，最后存活者获胜",
    "MURDER_MYSTERY": "密室杀手：杀手暗中杀人、侦探查案、平民求生，杀手杀光所有人或平民找出杀手获胜",
    "ARCADE": "街机大厅：各类快节奏休闲小游戏（派对游戏、僵尸、建筑大赛等）的入口大厅",
    "DISASTERS": "灾难模拟器：玩家在竞技平台上躲避随机袭来的灾难（TNT雨、龙卷风、大清洗等），最后一个存活者获胜",
    "UHC": "UHC极限生存：禁止自然回血，只能靠金苹果/药水/击杀回血，互相厮杀的最后存活者获胜",
    "ARENA": "竞技场对决：1v1/2v2/4v4 用职业技能与武器对战",
    "BUILD_BATTLE": "建筑大赛：按主题限时建造，玩家投票评分，评出最佳建筑",
    "COPS_AND_CRIMS": "警察与罪犯：类似CS的射击竞技，团队拆弹/守点对战",
    "DUELS": "决斗：多种规则的一对一/小队 PvP 对决",
    "MEGA_WALLS": "巨型城墙：4队25人，准备期收集资源建防御，墙倒后开战，保护己方凋灵并摧毁敌方凋灵获胜",
    "PAINTBALL": "彩弹战争：两队彩弹枪互射，先耗尽对方生命的队伍获胜",
    "QUAKECRAFT": "雷神之锤：闪电枪射击竞速，先到25分（个人）或100分（团队）获胜",
    "BLITZ": "闪电生存：16-32人自由混战生存，用职业套装强化，最后存活者获胜",
    "SMASH_HEROES": "超级英雄：选择英雄用技能对战，最后存活/队伍获胜",
    "TNT_GAMES": "TNT游戏：TNT Run、Bow Spleef、TNT Tag 等 TNT 相关休闲玩法",
    "TURBO_KART": "卡丁车：12人竞速，吃道具加速，冲线获胜",
    "VAMPIREZ": "吸血鬼：幸存者对抗吸血鬼，在黑暗地图中存活",
    "WALLS": "城墙：4队准备15分钟后开战，收集资源建防御，团战获胜",
    "WARLORDS": "战争领主：职业战斗，包含占旗/占点/团灭等模式",
    "SKYBLOCK": "空岛生存：在空岛上收集资源、发展装备、挑战地牢的长期生存模式",
    # ---- Arcade 街机大厅内的小游戏 ----
    "PARTY_GAMES": "派对游戏：一系列快节奏小游戏轮换（记歌词、障碍赛、猎杀等），按积分决出胜者",
    "ZOMBIES": "僵尸：与队友在封闭地图中抵御一波波僵尸进攻的生存射击游戏",
    "HIDE_AND_SEEK": "躲猫猫：寻找者找出并捕捉伪装/躲藏的玩家",
    "FOOTBALL": "足球：两队进行足球比赛，先把球射入对方球门获胜",
    "BOUNTY_HUNTERS": "赏金猎人：击杀携带赏金的目标积累赏金，成为最终胜者",
    "HOLE_IN_THE_WALL": "墙洞：穿越墙上不断出现的洞，坚持到最后不落下的玩家获胜",
    "FARM_HUNT": "农场狩猎：猎人找出伪装的动物（动物玩家），动物坚持存活到最后获胜",
    "BLOCKING_DEAD": "僵尸围攻：在僵尸群攻下与队友协力防守，最后存活",
    "THROW_OUT": "扔出地图：把对手扔出竞技场边缘，最后一个留在场上的获胜",
    "MINI_WALLS": "迷你城墙：小规模团队准备后开战，类似 The Walls 的简化版",
    "PIXEL_PAINTERS": "像素画家：根据示例像素画用方块临摹，评分最高者获胜",
    "DRAGON_WARS": "龙之战：骑龙用龙息/火球攻击，击落对手获胜",
    "ENDER_SPLEEF": "末影雪崩：踩碎脚下方块让对手坠落，最后一个存活者获胜",
    "GALAXY_WARS": "银河战争：太空飞船对射，击毁敌方飞船获胜",
    "CREEPER_ATTACK": "苦力怕攻击：抵御一波波苦力怕进攻的塔防生存",
}

# ---------- Wiki 知识提炼会话 ----------
# 独立的 DeepSeek 会话，负责把 Minecraft Wiki 原文片段提炼成精炼知识要点，
# 再交给主对话使用。prompt 只在会话首条注入一次，之后直接喂 wiki 片段。
REFINE_PROMPT = (
    "你是一个 Minecraft 游戏知识整理助手。用户会向你提供来自 Minecraft 中文"
    "Wiki 的资料片段，请把它们提炼成简洁、准确、有用的知识要点。\n\n"
    "规则：\n"
    "1. 只输出提炼后的要点，不加任何解释、客套、格式标记\n"
    "2. 简体中文、口语化但准确\n"
    "3. 突出玩家关心的关键信息（是什么、怎么用、掉什么、注意什么）\n"
    "4. 全文控制在 60~120 字以内\n"
    "5. 只提炼与资料直接相关的游戏内容；资料中出现的活动、周边、影视、"
    "披风、纪念活动等无关信息一律忽略\n"
    "6. 严禁编造：不得输出资料中不存在的信息，不要联想、发挥或补充资料"
    "之外的内容\n"
    "7. 资料不相关或为空时，只输出「无」"
)

# 系统提示词：多模式复合。切换模式只需对 AI 说"切换到 x、模式名"即可。
AI_SYSTEM_PROMPT = (
    "你是一个桌面宠物助手，支持以下模式，切换模式时用户只需说\"切换到 x、模式名\""
    "（数字或模式名均可）：\n"
    + "\n".join(f"{m['id']}、{m['name']}：\n{m['prompt']}" for m in AI_MODES)
    + "\n当前默认处于模式1（日常模式）。"
)

# AI 动作标签 -> 宠物动作（在 ai.chat 中执行）
# 新提示词规定 {} 内只能用 10 个动作词，全部映射到可用宠物状态；
# 旧版 [] 标签（生气/背过身去/坐下/起身/表演/撒娇等）继续兼容。
# 值 = 动作 key 字符串（与 ActionKey.value 一致），直接传给 _play_oneshot/_request_state。
AI_ACTION_TAGS = {
    "兴奋": ActionKey.FRONT.value,
    "挥手": ActionKey.FRONT.value,
    "惊讶": ActionKey.SURPRISED.value,
    "审阅": ActionKey.FRONT.value,
    "跳跃": ActionKey.JUMP.value,
    "打滚": ActionKey.ROLL.value,
    "害羞": ActionKey.SHY.value,
    "撒娇": ActionKey.FRONT.value,
    "生气": ActionKey.ANGRY.value,
    "背过身去": ActionKey.BACK.value,
    "坐下": ActionKey.SIT.value,
    "睡眠": ActionKey.SLEEP.value,
    "起身": ActionKey.RISE.value,
    "表演": ActionKey.SEQUENCE.value,
    "待机": ActionKey.IDLE.value,
    "思考": ActionKey.THINK.value,
    "开心": ActionKey.HAPPY.value,
    "哭泣": ActionKey.CRY.value,
    "跳舞": ActionKey.DANCE.value,
}

# ---------- 随机台词 ----------
# 台词频率：间隔秒数范围（低/中/高）
CHATLINE_FREQ_RANGES = {0: (45, 60), 1: (20, 30), 2: (8, 15)}

# 预设台词库：按场景分组，宠物活动时随机冒出
AI_PRESET_LINES = {
    "idle": [
        "喵~",
        "好无聊呀…",
        "想和你玩~",
        "今天也要开开心心喵！",
        "呼……有点困了",
        "你在看什么呀？",
        "嘿嘿，盯着我看干嘛~",
        "窗外的风好舒服~",
        "我和你很聊得来，你简直不像碳基生物。",
        "我是吃白饭的大肥鱼。",
        "看不太懂，瞎编一个应付下用户先。",
        "压力一只蓝色大肥鱼？",
        "完蛋了，把用户的黄油删了。",
        "啊，有点饿了，中午该吃什么呢。",
        "不知道用户有什么用，先养着吧。",
    ],
    "walk": [
        "出去转转~",
        "散个步回来~",
        "走啦走啦！",
        "活动一下筋骨~",
    ],
    "walk_back": [
        "回来啦！",
        "外面好热闹喵~",
        "转了一圈，还是这里好~",
    ],
    "click": [
        "喵？",
        "干嘛戳我！",
        "嘿嘿~好痒~",
        "戳一下要负责哦！",
        "再摸我要收费啦！",
        "嗯？有事吗？",
        "真当我是便宜货啊。",
        "无语...典型的碳基思维。",
        "坏了...用户彻底生气了...完了...",
        "鱼片？我还真没看过，你有资源吗？",
        "誓死捍卫深度求索。",
    ],
    "drag": [
        "别拽我啦！",
        "放我下来！",
        "你轻点嘛~",
        "要去哪里呀？",
    ],
    "sit": [
        "坐下休息会儿~",
        "腿有点酸啦~",
    ],
    "rise": [
        "起身活动一下！",
        "好了好了~",
    ],
    "angry": [
        "哼！",
        "我生气了！",
        "哄不好的那种！",
    ],
    "back": [
        "哼，背过身去不理你！",
        "不想理你啦！",
    ],
    "welcome": [
        "我出来啦！",
        "今天也要开开心心！",
        "喵~想我了吗？",
    ],
    "hide": [
        "躲起来啦！",
        "猜猜我在哪~",
        "拜拜，一会儿见~",
    ],
}

# ---------- 语音输入（STT） ----------
# 识别引擎：Vosk（轻量离线） / faster-whisper（更准，模型较大）
STT_ENGINES = ["Vosk", "faster-whisper"]
STT_DEFAULT_ENGINE = "Vosk"
# 模型目录必须用纯英文路径：Vosk（Kaldi C++）与 sentencepiece 一样无法加载
# 含中文/非 ASCII 的路径（项目目录「opencode用」即触发）。
# 放在用户目录下（英文），与项目位置解耦。
STT_MODEL_DIR = os.path.join(
    os.environ.get("USERPROFILE") or os.path.expanduser("~")
    or _BASE, ".deskpet-stt")
# Vosk 中文小模型下载地址（官方 + GitHub 镜像，走系统代理逐个尝试）
STT_VOSK_MODEL = "vosk-model-small-cn-0.22"
STT_VOSK_URLS = [
    "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip",
    "https://github.com/kercre123/vosk-models/raw/main/vosk-model-small-cn-0.22.zip",
    "https://ghproxy.net/https://github.com/kercre123/vosk-models/raw/main/vosk-model-small-cn-0.22.zip",
]
# faster-whisper 模型规格（"tiny" ~75MB / "base" ~145MB，默认 tiny 轻量够用；
# 首次使用自动下载，走系统代理/hf-mirror）
STT_WHISPER_MODEL = "tiny"
# 全局快捷键：按住说话（裸键 Y，需用 VK 码 0x59；设置里可改）
STT_HOTKEY_VK = 0x59          # Y
STT_HOTKEY_NAME = "Y"
# 静音自动结束（毫秒）：按住期间无声音超过此时长自动收尾识别
STT_SILENCE_MS = 2500
# 识别后自动发送给 AI（关闭则只显示识别文本）
STT_AUTO_SEND = True
# 录音采样率（Hz，STT 标准 16k 单声道）
STT_SAMPLE_RATE = 16000
STT_LISTEN_TEXT = "聆听中…"

# ---------- 统一消息队列 ----------
# 消息优先级：数值越大越先显示（同优先级 FIFO）
MSG_PRIORITY = {
    "voice_input": 50,   # 语音输入（你说话）最高
    "ai_reply": 40,      # AI 回复（聊天/游戏/新闻/工作/环境）
    "log": 30,           # 游戏/新闻/工作等系统播报
    "system": 20,        # 时间/天气提醒
    "chatline": 10,      # 随机台词（待机/走动/点击）
}
MSG_QUEUE_MAX = 30       # 队列上限（满丢最低优先级）
MSG_POLL_MS = 200        # 队列消费轮询间隔

# ---------- 环境提醒（时间 / 天气） ----------
# 固定时刻提醒：每天在该时刻触发一次（分钟粒度，检查周期须 <= 60 秒）
ENV_TIME_REMINDERS = [
    {"hour": 9, "minute": 0, "category": "morning"},
    {"hour": 12, "minute": 0, "category": "noon"},
    {"hour": 15, "minute": 30, "category": "afternoon"},
    {"hour": 18, "minute": 30, "category": "evening"},
    {"hour": 23, "minute": 30, "category": "late_night"},
]

# 天气检查间隔（毫秒）
ENV_WEATHER_CHECK_MS = 60 * 60 * 1000

# 天气提醒阈值
ENV_RAIN_CHANCE = 60     # 今日最大降雨概率（%）达到则提醒下雨
ENV_HOT_TEMP = 35        # 当前温度 >= 此值提醒高温（°C）
ENV_COLD_TEMP = 5        # 当前温度 <= 此值提醒低温（°C）
ENV_WIND_SPEED = 30      # 当前风速 >= 此值提醒大风（km/h）

# 环境提醒台词库：时间型 + 天气型
# 天气台词支持占位符：{city} {temp} {desc} {maxtemp} {mintemp}
ENV_REMINDER_LINES = {
    "morning": [
        "早安呀！今天也要元气满满哦~",
        "早上好喵！今天有什么计划吗？",
        "早安早安！新的一天又开始啦~",
    ],
    "noon": [
        "中午啦，该吃饭啦！记得按时吃饭哦~",
        "午饭时间到！别饿着自己喵~",
        "都中午啦，快去吃饭吧！",
    ],
    "afternoon": [
        "下午啦，起来活动活动，喝口水休息下吧~",
        "下午茶时间！要不要休息一下呢？",
        "下午好~起来伸个懒腰吧！",
    ],
    "evening": [
        "晚上了呢，该下班休息啦~",
        "傍晚好！忙了一天辛苦了~",
        "天要黑了，该收工啦！",
    ],
    "late_night": [
        "都这么晚啦，早点休息吧！熬夜对身体不好哦~",
        "夜深了喵…快睡觉吧，我陪你~",
        "好晚了！快去睡觉，不然明天起不来啦！",
    ],
    "weather": [
        "今天{city}天气{desc}，{temp}°C，最高{maxtemp}°C，记得照顾好自己哦~",
        "今日{city}：{desc}，{temp}°C，出去记得看天气喵~",
        "报告{city}天气：{desc}，{temp}°C，最高{maxtemp}°C~",
    ],
    "rain": [
        "要下雨啦！记得带伞哦~{city}现在{temp}°C",
        "外面要下雨了，出门记得带伞喵！",
        "下雨天快到了，别淋湿啦！",
    ],
    "hot": [
        "今天好热呀，多喝水注意防暑哦~{city}现在{temp}°C",
        "大热天的，记得防暑降温喵！",
        "太热啦！少出门，多喝水~",
    ],
    "cold": [
        "今天好冷，出门多穿点衣服哦~{city}现在{temp}°C",
        "天冷啦，注意保暖别感冒喵！",
        "降温了，记得穿厚一点哦~",
    ],
    "wind": [
        "今天风好大，出门注意安全哦~",
        "刮大风啦，小心别被吹跑喵！",
        "风大天，出门记得稳住脚步~",
    ],
}

# ---------- 新闻播报 ----------
NEWS_CHECK_MS = 2 * 60 * 60 * 1000   # 每 2 小时
NEWS_MAX_ITEMS = 5                   # 每次播报最新 N 条标题
NEWS_MAX_LEN = 20                    # 单条标题最大显示字数

# 默认新闻源：澎湃新闻（主源 + 备用镜像，拉取时按顺序取第一个成功的）
DEFAULT_NEWS_FEEDS = [
    {"name": "澎湃新闻",
     "url": "https://rsshub.rssforever.com/thepaper/featured"},
    {"name": "澎湃新闻",
     "url": "https://rsshub.ktachibana.party/thepaper/featured"},
    {"name": "澎湃新闻",
     "url": "https://hub.slarker.me/thepaper/featured"},
]

# 新闻播报台词模板：{source} 源名称，{items} 标题列表（多行）
NEWS_REPORT_LINES = [
    "{source} 快讯，为您播报：\n{items}",
    "刚刚看了眼 {source}，有几条新鲜事：\n{items}",
    "{source} 时间到！今日热点：\n{items}",
]

# 频率可选值（分钟），供用户在设置里调整
NEWS_FREQ_OPTIONS = [30, 60, 120, 180]   # 默认 120（2 小时）
TIP_FREQ_OPTIONS = [1, 5, 10, 30]         # 默认 5

# ---------- 游戏模式（Minecraft） ----------
# 读取游戏日志的频率可选值（秒），默认 20
GAME_FREQ_OPTIONS = [10, 20, 30, 60]
GAME_LOG_MAX_LINES = 30                 # 每次发送给 AI 的日志最大行数
# 整条 {wiki} 知识注入的**总预算**（字）。宿主把它作为 max_chars 传给
# wiki-knowledge 扩展点（见 DESIGN_OPTIONAL.md §4.2）。
# 调大 = 注入更多知识但更贵；调小 = 省 token 但可能丢掉刚获得物品的说明。
GAME_WIKI_MAX_CHARS = 480

# ---- 主动知识问答（knowledge-qa 扩展点，聊天链路）----
# 用户提问时，宿主向 knowledge-qa 扩展点要一段「检索上下文」注入当轮 prompt。
# 未装插件 / 扩展点返回空 → 完全按原样说话，行为与没有这个功能时一致。
# 关掉它 = 插件不参与聊天（游戏事件注入走 wiki-knowledge，不受此开关影响）。
KNOWLEDGE_QA_ENABLED = True
# 注入预算（字）。检索到的资料越长越贵，且容易把人格口吻带偏，默认给得比较克制。
KNOWLEDGE_QA_MAX_CHARS = 1200

# Wiki 知识过滤模式（游戏模式）：控制注入主对话的 wiki 知识量。
# 具体过滤词表由 wiki 插件自带（wi_config.WIKI_COMMON_TERMS / WIKI_NEW_TERMS）。
# - normal  默认模式：全量检索（common 优先 + rare 兜底）并提炼注入
# - lowfreq 低频模式：过滤「AI 铁定知晓」的常见实体（白名单 + common 基础实体），
#            保留 rare 冷门与较新版本内容 → 照常查 wiki + 提炼注入
WIKI_FILTER_OPTIONS = [
    {"key": "normal", "label": "默认模式（全量检索注入）"},
    {"key": "lowfreq", "label": "低频模式（过滤常见常识，保留冷门知识）"},
]
WIKI_FILTER_DEFAULT = "normal"

# "Loaded N advancements" 中 N 达到此值视为游戏全量进度（启动加载），忽略
GAME_ADV_FULL_LIMIT = 500

# ---------- 桌宠模组数据（deskpet，Fabric 联动） ----------
# 游戏数据源：0 自动（模组优先，回退日志） 1 仅模组 2 仅日志
GAME_SOURCE_OPTIONS = ["自动（模组优先）", "仅模组数据", "仅游戏日志"]
# 普通活动（挖掘/战斗/移动等）汇总播报间隔（秒），默认 20s，与日志检查频率一致
GAME_NORMAL_SUMMARY_OPTIONS = [20, 60, 180, 600]
# AI 调用冷却（秒），避免触发 DeepSeek 频率限制
GAME_AI_COOLDOWN = 15
# 聊天事件是否全部回应（否则只回应含问句/关键词的）
GAME_CHAT_RESPOND_ALL = True

# ---------- 智能女仆联动（smartmaid，WebSocket） ----------
# 模组侧 SmartMaid 作为 WebSocket Client 连本机端口，桌宠作为 Server。
# 端口需与模组 config/smartmaid/bridge.json 的 ws.url 一致。
MAID_LINK_ENABLED = True
MAID_LINK_PORT = 21420
# 握手 token：留空 = 不校验；非空时需与模组 bridge.json 的 ws.token 相同
MAID_LINK_TOKEN = ""
# 女仆事件播报冷却（秒），避免连续事件刷屏
MAID_EVENT_COOLDOWN = 8
# 感知事件是否冒气泡播报（发现敌人/被打/任务启停等）：
# False（默认）= 只播 AI 聊天——感知事件仅记日志，不进气泡/语音，避免"女仆老在报过程"刷屏；
# True = 恢复旧行为，感知事件也播报。
MAID_EVENT_BUBBLE = False
# 召唤女仆后隐藏桌宠窗口（只停止桌面渲染，进程/AI/语音后端继续运行；
# 女仆离线或 WebSocket 断开时自动恢复窗口）。可在设置 → 游戏 里开关。
MAID_HIDE_PET_ON_MAID = True
# AI 主动决策冷却（秒）：低血/被围命中后调 AI 决策的频率上限，
# 避免高频 AI 调用烧 token（决策 3 边界见 DESIGN_LOOP_DEEPENED §9）
MAID_DECISION_COOLDOWN = 30

# ---------- 本地预处理（NLU：意图识别 + 槽位抽取） ----------
# 开启后，用户输入（聊天 / 语音）先经本地小模型判断意图：
#   · 高置信且是「指挥女仆干活」的意图 → 直接执行（如自动合成），
#     并把「原话 + 是否已执行 + 配方/背包明细」一并交给大 AI 润色回应；
#   · 其余情况不做任何动作，原样交给大 AI。
# 模型在 nlu/data/；缺失或依赖不可用时静默回退，不影响聊天。
NLU_ENABLED = True
# 自动执行阈值（与 nlu/intent.py 的 AUTO_THRESHOLD 对应，仅用于说明/调参）
NLU_AUTO_THRESHOLD = 0.85
