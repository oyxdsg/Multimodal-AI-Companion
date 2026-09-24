"""气泡消息渲染：语音朗读 + 统一消息队列 + 音画同步。

原为 pet/window.py 的一部分，现拆为独立 Mixin（PetBubbleMixin），
由 PetWindow 继承。_speak_reminder 供各模式（环境/新闻/台词）复用。
"""

import config
from ai.qwen import DEFAULT_VOICE
from core.widgets import AIBubble


def _tts_voice():
    """当前音色（credentials_store 为唯一事实来源，含旧键 `qwen_cosy_voice` 回退）。"""
    try:
        from ai import credentials_store as creds
        return creds.get_tts("voice", "") or DEFAULT_VOICE
    except Exception:
        return DEFAULT_VOICE


class PetBubbleMixin:
    """气泡渲染层（音画同步）与语音朗读。由 PetWindow 继承。"""

    def _speak_reminder(self, text):
        mode = str(self._store.value("ai_voice_mode", "off") or "off")
        if mode not in ("edge", "local", "cosy"):
            return None
        if self._voice is None:
            try:
                from voice.tts import VoiceModule
                self._voice = VoiceModule.shared()
            except Exception:
                return None
        if not self._voice_done_hooked:
            self._voice.done.connect(self._on_voice_done)
            self._voice.started.connect(self._on_voice_started)
            self._voice_done_hooked = True
        if mode == "cosy":
            voice = _tts_voice()
            return self._voice.enqueue(text, mode, voice)
        else:
            return self._voice.enqueue(text, mode)

    def _frame_content_top(self):
        """当前显示帧中角色内容最高点的 y（相对窗口顶部）。"""
        act = None
        if self.state == self.STATE_TURN:
            act = self.actions.get("turn")
        elif self.state in (self.STATE_WALK, self.STATE_DRAG):
            act = self.actions.get("side")
        else:
            act = self.actions.get(self.state)
        if not act:
            return 0
        tops = act.get("tops")
        if not tops:
            return 0
        return tops[self.frame_i % len(tops)]

    def _show_ai_bubble(self, text, error=False, speak=False,
                        kind="ai_reply", priority=None):
        """统一消息入口：所有消息（语音输入 / AI 回复 / 日志 / 系统 / 台词）
        经 PetMessageQueue 按优先级排队串行显示，避免并发消息互相打断。
        底层渲染见 _render_message（气泡 + 语音，音画同步）。

        兜底剥离【指令】与动作标签：指令/标签既不该上屏也不该进 TTS，
        玩家听到语音念指令会很出戏（上游各链路已剥，这里防漏网）。
        """
        if not text:
            return
        try:
            import ai.client as ai_client
            text = ai_client.strip_action_tags(text)
        except Exception:
            pass
        if not text:
            return
        self._msg.post(text, kind=kind, speak=speak,
                       error=error, priority=priority)

    def _render_message(self, text, error=False, speak=False):
        """消息队列消费：把一条消息渲染为气泡（内部 _bubble_queue 串行 +
        音画同步）。由 PetMessageQueue 串行调用，完成后 _render_idle() 为真。"""
        if not text:
            return
        self._bubble_queue.append((text, error, speak))
        if len(self._bubble_queue) > config.BUBBLE_QUEUE_MAX:
            self._bubble_queue.pop(0)   # 队列上限，丢弃最旧
        self._pump_bubble_queue()

    def _render_idle(self):
        """渲染层是否空闲（气泡队列清空且当前不在显示）——消息队列据此决定是否取下一条。"""
        return not self._bubble_busy and not self._bubble_queue

    def _pump_bubble_queue(self):
        if self._bubble_busy or not self._bubble_queue:
            return
        self._bubble_busy = True
        text, error, speak = self._bubble_queue.pop(0)
        self._bubble_wait_task = None
        self._bubble_pending = None
        if speak:
            # 音画同频：先入队语音，语音真正开始播放时才显示气泡，
            # 语音播完时收尾气泡（声音开始=画面开始，声音结束=画面结束）。
            self._bubble_pending = (text, error)
            self._bubble_wait_task = self._speak_reminder(text)
            if self._bubble_wait_task is None:
                # 语音未启用：立即显示 + 固定时长
                self._bubble_pending = None
                self._show_bubble_now(text, error)
                self._bubble_timer.start(config.BUBBLE_DISPLAY_MS)
        else:
            self._show_bubble_now(text, error)
            self._bubble_timer.start(config.BUBBLE_DISPLAY_MS)

    def _show_bubble_now(self, text, error=False):
        # 女仆在场（桌宠窗口已隐藏）：不渲染桌面气泡，语音仍由 _speak_reminder 播放
        if getattr(self, "_suppress_bubble", False):
            return
        if self._bubble is None:
            self._bubble = AIBubble()
            self._bubble.closed.connect(self._on_bubble_done)
        self._bubble.show_text(text, error=error)
        self._position_bubble()
        self._bubble.show()
        self._bubble.raise_()

    def _on_bubble_done(self):
        self._bubble_timer.stop()
        self._bubble_wait_task = None
        self._bubble_pending = None
        self._bubble_busy = False
        self._pump_bubble_queue()

    def _on_voice_started(self, idx):
        """对应语音开始播放 → 此刻才显示气泡（画面与声音同起点）。"""
        if (self._bubble_pending is not None
                and self._bubble_wait_task is not None
                and idx >= self._bubble_wait_task):
            text, error = self._bubble_pending
            self._bubble_pending = None
            self._show_bubble_now(text, error)

    def _on_voice_done(self, idx):
        """对应语音播完 → 结束当前气泡，播放下一条。"""
        if (self._bubble_wait_task is not None
                and idx >= self._bubble_wait_task):
            self._bubble_wait_task = None
            self._on_bubble_done()

    def _position_bubble(self):
        b = self._bubble
        if b is None:
            return
        bw, bh = b.width(), b.height()
        if bw == 0 or bh == 0:
            b.adjustSize()
            bw, bh = b.width(), b.height()
        top = self._frame_content_top()
        x = self.x() + (self.width() - bw) // 2
        y = self.y() + top - bh - 6   # 气泡底部贴近角色头顶上方 6px
        if y < 0:
            y = self.y() + self.height() + 6
        b.move(x, y)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._bubble is not None and self._bubble.isVisible():
            self._position_bubble()