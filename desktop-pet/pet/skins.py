"""皮肤（角色包）管理：扫描 desktop-pet/skins/ 下的角色包，加载并应用到运行时。

角色包结构（由 deskpet-tools 打包生成）：
  skins/<角色id>/
    role.json            # 元数据
    prompt.txt           # 可选：角色专属人设（覆盖当前人格 prompt 全文）
    <动作key>/frame_0000.png ...   # 帧图目录名 = 桌宠内部动作 key
    <动作key>/traj.json           # 位移动作可选

切换皮肤 = 换外观动作 + 右键动作名 + 宠物名 + AI 动作标签词 +（可选）台词/prompt。
人格（宠物女仆/御坂美琴）体系保持不变，皮肤不参与人格切换。
"""
import json
import os

import config
from core.action_key import ActionKey

# 皮肤动作目录下的缓存（分析缓存 / 基础帧），与角色包本体分离，避免污染用户打包目录
_SKIN_CACHE_ROOT = os.path.join(config.SKINS_DIR, ".skincache")


class Skin:
    """加载后的皮肤数据。actions 只含桌宠已知的动作 key。"""

    def __init__(self, role, path):
        self.id = str(role.get("id") or os.path.basename(path) or "")
        self.name = str(role.get("name") or self.id)
        self.author = str(role.get("author") or "")
        self.version = str(role.get("version") or "")
        self.path = path
        self.default_pet_name = str(role.get("default_pet_name")
                                    or self.name or "")
        # 动作映射：key -> {"label": 显示名, "dir": 帧图目录名}
        self.actions = {}
        for key, info in (role.get("actions") or {}).items():
            ak = ActionKey.from_value(key)
            if ak is None or ak == ActionKey.SEQUENCE:
                continue            # 忽略桌宠不认识的 key（未知动作 key / sequence 容错）
            if not isinstance(info, dict):
                info = {}
            self.actions[key] = {
                "label": str(info.get("label") or config.ACTIONS.get(key, key)),
                "dir": str(info.get("dir") or key),
            }
        # 动作标签词 {词 -> key}：皮肤提供则合并覆盖默认
        tags = {}
        if isinstance(role.get("action_tags"), dict):
            for word, key in role["action_tags"].items():
                if ActionKey.from_value(key) is not None:
                    tags[str(word)] = str(key)
        self.action_tags = tags
        # 随机台词：皮肤提供则合并覆盖默认
        self.chatlines = {}
        if isinstance(role.get("chatlines"), dict):
            for cat, lines in role["chatlines"].items():
                if isinstance(lines, (list, tuple)):
                    self.chatlines[str(cat)] = [str(l) for l in lines]
        # 皮肤自带 prompt
        self.prompt_text = None
        pf = role.get("prompt_file")
        if pf:
            pp = os.path.join(path, os.path.basename(str(pf)))
            if os.path.isfile(pp):
                try:
                    with open(pp, encoding="utf-8") as f:
                        self.prompt_text = f.read().strip()
                except OSError:
                    self.prompt_text = None

    @property
    def assets_dir(self):
        """当前皮肤的动作素材根目录（内含各动作帧图子目录）。"""
        return self.path

    @property
    def base_frames_dir(self):
        return os.path.join(_SKIN_CACHE_ROOT, self.id, ".base_frames")

    @property
    def analyze_cache_path(self):
        return os.path.join(_SKIN_CACHE_ROOT, self.id, "cache.pkl")


class BuiltinSkin(Skin):
    """内置皮肤（默认大肥鱼），不读 role.json。"""

    # 右键「动作」菜单显示名（保持原有文案）
    _MENU_LABELS = {
        "idle": "坐下",
        "front": "起身",
        "angry": "生气",
        "back": "背过身去",
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

    def __init__(self):
        self.id = ""
        self.name = "内置"
        self.author = ""
        self.version = config.VERSION
        self.path = config.ASSETS_DIR
        self.default_pet_name = config.PET_NAME
        self.actions = {
            k: {"label": self._MENU_LABELS.get(k, v), "dir": v}
            for k, v in config.ACTIONS.items()
        }
        self.action_tags = {}
        self.chatlines = {}
        self.prompt_text = None

    @property
    def base_frames_dir(self):
        return config.BASE_FRAMES_DIR

    @property
    def analyze_cache_path(self):
        return os.path.join(config.ASSETS_DIR, ".cache.pkl")


def list_skins():
    """扫描 skins/ 目录，返回 [(skin_id, 名称, 路径), ...]，按名称排序。"""
    out = []
    if not os.path.isdir(config.SKINS_DIR):
        return out
    for name in sorted(os.listdir(config.SKINS_DIR)):
        p = os.path.join(config.SKINS_DIR, name)
        if not os.path.isdir(p):
            continue
        rp = os.path.join(p, "role.json")
        if not os.path.isfile(rp):
            continue
        try:
            with open(rp, encoding="utf-8") as f:
                role = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(role, dict):
            continue
        sid = str(role.get("id") or name)
        # 校验：至少含 idle，否则桌宠无法启动动画
        acts = role.get("actions") or {}
        if ActionKey.IDLE.value not in acts:
            continue
        out.append((sid, str(role.get("name") or sid), p))
    return out


def load_skin(skin_id):
    """按 id 加载皮肤；找不到返回 None。"""
    if not skin_id:
        return None
    for sid, _name, path in list_skins():
        if sid == skin_id:
            try:
                with open(os.path.join(path, "role.json"), encoding="utf-8") as f:
                    role = json.load(f)
            except (OSError, ValueError):
                return None
            return Skin(role, path)
    return None


def apply_skin(skin):
    """把皮肤应用到 config 运行时状态（台词 / 动作标签词 / prompt 覆盖）。"""
    if skin is None:
        skin = BuiltinSkin()

    # 随机台词：皮肤合并覆盖默认
    lines = dict(config.AI_PRESET_LINES)
    for cat, ls in skin.chatlines.items():
        if ls:
            lines[cat] = ls
    config.AI_PRESET_LINES = lines

    # 动作标签词：皮肤合并覆盖默认
    tags = dict(config.AI_ACTION_TAGS)
    tags.update(skin.action_tags)
    config.AI_ACTION_TAGS = tags
    # prompt 动作词列表替换：皮肤自定义了标签词才替换，内置保持原样
    config.SKIN_ACTION_TAGS = skin.action_tags or None

    # prompt / 默认名
    config.SKIN_PROMPT = skin.prompt_text
    config.SKIN_DEFAULT_NAME = skin.default_pet_name or None
    return skin