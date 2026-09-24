# -*- coding: utf-8 -*-
"""千问（通义千问）客户端：流式文字 + qwen TTS 语音合成（阿里云百炼官方 API）。

- 流式聊天：OpenAI 兼容接口（qwen-turbo / qwen-plus），SSE 增量返回文字
- 语音合成：**模型与音色都从 `voice/tts_catalog.py` 的实时目录取**，
  配合句子级流水线实现"边生成文字、边合成朗读"

聊天部分现已是 `OpenAIChatAdapter` 的**薄子类**（P2 收敛）；语音部分也已完成
**数据化**（2.26.0）：原先写死 `qwen3-tts-flash` + 两个音色，而百炼实际有
9 个非实时 TTS 模型、48 个音色，且**音色可用性随模型变化**（flash 48 个 /
instruct-flash 24 个 / qwen-tts 4 个）—— 硬编码只能用到 4%。

> 命名说明：模式 id 仍叫 `cosy`、常量仍叫 `COSYVOICE_*`，但调的是 **qwen3-tts**
> 系列而不是 CosyVoice。id 不改是为了不破坏既有设置值（改了老用户会掉回默认），
> 界面文案已改称「千问 TTS」。
"""

import base64
import io
import time
import wave

import numpy as np

from curl_cffi import requests as cffi_requests

from ai.protocols.openai_chat import OpenAIChatAdapter
from ai.retry import retry_call
from voice import tts_catalog as catalog_mod

BASE_URL = "https://dashscope.aliyuncs.com"
# 对话走 OpenAI **兼容入口**。⚠️ 别直接拿 BASE_URL 当 base_url —— 少了
# `/compatible-mode/v1` 会 404（TTS 那条链路用的是裸域名 + 自己的 path，
# 所以 BASE_URL 本身必须保持不带后缀）。实机踩过：HTTP 404。
COMPAT_BASE_URL = f"{BASE_URL}/compatible-mode/v1"
CHAT_URL = f"{COMPAT_BASE_URL}/chat/completions"
# 千问 TTS 语音合成（HTTP，非流式返回音频下载 URL）
TTS_URL = f"{BASE_URL}/api/v1/services/aigc/multimodal-generation/generation"

# 可选千问**对话**模型（按生成速度/质量排序，qwen-turbo 最快且免费额度大）。
# 这份清单没有可靠远端来源：百炼 /models 返回 256 项且不区分场景（含图像/语音/
# 向量模型），拿它当对话模型下拉会是一团乱。所以对话模型仍随代码走。
CHAT_MODELS = ["qwen-turbo", "qwen-plus"]
DEFAULT_CHAT_MODEL = "qwen-turbo"

# ---------------------------------------------------------------- 语音合成（TTS）
# 模型与音色**不再硬编码**：目录见 voice/tts_catalog.py（抓官方文档）。
# 下面两个只是"目录读不到"时的兜底值（均已实机验证可用）。
DEFAULT_VOICE = catalog_mod.FALLBACK_VOICE       # Chelsie（千雪）
TTS_MODEL = catalog_mod.FALLBACK_MODEL           # qwen3-tts-flash
COSYVOICE_SAMPLE_RATE = 24000


def tts_model():
    """当前 TTS 模型：设置值优先，否则目录默认。"""
    try:
        from ai import credentials_store as _creds
        v = _creds.get_tts("model", "")
        if v:
            return v
    except Exception:
        pass
    return catalog_mod.default_model()


def tts_voice():
    """当前音色：设置值优先，否则目录默认（Chelsie）。"""
    try:
        from ai import credentials_store as _creds
        v = _creds.get_tts("voice", "")
        if v:
            return v
    except Exception:
        pass
    return DEFAULT_VOICE


def tts_voices(model=None):
    """可选音色（按模型过滤，目录已按模型声明适配关系）。"""
    return catalog_mod.voices(model or tts_model())


