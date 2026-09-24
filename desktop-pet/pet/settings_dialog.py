"""统一设置对话框：宠物设置与 AI 对话设置合并为**唯一**设置入口。

- 宠物相关页（皮肤/画面/台词/环境/新闻/工作/游戏）与 AI 对话页
  （基础/对话/语音/语音输入/宠物）在同一个对话框中呈现；
- 全局唯一入口为宠物右键「设置」；聊天窗不再单设设置页。
- 保存时统一写 QSettings，并回调 pet.apply_pet_settings(opts)；
  chat（聊天窗实例）在运行时传入，用于同步聊天运行时状态，
  未打开聊天窗时仅写 QSettings，聊天窗下次打开读取生效。
"""

import os

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import config
import game.mc_log as mc
from core import theme
from ai import credentials_store as creds
from ai import providers as prov
from ai.client import (
    current_model as _current_model,
    backend_name as _backend_name,
    set_backend as _set_backend,
)
from ai.qwen import (
    DEFAULT_VOICE as DEFAULT_TTS_VOICE,
    TTS_MODEL as DEFAULT_TTS_MODEL,
    VOICE_MODES,
    omni_voice_ids as _omni_ids,
)
from voice import tts_catalog
from core.action_key import ActionKey
from core.icons import icon
from core.widgets import NoWheelComboBox

_STORE = QSettings("DesktopPet", "PetWindow")


# ---------------------------------------------------------------------------
# AI 对话设置存取（读 / 写 QSettings 的唯一入口，供对话框与聊天窗口共用）
# ---------------------------------------------------------------------------

def _tts_model():
    """当前 TTS 模型：设置值优先，否则目录默认（`ai.qwen.tts_model` 的薄封装）。"""
    try:
        return creds.get_tts("model", "") or tts_catalog.default_model()
    except Exception:
        return DEFAULT_TTS_MODEL


def _valid_cosy_voice(voice):
    """校验音色是否在目录（按当前 TTS 模型）内，否则回退默认（千雪）。

    目录读不到时**不校正** —— 宁可留着一个暂时无法验证的音色，
    也不要在离线时把用户已选的音色抹成默认。
    """
    try:
        ids = [v.id for v in tts_catalog.voices(_tts_model())]
    except Exception:
        ids = []
    if voice and (not ids or voice in ids):
        return voice
    try:
        d = tts_catalog.catalog().default_voice(_tts_model())
    except Exception:
        d = ""
    return d or DEFAULT_TTS_VOICE


def load_ai_opts():
    """从 QSettings 读取对话设置。"""
    def _bool(key, default):
        v = _STORE.value(key, default)
        return v in (True, "true", "1") if isinstance(v, str) else bool(v)

    saved_voice = creds.get_tts("voice", "")
    cosy_voice = _valid_cosy_voice(saved_voice or DEFAULT_TTS_VOICE)
    if cosy_voice != saved_voice:
        creds.set_tts("voice", cosy_voice)   # 校正失效音色并写回新键

    pet_name = str(_STORE.value("pet_name", "") or "")
    persona = str(_STORE.value("persona", "") or "") or config.DEFAULT_PERSONA
    # 宠物名按人格独立存储；默认人格兼容迁移旧版全局 pet_name
    pet_name = str(_STORE.value("pet_name_" + persona, "") or "")
    if not pet_name and persona == config.DEFAULT_PERSONA:
        legacy = str(_STORE.value("pet_name", "") or "")
        if legacy:
            _STORE.setValue("pet_name_" + persona, legacy)
            pet_name = legacy

    # 当前提供方与模型的凭据 / 思考档位（命名空间键，见 ai/credentials_store.py）
    pid = _backend_name()
    mid = creds.get_model(pid)
    _kind, _vals, variant_default, _note = prov.reasoning_ui(pid, mid)
    thinking_variant = creds.variant(pid, mid) or variant_default \
        or prov.REASONING_OFF
    thinking_on = thinking_variant not in ("", prov.REASONING_OFF, "none",
                                           "false", "0")
    # 人设精简程度：full（网页版完整）/ slim（API 精简）/ min（极简，两类后端都可用）
    prompt_style = config.sanitize_prompt_style(
        str(_STORE.value("prompt_style", "") or ""), pid)
    return {
        # V4.1 起网页端三模式（快速/专家/识图）已合并为统一模型，不再有模式之分
        "model": config.AI_MODEL_TYPE,
        "thinking": thinking_on,
        # 具体档位（off / low / high / max / "8192"…）：API 后端靠它表达 effort，
        # 光靠 bool 会把档位压成一个"开"
        "thinking_variant": thinking_variant,
        "search": _bool("ai_search", False),
        "persona": persona,
        "prompt": config.load_system_prompt(persona, pet_name, backend=pid,
                                            style=prompt_style),
        "prompt_style": prompt_style,
        "pet_name": pet_name,
        "memory": _bool("ai_memory", True),
        "wiki_refine": _bool("ai_wiki_refine", True),
        "wiki_filter": str(_STORE.value(
            "ai_wiki_filter", config.WIKI_FILTER_DEFAULT) or config.WIKI_FILTER_DEFAULT),
        "voice_mode": _valid_voice_mode(
            str(_STORE.value("ai_voice_mode", "off") or "off")),
        "ai_mode": int(_STORE.value("ai_default_mode", 1) or 1),
        "backend": pid,
        # 提供方凭据（单一事实来源 = credentials_store）
        "provider_key": creds.get_key(pid),
        "provider_model": mid,
        "provider_base_url": creds.get_text(pid, "base_url", ""),
        # P8：辅助调用（提炼/润色）可以另指一个更便宜的后端；空 = 跟随主对话
        "utility_provider": creds.get_text("_utility", "provider", ""),
        "utility_model": creds.get_text("_utility", "model", ""),
        "cosy_voice": cosy_voice,
        # TTS 模型：**必须读回**，否则设置页每次打开都会把模型重置成目录默认，
        # 用户改过的模型悄悄丢失（音色是按模型过滤的，模型错了音色也跟着错）。
        "tts_model": _tts_model(),
        # P9 原生语音音色：按**对话模型**分别存（各 Omni 模型的音色集不同）
        "audio_voice": creds.get_tts("native_voice/" + mid, ""),
        "voice_volume": int(_STORE.value("voice_volume", 80) or 80),
        "voice_rate": int(_STORE.value("voice_rate", 110) or 110),
    }


def save_ai_opts(opts):
    # 模式已取消（V4.1 三模式合并为统一模型），清理旧键
    _STORE.remove("ai_model_type")
    _STORE.setValue("ai_thinking", bool(opts.get("thinking", False)))
    _STORE.setValue("ai_search", opts["search"])
    persona = opts.get("persona", "") or config.DEFAULT_PERSONA
    _STORE.setValue("pet_name_" + persona, opts.get("pet_name", ""))
    _STORE.setValue("persona", persona)
    _STORE.setValue("ai_memory", opts["memory"])
    _STORE.setValue("ai_wiki_refine", opts.get("wiki_refine", True))
    _STORE.setValue("ai_wiki_filter",
                    opts.get("wiki_filter", config.WIKI_FILTER_DEFAULT))
    _STORE.setValue("ai_voice_mode", opts["voice_mode"])
    _STORE.setValue("ai_default_mode", opts.get("ai_mode", 1))
    if "prompt_style" in opts:
        _STORE.setValue("prompt_style", opts.get("prompt_style", ""))

    # 提供方与凭据：写命名空间键（ai/<id>/…），老键保留不动（可回滚）
    pid = prov.canonical(opts.get("backend", prov.DEFAULT_PROVIDER))
    _set_backend(pid)
    if "provider_key" in opts:
        creds.set_key(pid, opts.get("provider_key", ""))
    if "provider_model" in opts:
        creds.set_model(pid, opts.get("provider_model", ""))
    if "provider_base_url" in opts:
        creds.set_base_url(pid, opts.get("provider_base_url", ""))
    # 思考档位按 provider+model 存：换模型读不到就回落到该模型的默认档
    variant = opts.get("thinking_variant")
    if variant is not None and "provider_model" in opts:
        creds.set_variant(pid, opts.get("provider_model", ""), variant)
    # P8：辅助调用后端
    if "utility_provider" in opts:
        creds.set_text("_utility", "provider", opts.get("utility_provider", ""))
    if "utility_model" in opts:
        creds.set_text("_utility", "model", opts.get("utility_model", ""))

    creds.set_tts("model", opts.get("tts_model", "") or DEFAULT_TTS_MODEL)
    creds.set_tts("voice", opts.get("cosy_voice", DEFAULT_TTS_VOICE))
    # P9：原生语音音色按**对话模型**分键存 —— 各 Omni 模型的音色集不同
    # （实测 qwen3.5-omni-flash 拒 Cherry/Chelsie），共用一个键会互相覆盖。
    _nv_model = str(opts.get("provider_model") or "")
    if "audio_voice" in opts and _nv_model:
        creds.set_tts("native_voice/" + _nv_model, opts.get("audio_voice", ""))
    _STORE.setValue("voice_volume", int(opts.get("voice_volume", 80)))
    _STORE.setValue("voice_rate", int(opts.get("voice_rate", 110)))
    _STORE.remove("moss_voice")   # MOSS 已移除，清理残留键


def _valid_voice_mode(mode):
    """校验语音引擎模式，已移除的 moss 等旧值回退为 off 并写回清理。"""
    if mode in _VOICE_MODE_ITEMS:
        return mode
    _STORE.setValue("ai_voice_mode", "off")
    _STORE.remove("moss_voice")
    return "off"


# 语音引擎下拉文案 → 引擎 key
# 语音引擎下拉文案 → 引擎 key（顺序与 ai/qwen.VOICE_MODES 一致，单一事实来源）
_VOICE_MODE_ITEMS = list(VOICE_MODES)
_VOICE_MODE_LABELS = [
    "关闭",
    "Edge 语音（联网，音质好）",
    "本地语音（离线，即时）",
    "千问 TTS（联网，音色多、可配模型）",
    "模型原生语音（模型自己出语音，无额外 TTS）",
]


