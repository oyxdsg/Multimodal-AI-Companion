"""宠物各功能模式的调度壳（PetModesMixin）。

原为 pet/window.py 的一部分；各功能域（环境/新闻/工作/游戏/修仙/STT）已拆到
pet/handlers/ 下的独立 Handler，本文件只保留：
- 窗口级共享状态（游戏联动类型、随机台词系统、AI 动作标签驱动）
- 各 Handler 的创建、信号连接与统一心跳调度
- 对外转发方法（兼容 window.py / settings.py / settings_dialog.py 的既有调用）

AI 会话调用统一走 ai.chat_service，游戏/修仙业务走 game.processor。
"""

import random
import time

from PySide6.QtCore import QTimer

import config
import ai.client as ai_client
from core.action_key import ActionKey
from pet.handlers.game_handler import GameHandler
from pet.handlers.xiuxian_handler import XiuxianHandler
from pet.handlers.env_handler import EnvHandler
from pet.handlers.news_handler import NewsHandler
from pet.handlers.work_handler import WorkHandler
from pet.handlers.stt_handler import SttHandler
from pet.handlers.maid_handler import MaidHandler


class PetModesMixin:
    """各功能模式（环境/新闻/工作/游戏/修仙/STT）调度壳。由 PetWindow 继承。"""

    # ---------- 随机台词 ----------

    def _chatline_freq_range(self):
        return config.CHATLINE_FREQ_RANGES.get(
            self._chatline_freq, config.CHATLINE_FREQ_RANGES[0])

    def _schedule_chatline(self):
        self._chatline_timer.stop()
        if not self._chatline_enabled:
            return
        lo, hi = self._chatline_freq_range()
        self._chatline_timer.start(random.randint(lo * 1000, hi * 1000))

    def _on_chatline_timeout(self):
        self._say_random("idle", force=True)
        self._schedule_chatline()

    def _say_random(self, category, force=False):
        """从台词库随机选一句并冒气泡；受开关与冷却控制。"""
        if not self._chatline_enabled:
            return
        now = time.monotonic()
        lo, _ = self._chatline_freq_range()
        if not force and now - self._last_line_time < lo:
            return
        lines = config.AI_PRESET_LINES.get(category)
        if not lines:
            return
        candidates = [l for l in lines if l != self._last_line]
        line = random.choice(candidates or lines)
        self._last_line = line
        self._last_line_time = now
        self._show_ai_bubble(line, kind="chatline")
        self._speak_line(line)

    def _speak_line(self, text):
        """朗读预设台词（口头禅）：千问 TTS 用本地缓存（每句仅合成一次，
        不浪费 API）；edge / local 走原语音引擎。"""
        mode = str(self._store.value("ai_voice_mode", "off") or "off")
        if mode == "cosy":
            # 音色/TTS 模型统一走 credentials_store（含旧键只读回退）
            try:
                from ai import credentials_store as _creds
                voice = _creds.get_tts("voice", "") or "Chelsie"
            except Exception:
                voice = "Chelsie"
            if self._voice is None:
                try:
                    from voice.tts import VoiceModule
                    self._voice = VoiceModule.shared()
                except Exception:
                    return
            self._voice.speak_cached(text, voice)
        elif mode in ("edge", "local"):
            self._speak_reminder(text)

    # ---------- 初始化与统一心跳 ----------

    def _init_env(self):
        # 游戏联动类型（互斥单选：0 关闭 / 1 Minecraft / 2 修仙小游戏）
        self._game_type = int(self._store.value("game_type", -1) or -1)
        if self._game_type not in (0, 1, 2):
            # 兼容旧版本独立开关设置
            _old_xx = str(self._store.value("xiuxian_enabled", False)
                          ).lower() in ("true", "1", "yes", "on")
            _old_game = str(self._store.value("game_enabled", False)
                            ).lower() in ("true", "1", "yes", "on")
            self._game_type = 2 if _old_xx else (1 if _old_game else 0)
        # 以下两个开关由 _game_type 推导，仅兼容既有代码分支
        self._game_enabled = (self._game_type == 1)
        self._xiuxian_enabled = (self._game_type == 2)
        self._voice = None        # 语音模块（_warm_voice 预热后共享）

        # 各功能域 Handler（环境/新闻/工作/游戏/修仙/STT）
        self._env_handler = EnvHandler(self)
        self._news_handler = NewsHandler(self)
        self._work_handler = WorkHandler(self)
        self._game_handler = GameHandler(self)
        self._xiuxian_handler = XiuxianHandler(self)
        self._stt_handler = SttHandler(self)
        # 智能女仆联动（M5-b）：WebSocket Server 连 SmartMaid 模组
        self._maid_handler = MaidHandler(self)

        # 统一心跳调度：一个 1s QTimer 按各自周期分发环境/工作/游戏检查
        self._hb_env = 0.0
        self._hb_work = 0.0
        self._hb_game = 0.0
        self._hb_xiuxian = 0.0
        self._heartbeat = QTimer(self)
        self._heartbeat.timeout.connect(self._heartbeat_tick)
        self._heartbeat.start(1000)
        self.weather_ready.connect(self._env_handler.on_weather_ready)
        self.news_ready.connect(self._news_handler.on_news_ready)
        self.tip_ready.connect(self._work_handler.on_tip_ready)
        self.game_ready.connect(self._on_game_ready)
        QTimer.singleShot(3000, self._env_tick)

    def _heartbeat_tick(self):
        """统一心跳：按各自周期分发环境(60s)/工作(30s)/游戏(5s)/修仙(5s)检查。"""
        now = time.time()
        if now - self._hb_env >= 60:
            self._hb_env = now
            self._env_tick()
        if now - self._hb_work >= 30:
            self._hb_work = now
            self._work_tick()
        if now - self._hb_game >= 5:
            self._hb_game = now
            self._game_tick()
        if now - self._hb_xiuxian >= 5:
            self._hb_xiuxian = now
            self._xiuxian_tick()
        # 女仆联动：每秒轮询一次（队列为空时几乎零开销）
        self._maid_tick()

    # ---------- 兼容代理（供设置对话框等外部读取各 Handler 运行时状态） ----------

    @property
    def _env_enabled(self):
        return self._env_handler._env_enabled

    @property
    def _manual_city(self):
        return self._env_handler._manual_city

    @property
    def _news_enabled(self):
        return self._news_handler._news_enabled

    @property
    def _news_feeds(self):
        return self._news_handler._news_feeds

    @property
    def _news_freq_min(self):
        return self._news_handler._news_freq_min

    @property
    def _work_enabled(self):
        return self._work_handler._work_enabled

    @property
    def _work_freq_min(self):
        return self._work_handler._work_freq_min

    @property
    def _game_log_path(self):
        return self._game_handler._game_log_path

    @property
    def _game_freq(self):
        return self._game_handler._game_freq

    @property
    def _game_source(self):
        return self._game_handler._game_source

    @property
    def _game_normal_summary_min(self):
        return self._game_handler._game_normal_summary_min

    @property
    def _game_chat_respond(self):
        return self._game_handler._game_chat_respond

    @property
    def _xiuxian_game_dir(self):
        return self._xiuxian_handler._xiuxian_game_dir

    # ---------- 对外转发（兼容 window / settings / settings_dialog 调用） ----------

    def _env_tick(self):
        self._env_handler.tick()

    def _maybe_fetch_news(self):
        self._news_handler.tick()

    def _work_tick(self):
        self._work_handler.tick()

    def _game_tick(self):
        self._game_handler.tick()

    def _xiuxian_tick(self):
        self._xiuxian_handler.tick()

    def _maid_tick(self):
        self._maid_handler.tick()

    # ---------- 智能女仆联动对外接口（供对话/UI 调用） ----------

    @property
    def maid_link_connected(self):
        return self._maid_handler.connected

    def maid_state_line(self, maid=""):
        return self._maid_handler.maid_state_line(maid)

    def maid_command(self, cmd, params=None, maid="", cancel_previous=False):
        """驱动女仆执行指令（attack/mine/farm/build/craft/... 同 /maidtasks）。
        cancel_previous 默认 False（排队，不抢占女仆当前任务）。"""
        return self._maid_handler.send_command(cmd, params, maid=maid,
                                               cancel_previous=cancel_previous)

    def maid_speak(self, text, maid=""):
        """让女仆头顶冒气泡。"""
        return self._maid_handler.speak(text, maid=maid)

    def maid_animate(self, name="", anim_id=None, maid=""):
        """让女仆播放动作。"""
        return self._maid_handler.animate(name=name, anim_id=anim_id, maid=maid)

    # ---------- 本地预处理（NLU：意图识别 + 槽位抽取） ----------

    def nlu_router(self):
        """懒加载 NLU 路由器；未启用 / 模型缺失 / 依赖不可用时返回 None。

        返回 None 时调用方应**原样回退**给大 AI，绝不能让预处理影响正常聊天。
        """
        cached = getattr(self, "_nlu_router", False)
        if cached is not False:
            return cached

        router = None
        try:
            enabled = bool(self._store.value("nlu_enabled", config.NLU_ENABLED))
            if enabled:
                from nlu import intent as nlu_intent
                if nlu_intent.available():
                    from nlu.router import NluRouter
                    from pet.handlers.nlu_bridge import MaidNluBridge
                    router = NluRouter(MaidNluBridge(self._maid_handler))
        except Exception:
            router = None
        self._nlu_router = router
        return router

    def nlu_preprocess(self, text):
        """对用户输入做本地预处理，返回「注入大 AI」的结构化文本（无内容则空串）。

        **会阻塞等女仆回执**，只能在工作线程调用（聊天 / 语音 worker）。

        返回 ``(inject_text, executed)``：
        - inject_text：给大 AI 的结构化文本（无内容为空串）
        - executed：本地程序是否已自动执行（NLU 已处理用户意图时，AI 兜底
          不应再发重复/冲突的 DSL 指令——如「穿金胸甲」NLU 已 transfer 穿上，
          AI 若再回【equip】会把胸甲拿回主手，撤销执行效果）。
        """
        router = self.nlu_router()
        if router is None or not text:
            return "", False
        try:
            res = router.preprocess(text)
        except Exception:
            return "", False
        if res.executed or res.mode == "hint":
            try:
                from core.perf_log import log as _perf
                slot = res.slots or {}
                picked = (slot.get("item_id") or slot.get("item")
                          or slot.get("target_id") or slot.get("target") or "-")
                _perf("NLU", "意图", len(text),
                      note="%s(%.2f)%s | 「%s」→ %s" % (
                          res.intent, res.confidence,
                          "/已执行" if res.executed else "/未执行",
                          text[:20], picked))
            except Exception:
                pass
        return (res.summary if res.useful else ""), bool(res.executed)

    def _hotkey_tick(self):
        self._stt_handler.hotkey_tick()

    def _start_xiuxian(self):
        self._xiuxian_handler.start()

    def _stop_xiuxian(self):
        self._xiuxian_handler.stop()

    def _cleanup_xiuxian(self):
        self._xiuxian_handler.cleanup()

    def _set_game_type(self, t):
        """互斥设置游戏联动类型（0 关闭 / 1 Minecraft / 2 修仙），并持久化。"""
        self._game_type = int(t)
        self._game_enabled = (self._game_type == 1)
        self._xiuxian_enabled = (self._game_type == 2)
        self._store.setValue("game_type", self._game_type)

    def _on_game_ready(self, text):
        if self._game_type not in (1, 2) or not text:
            return
        clean = self._strip_action_tags(text)
        # 女仆在线：回复同时冒到游戏内女仆头顶（桌宠窗口此时通常已隐退）。
        # 但玩家正在跟女仆主动对话时，游戏事件线（挖矿/打怪/发现敌人的 AI 互动）
        # 不再 speak 到女仆——避免"两条线抢话"：只显示桌宠气泡、不插到女仆嘴边。
        if self._game_type == 1 and self.maid_link_connected:
            try:
                if not self._maid_handler.recently_in_chat():
                    self._maid_handler.speak(clean)
            except Exception:
                pass
        self._show_ai_bubble(clean, speak=True)

    def _strip_action_tags(self, text):
        """移除 AI 回复中夹带的动作标签（正常聊天才用标签驱动宠物）。"""
        return ai_client.strip_action_tags(text)

    def _apply_ai_actions(self, actions):
        """根据 AI 输出动作标签驱动宠物（只取第一个有效标签）。"""
        for tag in actions:
            act = config.AI_ACTION_TAGS.get(tag)
            if not act:
                continue
            ak = ActionKey.from_value(act)
            if ak is ActionKey.SEQUENCE:
                self._play_sequence()
            elif ak is ActionKey.IDLE:
                self._go_idle()
            elif ak is ActionKey.ANGRY:
                self._request_state(self.STATE_ANGRY)
            elif ak is ActionKey.BACK:
                self._request_state(self.STATE_BACK)
            elif ak is ActionKey.SIT:
                self._request_state(self.STATE_IDLE)
            elif ak in (ActionKey.THINK, ActionKey.HAPPY, ActionKey.CRY,
                        ActionKey.SHY, ActionKey.SURPRISED, ActionKey.SLEEP,
                        ActionKey.JUMP, ActionKey.ROLL):
                self._play_oneshot(act)
            else:  # rise / front
                self._request_state(self.STATE_FRONT)
            break
