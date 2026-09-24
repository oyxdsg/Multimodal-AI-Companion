"""宠物主窗口：PetWindow 骨架（窗口生命周期 / 状态常量 / 信号 / 右键菜单 / 聊天联动）。

动画、气泡、各功能模式、设置 UI 已拆分为独立 Mixin：
  - pet/anim.py     PetAnimationMixin  动画/状态机/巡逻/鼠标交互
  - pet/bubble.py   PetBubbleMixin     气泡渲染 + 语音 + 音画同步
  - pet/modes.py    PetModesMixin      环境/新闻/工作/游戏/修仙/STT + 共享 AI 会话
  - pet/settings.py PetSettingsMixin   宠物设置对话框
"""

import json
import os

from PySide6.QtCore import QSettings, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

import config
import ai.client as ai_client
from ai import providers as prov
from core.action_key import ActionKey
from core.message_queue import PetMessageQueue
from pet.anim import PetAnimationMixin
from pet.bubble import PetBubbleMixin
from pet.modes import PetModesMixin
from pet.settings import PetSettingsMixin

# desktop-pet 项目根（注入口文件放在这里，与 .maid_command.json 同级）
_PET_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PetWindow(PetSettingsMixin, PetBubbleMixin, PetModesMixin,
                PetAnimationMixin, QWidget):
    # 动作状态常量：与 ActionKey.value 一致（WALK/DRAG 为窗口专属状态，非动作 key）
    STATE_IDLE = ActionKey.IDLE.value
    STATE_WALK = "walk"
    STATE_DRAG = "drag"
    STATE_SIT = ActionKey.SIT.value
    STATE_ANGRY = ActionKey.ANGRY.value
    STATE_BACK = ActionKey.BACK.value
    STATE_RISE = ActionKey.RISE.value
    STATE_FRONT = ActionKey.FRONT.value
    STATE_TURN = ActionKey.TURN.value
    STATE_THINK = ActionKey.THINK.value
    STATE_HAPPY = ActionKey.HAPPY.value
    STATE_CRY = ActionKey.CRY.value
    STATE_SHY = ActionKey.SHY.value
    STATE_SURPRISED = ActionKey.SURPRISED.value
    STATE_SLEEP = ActionKey.SLEEP.value
    STATE_JUMP = ActionKey.JUMP.value
    STATE_ROLL = ActionKey.ROLL.value
    STATE_DANCE = ActionKey.DANCE.value

    # 姿势组：坐姿组与站姿组之间切换时必须经过 sit/rise 过渡
    _SIT_POSTURE = {ActionKey.IDLE.value, ActionKey.ANGRY.value}
    _STAND_POSTURE = {ActionKey.FRONT.value, ActionKey.BACK.value,
                      ActionKey.THINK.value, ActionKey.HAPPY.value, ActionKey.CRY.value,
                      ActionKey.SHY.value, ActionKey.SURPRISED.value,
                      ActionKey.SLEEP.value, ActionKey.JUMP.value,
                      ActionKey.ROLL.value, ActionKey.DANCE.value}
    _STAND_POSTURE |= {STATE_WALK, STATE_DRAG}
    _ONESHOT = {ActionKey.ANGRY.value, ActionKey.FRONT.value, ActionKey.BACK.value,
                ActionKey.THINK.value, ActionKey.HAPPY.value, ActionKey.CRY.value,
                ActionKey.SHY.value, ActionKey.SURPRISED.value,
                ActionKey.SLEEP.value, ActionKey.JUMP.value,
                ActionKey.ROLL.value, ActionKey.DANCE.value}

    weather_ready = Signal(object, object)   # (天气dict, 提醒类别)
    news_ready = Signal(list)                # 新闻播报条目列表（逐条播报）
    tip_ready = Signal(str)                  # 工作模式小提示文本
    game_ready = Signal(str)                 # 游戏模式互动文本
    stt_text_ready = Signal(str)             # 语音识别出的文本
    stt_reply_ready = Signal(str)            # 语音输入触发的 AI 回复

    def __init__(self):
        super().__init__()
        self._pending_state = self.STATE_IDLE
        self.walking_left = False
        self.walk_target_x = 0

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)

        # 用户设置（QSettings）——提前创建，供皮肤加载读取 skin_id
        self._store = QSettings("DesktopPet", "PetWindow")
        self._load_skin()

        self._load_actions()
        self._init_ui()

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._advance_frame)
        self.walk_timer = QTimer(self)
        self.walk_timer.timeout.connect(self._walk_tick)
        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self._start_walk)
        # 周期强制置顶：对抗其它窗口重新排 z-order / 覆盖
        self._top_timer = QTimer(self)
        self._top_timer.timeout.connect(self._keep_topmost)
        self._top_timer.start(2000)
        # 右键菜单打开期间分片预加载常用懒加载动作（走正常 LRU，无额外内存池）
        self._preload_timer = QTimer(self)
        self._preload_timer.setInterval(220)
        self._preload_timer.timeout.connect(self._menu_preload_tick)
        self._menu_preload_keys = []

        self.frame_i = 0
        idle_act = self.actions.get("idle") or next(iter(self.actions.values()), None)
        self.cur_frames = idle_act["frames"] if idle_act else []
        self._seq_active = False
        self._loops_left = 0
        self._pending_state = None
        self._seq_playing = None
        self._chain = []
        self.turn_right = True
        # 用户可调图像参数（基础值已内置，可右键"设置"调整并持久化）
        self.brightness = int(self._store.value("brightness", 0))
        self.contrast = float(self._store.value("contrast", 1.0))
        self.saturation = float(self._store.value("saturation", 1.0))
        # 预生成"设置后"的显示帧缓存：动画 tick 直接取，避免每帧 numpy
        self._rebuild_frame_cache()

        # 随机台词设置
        self._chatline_enabled = str(
            self._store.value("chatline_enabled", True)) != "false"
        self._chatline_freq = int(self._store.value("chatline_freq", 0) or 0)
        self._last_line = None
        self._last_line_time = 0.0
        self._chatline_timer = QTimer(self)
        self._chatline_timer.timeout.connect(self._on_chatline_timeout)

        self._set_state(self.STATE_IDLE)
        self._place_random()
        self._bubble = None
        # 气泡消息队列：所有消息排队依次显示，每条至少显示 BUBBLE_DISPLAY_MS
        self._bubble_queue = []      # [(text, error, speak)]
        self._bubble_busy = False
        self._bubble_wait_task = None  # 正在等待的语音任务序号（音画同步）
        self._bubble_pending = None    # 等待语音开始时显示的气泡 (text, error)
        self._voice_done_hooked = False
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._on_bubble_done)
        self._init_env()
        # 桌宠退出时终止修仙桥接子进程
        QApplication.instance().aboutToQuit.connect(self._cleanup_xiuxian)
        # 桌宠退出时关闭智能女仆联动（WebSocket Server）
        QApplication.instance().aboutToQuit.connect(self._maid_handler.stop)
        # 统一消息队列：所有消息源（语音/AI/日志/系统/台词）按优先级排队串行显示
        self._msg = PetMessageQueue(self)
        # 语音输入（STT）Handler：按住热键说话，松开识别并对话
        # （录音/识别状态由 pet.handlers.stt_handler.SttHandler 管理）
        self._chat_window = None     # 当前打开的聊天窗口（语音同步历史）
        self.stt_text_ready.connect(self._stt_handler.on_stt_text)
        self.stt_reply_ready.connect(self._stt_handler.on_stt_reply)
        self._stt_timer = QTimer(self)
        self._stt_timer.timeout.connect(self._hotkey_tick)
        self._stt_timer.start(50)
        # 启动欢迎语
        QTimer.singleShot(2000, lambda: self._say_random("welcome", force=True))
        # 性能：预热设置对话框（进程首次构建设置 UI 较慢，后台提前构建一次）
        self._warm_timer = QTimer(self)
        self._warm_timer.setSingleShot(True)
        self._warm_timer.timeout.connect(self._warm_settings)
        self._warm_timer.start(2500)
        # 性能：预热语音模块（Edge/本地/千问 TTS 首次初始化含 pygame/edge_tts 导入与
        # 音频设备初始化，实测 1.3s+；提前在启动空闲期完成，首次台词/点击/拖动不卡顿）
        self._warm_voice_timer = QTimer(self)
        self._warm_voice_timer.setSingleShot(True)
        self._warm_voice_timer.timeout.connect(self._warm_voice)
        self._warm_voice_timer.start(3000)
        # 性能：启动后空闲期分片预加载常用懒加载动作（think/happy/shy/jump/dance），
        # 首次右键菜单预加载/播放不再阻塞（磁盘冷读每动作 0.7~1.4s）
        self._lazy_warm_keys = []
        self._lazy_warm_timer = QTimer(self)
        self._lazy_warm_timer.setInterval(350)
        self._lazy_warm_timer.timeout.connect(self._lazy_warm_tick)
        QTimer.singleShot(1000, self._start_lazy_warm)
        # 聊天注入口（自动化/调试）：写 desktop-pet/.chat_input.json（{"text": "..."}）
        # → 下一拍被当作一次**用户聊天输入**送进完整链路（本地 NLU → AI → DSL → 女仆）。
        # 与 .maid_command.json 同款：无鼠标、无 UI 依赖，供全链路测试使用。
        self._chat_input_file = os.path.join(_PET_DIR, ".chat_input.json")
        self._chat_input_timer = QTimer(self)
        self._chat_input_timer.timeout.connect(self._poll_chat_input)
        self._chat_input_timer.start(600)

    # ---------- 聊天注入口（自动化测试 / 调试） ----------

    def _poll_chat_input(self):
        """读 ``.chat_input.json``，把 text 当作一次用户聊天输入（主线程 tick）。"""
        path = self._chat_input_file
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None
        text = ""
        if isinstance(data, dict):
            text = str(data.get("text") or "")
        elif isinstance(data, str):
            text = data
        text = text.strip()
        if not text:
            try:
                os.remove(path)
            except OSError:
                pass
            return
        win = self._chat_window or self._create_chat_window()
        if win is None:
            try:
                os.remove(path)
            except OSError:
                pass
            print("[chat_input] 聊天窗口不可用，已丢弃输入：%r" % text)
            return
        if win.submit_text(text):
            try:
                os.remove(path)
            except OSError:
                pass
        # 正忙：文件保留，下一拍重试（不丢消息）

    def chat_input(self, text):
        """公开入口：把一段文本作为用户消息送进聊天链路（自动化/调试用）。"""
        win = self._chat_window or self._create_chat_window()
        return bool(win and win.submit_text(text))

    def _create_chat_window(self):
        """非模态创建聊天窗口并显示（手动入口仍是模态 exec，互不影响）。"""
        try:
            from ai.chat import ChatWindow
        except ImportError as e:
            print("[chat_input] 缺少 AI 依赖：%s" % e)
            return None
        try:
            win = ChatWindow(self, preprocessor=self.nlu_preprocess)
        except Exception as e:
            print("[chat_input] 创建聊天窗口失败：%s" % e)
            return None
        self._chat_window = win
        win.finished.connect(lambda _=0: setattr(self, "_chat_window", None))
        win.show()
        return win

    # ---------- 皮肤（角色包） ----------

    def _load_skin(self):
        """启动时从设置加载皮肤（角色包），决定动作素材 / 动作名 / 宠物名等。"""
        from pet import skins
        sid = str(self._store.value("skin_id", "") or "")
        skin = skins.load_skin(sid) if sid else None
        if skin is None:
            skin = skins.BuiltinSkin()
        skins.apply_skin(skin)
        self._skin = skin

    def _warm_settings(self):
        """预热设置对话框：进程首次构建设置 UI 的一次性 Qt 开销较慢（约 0.4s），
        在启动后台提前构建+销毁一次，用户首次打开「设置」即秒开。"""
        if getattr(self, "_open_settings_busy", False):
            return
        try:
            from pet.settings_dialog import SettingsDialog
            d = SettingsDialog(self, pet=self)
            d.close()
            d.deleteLater()
        except Exception:
            pass

    def _warm_voice(self):
        """预热语音模块：Edge/本地/千问 TTS 首次初始化（模块导入 + pygame 音频设备
        初始化）实测 1.3s+，会阻塞 UI 导致首次点击/拖动/台词明显卡顿。
        提前在启动空闲期完成，首次说话即就绪、零阻塞。"""
        mode = str(self._store.value("ai_voice_mode", "off") or "off")
        if mode not in ("edge", "local", "cosy"):
            return
        try:
            from voice.tts import VoiceModule
            v = VoiceModule.shared()
            if not self._voice_done_hooked:
                v.done.connect(self._on_voice_done)
                v.started.connect(self._on_voice_started)
                self._voice_done_hooked = True
            self._voice = v
        except Exception:
            pass

    def _start_lazy_warm(self):
        """启动后空闲期分片预加载常用懒加载动作，首次右键菜单/播放不再卡顿。"""
        self._lazy_warm_keys = [
            k for k in self._MENU_PRELOAD_ORDER
            if k in self._actions_map and k not in getattr(self, "_lazy_loaded", set())]
        if self._lazy_warm_keys:
            self._lazy_warm_timer.start()

    def _lazy_warm_tick(self):
        """每 tick 预加载一个懒加载动作；非空闲状态（拖动/菜单/走动等）跳过，
        避免阻塞用户交互。全部加载完即停。"""
        if not self._lazy_warm_keys:
            self._lazy_warm_timer.stop()
            return
        if self.state != self.STATE_IDLE:
            return
        key = self._lazy_warm_keys.pop(0)
        try:
            self._ensure_lazy_loaded(key)
        except Exception:
            pass

    # ---------- 右键菜单 ----------

    # 菜单弹出期间后台预加载的常用懒加载动作（走正常 LRU，无额外内存池）
    _MENU_PRELOAD_ORDER = (ActionKey.THINK.value, ActionKey.HAPPY.value,
                           ActionKey.SHY.value, ActionKey.JUMP.value,
                           ActionKey.DANCE.value)

    # 动作菜单规格：(动作key枚举, 触发方式)  None=分隔线
    # state=直接切换状态并保持；oneshot=一次性动画播完回待机
    _ACTION_MENU_SPEC = [
        (ActionKey.IDLE, "state"), (ActionKey.FRONT, "state"),
        (ActionKey.ANGRY, "state"), (ActionKey.BACK, "state"),
        None,
        (ActionKey.THINK, "oneshot"), (ActionKey.HAPPY, "oneshot"), (ActionKey.CRY, "oneshot"),
        (ActionKey.SHY, "oneshot"), (ActionKey.SURPRISED, "oneshot"),
        (ActionKey.SLEEP, "oneshot"), (ActionKey.JUMP, "oneshot"), (ActionKey.ROLL, "oneshot"),
        (ActionKey.DANCE, "oneshot"),
    ]
    # 动作 key 枚举 → 窗口状态常量名
    _STATE_BY_KEY = {
        ActionKey.IDLE: "idle", ActionKey.FRONT: "front", ActionKey.ANGRY: "angry",
        ActionKey.BACK: "back", ActionKey.THINK: "think", ActionKey.HAPPY: "happy",
        ActionKey.CRY: "cry", ActionKey.SHY: "shy", ActionKey.SURPRISED: "surprised",
        ActionKey.SLEEP: "sleep", ActionKey.JUMP: "jump", ActionKey.ROLL: "roll",
        ActionKey.DANCE: "dance",
    }

    def _build_action_menu(self, menu):
        """按当前皮肤的动作映射动态生成「动作」子菜单。"""
        added = False
        for item in self._ACTION_MENU_SPEC:
            if item is None:
                if added:
                    menu.addSeparator()
                continue
            key, kind = item
            if key.value not in self._actions_map:
                continue
            label = self._action_label(key.value)
            st = getattr(self, "STATE_" + self._STATE_BY_KEY[key].upper())
            if kind == "state":
                menu.addAction(label, lambda k=st: self._request_state(k))
            else:
                menu.addAction(label, lambda k=st: self._play_oneshot(k))
            added = True
        menu.addSeparator()
        menu.addAction("表演一套动作", self._play_sequence)
        menu.addAction("待机", self._go_idle)

    def contextMenuEvent(self, event):
        # 菜单打开期间分片预加载常用懒加载动作：用户慢点动作时已就绪（0ms）
        self._menu_preload_keys = [
            k for k in self._MENU_PRELOAD_ORDER
            if k in self._actions_map and k not in getattr(self, "_lazy_loaded", set())]
        self._preload_timer.start()
        menu = QMenu(self)

        action_menu = menu.addMenu("动作")
        self._build_action_menu(action_menu)

        menu.addAction("躲猫猫", self._hide_then_show)
        menu.addAction("设置", self._open_settings)
        menu.addSeparator()
        if prov.exists("deepseek-web"):        # 网页版为可选插件
            menu.addAction("登录 DeepSeek", self._open_ai_login)
        menu.addAction("聊天", self._open_ai_chat)
        menu.addSeparator()
        menu.addAction("退出", QApplication.instance().quit)
        menu.exec(event.globalPos())
        self._preload_timer.stop()

    def _menu_preload_tick(self):
        """菜单打开期间每 tick 预加载一个常用懒加载动作（走正常 LRU 缓存）。"""
        if not self._menu_preload_keys:
            self._preload_timer.stop()
            return
        key = self._menu_preload_keys.pop(0)
        try:
            self._ensure_lazy_loaded(key)
        except Exception:
            pass

    # ---------- DeepSeek AI ----------

    def _open_ai_login(self):
        if not prov.exists("deepseek-web"):
            QMessageBox.information(
                self, "提示",
                "网页版后端未安装（缺 deskpet-plugin-deepseek-web）。\n"
                "请在设置中选择其它 AI 后端（官方 API / 千问 / 自定义）。")
            return
        from ai.chat import LoginDialog
        self._top_timer.stop()
        try:
            LoginDialog(self).exec()
        finally:
            self._top_timer.start(2000)

    def _open_ai_chat(self):
        try:
            from ai.chat import LoginDialog, ChatWindow, is_logged_in
        except ImportError as e:
            QMessageBox.warning(self, "提示", f"缺少 AI 依赖：{e}\n请先安装 wasmtime、curl_cffi")
            return
        pid = ai_client.backend_name()
        if pid == "qwen":
            if not ai_client.load_qwen_key():
                QMessageBox.information(
                    self, "提示", "请先在设置中填写千问 API Key")
                return
        elif pid == "deepseek-api":
            if not ai_client.load_deepseek_api_key():
                QMessageBox.information(
                    self, "提示", "请先在设置中填写 DeepSeek 官方 API Key")
                return
        elif pid == "deepseek-web":
            if not is_logged_in():
                QMessageBox.information(self, "提示", "请先右键「登录 DeepSeek」完成登录")
                return
        self._top_timer.stop()
        try:
            win = ChatWindow(self, preprocessor=self.nlu_preprocess)
            self._chat_window = win
            win.exec()
        finally:
            self._chat_window = None
            self._top_timer.start(2000)