class SettingsDialog(QDialog):
    """统一设置对话框。构造参数：
      pet  PetWindow  提供宠物相关页（皮肤/画面/台词/环境/新闻/工作/游戏）
      chat ChatWindow 可选：聊天窗在运行时传入，用于保存时同步运行时状态
    保存时写 QSettings，并回调 pet.apply_pet_settings / chat.apply_chat_settings。
    """

    #: 「测试连接」在后台线程发请求，结果用信号回到 UI 线程
    test_done = Signal(str)

    def __init__(self, parent=None, pet=None, chat=None):
        super().__init__(parent)
        self.pet = pet
        self.chat = chat
        self.test_done.connect(self._on_test_done)
        # 统一设置页：始终包含 AI 对话页（chat 仅用于保存时同步运行时状态）
        self.opts = load_ai_opts()
        # 无系统标题栏：去掉左上角「设置」文字与图标（Ant 无边框卡片）
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        # 外框留白是给外壳投影用的（见 _build_ui 的 outer margins），
        # 实际卡片尺寸 = resize − 2×投影留白。
        self.resize(900, 690)

        self._build_ui()

    # ---------- 构建 ----------

    def _build_ui(self):
        # 页面按「分组」组织：导航里每组先插一行不可选的小标题，再放组内条目。
        # row → 页索引的映射记录在 self._row_page（导航行号与 stack 索引不再 1:1）。
        groups = []
        game_group = []
        if self.pet is not None:
            pet_pages = self._build_pet_pages()   # [皮肤/画面/台词/环境/新闻/工作/游戏]
            groups.append(("宠物", [
                ("皮肤", "palette", pet_pages[0]),
                ("画面", "sun", pet_pages[1]),
                ("台词", "message_square", pet_pages[2]),
                ("环境", "cloud_sun", pet_pages[3]),
                ("新闻", "newspaper", pet_pages[4]),
            ]))
            game_group = [
                ("工作", "briefcase", pet_pages[5]),
                ("游戏", "gamepad", pet_pages[6]),
            ]

        # AI 对话页：统一设置页始终包含（全局唯一设置入口，聊天窗不再有设置页）
        chat_pages = self._build_chat_pages()
        groups.append(("对话", [
            ("基础", "sliders", chat_pages[0]),
            ("对话", "message_circle", chat_pages[1]),
            ("语音", "volume", chat_pages[2]),
            ("语音输入", "mic", chat_pages[3]),
            # 原「宠物」页实为「人格 / 宠物名称」，改名以免与「宠物」分组混淆。
            # 图标用清爽的人形轮廓：原来的 cat 线条太多，16px 下糊成一团。
            ("人格", "user_round", chat_pages[4]),
        ]))
        if game_group:
            groups.append(("联动", game_group))
        groups.append(("扩展", [
            ("插件", "settings", self._build_plugin_page()),
        ]))

        stack = QStackedWidget(self)
        stack.setObjectName("settingsStack")

        nav = QListWidget(self)
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(theme.WBS_NAV_W)
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._row_page = {}    # 导航行号 -> stack 页索引
        self._row_icon = {}    # 导航行号 -> 图标名（分组标题行不在表内）
        for title, entries in groups:
            head = QListWidgetItem(title)
            head.setFlags(Qt.NoItemFlags)          # 不可选 → QSS 命中 :disabled
            head.setSizeHint(QSize(0, theme.WBS_GROUP_TITLE_H))
            nav.addItem(head)
            for label, ic, page in entries:
                idx = stack.addWidget(self._wrap_card(page))
                item = QListWidgetItem(icon(ic, 16, theme.WBS_ICON), label)
                item.setSizeHint(QSize(0, theme.WBS_ITEM_H))
                nav.addItem(item)
                row = nav.count() - 1
                self._row_page[row] = idx
                self._row_icon[row] = ic

        def _update_nav_icons(row):
            """图标随选中态换色（未选灰绿 / 选中深墨绿）；分组标题行跳过。"""
            for i in range(nav.count()):
                ic = self._row_icon.get(i)
                if ic is None:
                    continue
                color = theme.WBS_SEL_TEXT if i == row else theme.WBS_ICON
                nav.item(i).setIcon(icon(ic, 16, color))

        def _on_row(row):
            page = self._row_page.get(row)
            if page is not None:
                stack.setCurrentIndex(page)
            _update_nav_icons(row)

        nav.currentRowChanged.connect(_on_row)
        first_row = min(self._row_page) if self._row_page else 0
        nav.setCurrentRow(first_row)
        _update_nav_icons(first_row)
        # ---- 无边框卡片：标题条（可拖动 + 右上角关闭）/ Content / Footer ----
        drag_bar = QWidget()
        drag_bar.setObjectName("settingsDrag")
        drag_bar.installEventFilter(self)
        self._drag_bar = drag_bar
        title = QLabel("设置")
        title.setStyleSheet(
            f"color: {theme.WBS_TEXT}; font-size: 16px; line-height: 24px;"
            f" font-weight: 500;")
        # 让标题不吞鼠标事件，否则在标题上按住无法拖动窗口
        title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        close_btn = QPushButton()
        close_btn.setObjectName("iconBtn")
        close_btn.setIcon(icon("x", 15, theme.WBS_TEXT_2))
        close_btn.setIconSize(QSize(15, 15))
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.close)
        drag_lay = QHBoxLayout(drag_bar)
        drag_lay.setContentsMargins(20, 8, 8, 0)
        drag_lay.addWidget(title, 0)
        drag_lay.addStretch(1)
        drag_lay.addWidget(close_btn)

        # Content：左侧导航 + 右侧内容区（内容区自身留白，卡片落在灰底上）
        right = QWidget()
        right.setObjectName("settingsRight")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(theme.WBS_PAD_GAP, 0,
                                     theme.WBS_PAD_RIGHT, theme.WBS_PAD_RIGHT)
        right_lay.setSpacing(0)
        right_lay.addWidget(stack)

        main_row = QHBoxLayout()
        main_row.setSpacing(0)
        main_row.addWidget(nav)
        main_row.addWidget(right, 1)

        # Footer：右对齐 [取消][确定]，按钮宽度自适应内容
        cancel = QPushButton("取消")
        cancel.setObjectName("btnOutline")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.close)
        ok = QPushButton("确定")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self._save)
        footer = QHBoxLayout()
        footer.setContentsMargins(theme.WBS_NAV_W - theme.WBS_NAV_PAD + 4, 6,
                                  theme.WBS_PAD_RIGHT, 16)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(ok)

        shell = QFrame()
        shell.setObjectName("settingsShell")
        shell.setAttribute(Qt.WA_StyledBackground, True)
        # 整窗 QSS 挂在 shell 上：向所有子控件级联，但不影响对话框之外的界面
        shell.setStyleSheet(theme.wbs_dialog_qss())
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(0, 0, 0, 0)
        shell_lay.setSpacing(0)
        shell_lay.addWidget(drag_bar)
        shell_lay.addLayout(main_row, 1)
        shell_lay.addLayout(footer)

        # 双层柔和投影（Qt 只能叠一层，取更外扩的那层）
        shadow = QGraphicsDropShadowEffect(shell)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 58))
        shell.setGraphicsEffect(shadow)

        # 留白给投影；同时把对话框自身底色调透明，避免投影响应出白矩形
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(0)
        outer.addWidget(shell)
        self.setLayout(outer)
        self.setStyleSheet("QDialog { background: transparent; }")

    def _wrap_card(self, page):
        """把一页内容包进白色卡片（灰底 + 白卡 = 分层；全窗不加描边）。

        卡片内套 QScrollArea：长页面（如「游戏」页控件很多）可滚动，
        避免在固定窗口高度下被裁掉。
        """
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(page)
        # ⚠️ 必须在 setWidget() **之后**关：QScrollArea::setWidget 内部会强制
        # widget->setAutoFillBackground(true)，于是页面按调色板自绘 #EFEFEF，
        # 把卡片的白色整块盖掉。顺序颠倒会静默失效，别提前。
        page.setAutoFillBackground(False)
        card_lay.addWidget(scroll)
        return card

    def _slider_row(self, label, lo, hi, cur, fmt, on_change=None):
        """统一的「标签 + 滑块 + 数值」行。

        数值单独放在最右侧并定宽右对齐。原来是把数值拼进标签里（"亮度: +0"），
        既不像个可调项，拖动时标签宽度还会随数字变化、整行跟着抖。
        """
        name = QLabel(label)
        name.setMinimumWidth(56)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(cur)
        value = QLabel(fmt(cur))
        value.setFixedWidth(52)
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value.setStyleSheet(theme.WBS_SECONDARY_QSS)

        def _on_value(v):
            value.setText(fmt(v))
            if on_change is not None:
                on_change(v)

        slider.valueChanged.connect(_on_value)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(name, 0)
        row.addWidget(slider, 1)
        row.addWidget(value, 0)
        return row, slider

    def eventFilter(self, obj, event):
        """无边框窗口：拖动顶部拖动条可移动整个对话框。"""
        if obj is getattr(self, "_drag_bar", None):
            if (event.type() == QEvent.Type.MouseButtonPress
                    and isinstance(event, QMouseEvent)
                    and event.button() == Qt.LeftButton):
                self._drag_pos = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                return True
            if (event.type() == QEvent.Type.MouseMove
                    and isinstance(event, QMouseEvent)
                    and (event.buttons() & Qt.LeftButton)
                    and hasattr(self, "_drag_pos")):
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                if hasattr(self, "_drag_pos"):
                    del self._drag_pos
                return True
        return super().eventFilter(obj, event)

    # ---------- 插件可用性 ----------

    def _wiki_plugin_available(self):
        """是否装了提供真实 Wiki 知识的插件（非内置 wiki-none）。"""
        try:
            from plugin import host as plugin_host
            reg = plugin_host.registry()
            return (reg.wiki_knowledge() is not None
                    and reg.wiki_source() != "wiki-none")
        except Exception:
            return False

    # ---------- 插件页 ----------

    def _build_plugin_page(self):
        """插件页：列出已装 / 停用 / 加载失败的插件，可启停、打开插件目录。"""
        from PySide6.QtCore import QSettings, QUrl
        from PySide6.QtGui import QDesktopServices
        from plugin.loader import default_plugin_dirs, load_all
        import plugin.host as plugin_host

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        info = QLabel(
            "插件是可选的扩展（AI 后端 / Wiki 知识 / 动画素材）。"
            "取消勾选即停用，改动在**重启桌宠**后生效。")
        info.setWordWrap(True)
        info.setStyleSheet("color: rgba(0,0,0,0.55);")
        lay.addWidget(info)

        res = load_all(disabled=plugin_host.disabled_ids())
        off_ids = {m.id for m in res.disabled}

        listw = QListWidget()
        listw.setObjectName("pluginList")
        listw.blockSignals(True)
        for m in list(res.plugins) + list(res.disabled):
            status = "已停用" if m.id in off_ids else "已启用"
            item = QListWidgetItem("%s  ·  %s  ·  %s  ·  %s"
                                   % (m.name or m.id, ",".join(m.types),
                                      m.source, status))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if m.id in off_ids else Qt.Checked)
            item.setData(Qt.UserRole, m.id)
            listw.addItem(item)
        listw.blockSignals(False)
        lay.addWidget(listw, 1)

        if res.errors:
            err = QLabel("加载失败（已隔离，不影响运行）：" + "；".join(
                "%s（%s）" % (o, m) for o, m in res.errors))
            err.setWordWrap(True)
            err.setStyleSheet("color: #FF4D4F;")
            lay.addWidget(err)

        row = QHBoxLayout()
        open_btn = QPushButton("打开插件目录")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(default_plugin_dirs()[0])))
        row.addWidget(open_btn)
        row.addStretch(1)
        lay.addLayout(row)

        def _on_item_changed(_item):
            off = [listw.item(i).data(Qt.UserRole)
                   for i in range(listw.count())
                   if listw.item(i).checkState() == Qt.Unchecked]
            QSettings("DesktopPet", "PetWindow").setValue(
                "plugins/disabled", ",".join(off))
        listw.itemChanged.connect(_on_item_changed)
        return page

    # ---------- 宠物相关页 ----------

    def _build_pet_pages(self):
        pet = self.pet
        pages = [self._build_skin_page(pet)]

        # --- 画面 ---
        page_img = QWidget()
        lay_img = QVBoxLayout(page_img)
        lay_img.setContentsMargins(18, 16, 18, 16)
        lay_img.setSpacing(12)

        def make_row(label, lo, hi, cur, fmt, apply):
            # 画面重建防抖：滑块拖动会连续触发 valueChanged，若每次全量 numpy
            # 重建（~1s）会明显卡顿；停顿 150ms 后只重建一次
            deb = QTimer(self)
            deb.setSingleShot(True)
            deb.setInterval(150)

            def _rebuild():
                if pet is not None:
                    pet._rebuild_frame_cache()
                    pet._show_frame()

            deb.timeout.connect(_rebuild)

            def on_change(v):
                apply(v)
                if pet is not None:
                    deb.start()   # 拖动中只更新数值，停顿后统一重建

            return self._slider_row(label, lo, hi, cur, fmt, on_change)

        lay_img.addLayout(make_row("亮度", -100, 100, pet.brightness,
                                   lambda v: f"{v:+d}",
                                   lambda v: setattr(pet, "brightness", v))[0])
        lay_img.addLayout(make_row("对比度", 50, 150, int(round(pet.contrast * 100)),
                                   lambda v: f"{v/100:.2f}",
                                   lambda v: setattr(pet, "contrast", v / 100.0))[0])
        lay_img.addLayout(make_row("饱和度", 0, 200, int(round(pet.saturation * 100)),
                                   lambda v: f"{v/100:.2f}",
                                   lambda v: setattr(pet, "saturation", v / 100.0))[0])
        lay_img.addStretch(1)

        # --- 台词 ---
        page_line = QWidget()
        lay_line = QVBoxLayout(page_line)
        lay_line.setContentsMargins(18, 16, 18, 16)
        lay_line.setSpacing(12)
        self.line_on = QCheckBox("随机台词（宠物活动时卖萌说话）")
        self.line_on.setChecked(pet._chatline_enabled)
        self.line_freq = NoWheelComboBox()
        self.line_freq.addItems(["低频（约 1 分钟一句）", "中频（约 25 秒一句）", "高频（约 10 秒一句）"])
        self.line_freq.setCurrentIndex(pet._chatline_freq)
        line_row = QHBoxLayout()
        line_row.addWidget(QLabel("台词频率"), 1)
        line_row.addWidget(self.line_freq, 2)
        lay_line.addWidget(self.line_on)
        lay_line.addLayout(line_row)
        lay_line.addStretch(1)

        # --- 环境提醒 ---
        page_env = QWidget()
        lay_env = QVBoxLayout(page_env)
        lay_env.setContentsMargins(18, 16, 18, 16)
        lay_env.setSpacing(12)
        self.env_on = QCheckBox("环境提醒（整点关怀 / 天气播报）")
        self.env_on.setChecked(pet._env_enabled)
        self.city_edit = QLineEdit()
        self.city_edit.setPlaceholderText("城市（可留空，自动定位）")
        self.city_edit.setText(pet._manual_city)
        city_row = QHBoxLayout()
        city_row.addWidget(QLabel("城市"), 1)
        city_row.addWidget(self.city_edit, 2)
        lay_env.addWidget(self.env_on)
        lay_env.addLayout(city_row)
        lay_env.addStretch(1)

        # --- 新闻 ---
        page_news = QWidget()
        lay_news = QVBoxLayout(page_news)
        lay_news.setContentsMargins(18, 16, 18, 16)
        lay_news.setSpacing(10)
        self.news_on = QCheckBox("新闻播报（定时播报 RSS 最新标题）")
        self.news_on.setChecked(pet._news_enabled)
        self.news_freq = NoWheelComboBox()
        self.news_freq.addItems([f"{m} 分钟" for m in config.NEWS_FREQ_OPTIONS])
        self.news_freq.setCurrentIndex(self._freq_index(config.NEWS_FREQ_OPTIONS,
                                                        pet._news_freq_min))
        news_freq_row = QHBoxLayout()
        news_freq_row.addWidget(QLabel("播报频率"), 1)
        news_freq_row.addWidget(self.news_freq, 2)
        lay_news.addWidget(self.news_on)
        lay_news.addLayout(news_freq_row)

        self.feeds = [dict(f) for f in pet._news_feeds]
        news_list = QListWidget()
        news_list.setFixedHeight(90)
        for f in self.feeds:
            news_list.addItem(f"{f.get('name', '')}  {f.get('url', '')}")
        feed_name = QLineEdit()
        feed_name.setPlaceholderText("源名称，如：央视新闻")
        feed_url = QLineEdit()
        feed_url.setPlaceholderText("RSS 地址，如：https://example.com/feed.xml")
        add_btn = QPushButton("添加源")
        add_btn.setCursor(Qt.PointingHandCursor)
        del_btn = QPushButton("删除选中")
        del_btn.setObjectName("btnOutline")
        del_btn.setCursor(Qt.PointingHandCursor)

        def _add_feed():
            name = feed_name.text().strip() or "新闻"
            url = feed_url.text().strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                return
            self.feeds.append({"name": name, "url": url})
            news_list.addItem(f"{name}  {url}")
            feed_name.clear()
            feed_url.clear()

        def _del_feed():
            row = news_list.currentRow()
            if row < 0:
                return
            news_list.takeItem(row)
            self.feeds.pop(row)

        add_btn.clicked.connect(_add_feed)
        del_btn.clicked.connect(_del_feed)
        lay_news.addWidget(news_list)
        lay_news.addWidget(feed_name)
        lay_news.addWidget(feed_url)
        feed_btns = QHBoxLayout()
        feed_btns.setSpacing(8)
        feed_btns.addWidget(add_btn, 0)
        feed_btns.addWidget(del_btn, 0)
        feed_btns.addStretch(1)
        lay_news.addLayout(feed_btns)
        news_hint = QLabel("已登录时由 AI 逐条播报；播报第一个可成功拉取的源")
        news_hint.setWordWrap(True)
        news_hint.setStyleSheet(theme.WBS_HINT_QSS)
        lay_news.addWidget(news_hint)

        # --- 工作模式 ---
        page_work = QWidget()
        lay_work = QVBoxLayout(page_work)
        lay_work.setContentsMargins(18, 16, 18, 16)
        lay_work.setSpacing(12)
        self.work_on = QCheckBox("工作模式（检测当前打开的软件/文件，定时发实用小提示）")
        self.work_on.setChecked(pet._work_enabled)
        self.tip_freq = NoWheelComboBox()
        self.tip_freq.addItems([f"{m} 分钟" for m in config.TIP_FREQ_OPTIONS])
        self.tip_freq.setCurrentIndex(self._freq_index(config.TIP_FREQ_OPTIONS,
                                                       pet._work_freq_min))
        tip_freq_row = QHBoxLayout()
        tip_freq_row.addWidget(QLabel("提示频率"), 1)
        tip_freq_row.addWidget(self.tip_freq, 2)
        lay_work.addWidget(self.work_on)
        lay_work.addLayout(tip_freq_row)
        work_hint = QLabel("切换软件或新开文件会立即提示；无变化时按设定频率提示（内容不重复，需先登录 DeepSeek，提示由 AI 生成）")
        work_hint.setWordWrap(True)
        work_hint.setStyleSheet(theme.WBS_HINT_QSS)
        lay_work.addWidget(work_hint)
        lay_work.addStretch(1)

        # --- 游戏模式 ---
        page_game = QWidget()
        lay_game = QVBoxLayout(page_game)
        lay_game.setContentsMargins(18, 16, 18, 16)
        lay_game.setSpacing(12)
        self.game_type = NoWheelComboBox()
        self.game_type.addItems(["关闭", "我的世界（Minecraft）", "修仙小游戏（凡人修仙）"])
        self.game_type.setCurrentIndex(pet._game_type)
        gt_row = QHBoxLayout()
        gt_row.addWidget(QLabel("联动游戏"), 1)
        gt_row.addWidget(self.game_type, 2)
        lay_game.addLayout(gt_row)

        sep_mc = QLabel("我的世界（Minecraft）")
        sep_mc.setStyleSheet(theme.WBS_SECTION_QSS)
        lay_game.addWidget(sep_mc)
        self.log_edit = QLineEdit()
        self.log_edit.setPlaceholderText("日志路径（留空自动检测默认位置）")
        self.log_edit.setText(pet._game_log_path)
        browse_btn = QPushButton("浏览…")
        browse_btn.setObjectName("btnOutline")
        browse_btn.setCursor(Qt.PointingHandCursor)
        log_row = QHBoxLayout()
        log_row.setSpacing(8)
        log_row.addWidget(self.log_edit, 1)
        log_row.addWidget(browse_btn, 0)
        self.game_freq = NoWheelComboBox()
        self.game_freq.addItems([f"{m} 秒" for m in config.GAME_FREQ_OPTIONS])
        self.game_freq.setCurrentIndex(self._freq_index(config.GAME_FREQ_OPTIONS,
                                                        pet._game_freq))
        freq_row = QHBoxLayout()
        freq_row.addWidget(QLabel("读取频率"), 1)
        freq_row.addWidget(self.game_freq, 2)
        lay_game.addLayout(log_row)
        self.player_edit = QLineEdit()
        self.player_edit.setPlaceholderText("你的游戏角色名（如 oyxdsg；自动提取失败时用此名字告诉 AI 这是主人）")
        self.player_edit.setText(str(_STORE.value("game_player_name", "") or ""))
        player_row = QHBoxLayout()
        player_row.addWidget(QLabel("游戏角色名"), 1)
        player_row.addWidget(self.player_edit, 2)
        lay_game.addLayout(player_row)
        lay_game.addLayout(freq_row)

        self.game_source = NoWheelComboBox()
        self.game_source.addItems(config.GAME_SOURCE_OPTIONS)
        self.game_source.setCurrentIndex(pet._game_source)
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("数据源"), 1)
        src_row.addWidget(self.game_source, 2)
        lay_game.addLayout(src_row)

        self.normal_sum = NoWheelComboBox()
        self.normal_sum.addItems(
            [f"{m} 秒" if m < 60 else f"{m // 60} 分钟"
             for m in config.GAME_NORMAL_SUMMARY_OPTIONS])
        self.normal_sum.setCurrentIndex(self._freq_index(
            config.GAME_NORMAL_SUMMARY_OPTIONS, pet._game_normal_summary_min))
        sum_row = QHBoxLayout()
        sum_row.addWidget(QLabel("普通活动汇总"), 1)
        sum_row.addWidget(self.normal_sum, 2)
        lay_game.addLayout(sum_row)

        self.chat_respond = QCheckBox("聊天全部回应（关闭则只回应含问句/关键词的聊天）")
        self.chat_respond.setChecked(pet._game_chat_respond)
        lay_game.addWidget(self.chat_respond)

        self.maid_hide_on = QCheckBox("召唤女仆后隐藏桌宠窗口（AI/语音仍后台运行）")
        self.maid_hide_on.setChecked(
            str(_STORE.value("maid_hide_pet",
                             getattr(config, "MAID_HIDE_PET_ON_MAID", False))) != "false")
        lay_game.addWidget(self.maid_hide_on)

        test_btn = QPushButton("测试日志")
        test_btn.setObjectName("btnOutline")
        test_btn.setCursor(Qt.PointingHandCursor)
        self.log_status = QLabel("")
        self.log_status.setWordWrap(True)
        self.log_status.setStyleSheet(theme.WBS_HINT_QSS)

        def _browse_log():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择 Minecraft 日志文件", pet._game_log_path or "",
                "日志文件 (*.log);;所有文件 (*.*)")
            if path:
                self.log_edit.setText(path)

        def _test_log():
            path = self.log_edit.text().strip() or mc.find_log_path()
            files = mc.active_log_files(path) if path else []
            if not files:
                self.log_status.setText("未找到可用日志：请点击「浏览…」选择 latest.log，或选择其所在 logs 目录")
                self.log_status.setStyleSheet(theme.WBS_ERR_QSS)
                return
            total_lines = total_events = 0
            ok = 0
            for p in files:
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    total_lines += len(text.splitlines())
                    total_events += len(mc.parse_events(text))
                    ok += 1
                except Exception:
                    continue
            if not ok:
                self.log_status.setText("✗ 所选日志均读取失败，请检查路径")
                self.log_status.setStyleSheet(theme.WBS_ERR_QSS)
                return
            names = "、".join(os.path.basename(p) for p in files)
            self.log_status.setText(
                f"✓ 按时间筛选到 {len(files)} 个日志：{names}\n"
                f"共 {total_lines} 行，含游戏事件 {total_events} 行")
            self.log_status.setStyleSheet(theme.WBS_OK_QSS)

        browse_btn.clicked.connect(_browse_log)
        test_btn.clicked.connect(_test_log)
        lay_game.addWidget(test_btn, 0, Qt.AlignLeft)
        lay_game.addWidget(self.log_status)

        game_hint = QLabel("不同启动器的日志位置不同，可手动选择；留空则自动检测默认位置（%APPDATA%\\.minecraft\\logs\\latest.log）")
        game_hint.setWordWrap(True)
        game_hint.setStyleSheet(theme.WBS_HINT_QSS)
        lay_game.addWidget(game_hint)

        # ---- 修仙小游戏分组 ----
        lay_game.addSpacing(8)
        sep = QLabel("修仙小游戏（凡人修仙）")
        sep.setStyleSheet(theme.WBS_SECTION_QSS)
        lay_game.addWidget(sep)
        self.xx_dir_edit = QLineEdit()
        self.xx_dir_edit.setPlaceholderText("游戏目录（默认 desktop-pet/games/xiuxian）")
        self.xx_dir_edit.setText(pet._xiuxian_game_dir)
        xx_browse_btn = QPushButton("浏览…")
        xx_browse_btn.setObjectName("btnOutline")
        xx_browse_btn.setCursor(Qt.PointingHandCursor)
        xx_dir_row = QHBoxLayout()
        xx_dir_row.setSpacing(8)
        xx_dir_row.addWidget(self.xx_dir_edit, 1)
        xx_dir_row.addWidget(xx_browse_btn, 0)
        lay_game.addLayout(xx_dir_row)
        xx_start_btn = QPushButton("启动修仙游戏")
        xx_start_btn.setCursor(Qt.PointingHandCursor)
        xx_stop_btn = QPushButton("停止")
        xx_stop_btn.setObjectName("btnOutline")
        xx_stop_btn.setCursor(Qt.PointingHandCursor)
        xx_btn_row = QHBoxLayout()
        xx_btn_row.setSpacing(8)
        xx_btn_row.addWidget(xx_start_btn, 0)
        xx_btn_row.addWidget(xx_stop_btn, 0)
        xx_btn_row.addStretch(1)
        lay_game.addLayout(xx_btn_row)
        xx_hint = QLabel("启动后由桌宠拉起浏览器并自动读取游戏进度（境界/寿元/事件），"
                         "突破、晋级、死亡、结局时桌宠会互动")
        xx_hint.setWordWrap(True)
        xx_hint.setStyleSheet(theme.WBS_HINT_QSS)
        lay_game.addWidget(xx_hint)

        def _browse_xx_dir():
            path = QFileDialog.getExistingDirectory(
                self, "选择修仙游戏目录", pet._xiuxian_game_dir or "")
            if path:
                self.xx_dir_edit.setText(path)

        xx_browse_btn.clicked.connect(_browse_xx_dir)
        xx_start_btn.clicked.connect(pet._start_xiuxian)
        xx_stop_btn.clicked.connect(pet._stop_xiuxian)
        lay_game.addStretch(1)

        return pages + [page_img, page_line, page_env, page_news, page_work, page_game]

    # ---------- 皮肤（角色包）页 ----------

    def _build_skin_page(self, pet):
        from pet import skins

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        lay.addWidget(QLabel("皮肤（角色包）"))
        self.skin_combo = NoWheelComboBox()
        self.skins = [("", "内置（鲸鱼女仆）")] \
            + [(sid, name) for sid, name, _p in skins.list_skins()]
        for _sid, name in self.skins:
            self.skin_combo.addItem(name, _sid)
        cur = str(_STORE.value("skin_id", "") or "")
        ids = [s[0] for s in self.skins]
        self.skin_combo.setCurrentIndex(
            ids.index(cur) if cur in ids else 0)
        self.skin_combo.currentIndexChanged.connect(self._on_skin_change)
        lay.addWidget(self.skin_combo)

        lay.addWidget(QLabel("当前皮肤动作："))
        self.skin_actions_label = QLabel("")
        self.skin_actions_label.setWordWrap(True)
        self.skin_actions_label.setStyleSheet(theme.WBS_SECONDARY_QSS)
        lay.addWidget(self.skin_actions_label)
        self._on_skin_change()

        hint = QLabel(
            "用「桌宠角色工具」把角色包打包到 desktop-pet/skins/（一个角色一个文件夹），"
            "重启桌宠后即可在这里识别。切换皮肤会更换：动作素材、右键「动作」菜单名称、"
            "宠物名与 AI 的 {动作标签词}。切换不改变人格（宠物女仆 / 御坂美琴）。")
        hint.setWordWrap(True)
        hint.setStyleSheet(theme.WBS_HINT_QSS)
        lay.addWidget(hint)
        lay.addStretch(1)
        return page

    def _on_skin_change(self):
        from pet import skins

        sid = self.skin_combo.currentData() or ""
        if sid:
            skin = skins.load_skin(sid)
            if skin is not None:
                keys = (ActionKey.IDLE.value, ActionKey.FRONT.value, ActionKey.THINK.value,
                        ActionKey.HAPPY.value, ActionKey.SHY.value,
                        ActionKey.JUMP.value, ActionKey.ROLL.value)
                names = "、".join(
                    skin.actions.get(k, {}).get("label", k)
                    for k in keys if k in skin.actions)
                self.skin_actions_label.setText(
                    f"动作：{names}…  （宠物名：{skin.default_pet_name or '默认'}）")
                return
        self.skin_actions_label.setText(
            "动作：坐下、起身、生气、背过身去、思考、开心、哭泣、害羞、惊讶、站着睡着、跳跃、打滚、跳舞")

    # ---------- AI 对话相关页 ----------

    # -- 提供方 / 模型 / 能力（P6：全部由 ai.providers 注册表驱动）--

    def _ui_provider(self):
        return self.backend_combo.currentData() or prov.DEFAULT_PROVIDER

    def _ui_model(self):
        """当前模型 id：网页版没有模型选择，返回空串。"""
        if prov.profile(self._ui_provider()).protocol == prov.PROTO_DEEPSEEK_WEB:
            return ""
        return self.model_combo.currentText().strip()

    def _update_backend_ui(self):
        """按所选提供方刷新「基础」页：端点 / Key / 模型 / 能力标签 / 思考控件。

        加一家供应商只需在 `ai/providers.py` 加记录 —— 这里不再有写死的控件行
        （原实现是「DeepSeek 一排 + 千问一排」按索引显隐，还踩过"标签不跟着隐藏"）。
        """
        pid = self._ui_provider()
        p = prov.profile(pid)
        is_web = (p.protocol == prov.PROTO_DEEPSEEK_WEB)
        need_url = prov.needs_base_url(pid)

        # 端点：网页版不需要；其余可编辑（留空用注册表默认，自定义项必填）
        self.base_url_label.setVisible(not is_web)
        self.base_url_edit.setVisible(not is_web)
        if not is_web:
            self.base_url_edit.setText(creds.get_text(pid, "base_url", ""))
            self.base_url_edit.setPlaceholderText(
                p.api or "https://…（自定义端点必填）")
            self.base_url_label.setText(
                "端点（必填）" if need_url else "端点（留空用默认）")

        # API Key：网页版走登录流程，不需要
        self.api_key_label.setVisible(not is_web)
        self.api_key_edit.setVisible(not is_web)
        if not is_web:
            self.api_key_edit.setText(creds.get_key(pid))
            self.api_key_edit.setPlaceholderText(p.key_hint or "sk-…")

        # 模型：可编辑下拉（预置 id + 手填）；网页版无模型可选
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if is_web:
            self.model_combo.setEditable(False)
            self.model_combo.addItem("（网页版由服务端统一模型，无需选择）")
            self.model_combo.setEnabled(False)
        else:
            ids = list(prov.model_ids(pid))
            self.model_combo.setEditable(True)
            if ids:
                self.model_combo.addItems(ids)
            self.model_combo.setEnabled(True)
            self.model_combo.setCurrentText(creds.get_model(pid)
                                            or prov.default_model(pid))
        self.model_combo.blockSignals(False)

        self._refresh_model_info()
        self._refresh_think_ui()
        # 联网开关只在支持联网的供应商下出现（「对话」页可能尚未构建 → 守卫）
        if hasattr(self, "search_check"):
            has_search = prov.cap(pid, prov.CAP_SEARCH)
            self.search_check.setVisible(has_search)
            if not has_search:
                self.search_check.setChecked(False)
        # voice_combo 在「语音」页，可能尚未构建（_build_chat_pages 分页顺序）
        if hasattr(self, "voice_combo"):
            self._update_voice_ui()
        # 人设精简程度选项按后端过滤（「对话」页可能尚未构建 → 守卫）
        if hasattr(self, "prompt_style_combo"):
            self._refresh_prompt_style_ui()

    def _refresh_prompt_style_ui(self):
        """按当前后端重填「人设精简程度」下拉：网页版=完整/极简；无状态=精简/极简。"""
        pid = self._ui_provider()
        cur = self.prompt_style_combo.currentData()
        self.prompt_style_combo.blockSignals(True)
        self.prompt_style_combo.clear()
        for o in config.PROMPT_STYLE_OPTIONS:
            if config.is_allowed_prompt_style(o["key"], pid):
                self.prompt_style_combo.addItem(o["label"], o["key"])
        if cur and config.is_allowed_prompt_style(cur, pid):
            idx = self.prompt_style_combo.findData(cur)
        else:
            idx = self.prompt_style_combo.findData(config.default_prompt_style(pid))
        self.prompt_style_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.prompt_style_combo.blockSignals(False)

    def _refresh_model_info(self):
        """模型说明行 + 只读能力标签（把"动态"这件事直接摆给用户看）。"""
        pid = self._ui_provider()
        mid = self._ui_model()
        is_web = prov.profile(pid).protocol == prov.PROTO_DEEPSEEK_WEB
        if is_web:
            self.model_info_label.setText(
                "服务端统一模型 · 支持传图与联网搜索")
        else:
            m = prov.model(pid, mid)
            bits = []
            if m.label and m.label != mid:
                bits.append(m.label)
            for line in (prov.limit_text(pid, mid), prov.cost_text(pid, mid)):
                if line:
                    bits.append(line)
            if mid and not m.attachments:
                bits.append("该模型不支持传图")
            self.model_info_label.setText(
                " · ".join(bits) or "未收录的模型（能力未知，可用「测试连接」验证）")

        caps = []
        if prov.cap(pid, prov.CAP_STREAM):
            caps.append("流式")
        if prov.supports_attachments(pid):
            caps.append("识图")
        if prov.cap(pid, prov.CAP_SEARCH):
            caps.append("联网")
        kind, _v, _d, _n = prov.reasoning_ui(pid, mid)
        caps.append({
            prov.REASONING_KIND_EFFORT: "思考:分档",
            prov.REASONING_KIND_TOGGLE: "思考:开关",
            prov.REASONING_KIND_BUDGET: "思考:预算",
        }.get(kind, "无思考"))
        if not prov.caps_known(pid):
            caps.append("能力未验证")
        self.caps_label.setText("  ".join("[%s]" % c for c in caps))

    def _refresh_think_ui(self):
        """思考控件按**当前模型**的 reasoning_kind 动态渲染（§11.4）。

        同一个供应商里不同模型的思考语义可能不同（Opus 4.8 是分档、
        Sonnet 4.5 只有 token 预算），所以控件必须跟着模型走。
        """
        pid = self._ui_provider()
        mid = self._ui_model()
        kind, values, default, note = prov.reasoning_ui(pid, mid)
        saved = creds.variant(pid, mid) or default or prov.REASONING_OFF

        self.think_label.setVisible(bool(kind))
        self.think_toggle.setVisible(kind in (prov.REASONING_KIND_TOGGLE,
                                             prov.REASONING_KIND_BUDGET))
        self.think_effort.setVisible(kind == prov.REASONING_KIND_EFFORT)
        self.think_budget.setVisible(kind == prov.REASONING_KIND_BUDGET)
        if not kind:
            self.think_hint.setText(note or "该模型不支持思考")
            return
        self.think_hint.setText(note or "")

        if kind == prov.REASONING_KIND_TOGGLE:
            self.think_toggle.setChecked(saved not in ("", prov.REASONING_OFF))
        elif kind == prov.REASONING_KIND_EFFORT:
            items = [prov.REASONING_OFF] + [v for v in values
                                            if v != prov.REASONING_OFF]
            self.think_effort.clear()
            for v in items:
                self.think_effort.addItem(
                    "关闭" if v == prov.REASONING_OFF else v, v)
            self.think_effort.setCurrentIndex(
                items.index(saved) if saved in items else 0)
        else:  # budget_tokens：开关 + token 数
            m = prov.model(pid, mid)
            lo = max(m.reasoning_min or 1024, 1024)
            hi = max(m.reasoning_max or 32768, lo)
            self.think_budget.setRange(lo, hi)
            self.think_budget.setSuffix(" tokens")
            self.think_toggle.setChecked(saved not in ("", prov.REASONING_OFF))
            self.think_budget.setEnabled(self.think_toggle.isChecked())
            try:
                self.think_budget.setValue(int(saved))
            except (TypeError, ValueError):
                self.think_budget.setValue(min(8192, hi))

    def _on_think_toggled(self, on):
        """预算型模型：关掉思考时把 token 输入框一并灰掉，避免误以为它还在生效。"""
        if hasattr(self, "think_budget"):
            self.think_budget.setEnabled(bool(on))

    def _current_thinking_variant(self):
        """把当前思考控件状态读成一个"档位"字符串（关=off）。"""
        pid = self._ui_provider()
        mid = self._ui_model()
        kind, _v, _d, _n = prov.reasoning_ui(pid, mid)
        if kind == prov.REASONING_KIND_TOGGLE:
            return "on" if self.think_toggle.isChecked() else prov.REASONING_OFF
        if kind == prov.REASONING_KIND_EFFORT:
            return self.think_effort.currentData() or prov.REASONING_OFF
        if kind == prov.REASONING_KIND_BUDGET:
            if not self.think_toggle.isChecked():
                return prov.REASONING_OFF
            return str(self.think_budget.value())
        return prov.REASONING_OFF

    def _on_model_changed(self, _text):
        """模型变了 → 说明行、能力标签、思考控件全部跟着重渲染。

        档位不合法时回落到该模型的默认档（§11.4：避免"存着 high 但新模型只有开关"）。
        """
        self._refresh_model_info()
        self._refresh_think_ui()

    def _on_test_connection(self):
        """「测试连接」：后台线程发一条最小请求（不写 QSettings、不启桌宠）。

        用**界面上当前输入**测（而不是已存的值），这样填完没保存也能先验证。
        """
        import threading
        pid = self._ui_provider()
        key = self.api_key_edit.text().strip()
        url = self.base_url_edit.text().strip()
        model = self._ui_model()
        self.test_btn.setEnabled(False)
        self.test_hint.setText("正在测试…")

        def _run():
            try:
                if prov.profile(pid).protocol == prov.PROTO_DEEPSEEK_WEB:
                    from ai.client import load_credentials
                    token, _cookies = load_credentials()
                    ok = bool(token)
                    msg = ("登录凭证可用" if ok else
                           "未登录：请先右键「登录 DeepSeek」")
                else:
                    from ai.protocols import make_adapter
                    adapter = make_adapter(pid, api_key=key,
                                           base_url=url, model=model or None)
                    ok = bool(adapter.verify())
                    msg = "连接正常" if ok else "连接失败：请检查 Key / 端点 / 模型名"
            except Exception as exc:
                ok, msg = False, "失败：%s" % exc
            self.test_done.emit(("成功：" if ok else "") + msg)

        threading.Thread(target=_run, daemon=True).start()

    def _on_test_done(self, text):
        self.test_hint.setText(text)
        self.test_btn.setEnabled(True)

    def _build_chat_pages(self):
        opts = self.opts

        # --- 基础 ---
        basic = QWidget()
        blay = QVBoxLayout(basic)
        blay.setContentsMargins(18, 16, 18, 16)
        blay.setSpacing(8)
        blay.addWidget(QLabel("AI 后端（提供方）"))
        self.backend_combo = NoWheelComboBox()
        for _p in prov.profiles():
            if _p.selectable:
                self.backend_combo.addItem(_p.label, _p.id)
        _bidx = self.backend_combo.findData(opts["backend"])
        self.backend_combo.setCurrentIndex(_bidx if _bidx >= 0 else 0)
        self.backend_combo.currentIndexChanged.connect(self._update_backend_ui)
        blay.addWidget(self.backend_combo)

        # 端点：网页版隐藏；预置值可覆盖，自定义项必填
        self.base_url_label = QLabel("端点")
        blay.addWidget(self.base_url_label)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://…")
        blay.addWidget(self.base_url_edit)

        # API Key：网页版隐藏（走右键「登录 DeepSeek」）
        self.api_key_label = QLabel("API Key")
        blay.addWidget(self.api_key_label)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        blay.addWidget(self.api_key_edit)

        # 模型：**可编辑**下拉 —— 预置清单不可能覆盖厂商全部型号，允许手填
        blay.addWidget(QLabel("模型"))
        self.model_combo = NoWheelComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        blay.addWidget(self.model_combo)

        # 模型说明（中文名 / 上下文 / 价格）
        self.model_info_label = QLabel("")
        self.model_info_label.setWordWrap(True)
        blay.addWidget(self.model_info_label)

        # 只读能力标签：把"能力是数据驱动的"这件事直接摆给用户看
        self.caps_label = QLabel("")
        blay.addWidget(self.caps_label)

        # 思考（按当前模型动态渲染：开关 / 分档 / token 预算）
        self.think_label = QLabel("思考")
        blay.addWidget(self.think_label)
        self.think_toggle = QCheckBox("开启深度思考")
        self.think_toggle.toggled.connect(self._on_think_toggled)
        blay.addWidget(self.think_toggle)
        self.think_effort = NoWheelComboBox()
        blay.addWidget(self.think_effort)
        self.think_budget = QSpinBox()
        self.think_budget.setMinimum(1024)
        blay.addWidget(self.think_budget)
        self.think_hint = QLabel("")
        self.think_hint.setWordWrap(True)
        blay.addWidget(self.think_hint)

        # 测试连接：后台线程发最小请求（用界面上当前输入，不必先保存）
        _test_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._on_test_connection)
        self.test_hint = QLabel("")
        _test_row.addWidget(self.test_btn)
        _test_row.addWidget(self.test_hint, 1)
        blay.addLayout(_test_row)

        self.cosy_voice_label = QLabel("CosyVoice 音色")
        blay.addWidget(self.cosy_voice_label)
        self.cosy_voice_combo = NoWheelComboBox()
        blay.addWidget(self.cosy_voice_combo)
        blay.addStretch(1)
        self._update_backend_ui()

        # --- 对话 ---
        chat = QWidget()
        clay = QVBoxLayout(chat)
        clay.setContentsMargins(18, 16, 18, 16)
        clay.setSpacing(8)
        clay.addWidget(QLabel("AI 模式"))
        self.ai_mode_combo = NoWheelComboBox()
        self.ai_mode_combo.addItems(
            [f"{m['id']}、{m['name']}" for m in config.AI_MODES])
        self.ai_mode_combo.setCurrentIndex(
            max(0, min(int(opts.get("ai_mode", 1)) - 1, len(config.AI_MODES) - 1)))
        clay.addWidget(self.ai_mode_combo)

        # 思考控件已移到「基础」页（按当前模型动态渲染，见 §11.4）——
        # 同一个供应商里不同模型的思考语义可能不同（分档 / token 预算），
        # 放在模型旁边才不会误操作；原来这里是个与模型无关的全局复选框。
        self.search_check = QCheckBox("联网搜索")
        self.search_check.setChecked(opts["search"])
        clay.addWidget(self.search_check)
        # 联网只有网页版支持（能力位 CAP_SEARCH），按当前提供方显隐
        if prov.cap(opts.get("backend") or prov.DEFAULT_PROVIDER,
                    prov.CAP_SEARCH):
            self.search_check.setVisible(True)
        else:
            self.search_check.setVisible(False)
            self.search_check.setChecked(False)
        self.memory_check = QCheckBox("对话记忆（记住之前的对话）")
        self.memory_check.setChecked(opts["memory"])
        clay.addWidget(self.memory_check)
        self.wiki_refine_check = QCheckBox("Wiki 知识提炼（减小主对话上下文）")
        self.wiki_refine_check.setChecked(opts.get("wiki_refine", True))
        clay.addWidget(self.wiki_refine_check)
        self.wiki_filter_combo = NoWheelComboBox()
        for o in config.WIKI_FILTER_OPTIONS:
            self.wiki_filter_combo.addItem(o["label"], o["key"])
        self.wiki_filter_combo.setCurrentIndex(
            next((i for i, o in enumerate(config.WIKI_FILTER_OPTIONS)
                  if o["key"] == opts.get("wiki_filter", config.WIKI_FILTER_DEFAULT)),
                 0))
        self.wiki_filter_label = QLabel("Wiki 知识过滤")
        clay.addWidget(self.wiki_filter_label)
        clay.addWidget(self.wiki_filter_combo)
        # 无 Wiki 插件（未装 deskpet-plugin-wiki-local）时隐藏这些开关：
        # 宿主默认「不注入」，由大模型凭自身知识作答（见 DESIGN_OPTIONAL.md §4.2）
        if not self._wiki_plugin_available():
            self.wiki_refine_check.setVisible(False)
            self.wiki_filter_label.setVisible(False)
            self.wiki_filter_combo.setVisible(False)

        # 人设精简程度：full（网页版完整）/ slim（API 精简）/ min（极简标签式）。
        # 选项按当前后端过滤（网页版: 完整/极简；官方 API 等无状态: 精简/极简）。
        clay.addWidget(QLabel("人设精简程度"))
        self.prompt_style_combo = NoWheelComboBox()
        self._refresh_prompt_style_ui()
        clay.addWidget(self.prompt_style_combo)

        # P8：辅助调用（Wiki 提炼 / 记忆提炼 / 播报润色）可另指一个更便宜的后端。
        # 这两类调用**不进主对话历史**，用弱模型完全够用，是实打实的省钱点。
        clay.addWidget(QLabel("辅助调用后端（留空即跟随主对话）"))
        self.utility_provider_combo = NoWheelComboBox()
        self.utility_provider_combo.addItem("（跟随主对话）", "")
        for _up in prov.profiles():
            if _up.selectable:
                self.utility_provider_combo.addItem(_up.label, _up.id)
        _uidx = self.utility_provider_combo.findData(
            opts.get("utility_provider", ""))
        self.utility_provider_combo.setCurrentIndex(_uidx if _uidx >= 0 else 0)
        clay.addWidget(self.utility_provider_combo)
        clay.addStretch(1)

        # --- 语音 ---
        voice = QWidget()
        vlay = QVBoxLayout(voice)
        vlay.setContentsMargins(18, 16, 18, 16)
        vlay.setSpacing(8)
        vlay.addWidget(QLabel("语音朗读"))
        self.voice_combo = NoWheelComboBox()
        self.voice_combo.addItems(_VOICE_MODE_LABELS)
        self.voice_combo.setCurrentIndex(
            _VOICE_MODE_ITEMS.index(opts["voice_mode"])
            if opts["voice_mode"] in _VOICE_MODE_ITEMS else 0)
        self.voice_combo.currentIndexChanged.connect(self._update_voice_ui)
        vlay.addWidget(self.voice_combo)

        vol_row, self.vol_slider = self._slider_row(
            "语音音量", 0, 100, int(opts.get("voice_volume", 80)),
            lambda v: f"{v}%")
        vlay.addLayout(vol_row)

        rate_row, self.rate_slider = self._slider_row(
            "语速", 50, 200, int(opts.get("voice_rate", 110)),
            lambda v: f"{v}%")
        vlay.addLayout(rate_row)

        # --- 千问 TTS：模型与音色都来自**实时目录**（voice/tts_catalog.py）---
        # 原先写死一个模型 + 两个音色，而百炼实际有 9 个非实时模型、48 个音色，
        # 且音色可用性随模型变化（flash 48 / instruct-flash 24 / qwen-tts 4）。
        self.tts_model_label = QLabel("语音合成模型")
        vlay.addWidget(self.tts_model_label)
        self.tts_model_combo = NoWheelComboBox()
        self.tts_model_combo.currentIndexChanged.connect(self._on_tts_model_changed)
        vlay.addWidget(self.tts_model_combo)

        self.cosy_voice_label = QLabel("音色")
        vlay.addWidget(self.cosy_voice_label)
        self.cosy_voice_combo = NoWheelComboBox()
        vlay.addWidget(self.cosy_voice_combo)

        _tts_row = QHBoxLayout()
        self.tts_preview_btn = QPushButton("试听")
        self.tts_preview_btn.setCursor(Qt.PointingHandCursor)
        self.tts_preview_btn.clicked.connect(self._on_tts_preview)
        self.tts_refresh_btn = QPushButton("刷新音色列表")
        self.tts_refresh_btn.setCursor(Qt.PointingHandCursor)
        self.tts_refresh_btn.clicked.connect(self._on_tts_refresh)
        _tts_row.addWidget(self.tts_preview_btn)
        _tts_row.addWidget(self.tts_refresh_btn)
        _tts_row.addStretch(1)
        vlay.addLayout(_tts_row)

        self.tts_status_label = QLabel("")
        self.tts_status_label.setWordWrap(True)
        vlay.addWidget(self.tts_status_label)

        # --- 模型原生语音：音色来自 omni 音色目录，按当前**对话**模型过滤 ---
        self.native_voice_label = QLabel("原生音色")
        vlay.addWidget(self.native_voice_label)
        self.native_voice_combo = NoWheelComboBox()
        vlay.addWidget(self.native_voice_combo)
        self.native_hint = QLabel("")
        self.native_hint.setWordWrap(True)
        vlay.addWidget(self.native_hint)
        vlay.addStretch(1)

        # --- 语音输入（STT） ---
        stt_input = QWidget()
        sly = QVBoxLayout(stt_input)
        sly.setContentsMargins(18, 16, 18, 16)
        sly.setSpacing(8)
        self.stt_on = QCheckBox("语音输入（按住 Y 说话，松开识别并和宠物对话）")
        self.stt_on.setChecked(str(_STORE.value("stt_enabled", False)) != "false")
        sly.addWidget(self.stt_on)
        sly.addWidget(QLabel("识别引擎"))
        self.stt_engine_combo = NoWheelComboBox()
        self.stt_engine_combo.addItems(config.STT_ENGINES)
        stt_engine = str(_STORE.value("stt_engine", config.STT_DEFAULT_ENGINE) or "")
        self.stt_engine_combo.setCurrentIndex(max(0, [
            e.lower() for e in config.STT_ENGINES].index(stt_engine.lower())
            if stt_engine.lower() in [e.lower() for e in config.STT_ENGINES] else 0))
        sly.addWidget(self.stt_engine_combo)
        sly.addWidget(QLabel("faster-whisper 模型"))
        self.stt_whisper_combo = NoWheelComboBox()
        self.stt_whisper_combo.addItems(["small", "base", "tiny"])
        wm = str(_STORE.value("stt_whisper_model", config.STT_WHISPER_MODEL)
                 or config.STT_WHISPER_MODEL).lower()
        self.stt_whisper_combo.setCurrentIndex(
            max(0, ["small", "base", "tiny"].index(wm)
                if wm in ("small", "base", "tiny") else 0))
        sly.addWidget(self.stt_whisper_combo)
        sly.addWidget(QLabel("静音自动收尾"))
        self.stt_silence_combo = NoWheelComboBox()
        self.stt_silence_combo.addItems(["1 秒", "2 秒", "2.5 秒", "3 秒", "5 秒"])
        silence = int(_STORE.value("stt_silence", config.STT_SILENCE_MS)
                      or config.STT_SILENCE_MS)
        self.stt_silence_combo.setCurrentIndex(max(0, [
            1000, 2000, 2500, 3000, 5000].index(silence)
            if silence in (1000, 2000, 2500, 3000, 5000) else 2))
        sly.addWidget(self.stt_silence_combo)
        self.stt_auto = QCheckBox("识别后自动发送给 AI")
        self.stt_auto.setChecked(str(_STORE.value("stt_auto_send", True)) != "false")
        sly.addWidget(self.stt_auto)
        hint = QLabel("按住 Y 说话，松开即识别。Vosk 首次使用自动下载模型（~42MB）；"
                      "faster-whisper 更准但模型较大。识别结果同步进聊天窗口。")
        hint.setWordWrap(True)
        sly.addWidget(hint)
        sly.addStretch(1)

        # --- 宠物（人格 / 名称） ---
        pet = QWidget()
        play = QVBoxLayout(pet)
        play.setContentsMargins(18, 16, 18, 16)
        play.setSpacing(8)
        play.addWidget(QLabel("人格"))
        self.persona_combo = NoWheelComboBox()
        cur_persona = opts.get("persona") or config.DEFAULT_PERSONA
        for key, name in config.persona_names():
            self.persona_combo.addItem(name, key)
        self.persona_combo.setCurrentIndex(max(0, [
            k for k, _ in config.persona_names()].index(cur_persona)
            if cur_persona in [k for k, _ in config.persona_names()] else 0))
        self.persona_combo.currentIndexChanged.connect(self._on_persona_change)
        play.addWidget(self.persona_combo)
        play.addWidget(QLabel("宠物名称"))
        self.pet_name_edit = QLineEdit()
        self.pet_name_edit.setText(opts.get("pet_name") or "")
        play.addWidget(self.pet_name_edit)
        self._update_persona_hint()
        hint = QLabel("切换人格会加载对应提示词文件；改名会替换提示词里的角色名，完整人设自动生效")
        hint.setWordWrap(True)
        play.addWidget(hint)
        play.addStretch(1)

        self._update_voice_ui()
        return [basic, chat, voice, stt_input, pet]

    def _update_persona_hint(self):
        """切换人格时更新宠物名称占位（显示该人格默认名）。"""
        key = self.persona_combo.currentData() or config.DEFAULT_PERSONA
        info = config.persona_info(key)
        self.pet_name_edit.setPlaceholderText(
            f"给宠物起个名字（默认：{info['default_name']}）")

    def _on_persona_change(self):
        """切换人格：更新占位符 + 加载该人格已保存的宠物名。"""
        self._update_persona_hint()
        key = self.persona_combo.currentData() or config.DEFAULT_PERSONA
        saved = str(_STORE.value("pet_name_" + key, "") or "")
        self.pet_name_edit.setText(saved)

    def _update_voice_ui(self):
        """语音页显隐：只有对应引擎的控件才显示。

        **不再看对话后端** —— 语音与对话是两条独立的轴（同一家的 Key 可以只用语音、
        不用对话；反过来也一样）。原先按 `backend_combo.currentIndex() == 2` 判断，
        等于把语音绑死在对话后端上（DESIGN_AI_PROVIDERS.md §4.3 列为待解耦项）。
        """
        mode = _VOICE_MODE_ITEMS[self.voice_combo.currentIndex()]
        show_tts = (mode == "cosy")
        show_native = (mode == "native")
        # 标签与控件必须**一起**显隐：曾经只藏标签，留下空下拉框挂在页面上
        for w in (self.tts_model_label, self.tts_model_combo,
                  self.cosy_voice_label, self.cosy_voice_combo,
                  self.tts_preview_btn, self.tts_refresh_btn,
                  self.tts_status_label):
            w.setVisible(show_tts)
        for w in (self.native_voice_label, self.native_voice_combo,
                  self.native_hint):
            w.setVisible(show_native)
        if show_tts:
            self._reload_tts_choices()
        elif show_native:
            self._reload_native_voices()
        else:
            self.cosy_voice_combo.setEnabled(False)

    # ---------- 千问 TTS 模型 / 音色（数据来自实时目录）----------

    def _current_voice_id(self):
        """当前界面上选中的音色 id（下拉为空时退到设置里的值）。"""
        d = self.cosy_voice_combo.currentData()
        if d:
            return str(d)
        return str(self.opts.get("cosy_voice") or DEFAULT_TTS_VOICE)

    def _reload_tts_choices(self):
        """把目录里的模型灌进下拉框，再据此刷新音色列表（保留当前选择）。"""
        cat = tts_catalog.catalog()
        want_model = str(self.opts.get("tts_model") or "") or cat.default_model()
        self.tts_model_combo.blockSignals(True)
        self.tts_model_combo.clear()
        for mid in cat.model_ids():
            self.tts_model_combo.addItem(mid, mid)
        if not self.tts_model_combo.count():      # 目录为空 → 至少给兜底值
            self.tts_model_combo.addItem(DEFAULT_TTS_MODEL, DEFAULT_TTS_MODEL)
        idx = self.tts_model_combo.findData(want_model)
        self.tts_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.tts_model_combo.blockSignals(False)
        self._reload_voices()

    def _reload_voices(self, announce=True):
        """按当前模型过滤音色。原来选的音色若不被该模型支持 → 回落并在状态行说明。"""
        model = self.tts_model_combo.currentData() or tts_catalog.default_model()
        pool = tts_catalog.voices(model)
        want = self._current_voice_id()
        self.cosy_voice_combo.blockSignals(True)
        self.cosy_voice_combo.clear()
        for v in pool:
            self.cosy_voice_combo.addItem(v.label(), v.id)
        if not self.cosy_voice_combo.count():
            self.cosy_voice_combo.addItem(DEFAULT_TTS_VOICE, DEFAULT_TTS_VOICE)
        idx = self.cosy_voice_combo.findData(want)
        fell = (idx < 0 and bool(want))
        self.cosy_voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.cosy_voice_combo.blockSignals(False)
        self.cosy_voice_combo.setEnabled(True)
        if fell:
            self._update_tts_status(
                "音色 %s 不被 %s 支持，已回落到 %s"
                % (want, model, self.cosy_voice_combo.currentData()))
        else:
            self._update_tts_status()

    def _reload_native_voices(self):
        """按当前**对话**模型过滤原生音色（omni 目录，音色集按模型族不同）。

        三种不可用态都要讲清：模型不出音频 / 后端不走流式 / 两种都能用时的可用数。
        """
        pid = self._ui_provider()
        mid = self._ui_model()
        supported = prov.supports(pid, mid, "audio")
        streaming = prov.chat_streams(pid)
        default, voices = tts_catalog.omni_voices(mid)
        ids = [v.id for v in voices] or ([default] if default else [])
        saved = creds.get_tts("native_voice/%s" % mid, "") \
            or prov.model(pid, mid).audio_voice
        self.native_voice_combo.blockSignals(True)
        self.native_voice_combo.clear()
        # 默认音色置顶，便于选回
        ordered = ([default] if default else []) + [i for i in ids if i != default]
        if not ordered:
            self.native_voice_combo.addItem("（模型默认音色）", "")
        else:
            for vid in ordered:
                self.native_voice_combo.addItem(self._omni_voice_label(mid, vid), vid)
        idx = self.native_voice_combo.findData(saved)
        self.native_voice_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.native_voice_combo.blockSignals(False)
        if not supported:
            who = ("对话模型 %s" % mid) if mid else "当前对话后端"
            self.native_hint.setText(
                "%s 不支持原生语音——到「基础」页把模型换成 Omni 系列"
                "（如 qwen3-omni-flash）才能用。" % who)
        elif not streaming:
            self.native_hint.setText("原生语音需要后端走流式，当前后端不支持。")
        else:
            self.native_hint.setText(
                "模型自己出语音（默认 %s，可选 %d 个音色）；AI 回复不再调千问 TTS，"
                "预设台词/播报仍走千问 TTS。" % (default or "音色", len(ids)))

    def _omni_voice_label(self, mid, vid):
        _d, voices = tts_catalog.omni_voices(mid)
        for v in voices:
            if v.id == vid:
                return v.label()
        return vid

    def _update_tts_status(self, prefix=""):
        """状态行：音色数 / 数据来源 / 抓取时间（过期或未配 Key 时点明）。"""
        cat = tts_catalog.catalog()
        src = {"doc": "官方文档", "cache": "本地缓存",
               "snapshot": "随包快照", "empty": "无"}.get(cat.source, cat.source)
        n = len(tts_catalog.voices(self.tts_model_combo.currentData()))
        parts = ["%d 个音色" % n, "来源：%s" % src]
        if cat.fetched_at:
            parts.append(cat.fetched_at_text)
            if cat.is_stale():
                parts.append("已过期，建议刷新")
        try:
            has_key = bool(creds.get_key("qwen"))
        except Exception:
            has_key = False
        if not has_key:
            parts.append("未配置千问 API Key，无法合成")
        txt = " · ".join(parts)
        self.tts_status_label.setText((prefix + "｜" + txt) if prefix else txt)

    def _on_tts_model_changed(self, _idx):
        self._reload_voices()

    def _on_tts_refresh(self):
        """联网刷新音色目录（后台线程；完成经信号回到 UI 线程）。"""
        if self._tts_busy:
            return
        self._tts_busy = True
        self.tts_refresh_btn.setEnabled(False)
        self.tts_status_label.setText("正在刷新音色列表…")
        key = creds.get_key("qwen")
        tts_catalog.refresh_async(api_key=key, on_done=self._tts_refresh_worker_done)

    def _tts_refresh_worker_done(self, ok, _cat, err):
        # 回调发生在后台线程：只发信号，绝不碰控件
        self.tts_refresh_signal.emit(bool(ok), str(err or ""))

    def _on_tts_refresh_done(self, ok, err):
        self._tts_busy = False
        self.tts_refresh_btn.setEnabled(True)
        if not ok:
            self._update_tts_status("刷新失败：%s" % (err or "未知错误"))
            return
        self.opts["tts_model"] = self.tts_model_combo.currentData() or ""
        self._reload_tts_choices()

    def _on_tts_preview(self):
        """试听：用界面当前选中的模型+音色合成一句（不必先保存设置）。"""
        voice = self._current_voice_id()
        model = self.tts_model_combo.currentData() or ""
        try:
            from ai.client import load_qwen_key
            key = load_qwen_key()
        except Exception:
            key = ""
        if not key:
            self._update_tts_status("未配置千问 API Key，无法试听")
            return
        self.tts_status_label.setText("正在合成试听…")
        try:
            from voice.tts import VoiceModule
            VoiceModule.shared().enqueue(
                "你好呀，我是你的桌面小女仆，今天也要一起玩哦。",
                mode="cosy", voice=voice, source="试听", model=model)
        except Exception as e:
            self._update_tts_status("试听失败：%s" % e)

    # ---------- 保存 ----------

    def _save(self):
        if self.pet is not None:
            self.pet.apply_pet_settings(self._collect_pet_opts())
        chat_opts = self._collect_chat_opts()
        if self.chat is not None:
            # 聊天窗在运行时：同步客户端/语音/STT 等运行时状态
            self.chat.apply_chat_settings(chat_opts)
        else:
            # 聊天窗未打开：仅写 QSettings，聊天窗下次打开时读取生效
            save_ai_opts(chat_opts)
        self.accept()

    def _collect_pet_opts(self):
        return {
            "skin_id": self.skin_combo.currentData() or "",
            "brightness": self.pet.brightness,
            "contrast": self.pet.contrast,
            "saturation": self.pet.saturation,
            "chatline_enabled": self.line_on.isChecked(),
            "chatline_freq": self.line_freq.currentIndex(),
            "env_enabled": self.env_on.isChecked(),
            "env_city": self.city_edit.text().strip(),
            "news_enabled": self.news_on.isChecked(),
            "news_feeds": [dict(f) for f in self.feeds],
            "news_freq": config.NEWS_FREQ_OPTIONS[self.news_freq.currentIndex()],
            "work_enabled": self.work_on.isChecked(),
            "work_freq": config.TIP_FREQ_OPTIONS[self.tip_freq.currentIndex()],
            "game_type": self.game_type.currentIndex(),
            "game_log_path": self.log_edit.text().strip(),
            "game_player_name": self.player_edit.text().strip(),
            "game_freq": config.GAME_FREQ_OPTIONS[self.game_freq.currentIndex()],
            "game_source": self.game_source.currentIndex(),
            "game_normal_summary": config.GAME_NORMAL_SUMMARY_OPTIONS[
                self.normal_sum.currentIndex()],
            "game_chat_respond": self.chat_respond.isChecked(),
            "maid_hide_pet": self.maid_hide_on.isChecked(),
            "xiuxian_game_dir": self.xx_dir_edit.text().strip(),
        }

    def _collect_chat_opts(self):
        voice_mode = _VOICE_MODE_ITEMS[self.voice_combo.currentIndex()]
        pid = self._ui_provider()
        variant = self._current_thinking_variant()
        # 音色与 TTS 模型：只在千问 TTS 引擎下有意义（两者是独立于对话后端的一条轴）
        if voice_mode == "cosy":
            cosy_voice = self._current_voice_id()
            tts_model = self.tts_model_combo.currentData() or DEFAULT_TTS_MODEL
        else:
            cosy_voice = self.opts["cosy_voice"]
            tts_model = self.opts.get("tts_model") or DEFAULT_TTS_MODEL
        # P9 原生语音音色：只在 native 引擎下取界面值，否则保留原值不覆盖
        if voice_mode == "native":
            audio_voice = self.native_voice_combo.currentData() or ""
        else:
            audio_voice = str(self.opts.get("audio_voice") or "")
        utility = getattr(self, "utility_provider_combo", None)
        return {
            # V4.1 三模式已合并，无「快速/专家」可选，固定统一模型
            "model": config.AI_MODEL_TYPE,
            # bool 供网页版（thinking_enabled）；档位字符串供 API 后端表达 effort/预算
            "thinking": variant not in ("", prov.REASONING_OFF, "none",
                                        "false", "0"),
            "thinking_variant": variant,
            "search": self.search_check.isChecked(),
            "memory": self.memory_check.isChecked(),
            "wiki_refine": self.wiki_refine_check.isChecked(),
            "wiki_filter": self.wiki_filter_combo.currentData()
                or config.WIKI_FILTER_DEFAULT,
            "voice_mode": voice_mode,
            "persona": self.persona_combo.currentData() or config.DEFAULT_PERSONA,
            "pet_name": self.pet_name_edit.text().strip(),
            "prompt_style": self.prompt_style_combo.currentData()
                or config.default_prompt_style(pid),
            "prompt": config.load_system_prompt(
                self.persona_combo.currentData(),
                self.pet_name_edit.text().strip(),
                backend=pid,
                style=self.prompt_style_combo.currentData()
                      or config.default_prompt_style(pid)),
            "ai_mode": self.ai_mode_combo.currentIndex() + 1,
            "backend": pid,
            "provider_key": self.api_key_edit.text().strip(),
            "provider_model": self._ui_model(),
            "provider_base_url": self.base_url_edit.text().strip(),
            # P8：辅助调用后端（空 = 跟随主对话）
            "utility_provider": (utility.currentData() if utility else "") or "",
            "utility_model": "",
            "cosy_voice": cosy_voice,
            "tts_model": tts_model,
            "audio_voice": audio_voice,
            "voice_volume": self.vol_slider.value(),
            "voice_rate": self.rate_slider.value(),
            "stt_enabled": self.stt_on.isChecked(),
            "stt_engine": config.STT_ENGINES[self.stt_engine_combo.currentIndex()],
            "stt_whisper_model": ["small", "base", "tiny"][
                self.stt_whisper_combo.currentIndex()],
            "stt_silence": [1000, 2000, 2500, 3000, 5000][
                self.stt_silence_combo.currentIndex()],
            "stt_auto_send": self.stt_auto.isChecked(),
        }

    @staticmethod
    def _freq_index(options, value):
        for i, m in enumerate(options):
            if m == value:
                return i
        return 0