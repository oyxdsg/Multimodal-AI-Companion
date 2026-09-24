"""AI 相关 UI：DeepSeek 登录对话框 + 聊天窗口（气泡式、非流式）。"""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from PySide6.QtCore import Qt, QSettings, Signal, QObject, QTimer, QRect, QSize, QEvent
from PySide6.QtGui import (
    QColor, QPainter, QPainterPath, QPen, QKeyEvent, QMouseEvent, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QHBoxLayout,
    QScrollArea,
    QTextEdit,
    QWidget,
    QFrame,
    QTabWidget,
    QStackedWidget,
    QComboBox,
    QCheckBox,
    QListWidget,
    QListWidgetItem,
)

import config
import ai.client as ai_client
from ai import providers as prov
from core.action_key import ActionKey
from core.trace import traced
from ai.client import (
    load_credentials as _load_credentials,
    save_credentials as _save_credentials,
    is_logged_in,
    make_client,
    make_web_client,
    parse_ai_output,
    save_thread_state as _save_thread_state,
    clear_thread_state as _clear_thread_state,
)
from ai.stream_strip import SENT_SPLIT_RE, TagStripper, split_speakable
from ai.deepseek_api import DeepSeekApiError
from ai.qwen import QwenError
from ai.protocols.base import KIND_AUDIO, KIND_TEXT
from core.icons import icon
from pet.settings_dialog import load_ai_opts, save_ai_opts
from voice.tts import VoiceModule, StreamVoice

_STORE = QSettings("DesktopPet", "PetWindow")


# ---------------------------------------------------------------------------
# 登录对话框
# ---------------------------------------------------------------------------

_EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _find_edge():
    for p in _EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _login_helper_path():
    """登录辅助脚本（由 deepseek-web 插件提供）；缺失返回 ""。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "plugins", "deepseek-web", "login.py")
    return p if os.path.isfile(p) else ""


class LoginDialog(QDialog):
    """登录 DeepSeek：
    - 自动登录：以独立子进程启动 playwright 驱动系统 Edge 登录，
      成功后在子进程里抓取 Token/Cookies。主程序不加载 playwright，
      即使浏览器出问题也绝不会影响主程序。
    - 手动粘贴：作为自动登录不可用时的备用方式
    """

    @property
    def _HELPER(self):
        return _login_helper_path()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("登录 DeepSeek")
        self.resize(640, 440)

        self._cancelled = False
        self._proc = None
        self._out_file = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._poll_result)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: rgba(0,0,0,0.45);")

        tabs = QTabWidget(self)
        tabs.addTab(self._build_auto_tab(), "自动登录")
        tabs.addTab(self._build_manual_tab(), "手动粘贴凭证")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(self._status)

        token, _ = _load_credentials()
        if token:
            self._status.setText("已保存过登录状态；如需更换账号请重新登录。")
            self._status.setStyleSheet("color: #52C41A;")

    def closeEvent(self, event):
        self._cancelled = True
        self._poll_timer.stop()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass
        super().closeEvent(event)

    # ---------- 自动登录 tab ----------

    def _build_auto_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        tip = QLabel(
            "点击下方按钮，程序会自动完成登录：\n"
            "· 若已登录过，将静默获取凭证，无需任何操作；\n"
            "· 若未登录过，会弹出 Edge 窗口，登录一次即可（扫码 / 账号密码均可）。\n"
            "登录状态会自动保存，以后都是秒级自动完成，无需再登录、也无需手动粘贴任何内容。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: rgba(0,0,0,0.65);")
        self._start_btn = QPushButton("开始自动登录")
        self._start_btn.setMinimumHeight(44)
        self._start_btn.clicked.connect(self._start_auto_login)
        self._force_btn = QPushButton("我已在 Edge 中登录完成，点击获取凭证")
        self._force_btn.setMinimumHeight(40)
        self._force_btn.hide()
        self._force_btn.clicked.connect(self._force_finish)
        lay.addWidget(tip)
        lay.addWidget(self._start_btn)
        lay.addWidget(self._force_btn)
        lay.addStretch(1)
        return w

    def _force_finish(self):
        if self._out_file:
            try:
                with open(self._out_file + ".force", "w", encoding="utf-8") as f:
                    f.write("1")
            except Exception:
                pass
            self._status.setText("已通知程序获取凭证…")
            self._status.setStyleSheet("color: rgba(0,0,0,0.45);")

    def _start_auto_login(self):
        if not self._start_btn.isEnabled():
            return
        edge = _find_edge()
        if edge is None:
            self._status.setText("未找到系统 Edge，请改用「手动粘贴凭证」方式登录。")
            self._status.setStyleSheet("color: #FF4D4F;")
            return
        fd, out_path = tempfile.mkstemp(prefix="ds_login_", suffix=".json")
        os.close(fd)
        self._out_file = out_path
        self._cancelled = False
        self._start_btn.setEnabled(False)
        self._start_btn.setText("正在打开 Edge…")
        self._status.setText("正在打开浏览器，请在 Edge 中完成登录…")
        self._status.setStyleSheet("color: rgba(0,0,0,0.45);")
        try:
            self._proc = subprocess.Popen(
                [sys.executable, self._HELPER, edge, out_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._start_btn.setEnabled(True)
            self._start_btn.setText("重新尝试自动登录")
            self._status.setText(f"启动登录进程失败：{e}")
            self._status.setStyleSheet("color: #FF4D4F;")
            return
        self._force_btn.show()
        self._poll_timer.start(500)

    def _poll_result(self):
        if self._cancelled:
            return
        if self._out_file and os.path.exists(self._out_file):
            try:
                with open(self._out_file, encoding="utf-8") as f:
                    result = json.load(f)
                self._cleanup_out_file()
                self._on_worker_result(result)
                return
            except (ValueError, OSError):
                pass
        self._poll_timer.start(500)

    def _cleanup_out_file(self):
        if self._out_file and os.path.exists(self._out_file):
            try:
                os.remove(self._out_file)
            except OSError:
                pass
        self._out_file = None

    def _on_worker_result(self, result):
        if self._cancelled:
            return
        self._start_btn.setEnabled(True)
        self._force_btn.hide()
        if result.get("ok"):
            _save_credentials(result["token"], result.get("cookies", ""))
            self._status.setText("登录成功！")
            self._status.setStyleSheet("color: #52C41A;")
            self.accept()
        else:
            self._start_btn.setText("重新尝试自动登录")
            self._status.setText(result.get("msg", "自动登录失败"))
            self._status.setStyleSheet("color: #FF4D4F;")
            QMessageBox.warning(
                self, "自动登录失败",
                result.get("msg", "自动登录失败")
                + "\n\n详细日志见 desktop-pet\\logs\\login_debug.log",
            )

    # ---------- 手动粘贴 tab ----------

    def _build_manual_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel(
            "从浏览器获取凭证后粘贴到下方（自动登录不可用时用此方式）：\n"
            "1. 用浏览器打开 chat.deepseek.com 并登录\n"
            "2. 按 F12 打开开发者工具 →「网络(Network)」→ 随便发一条消息\n"
            "3. 点击名为 chat/completion 的请求，复制「Authorization」的 Bearer 值填入 Token\n"
            "4. 把整个 Cookie 请求头的值复制到 Cookies（可留空）"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: rgba(0,0,0,0.65);")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("粘贴 Authorization Bearer 的 Token")
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setPlaceholderText("粘贴完整 Cookie（可留空）")
        test_btn = QPushButton("测试并保存")
        test_btn.clicked.connect(self._manual_test)

        lay.addWidget(info)
        lay.addWidget(QLabel("Token："))
        lay.addWidget(self.token_edit)
        lay.addWidget(QLabel("Cookies（可选）："))
        lay.addWidget(self.cookie_edit)
        lay.addWidget(test_btn)
        lay.addStretch(1)

        token, cookies = _load_credentials()
        self.token_edit.setText(token)
        self.cookie_edit.setText(cookies)
        return w

    def _manual_test(self):
        token = self.token_edit.text().strip()
        cookies = self.cookie_edit.text().strip()
        if not token:
            self._status.setText("请先填写 Token")
            self._status.setStyleSheet("color: #FF4D4F;")
            return
        self._status.setText("正在测试连接…")
        self._status.setStyleSheet("color: rgba(0,0,0,0.45);")

        result = {}

        def done():
            ok, msg = result["ok"], result["msg"]
            if ok:
                _save_credentials(token, cookies)
                self._status.setText(msg)
                self._status.setStyleSheet("color: #52C41A;")
                self.accept()
            else:
                self._status.setText(msg)
                self._status.setStyleSheet("color: #FF4D4F;")

        def run():
            try:
                _save_credentials(token, cookies)
                client = make_web_client()
                if client is None:
                    raise RuntimeError("网页版后端未安装")
                client.verify()
                result["ok"], result["msg"] = True, "连接成功，凭证有效！"
            except Exception as e:
                result["ok"], result["msg"] = False, f"连接失败：{e}"

        t = threading.Thread(target=run, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                QTimer.singleShot(100, poll)
            else:
                done()
        poll()


# ---------------------------------------------------------------------------
# 聊天窗口
# ---------------------------------------------------------------------------

# 句子切分与流式剥离共用同一套正则（规范定义在 ai/stream_strip.py，避免两份事实）
_SENT_SPLIT_RE = SENT_SPLIT_RE


def _split_sentences(text):
    """按句子边界切分为完整句子列表（用于逐句合成朗读）。"""
    return [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]


_CHAT_TRACE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "logs", "chat_trace.log")


def trace(kind, text):
    """全链路追踪日志（UTF-8，一行一条）：用户输入 / NLU 决策 / AI 回复 / DSL 决策。

    给自动化全链路测试用——没有它就只能看 UI 气泡，出不了可核查的报告。
    低频（每次聊天几条），不参与 MaidDebug 那种高频门控。
    """
    try:
        os.makedirs(os.path.dirname(_CHAT_TRACE), exist_ok=True)
        with open(_CHAT_TRACE, "a", encoding="utf-8") as f:
            f.write("[%s] %-4s | %s\n" % (time.strftime("%H:%M:%S"), kind,
                                          (text or "").replace("\n", " ⏎ ")))
    except Exception:
        pass


class _ChatWorker(QObject):
    finished = Signal(str, bool)  # (文本, 是否错误)
    delta = Signal(str)           # 流式文本增量

    def __init__(self, client, prompt, opts, images=None, preprocess=None,
                 maid_loop=None):
        super().__init__()
        self.client = client
        self.prompt = prompt
        self.opts = opts
        self.images = list(images or [])
        self.preprocess = preprocess
        self.maid_loop = maid_loop        # 女仆 DSL 闭环（None = 不指挥女仆）
        # 是否走流式路径：由注册表的能力位决定，不用 hasattr(client,"chat_stream")
        # —— 那种隐式判断在适配器统一后会失效（P2 踩过）。
        self.is_stream = prov.chat_streams(opts.get("backend"))
        # P9 原生语音：让**模型自己出音频**（不额外调 TTS）。
        # 需要同时满足三件事，缺一就退化成普通流式（外层 TTS 照常兜）：
        #   ① 语音模式选了 native  ② 该模型声明 output_modalities 含 audio
        #   ③ 本后端走流式（原生语音只在流式路径有，非流式连 audio 字段都不给）
        self.native_audio = False
        self.native_used = False          # 本次真的产出了音频事件
        self.native_fallback = ""         # 原生语音退化的原因（给 UI 提示）
        self.audio_voice = ""
        if opts.get("voice_mode") == "native":
            try:
                pid = opts.get("backend")
                mid = str(opts.get("provider_model") or "")
                if prov.supports(pid, mid, "audio") and prov.chat_streams(pid):
                    self.native_audio = True
                    self.audio_voice = str(opts.get("audio_voice") or "")
            except Exception:
                self.native_audio = False
        self.dsl_inject = ""              # 女仆指令回执注入文本（下轮用）
        self.clean_text = ""              # 流式：工作线程里全量解析后的正文
        self._nlu_executed = False        # 本轮 NLU 是否已本地执行（抑制 AI 冲突 DSL）

    def _prepare_prompt(self):
        prompt = self.prompt
        trace("USER", self.prompt)
        mode_id = int(self.opts.get("ai_mode", 1) or 1)
        # 模式声明分流（两条后端的信息流差异）：
        # - 网页版（SessionBackend）：system 只在会话首条注入，之后服务端靠 parent 链记忆，
        #   只能靠 user 消息里的「现在切换到 x、模式名」提醒当前模式（且会被服务端记住，只发变化时）。
        # - 官方 API / 千问（MessagesBackend）：无状态、system 每轮重发，当前模式已写进
        #   system（见 config.get_api_prompt / MessagesBackend.build），user 消息里不再重复声明，
        #   避免污染 history 被反复重放。
        if not config.is_messages_backend(self.opts.get("backend")):
            name = next((m["name"] for m in config.AI_MODES
                         if m["id"] == mode_id), "")
            if name:
                # 显式声明当前模式（含日常模式），避免多轮记忆中的历史模式指令导致漂移
                prompt = f"现在切换到 {mode_id}、{name}。\n" + prompt
        if self.images:
            prompt = prompt + "\n" + config.IMAGE_PROMPT_HINT
        # P9 原生语音：**音频无法事后剥离**（模型是"文本+语音"同源生成的），
        # 所以约束只能放在生成之前 —— 见 config.NATIVE_VOICE_PROMPT。
        # 这也顺便让 60 字上限生效：语音场景长回复本来就听不完。
        if self.native_audio:
            prompt = config.NATIVE_VOICE_PROMPT + "\n" + prompt
        # 女仆上下文注入：回执 + 实时状态（仅当女仆闭环可用时）
        if self.maid_loop is not None:
            try:
                ctx_lines = self._maid_context_lines()
                if ctx_lines:
                    prompt = "\n".join(ctx_lines) + "\n" + prompt
            except Exception:
                pass
        # 本地预处理（NLU）：识别出「指挥女仆干活」的意图时，可能已直接执行，
        # 这里把「原话 + 是否已执行 + 配方/背包明细」前置给大 AI，让它据此回应。
        if self.preprocess is not None:
            try:
                extra = self.preprocess(self.prompt)
                if isinstance(extra, tuple):
                    extra, executed = extra
                    self._nlu_executed = bool(executed)
                else:
                    self._nlu_executed = False
            except Exception:
                extra = ""
                self._nlu_executed = False
            if extra:
                prompt = extra + "\n\n" + prompt
                trace("NLU", "已本地执行=%s | %s" % (self._nlu_executed, extra))
        # 主动知识问答（knowledge-qa 扩展点）：用户提问时向插件要一段检索上下文。
        # 未装插件 → qa_context 返回 ""，下面整块不生效（零行为变化）。
        if config.KNOWLEDGE_QA_ENABLED:
            try:
                from plugin import host as _plugin_host
                ctx = _plugin_host.qa_context(
                    self.prompt, max_chars=config.KNOWLEDGE_QA_MAX_CHARS)
            except Exception:
                ctx = ""
            if ctx:
                prompt = ("[知识] 以下为检索到的参考资料，供回答本轮提问时参考"
                          "（与提问无关就忽略）：\n" + ctx + "\n\n" + prompt)
                trace("KNOWLEDGE_QA", ctx)
        return prompt

    def _maid_context_lines(self):
        """女仆闭环上下文行：最近回执 [系统] + 女仆实时状态 [态]。"""
        lines = []
        try:
            for rp in self.maid_loop.drain_replies():
                lines.append(rp)
        except Exception:
            pass
        try:
            state = self.maid_loop.status_line()
            if state:
                lines.append("[态] " + state)
        except Exception:
            pass
        return lines

    def _upload_images(self):
        """上传待发送的图片，返回 file_id 列表。

        全部失败时发出错误信号并返回 None；部分失败则跳过并在状态里提示。
        """
        if not self.images:
            return []
        ids, failed = [], 0
        first_err = None
        for p in self.images:
            try:
                ids.append(self.client.upload_image_file(p))
            except Exception as e:
                failed += 1
                if first_err is None:
                    first_err = e
        if not ids:
            self.finished.emit(f"图片上传失败：{first_err}", True)
            return None
        if failed:
            self.status_hint = f"（{failed} 张图片上传失败，已跳过）"
        return ids

    @traced("chat_window")
    def run(self):
        if self.is_stream:
            self._run_stream()
        else:
            self._run_plain()

    def _perf_log(self, chars, t0):
        try:
            from core.perf_log import log
            log("AI", "聊天", chars, ai_ms=(time.time() - t0) * 1000)
        except Exception:
            pass

    def _apply_dsl(self, content):
        """从 AI 回复提取【DSL 指令】并下发给女仆，返回 (clean, 注入文本)。

        只在有 MaidLoop（女仆已连接）时生效；否则原样返回。
        千问流式后端不走这里（见 _run_stream）。

        **NLU 已本地执行时抑制 AI 重复/冲突指令**：例如「穿金胸甲」NLU 已
        执行 transfer 穿上，AI 若再回【equip】会把胸甲拿回主手、撤销穿甲效果——
        此时只剥离 DSL 文本（不显示【...】），但不下发。
        """
        if not self.maid_loop:
            return content, ""
        if self._nlu_executed:
            try:
                from game.maid_intent import parse as _parse
                _cmd, clean = _parse(content)   # parse 返回 (cmd, clean)
                trace("DSL", "NLU 已本地执行 → 剥离并抑制 AI 指令%s"
                      % ("（原本有指令）" if _cmd else "（本轮无指令）"))
                return clean, ""
            except Exception:
                return content, ""
        try:
            return self.maid_loop.process_reply(content)
        except Exception:
            return content, ""

    def _run_plain(self):
        t0 = time.time()
        from game.agent_loop import AgentLoop
        loop = AgentLoop()
        try:
            prompt = self._prepare_prompt()
            if self.preprocess is not None:
                loop.to_nlu()          # NLU 前置已执行
            ref_file_ids = self._upload_images()
            if ref_file_ids is None:
                return
            # 思考参数的形态按后端分流：网页版要 bool（thinking_enabled），
            # API 后端要**档位字符串** —— 否则 effort 档会被压成一个"开"
            # （reasoning_effort 只会拿到 True → 恒映射成 high）。
            thinking_arg = self.opts["thinking"]
            if config.is_messages_backend(self.opts.get("backend")):
                thinking_arg = (self.opts.get("thinking_variant")
                                or self.opts["thinking"])
            loop.to_generate()          # 主 AI 生成阶段
            # P1-1 双通道：支持原生 tools 的非流式后端（官方 API / Anthropic…）
            # 直接拿结构化 tool_calls，不再靠【DSL】文本解析；
            # 不支持（网页版 / 流式 qwen）→ 走既有 DSL 通道（_apply_dsl）。
            if self._native_tools_enabled():
                content, inject = self._run_plain_native(
                    prompt, thinking_arg)
            else:
                content, _ = self.client.chat(
                    prompt,
                    system_prompt=self._system_prompt(),
                    model_type=self.opts["model"],
                    thinking_enabled=thinking_arg,
                    search_enabled=self.opts["search"],
                    memory=self.opts["memory"],
                    ref_file_ids=ref_file_ids,
                )
                content, inject = self._apply_dsl(content)
            if inject:
                loop.to_dispatch()       # 有回执 → 工具下发阶段
            loop.to_done()
            self._perf_log(len(content), t0)
            self.dsl_inject = inject
            trace("AI", content)
            trace("DSL", inject.strip() if inject.strip() else "（无回执注入）")
            self.finished.emit(content, False)
        except prov.ProviderError as e:
            self.finished.emit(f"{e}", True)
        except DeepSeekApiError as e:
            self.finished.emit(f"{e}", True)
        except Exception as e:
            self.finished.emit(f"出错：{e}", True)

    def _native_tools_enabled(self):
        """本轮是否走原生 tool_calls 通道（P1-1 双通道）。

        需要同时满足：女仆在线 + 当前后端/模型声明 CAP_TOOLS + 非流式
        （原生 tool_calls 走非流式 `chat_message`；流式 qwen 保持 DSL 兜底）。
        """
        if not self.maid_loop:
            return False
        try:
            pid = self.opts.get("backend")
            mid = str(self.opts.get("provider_model") or "")
            return prov.supports(pid, mid, "tools") and not self.is_stream
        except Exception:
            return False

    def _run_plain_native(self, prompt, thinking_arg):
        """原生 tool_calls 通道：模型返回结构化工具调用 → 校验 → 下发女仆。

        端到端实测（2026-09-22）：模型**可能不产 tool_calls**，而是按人设习惯
        输出文本 DSL（`{兴奋}【attack(...)】`）。此时必须**回退 `_apply_dsl`**
        把文本 DSL 剥离 + 下发，否则 `【attack(...)】` 会原样显示进正文。

        :return: ``(content, inject)`` —— content 为正文，inject 为回执注入文本。
        """
        from nlu import mod_contract as _mc
        messages = []
        sysp = self._system_prompt()
        if sysp:
            messages.append({"role": "system", "content": sysp})
        messages.append({"role": "user", "content": prompt})
        msg = self.client.chat_message(
            messages, thinking=thinking_arg, tools=_mc.to_openai_tools())
        content = (msg.get("content") or "").strip()
        calls = msg.get("tool_calls") or []
        if calls:
            inject = self._apply_native_tools(calls)
        else:
            # 模型未走 tools（倾向文本 DSL）→ DSL 文本通道兜底剥离 + 下发
            content, inject = self._apply_dsl(content)
        return content, inject

    def _apply_native_tools(self, tool_calls):
        """原生 tool_calls → 白名单校验 + clamp → 女仆下发，返回回执注入文本。

        **NLU 已本地执行时抑制**：与 DSL 通道的 `_apply_dsl` 一致——
        若本轮 NLU 已直接执行（如「穿金胸甲」已 transfer），AI 再发工具会
        撤销执行结果，此时只剥离不执行。
        """
        if not tool_calls:
            return ""
        if self._nlu_executed:
            return ""
        from nlu import mod_contract as _mc
        inject_lines = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            if name not in _mc.COMMANDS or \
                    _mc.COMMANDS[name].layer == "meta-dryrun":
                continue
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(args, dict):
                continue
            if _mc.missing_required(name, args):
                continue
            params, _dropped = _mc.clamp(name, args)
            cancel = bool(str(params.pop("cancel_previous", False)).lower()
                          in ("true", "1", "yes"))
            cmd = {"cmd": name, "params": params,
                   "cancel_previous": cancel}
            inj = self.maid_loop.execute(cmd)
            if inj:
                inject_lines.append(inj)
        return "\n".join(inject_lines)

    def _system_prompt(self):
        """按后端给人格 system：
        - 官方 API / 千问：精简/极简人设 + 当前模式（每轮重发，模式写在 system 里）；
        - 网页版：完整/极简人设（只在会话首条注入一次，模式声明走 user 消息）。"""
        if config.is_messages_backend(self.opts.get("backend")):
            mode_id = int(self.opts.get("ai_mode", 1) or 1)
            return config.get_api_prompt(self.opts.get("persona"),
                                         mode_id,
                                         self.opts.get("pet_name") or "",
                                         style=self.opts.get("prompt_style"))
        return self.opts["prompt"]

    def _run_stream(self):
        """流式：增量文本走 delta 信号，完成后 finished("", False)。

        收尾时在**工作线程**里做一次全量解析（剥离【指令】并下发女仆 + 取动作标签），
        不放到主线程 —— `_apply_dsl` 会往女仆链路发指令，在 UI 线程做会卡界面。

        **P9 原生语音**：所选模型声明了出音频且用户开了 native 模式时改走
        `stream_events`，把 `audio` 事件直接喂给流式播放管线（边收边播，
        见 voice/stream_audio.py）；文本照旧走 delta → 显示。
        **此时不再调外部 TTS**（抑制在 UI 侧由 `_stream_speech=False` 兜，二者一致）。
        """
        t0 = time.time()
        buf = []
        self.clean_text = ""
        native = self.native_audio
        player = None
        from game.agent_loop import AgentLoop
        loop = AgentLoop()
        try:
            prompt = self._prepare_prompt()
            if self.preprocess is not None:
                loop.to_nlu()          # NLU 前置已执行
            loop.to_generate()         # 主 AI 生成阶段
            sysp = self._system_prompt()
            if native:
                # 组装 messages（形态与 stream_prompt 相同：system + 当轮 user）
                messages = []
                if sysp:
                    messages.append({"role": "system", "content": sysp})
                messages.append({"role": "user", "content": prompt})
                from voice.stream_audio import player as _native_player
                player = _native_player()
                player.begin()
                try:
                    for ev in self.client.stream_events(
                            messages, audio_voice=self._resolve_native_voice()):
                        if ev.kind == KIND_TEXT and ev.text:
                            buf.append(ev.text)
                            self.delta.emit(ev.text)
                        elif ev.kind == KIND_AUDIO and ev.audio:
                            player.feed(ev.audio)
                            self.native_used = True
                except Exception as e:
                    # 原生语音失败（典型：音色被服务端拒）。**已吐出东西**就整体失败，
                    # 否则退化为纯文本流式再来一次 —— 宁可用户多等一次，也不能没声。
                    if buf:
                        raise
                    self.native_fallback = "%s" % e
                    native = False
                    player.stop()
                    player = None
                    trace("AI", "原生语音退化：%s" % e)
            if not native:
                for piece in self.client.chat_stream(
                        prompt,
                        system_prompt=sysp,
                        # 不传 model：由适配器用自己已配置的模型（去品牌化，见 §10.1）
                        model=None):
                    if piece:
                        buf.append(piece)
                        self.delta.emit(piece)
            raw = "".join(buf)
            self._perf_log(len(raw), t0)
            trace("AI", raw)
            # 【P3 修复】流式原先完全不接 DSL（旧 trace 明写"已知缺口"）。
            # 这里复用非流式那条已被测试覆盖的路径：剥离【指令】并下发。
            content, inject = self._apply_dsl(raw)
            if inject:
                loop.to_dispatch()       # 有回执 → 工具下发阶段
            loop.to_done()
            self.dsl_inject = inject
            self.clean_text = content
            trace("DSL", inject.strip() if inject.strip() else "（流式：无回执注入）")
            if player is not None:
                player.end()
                # 原生语音路径绕过了 chat_stream 的收尾，多轮记忆要在这里补记
                try:
                    self.client.remember(prompt, content)
                except Exception:
                    pass
            self.finished.emit("", False)
        except QwenError as e:
            if player is not None:
                player.stop()
            self.finished.emit(f"{e}", True)
        except Exception as e:
            if player is not None:
                player.stop()
            self.finished.emit(f"出错：{e}", True)

    def _resolve_native_voice(self):
        """决定本次原生语音的音色：用户选的（须在该模型清单里）→ 模型默认。"""
        try:
            import ai.qwen as qwen_mod
            pid = self.opts.get("backend")
            mid = str(self.opts.get("provider_model") or "")
            return qwen_mod.audio_voice_for(pid, mid, self.audio_voice)
        except Exception:
            return ""


def _load_ai_opts():
    return load_ai_opts()


# ---------- AI 动作标签解析（见 ai_client.parse_ai_output） ----------


class ChatWindow(QDialog):
    """聊天输入面板：无边框圆角卡片，含内嵌设置页。
    宠物的回复以气泡形式从宠物身上冒出（见 PetWindow._show_ai_bubble）。"""

    def __init__(self, parent=None, preprocessor=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(520, 700)

        # NLU 前处理（由桌宠注入；None = 关闭）。在工作线程里调用，会阻塞等回执。
        self._preprocess = preprocessor
        self.opts = _load_ai_opts()
        self._last_persona = self.opts.get("persona") or config.DEFAULT_PERSONA
        self._persona_switch = None
        # 语音输入（STT）设置：与桌宠 pet 共享同一份 QSettings
        self._stt_enabled = str(_STORE.value("stt_enabled", False)) != "false"
        self._stt_engine_name = str(
            _STORE.value("stt_engine", config.STT_DEFAULT_ENGINE) or "")
        self._stt_whisper_model = str(
            _STORE.value("stt_whisper_model", config.STT_WHISPER_MODEL)
            or config.STT_WHISPER_MODEL)
        self._stt_silence_ms = int(
            _STORE.value("stt_silence", config.STT_SILENCE_MS)
            or config.STT_SILENCE_MS)
        self._stt_auto_send = str(
            _STORE.value("stt_auto_send", True)) != "false"
        self.client = make_client()
        self.pet = parent
        self._maid_loop = self._make_maid_loop()
        self._maid_inject = ""        # 女仆 DSL 回执注入（下一轮 prompt 携带）
        self.voice = VoiceModule.shared()
        self.voice.speaking.connect(self._on_speaking)
        self.stream_voice = StreamVoice(self)
        # 流式回复状态
        self._stream_full = ""          # 原始增量（收尾时走既有 _apply_dsl 全量解析）
        self._stream_speech = False
        self._stream_speech_buf = ""    # 已剥离、但还没凑成完整句的尾巴
        self._stream_safe = ""          # 已剥离、可直接显示的文本
        # 增量剥离器：显示层与语音层**共用同一个实例**（一轮一个，见 §11.2）
        self._stripper = TagStripper()
        self._stream_tags = []
        self._ai_bubble = None
        # 待发送的图片路径（V4.1 识图：随下一条消息一起上传并发给 AI）
        self._pending_images = []

        self._shell = QFrame(self)
        self._shell.setObjectName("chatShell")

        # ---------- 标题栏（整行可拖动） ----------
        self.drag_bar = QWidget(self._shell)
        self.drag_bar.setObjectName("dragBar")
        title_icon = QLabel()
        title_icon.setPixmap(icon("message_circle", 16, "#1677FF").pixmap(16, 16))
        title_icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = QLabel("和宠物聊天")
        self.title.setObjectName("chatTitle")
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents)
        title_wrap = QWidget()
        title_box = QHBoxLayout(title_wrap)
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(7)
        title_box.addWidget(title_icon)
        title_box.addWidget(self.title)
        new_btn = QPushButton()
        new_btn.setObjectName("iconBtn")
        new_btn.setIcon(icon("refresh", 16, "#595959"))
        new_btn.setIconSize(QSize(16, 16))
        new_btn.setToolTip("开启新会话")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._new_thread)
        close_btn = QPushButton()
        close_btn.setObjectName("iconBtn")
        close_btn.setIcon(icon("x", 15, "#595959"))
        close_btn.setIconSize(QSize(15, 15))
        close_btn.setToolTip("关闭")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        top = QHBoxLayout()
        self.drag_bar.setLayout(top)
        top.setContentsMargins(18, 10, 10, 4)
        top.addWidget(title_wrap)
        top.addStretch(1)
        top.addWidget(new_btn)
        top.addWidget(close_btn)

        # ---------- 聊天页 ----------
        chat_page = QWidget()
        self.msg_area = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_area)
        self.msg_layout.setAlignment(Qt.AlignTop)
        self.msg_layout.setContentsMargins(14, 8, 14, 8)
        self.msg_layout.setSpacing(10)
        self.msg_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.msg_area)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("chatScroll")
        scroll.viewport().setStyleSheet("background: #FFFFFF;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.status = QLabel("")
        self.status.setObjectName("chatStatus")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)

        # 待发送图片预览条（无图片时隐藏）
        self.attach_bar = QWidget()
        self.attach_layout = QHBoxLayout(self.attach_bar)
        self.attach_layout.setContentsMargins(14, 0, 14, 4)
        self.attach_layout.setSpacing(8)
        self.attach_layout.addStretch(1)
        self.attach_bar.hide()

        self.input_edit = QTextEdit()
        self.input_edit.setObjectName("chatInput")
        self.input_edit.setFixedHeight(48)
        self.input_edit.setPlaceholderText("想对我说点什么…（可点左侧回形针发图片）")
        self.attach_btn = QPushButton()
        self.attach_btn.setObjectName("iconBtn")
        self.attach_btn.setFixedSize(34, 34)
        self.attach_btn.setIcon(icon("paperclip", 17, "#595959"))
        self.attach_btn.setIconSize(QSize(17, 17))
        self.attach_btn.setToolTip("发送图片（让宠物看图）")
        self.attach_btn.setCursor(Qt.PointingHandCursor)
        self.attach_btn.clicked.connect(self._pick_images)
        self.send_btn = QPushButton()
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(42, 42)
        self.send_btn.setIcon(icon("send", 18, "#FFFFFF"))
        self.send_btn.setIconSize(QSize(18, 18))
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._send)
        input_row = QHBoxLayout()
        input_row.setContentsMargins(14, 2, 14, 14)
        input_row.setSpacing(10)
        input_row.addWidget(self.attach_btn, 0, Qt.AlignBottom)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(self.send_btn, 0, Qt.AlignBottom)
        cl = QVBoxLayout()
        chat_page.setLayout(cl)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(scroll, 1)
        cl.addWidget(self.status)
        cl.addWidget(self.attach_bar)
        cl.addLayout(input_row)

        # ---------- 聊天页（设置改为统一 SettingsDialog，见 _show_settings） ----------
        self.stack = QStackedWidget(self._shell)
        self.stack.addWidget(chat_page)

        inner = QVBoxLayout()
        self._shell.setLayout(inner)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        inner.addWidget(self.drag_bar)
        inner.addWidget(self.stack, 1)

        outer = QVBoxLayout()
        self.setLayout(outer)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self._shell)

        self._busy = False
        self._workers = []
        self.drag_bar.installEventFilter(self)
        self.input_edit.installEventFilter(self)
        self.setStyleSheet("""
            QFrame#chatShell {
                background: #FFFFFF;
                border-radius: 8px;
                border: 1px solid #F0F0F0;
            }
            QWidget#dragBar { background: transparent; }
            QLabel#chatTitle {
                color: rgba(0,0,0,0.88); font-size: 15px; font-weight: 600;
                padding: 2px 0;
            }
            QPushButton#iconBtn {
                background: transparent; color: rgba(0,0,0,0.65);
                border: none; border-radius: 6px;
                padding: 4px;
            }
            QPushButton#iconBtn:hover { background: rgba(0,0,0,0.06); }
            QPushButton#iconBtn:pressed { background: rgba(0,0,0,0.1); }
            QLabel#chatStatus { color: rgba(0,0,0,0.45); font-size: 12px; padding: 2px; }
            QTextEdit#chatInput {
                background: #FFFFFF; border: 1px solid #D9D9D9;
                border-radius: 8px; padding: 9px 14px;
                font-size: 14px; color: rgba(0,0,0,0.88);
            }
            QTextEdit#chatInput:hover { border: 1px solid #4096FF; }
            QTextEdit#chatInput:focus { border: 1px solid #1677FF; }
            QPushButton#sendBtn {
                background: #1677FF;
                color: white; border: none; border-radius: 8px;
                font-size: 18px;
            }
            QPushButton#sendBtn:hover { background: #4096FF; }
            QPushButton#sendBtn:pressed { background: #0958D9; }
            QPushButton#sendBtn:disabled { background: rgba(0,0,0,0.04); color: rgba(0,0,0,0.25); }
            QScrollArea#chatScroll { background: #FFFFFF; border: none; }
            QScrollArea#chatScroll > QWidget#qt_scrollarea_viewport { background: #FFFFFF; }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 4px; }
            QScrollBar::handle:vertical {
                background: rgba(0,0,0,0.15); border-radius: 4px;
            }
            QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: none; }
            QLabel#settingsTitle { color: rgba(0,0,0,0.88); font-size:15px; font-weight:600; }
            QComboBox {
                background:#FFFFFF; border:1px solid #D9D9D9; border-radius:6px;
                padding:0 12px; min-height:30px; font-size:14px; color:rgba(0,0,0,0.88);
            }
            QComboBox::drop-down { border:none; width:24px; }
            QCheckBox { color:rgba(0,0,0,0.88); font-size:14px; spacing:8px; }
            QTextEdit#promptEdit {
                background:#FFFFFF; border:1px solid #D9D9D9; border-radius:6px;
                padding:8px; font-size:12px; color:rgba(0,0,0,0.88);
            }
            QTextEdit#promptEdit:focus { border:1px solid #1677FF; background:#fff; }
            QLabel#settingsHint { color:rgba(0,0,0,0.45); font-size:12px; }
            QPushButton#settingsBack {
                background:rgba(0,0,0,0.06); color:rgba(0,0,0,0.88); border:none;
                border-radius:6px; padding:7px 20px; font-size:13px;
            }
            QPushButton#settingsBack:hover { background:rgba(0,0,0,0.1); }
            QPushButton#settingsSave {
                background: #1677FF;
                color:white; border:none; border-radius:6px;
                padding:7px 24px; font-size:13px;
            }
            QPushButton#settingsSave:hover { background: #4096FF; }
            QPushButton#settingsSave:pressed { background: #0958D9; }
        """)

    def apply_chat_settings(self, opts):
        """应用 AI 对话相关设置：写 QSettings + 同步运行时状态（后端客户端/流式语音等）。"""
        self.opts = opts
        save_ai_opts(opts)
        # 语音输入（STT）设置保存 + 同步到桌宠 pet（热键/引擎即时生效）
        _STORE.setValue("stt_enabled", opts["stt_enabled"])
        _STORE.setValue("stt_engine", opts["stt_engine"])
        _STORE.setValue("stt_whisper_model", opts["stt_whisper_model"])
        _STORE.setValue("stt_silence", opts["stt_silence"])
        _STORE.setValue("stt_auto_send", opts["stt_auto_send"])
        if self.pet is not None:
            # STT 运行时状态迁移到 pet._stt_handler（重构后窗口不再持有 _stt_* 属性）
            h = getattr(self.pet, "_stt_handler", None)
            if h is not None:
                h._stt_enabled = opts["stt_enabled"]
                h._stt_engine_name = opts["stt_engine"]
                h._stt_silence_ms = opts["stt_silence"]
                h._stt_auto_send = opts["stt_auto_send"]
                h._stt_engine = None   # 强制按新设置重建识别引擎
        # 人格变化 → 标记下一条消息开头告知 AI 切换人格（不清历史会话）
        persona = opts.get("persona") or config.DEFAULT_PERSONA
        if persona != self._last_persona:
            self._persona_switch = persona
        self._last_persona = persona
        # 后端可能变化：重建客户端与流式语音引擎
        self.client = make_client()
        self.stream_voice.end()
        self.status.setText("设置已保存")

    # ---------- 交互 ----------

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress \
                and isinstance(event, QKeyEvent) \
                and event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not (event.modifiers() & Qt.ShiftModifier):
            self._send()
            return True
        if obj is self.drag_bar and event.type() == QEvent.Type.MouseButtonPress \
                and isinstance(event, QMouseEvent) and event.button() == Qt.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
            return True
        if obj is self.drag_bar and event.type() == QEvent.Type.MouseMove \
                and isinstance(event, QMouseEvent) \
                and (event.buttons() & Qt.LeftButton) \
                and hasattr(self, "_drag_pos"):
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return True
        if obj is self.drag_bar and event.type() == QEvent.Type.MouseButtonRelease:
            if hasattr(self, "_drag_pos"):
                del self._drag_pos
            return True
        return super().eventFilter(obj, event)

    def _add_user_bubble(self, text, image_count=0):
        if image_count:
            text = f"[图片×{image_count}] " + text
        row = QHBoxLayout()
        row.addStretch(1)
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(int(self.width() * 0.7))
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setStyleSheet("""
            background: #1677FF;
            color: #ffffff; border-radius: 8px;
            border-bottom-right-radius: 2px;
            padding: 8px 12px; font-size: 14px;
        """)
        row.addWidget(bubble)
        wrap = QWidget()
        wrap.setLayout(row)
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, wrap)

    def _add_ai_bubble(self, text):
        """把 AI 回复追加为聊天历史气泡（语音输入同步用，左对齐）。"""
        row = QHBoxLayout()
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(int(self.width() * 0.72))
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setStyleSheet("""
            background: #FFFFFF; color: rgba(0,0,0,0.88); border-radius: 8px;
            border: 1px solid #F0F0F0;
            border-bottom-left-radius: 2px; padding: 8px 12px;
            font-size: 14px;
        """)
        row.addWidget(bubble)
        row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(row)
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, wrap)

    def append_chat(self, user_text, ai_text):
        """语音输入对话：把用户语音文本与 AI 回复同步进聊天历史（主线程调用）。"""
        if user_text:
            self._add_user_bubble(user_text)
        if ai_text:
            self._add_ai_bubble(ai_text)

    def _clear_messages(self):
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---------- 图片（V4.1 识图） ----------

    def _pick_images(self):
        """选择一个或多个图片文件（当前只做「选择文件」入口）。"""
        if self._busy:
            return
        exts = " ".join(f"*{e}" for e in config.IMAGE_EXTENSIONS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "", f"图片文件 ({exts})")
        if not paths:
            return
        room = config.IMAGE_MAX_FILES - len(self._pending_images)
        if room <= 0:
            self.status.setText(f"最多同时发送 {config.IMAGE_MAX_FILES} 张图片")
            return
        self._pending_images.extend(paths[:room])
        if len(paths) > room:
            self.status.setText(
                f"最多同时发送 {config.IMAGE_MAX_FILES} 张，已忽略多余图片")
        else:
            self.status.setText("")
        self._rebuild_attach_bar()

    def _remove_pending(self, path):
        try:
            self._pending_images.remove(path)
        except ValueError:
            pass
        self._rebuild_attach_bar()

    def _clear_pending_images(self):
        self._pending_images = []
        self._rebuild_attach_bar()

    def _rebuild_attach_bar(self):
        while self.attach_layout.count() > 1:
            item = self.attach_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for path in self._pending_images:
            self.attach_layout.insertWidget(self.attach_layout.count() - 1,
                                            self._make_thumb(path))
        self.attach_bar.setVisible(bool(self._pending_images))

    def _make_thumb(self, path):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        thumb = QLabel()
        thumb.setFixedSize(56, 56)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet(
            "background:#FFFFFF; border:1px solid #D9D9D9; border-radius:8px;")
        pix = QPixmap(path)
        if pix.isNull():
            thumb.setText("?")
        else:
            thumb.setPixmap(pix.scaled(56, 56, Qt.KeepAspectRatio,
                                       Qt.SmoothTransformation))
        thumb.setToolTip(os.path.basename(path))
        rm = QPushButton("×")
        rm.setFixedSize(18, 18)
        rm.setCursor(Qt.PointingHandCursor)
        rm.setToolTip("移除这张图片")
        rm.setStyleSheet(
            "QPushButton{background:rgba(0,0,0,0.06);color:rgba(0,0,0,0.65);border:none;"
            "border-radius:9px;font-size:12px;}"
            "QPushButton:hover{background:rgba(0,0,0,0.1);}")
        rm.clicked.connect(lambda _=False, p=path: self._remove_pending(p))
        lay.addWidget(thumb, 0, Qt.AlignHCenter)
        lay.addWidget(rm, 0, Qt.AlignHCenter)
        return wrap

    def _make_maid_loop(self):
        """从桌宠拿 maid_link 创建女仆 DSL 闭环；不可用时返回 None。"""
        try:
            pet = self.pet
            handler = getattr(pet, "_maid_handler", None)
            link = getattr(handler, "_link", None)
            if link is None:
                return None
            from game.maid_loop import MaidLoop
            return MaidLoop(link)
        except Exception:
            return None

    # ---------- 程序化入口（自动化 / 调试：等价于在输入框打字并回车） ----------

    def busy(self):
        """上一条是否仍在处理中。"""
        return bool(self._busy)

    def submit_text(self, text):
        """把一段文本作为用户消息提交（与手动输入后按回车**完全等价**）。

        与 ``_send()`` 走同一条链路：预处理器（本地 NLU）→ AI → DSL → 女仆。
        供 ``.chat_input.json`` 注入口与全链路自动化测试使用。

        :return: True=已提交；False=空文本或当前正忙（不排队，调用方可稍后重试）
        """
        text = (text or "").strip()
        if not text or self._busy:
            return False
        self.input_edit.setPlainText(text)
        self._send()
        return True

    def _send(self):
        if self._busy:
            return
        text = self.input_edit.toPlainText().strip()
        images = list(self._pending_images)
        if not text and not images:
            return
        # 不支持传图的后端（官方 API / 千问…）：丢弃图片并提示，不打断文本发送。
        # 判据走注册表能力位，不用 hasattr —— 加一个基类方法会让这个判断静默失效。
        if images and not prov.cap(ai_client.current_provider(),
                                   prov.CAP_ATTACHMENTS):
            self.status.setText("当前后端暂不支持图片，本次已忽略图片")
            images = []
            if not text:
                return
        # 仅发了图片没写文字：给一个默认提问
        if not text:
            text = "看看这张图片吧"
        # 刚切换人格时，在第一条消息里告知 AI 切换人格（历史会话不重置），并附上新人格完整
        # 提示词。仅网页版需要此写法：DeepSeek 多轮会话的 system_prompt 只在首轮注入，之后
        # 只能靠 user 消息补设定。官方 API / 千问每轮重发 system（见 _system_prompt），
        # 人设已随 system 刷新，塞进 user 消息只会污染 history。
        if getattr(self, "_persona_switch", None):
            if not config.is_messages_backend(self.opts.get("backend")):
                info = config.persona_info(self._persona_switch)
                full_prompt = config.load_system_prompt(
                    self._persona_switch, self.opts.get("pet_name") or "",
                    style=self.opts.get("prompt_style"))
                text = (f"现在切换人格到「{info['name']}」，请以新人格身份继续对话，"
                        f"忽略之前的人格设定。以下是你现在的人设完整设定，请严格遵守：\n"
                        f"{full_prompt}\n\n" + text)
            self._persona_switch = None
        self.input_edit.clear()
        self._add_user_bubble(text, image_count=len(images))
        self._clear_pending_images()
        if images:
            self.status.setText(f"正在上传 {len(images)} 张图片…")
        else:
            self.status.setText("宠物正在思考…")
        self._set_busy(True)

        # 流式回复状态（仅千问后端触发流式）
        self._stream_full = ""
        self._stream_speech_buf = ""
        self._stream_safe = ""
        self._stream_tags = []
        self._stripper = TagStripper()
        self._clear_ai_bubble()
        # P9 原生语音时**不调外部 TTS**：模型自己出的音频已在工作线程里
        # 直接进播放管线；这里再逐句合成会变成"两个人同时说话"。
        self._stream_speech = (prov.chat_streams(self.opts.get("backend"))
                               and self.opts.get("voice_mode", "off") != "off"
                               and self.opts.get("voice_mode") != "native")
        if self._stream_speech:
            self.stream_voice.begin(self.opts["voice_mode"],
                                    self.opts.get("cosy_voice"))

        worker = _ChatWorker(self.client, text, self.opts, images,
                             preprocess=self._preprocess,
                             maid_loop=self._maid_loop)
        worker.finished.connect(self._on_reply)
        worker.delta.connect(self._on_delta)

        def _cleanup(*_a):
            if worker in self._workers:
                self._workers.remove(worker)
            hint = getattr(worker, "status_hint", None)
            if hint:
                self.status.setText(hint)

        worker.finished.connect(_cleanup)
        self._workers.append(worker)
        threading.Thread(target=worker.run, daemon=True).start()

    def _on_delta(self, delta):
        """流式增量：**一个剥离器、两个下游**（显示 + 语音），见 §11.2。

        顺序很关键：**剥离必须发生在句切分之前**。原实现是"先切句、再逐句剥离"，
        而切句正则把 `~` 当句末终止符，偏偏 DSL 的相对坐标就用 `~`
        → `【move(pos=~,~,~)】` 被切成 4 段、每段缺 `】` 剥离失败 → 指令被逐段念出来。
        """
        self._stream_full += delta      # 原文留底（收尾统一解析 DSL / 动作标签）
        safe, tags = self._stripper.feed(delta)
        if tags:
            self._stream_tags.extend(tags)
        if not safe:
            return
        self._stream_safe += safe
        self._update_ai_bubble(self._stream_safe)
        if self._stream_speech:
            self._maybe_stream_speak(safe)

    def _update_ai_bubble(self, text):
        """聊天窗口内显示 AI 流式回复的气泡（实时更新）。"""
        if self._ai_bubble is None:
            row = QHBoxLayout()
            self._ai_bubble = QLabel(text)
            self._ai_bubble.setWordWrap(True)
            self._ai_bubble.setMaximumWidth(int(self.width() * 0.72))
            self._ai_bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._ai_bubble.setStyleSheet("""
                background: #FFFFFF; color: rgba(0,0,0,0.88); border-radius: 8px;
                border: 1px solid #F0F0F0;
                border-bottom-left-radius: 2px; padding: 8px 12px;
                font-size: 14px;
            """)
            row.addWidget(self._ai_bubble)
            row.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(row)
            self._ai_bubble_wrap = wrap
            self.msg_layout.insertWidget(self.msg_layout.count() - 1, wrap)
        else:
            self._ai_bubble.setText(text)
            self._ai_bubble.adjustSize()

    def _clear_ai_bubble(self):
        if getattr(self, "_ai_bubble_wrap", None) is not None:
            self._ai_bubble_wrap.deleteLater()
            self._ai_bubble_wrap = None
        self._ai_bubble = None

    def _maybe_stream_speak(self, safe_text):
        """把**已剥离**的文本切句，完整句立即入队朗读；尾部留到收尾时读。

        :param safe_text: 来自 `TagStripper` 的安全文本（**不含未闭合标签**），
            所以这里**不需要**再剥离一次，也不该在切句之后再剥（那正是原缺陷）。
        """
        if not safe_text:
            return
        self._stream_speech_buf += safe_text
        complete, tail = split_speakable(self._stream_speech_buf)
        for s in complete:
            self.stream_voice.enqueue(s)
        self._stream_speech_buf = tail

    def _on_speaking(self, on):
        if on:
            self.status.setText("宠物正在说话…")
        elif self.status.text() == "宠物正在说话…":
            self.status.setText("")

    def _apply_ai_actions(self, actions):
        """根据 AI 输出的动作标签驱动宠物执行对应动作（只取第一个有效标签）。"""
        pet = self.pet
        if pet is None:
            return
        for tag in actions:
            act = config.AI_ACTION_TAGS.get(tag)
            if not act:
                continue
            ak = ActionKey.from_value(act)
            if ak is ActionKey.SEQUENCE:
                pet._play_sequence()
            elif ak is ActionKey.IDLE:
                pet._go_idle()
            elif ak is ActionKey.ANGRY:
                pet._request_state(pet.STATE_ANGRY)
            elif ak is ActionKey.BACK:
                pet._request_state(pet.STATE_BACK)
            elif ak is ActionKey.SIT:
                pet._request_state(pet.STATE_IDLE)
            elif ak in (ActionKey.THINK, ActionKey.HAPPY, ActionKey.CRY,
                        ActionKey.SHY, ActionKey.SURPRISED, ActionKey.SLEEP,
                        ActionKey.JUMP, ActionKey.ROLL):
                pet._play_oneshot(act)
            else:  # rise / front
                pet._request_state(pet.STATE_FRONT)
            break

    def _on_reply(self, text, is_error):
        self._set_busy(False)
        if is_error:
            if self.pet is not None and hasattr(self.pet, "_show_ai_bubble"):
                self.pet._show_ai_bubble(text, error=True)
            self.status.setText("回复失败，请查看宠物气泡")
            self.stream_voice.end()
            return
        is_stream = prov.chat_streams(self.opts.get("backend"))
        # 女仆 DSL 回执注入（本 worker 下发的指令结果，供下一轮 prompt 携带）
        _worker = self.sender()
        self._maid_inject = (getattr(_worker, "dsl_inject", "")
                             if _worker is not None else "")
        status_hint = ""          # 非空则覆盖收尾时清空状态栏的动作（P9 静音兜底用）
        if is_stream:
            # 收尾：放行剥离器里被 holdback 压住的残余，并补读最后一段语音
            safe_tail, tags = self._stripper.flush()
            if tags:
                self._stream_tags.extend(tags)
            if self._stream_speech:
                rest = (self._stream_speech_buf + safe_tail).strip()
                if rest:
                    self.stream_voice.enqueue(rest)
            self._stream_speech_buf = ""
            # 正文用工作线程里已经全量解析过的结果（DSL 已剥离并在工作线程下发）
            text_clean = getattr(_worker, "clean_text", "") or self._stream_full
            clean, actions = parse_ai_output(text_clean)
            # P9 静音兜底：native 模式**这一轮一片音频都没拿到**（模型没声明支持、
            # 服务端忽略 modalities、或音色被拒后退化）→ 不能让宠物彻底没声音，
            # 退回千问 TTS 念一遍正文。这正是 §14.5 里"抑制与播放必须同时落地"
            # 所指的那个坑：只抑制不兜底，用户会以为宠物坏了。
            if (self.opts.get("voice_mode") == "native"
                    and not getattr(_worker, "native_used", False) and clean):
                self.stream_voice.begin("cosy", self.opts.get("cosy_voice"))
                for s in _split_sentences(clean):
                    self.stream_voice.enqueue(s)
                status_hint = ("原生语音没出声音（%s），已退回千问 TTS"
                               % (getattr(_worker, "native_fallback", "")
                                  or "模型未返回音频"))
        else:
            clean, actions = parse_ai_output(text)
            # 记忆开启时保存会话状态，重启后自动续接（DeepSeek 后端）
            if self.opts.get("memory"):
                _save_thread_state(self.client.session_id,
                                   self.client.parent_message_id)
            # P9：native 模式且模型出音频时不调外部 TTS（音频已进播放管线）。
            # 这里用 != native 排除：万一后端不支持流式走了这条非流式路径，
            # 原生语音本来就拿不到，也不该再偷偷调外部 TTS（行为要一致）。
            if self.opts.get("voice_mode", "off") not in ("off", "native"):
                vmode = self.opts["voice_mode"]
                if vmode == "cosy":
                    # 千问 TTS 逐句合成播放（不打断），长回复也能边合边播
                    self.stream_voice.begin("cosy",
                                            self.opts.get("cosy_voice"))
                    for s in _split_sentences(clean):
                        self.stream_voice.enqueue(s)
                else:
                    self.voice.speak(clean, vmode)
        if self.pet is not None and hasattr(self.pet, "_show_ai_bubble"):
            self.pet._show_ai_bubble(clean)
        self._apply_ai_actions(actions)
        self.status.setText(status_hint)

    def _set_busy(self, busy):
        self._busy = busy
        self.send_btn.setEnabled(not busy)
        self.input_edit.setEnabled(not busy)
        self.attach_btn.setEnabled(not busy)

    def _new_thread(self):
        if self._busy:
            self.status.setText("宠物正在回复中，请稍候再试")
            return
        # 清除持久化的 DeepSeek 会话状态（千问后端无持久化，靠内存消息，清除无副作用）
        _clear_thread_state()
        # 重建客户端：DeepSeek 后端此时 session 为空，下次发消息时
        # 在 worker 线程内自动创建全新会话，不阻塞界面、失败可明确反馈；
        # 千问后端重建后内存 messages 为空，即全新多轮记忆。
        try:
            self.client = make_client()
        except Exception as e:
            self.status.setText(f"开启新会话失败：{e}")
            return
        self._clear_messages()
        self._clear_ai_bubble()
        self._clear_pending_images()
        self.stream_voice.end()
        self.status.setText("已开启新会话，聊天记录已清空")
        self.input_edit.setFocus()