def voice_ids(model=None):
    """可选音色 id 元组（替代原先的 `VOICES` 常量）。"""
    return tuple(v.id for v in tts_voices(model))


def tts_models():
    """可选 TTS 模型（非实时；已排除 realtime / vc / vd）。"""
    return catalog_mod.models()


# 语音模式字符串（与设置界面对应；设置界面的下拉**从这里取**，不再各写一份）
VOICE_OFF = "off"
VOICE_EDGE = "edge"
VOICE_LOCAL = "local"
VOICE_COSY = "cosy"
#: P9：让**模型自己出语音**（不额外调 TTS）。只在所选对话模型声明了
#: `output_modalities` 含 audio、且走后端流式时可用。见 voice/stream_audio.py。
VOICE_NATIVE = "native"
VOICE_MODES = [VOICE_OFF, VOICE_EDGE, VOICE_LOCAL, VOICE_COSY, VOICE_NATIVE]


def audio_voice_for(pid, mid, wanted=""):
    """决定原生语音这次用哪个音色（P9）。

    优先级：**用户选的（须在该模型的音色清单里）** → 目录给的该模型默认音色
    → 注册表里的兜底默认值。

    ⚠️ 目录是按**模型族**给表，同族的非实时模型音色集可能更小
    （实测 `qwen3.5-omni-flash` 就拒了 Cherry/Chelsie，而文档 3.5 节里两者都在）。
    所以调用方还应有一条兜底：真被服务端拒了（`voice ... not supported`）
    就**省略 voice 重试一次**，用模型自己的默认音色。
    """
    from ai import providers as _prov
    default, voices = catalog_mod.omni_voices(mid)
    ids = [v.id for v in voices]
    if wanted and (not ids or wanted in ids):
        return wanted
    return (default
            or _prov.model(pid, mid).audio_voice
            or (ids[0] if ids else ""))


def omni_voice_ids(mid):
    """该模型的原生语音可选音色 id（目录为准；目录缺失时返回空 = 让调用方用默认）。"""
    _default, voices = catalog_mod.omni_voices(mid)
    return tuple(v.id for v in voices)

# TTS 复用连接（句子级流水线会频繁调用）
_TTS_SESSION = cffi_requests.Session(impersonate="chrome131", timeout=60)


class QwenError(Exception):
    """千问 API 错误。"""


class QwenClient(OpenAIChatAdapter):
    """千问流式聊天客户端（OpenAI 兼容接口，内存多轮记忆）。"""

    def __init__(self, api_key, model=DEFAULT_CHAT_MODEL):
        super().__init__(
            api_key=api_key,
            base_url=COMPAT_BASE_URL,
            model=model or DEFAULT_CHAT_MODEL,
            auth="bearer",
            error_cls=QwenError,
            thinking_mode="",       # 千问不支持思考控制
        )
        self.messages = []           # 多轮记忆（消息历史）

    def remember(self, user_prompt, assistant_text):
        """把一轮对话记进多轮记忆。

        **两条流式路径共用**：`chat_stream`（纯文本）与 P9 的原生语音路径
        （走 `stream_events`，拿不到 `chat_stream` 的收尾逻辑）。
        只在 `chat_stream` 里记的话，用原生语音聊天时多轮记忆会静默失效。
        """
        text = (assistant_text or "").strip()
        if not text or not user_prompt:
            return
        self.messages.append({"role": "user", "content": user_prompt})
        self.messages.append({"role": "assistant", "content": text})
        if len(self.messages) > 30:      # 防止无限增长，只保留最近 30 条
            self.messages = self.messages[-30:]

    def chat_stream(self, prompt, system_prompt=None, model=None):
        """流式聊天：yield 文本增量；结束后保存多轮记忆。

        形态与既有实现一致（`Iterator[str]`）——事件流原语见基类的
        :meth:`ai.protocols.base.ProtocolAdapter.stream_events`。
        """
        full = ""
        for piece in self.stream_prompt(prompt, system_prompt, model=model):
            full += piece
            yield piece
        self.remember(prompt, full)

    def chat(self, prompt, system_prompt=None, model=None, **kwargs):
        """非流式：返回完整文本。千问不支持思考，多余 kwargs 直接吞掉。"""
        return "".join(self.chat_stream(prompt, system_prompt, model))

    def reset_thread(self):
        self.messages = []


