# -*- coding: utf-8 -*-
"""语音输入（STT）Handler：按住全局快捷键说话，松开识别并自动对话。

从 pet/modes.py 的 PetModesMixin 语音输入域迁移：
- 热键轮询（长按开始录音，松开/静音超时收尾）
- 双识别引擎（Vosk / faster-whisper）
- 识别文本 → 共享 AI 会话 → 简短回复（30 字内）
结果经 app.stt_text_ready / app.stt_reply_ready 信号交给窗口展示。
"""
import threading
import time

import config
import voice.stt as stt
from ai.chat_service import shared as chat_service
from pet.handlers.base import BaseHandler


def _key_down(vk):
    """查询按键当前是否按下（GetAsyncKeyState，后台全局可用）。"""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


class SttHandler(BaseHandler):
    """语音输入（按住热键说话，松开识别并对话）。"""

    def __init__(self, app):
        super().__init__(app)
        store = self.store
        self._stt_enabled = str(
            store.value("stt_enabled", False)) != "false"
        self._stt_engine_name = str(
            store.value("stt_engine", config.STT_DEFAULT_ENGINE) or "")
        self._stt_auto_send = str(
            store.value("stt_auto_send", True)) != "false"
        self._stt_silence_ms = int(
            store.value("stt_silence", config.STT_SILENCE_MS)
            or config.STT_SILENCE_MS)
        self._stt_recorder = None
        self._stt_engine = None
        self._stt_recording = False
        self._stt_was_down = False
        self._stt_press_at = 0.0

    # ---------- 热键轮询（窗口 QTimer 绑定） ----------

    def hotkey_tick(self):
        """每 50ms 轮询热键：长按开始录音，松开/静音超时收尾识别。"""
        if not self._stt_enabled:
            return
        vk = config.STT_HOTKEY_VK
        down = _key_down(vk)
        now = time.time()
        if down and not self._stt_was_down:
            self._stt_press_at = now
        elif not down and self._stt_was_down:
            if self._stt_recording:
                self._stop_stt()
            self._stt_press_at = 0.0
        elif down and not self._stt_recording and self._stt_press_at:
            if now - self._stt_press_at >= 0.15:   # 长按判定，避免打字误触
                self._start_stt()
        # 按住期间静音超时 → 自动收尾识别
        if (self._stt_recording and self._stt_recorder is not None
                and self._stt_recorder.silent_for() * 1000
                >= self._stt_silence_ms):
            self._stop_stt()
        self._stt_was_down = down

    # ---------- 录音与识别 ----------

    def _get_stt_engine(self):
        if self._stt_engine is None:
            self._stt_engine = stt.STTEngine(
                self._stt_engine_name,
                whisper_size=str(self.store.value(
                    "stt_whisper_model", config.STT_WHISPER_MODEL)
                    or config.STT_WHISPER_MODEL))
        return self._stt_engine

    def _start_stt(self):
        """开始录音：停掉正在播放的 TTS 保证收音干净，气泡提示聆听中。"""
        if self._stt_recording:
            return
        voice = getattr(self.app, "_voice", None)
        if voice is not None:
            try:
                voice.stop()
            except Exception:
                pass
        try:
            self._stt_recorder = stt.Recorder(self._stt_silence_ms)
            self._stt_recorder.start()
        except Exception as e:
            self._stt_recording = False
            self.msg.post(f"无法打开麦克风：{e}",
                          kind="system", speak=False, force=True)
            return
        self._stt_recording = True
        self.msg.post(config.STT_LISTEN_TEXT, kind="system",
                      speak=False, force=True)

    def _stop_stt(self):
        if not self._stt_recording:
            return
        self._stt_recording = False
        res = self._stt_recorder.stop() if self._stt_recorder else None
        self._stt_recorder = None
        if not res:
            self.msg.post("没听清，再说一遍？", kind="system",
                          speak=True, force=True)
            return
        pcm, sr = res
        threading.Thread(target=self._stt_worker, args=(pcm, sr),
                         daemon=True).start()

    def _stt_worker(self, pcm, sr):
        try:
            text = self._get_stt_engine().recognize(pcm, sr)
        except Exception:
            text = ""
        text = (text or "").strip()
        if not text:
            self.msg.post("没听清，再说一遍？", kind="system",
                          speak=True, force=True)
            return
        self.app.stt_text_ready.emit(text)

    # ---------- 对话 ----------

    def on_stt_text(self, text):
        """stt_text_ready 信号槽：气泡显示你说的话，并自动发送给 AI。"""
        self.msg.post(f"你说：{text}", kind="voice_input", speak=False)
        self._inject_chat(text, "")
        # 女仆在线：语音识别结果当作对女仆说的话（回复显示在游戏内女仆 + 语音朗读）
        try:
            if self.app.maid_link_connected:
                handler = getattr(self.app, "_maid_handler", None)
                if handler is not None:
                    threading.Thread(target=handler.chat_turn,
                                     args=(text,), kwargs={"speak": True},
                                     daemon=True).start()
                    return
        except Exception:
            pass
        if self._stt_auto_send:
            threading.Thread(target=self._stt_ask, args=(text,),
                             daemon=True).start()

    def _stt_ask(self, text):
        """语音文本 → 共享 AI 会话 → 简短回复（语音场景 30 字内，减少朗读时长）。"""
        try:
            # 本地预处理（NLU）：语音输入是错字重灾区（「稿子」→「镐子」），
            # 高置信意图可直接执行，并把结果一并交给大 AI。
            extra = ""
            try:
                r = self.app.nlu_preprocess(text)
                extra = r[0] if isinstance(r, tuple) else (r or "")
            except Exception:
                extra = ""
            head = ("（当前是语音对话：请用简体中文一句话简短回复，"
                    "30 字以内，不要使用任何动作标签）\n")
            prompt = head + (extra + "\n\n" if extra else "") + text
            content = chat_service.chat(prompt, "语音")
            content = (content or "").strip()
            if content:
                self.app.stt_reply_ready.emit(content)
        except Exception:
            pass

    def on_stt_reply(self, content):
        """stt_reply_ready 信号槽：过滤动作标签 → 驱动宠物动作 → 气泡 + 语音。"""
        if not content:
            return
        import ai.client as ai_client
        clean, actions = ai_client.parse_ai_output(content)
        if actions:
            self.app._apply_ai_actions(actions)
        self.msg.post(clean, kind="ai_reply", speak=True)
        self._inject_chat("", clean)

    def _inject_chat(self, user_text, ai_text):
        """语音对话同步进聊天窗口历史（窗口已打开时）。"""
        win = getattr(self.app, "_chat_window", None)
        if win is not None and win.isVisible():
            try:
                win.append_chat(user_text, ai_text)
            except Exception:
                pass