def _wav_to_pcm(wav_bytes):
    """解析 wav 字节为 16-bit 单声道 PCM；失败返回 None。"""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if sw != 2 or not frames:
            return None
        arr = np.frombuffer(frames, dtype=np.int16)
        if nch > 1:
            arr = arr.reshape(-1, nch)
            arr = arr.mean(axis=1).astype(np.int16)
        return arr.tobytes()
    except Exception:
        return None


#: 值得重试的状态码：限流与瞬时故障。
#: **实机取证**：连续快速合成 7 句时第 7 句被打回（静置后同参数立刻成功）——
#: 也就是说百炼对短时间密集调用会限流。流式播报是"一句一次调用"，
#: 一旦被限流 `synth_sentence` 返回 None，voice/tts.py 会回退到本地 SAPI
#: 机器人音，用户听到的是"忽然换了个人"。所以这里补**一次**有界重试。
#: 400 之类属于永久错误（模型/音色不合法），重试只是白等，直接放弃。
_TTS_RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)
_TTS_RETRY_DELAY = 0.6


def _audio_from_response(resp):
    """从 200 响应里取音频并解析成 PCM；取不到返回 None。"""
    data = resp.json()
    audio = data.get("output", {}).get("audio") or {}
    audio_url = audio.get("url") or ""
    if not audio_url:
        # 部分场景可能直接返回 base64 data
        raw = audio.get("data") or ""
        if isinstance(raw, str) and raw:
            return _wav_to_pcm(base64.b64decode(raw))
        return None
    r2 = _TTS_SESSION.get(audio_url)
    if r2.status_code != 200 or not r2.content:
        return None
    return _wav_to_pcm(r2.content)


def synth_sentence(text, api_key, voice=None, model=None, retry=True):
    """合成一句话，返回 16-bit PCM 裸音频；失败返回 None。

    * `voice` / `model` 留空时自动取设置值（`tts/voice` / `tts/model`），
      再退到目录默认 —— 调用方不必知道用户当前选了哪一套。
    * voice 放入 input（该系列模型不支持顶层 voice 字段）；
      音频通过响应 output.audio.url 下载，再解析为 PCM。
    * 命中限流/瞬时故障时**重试一次**（见 `_TTS_RETRY_STATUS`）；
      400 这类永久错误立即返回，不浪费时间。
    """
    text = (text or "").strip()
    if not text or not api_key:
        return None
    if not voice:
        voice = tts_voice()
    if not model:
        model = tts_model()
    body = {
        "model": model,
        "input": {"text": text, "voice": voice},
        "response_format": {"format": "wav",
                            "sample_rate": COSYVOICE_SAMPLE_RATE},
    }
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    attempts = 2 if retry else 1

    def _attempt():
        """单次合成尝试 → ``(pcm | None, fatal)``。
        fatal=True = 永久错误（模型/音色非法），重试只是白等。
        """
        try:
            resp = _TTS_SESSION.post(TTS_URL, json=body, headers=headers)
        except Exception:
            return None, False              # 网络瞬时故障：可重试
        if resp is None:
            return None, False
        if resp.status_code == 200:
            pcm = _audio_from_response(resp)
            return (pcm, False) if pcm else (None, False)
        if resp.status_code not in _TTS_RETRY_STATUS:
            return None, True               # 永久错误：不再重试
        return None, False

    pcm, _ = retry_call(
        _attempt, attempts=attempts, delay=_TTS_RETRY_DELAY,
        should_retry=lambda r: not r[1] and r[0] is None)
    return pcm
