# -*- coding: utf-8 -*-
"""语音转文字（STT）：按住快捷键说话 → 录音 → 识别为文本。

双引擎（设置可切换，config.STT_ENGINES）：
- Vosk：离线轻量、CPU 实时、短句准（vosk-model-small-cn-0.22，~42MB）
- faster-whisper：更准、上下文更好（small/base 模型，首次自动下载）

流程：
- Recorder.start()：sounddevice 录音（16kHz 单声道 PCM16），后台线程累积
- Recorder.stop()：停止并返回录音 PCM；支持静音自动收尾（静音超时）
- STTEngine.recognize(pcm, sr)：按所选引擎识别，返回文本（后台线程调用）
- 模型缺失时自动下载（Vosk 官方/镜像；faster-whisper 走 HuggingFace/镜像）
"""

import os
import sys
import threading
import urllib.request
import zipfile

import config

# 繁体→简体转换：whisper/Vosk 中文模型输出偏繁体字形，统一转简体
# （OpenCC 纯 Python 实现，缺失时原样返回）
_OPENCC = None
try:
    from opencc import OpenCC
    _OPENCC = OpenCC("t2s")
except Exception:
    _OPENCC = None


def _to_simplified(text):
    if _OPENCC is not None and text:
        try:
            return _OPENCC.convert(text)
        except Exception:
            pass
    return text


class Recorder:
    """麦克风录音：手动停止 + 静音自动收尾。start/stop 均在调用线程安全。"""

    def __init__(self, silence_ms=None):
        self.silence_ms = silence_ms if silence_ms is not None \
            else config.STT_SILENCE_MS
        self._frames = []
        self._stream = None
        self._lock = threading.Lock()
        self._last_voice = 0.0
        self._started_at = 0.0
        self._running = False

    def start(self):
        """打开录音流（非阻塞，后台线程收音频）。"""
        import time
        import sounddevice as sd
        self._frames = []
        self._started_at = time.time()
        self._last_voice = time.time()
        self._running = True

        def _cb(indata, frames, time_info, status):
            if not self._running:
                return
            import numpy as np
            pcm = indata[:, 0].tobytes()
            with self._lock:
                self._frames.append(pcm)
            # 静音检测：帧 RMS 低于阈值视为静音，刷新最后有声时间
            arr = indata[:, 0].astype(np.float32)
            rms = float((arr * arr).mean()) ** 0.5
            if rms >= 0.02:          # 阈值 ≈ -34dBFS
                self._last_voice = time.time()

        self._stream = sd.InputStream(
            samplerate=config.STT_SAMPLE_RATE, channels=1,
            dtype="int16", callback=_cb, blocksize=1600)
        self._stream.start()

    def stop(self):
        """停止录音，返回 (pcm_bytes, sample_rate)。未录音返回 None。"""
        import time
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._running = False
        if not self._frames:
            return None
        with self._lock:
            pcm = b"".join(self._frames)
        self._frames = []
        if not pcm:
            return None
        return pcm, config.STT_SAMPLE_RATE

    def elapsed(self):
        import time
        return time.time() - self._started_at if self._started_at else 0.0

    def silent_for(self):
        """当前已静音的秒数（用于自动收尾判定）。"""
        import time
        return time.time() - self._last_voice

    @property
    def recording(self):
        return self._running


# ---------------------------------------------------------------------------
# 模型管理
# ---------------------------------------------------------------------------

def vosk_model_dir():
    return os.path.join(config.STT_MODEL_DIR, config.STT_VOSK_MODEL)


def vosk_model_ready():
    d = vosk_model_dir()
    # Vosk 模型目录含 am / conf / ivector 等子目录
    return os.path.isdir(d) and os.path.isfile(
        os.path.join(d, "am", "final.mdl"))


def _system_proxy():
    """读取 Windows 系统代理（Clash 等），curl 下载模型时走代理更稳。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if enabled and server:
            if not server.lower().startswith(("http://", "https://", "socks")):
                server = "http://" + server
            return server
    except Exception:
        pass
    return None


def _download(url, dest, log=None):
    """用 Windows 自带 curl 下载（走系统代理 + 长超时），成功返回 True。"""
    import subprocess
    tmp = dest + ".part"
    cmd = ["curl", "-L", "--connect-timeout", "15", "--max-time", "900",
           "--retry", "2", "-sS", "-o", tmp, url]
    proxy = _system_proxy()
    if proxy:
        cmd[1:1] = ["-x", proxy]
    try:
        r = subprocess.run(cmd, timeout=930)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            os.replace(tmp, dest)
            return True
        return False
    except Exception as e:
        if log:
            log(f"下载失败：{e}")
        return False


def ensure_vosk_model(log=None):
    """确保 Vosk 中文模型存在；缺失则从多个源下载解压。返回是否就绪。"""
    if vosk_model_ready():
        return True
    os.makedirs(config.STT_MODEL_DIR, exist_ok=True)
    zip_path = os.path.join(config.STT_MODEL_DIR,
                            config.STT_VOSK_MODEL + ".zip")
    ok = False
    for url in config.STT_VOSK_URLS:
        if log:
            log(f"下载 Vosk 模型：{url}")
        if _download(url, zip_path, log):
            ok = True
            break
    if not ok:
        if log:
            log("Vosk 模型下载失败：请确认网络/代理，或手动放置模型到 "
                f"{vosk_model_dir()}")
        return False
    try:
        if log:
            log("下载完成，解压…")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(config.STT_MODEL_DIR)
        os.remove(zip_path)
    except Exception as e:
        if log:
            log(f"解压失败：{e}")
        return False
    return vosk_model_ready()


def _whisper_local_model():
    """faster-whisper 模型在本地缓存目录的存在性（目录名含规格）。"""
    try:
        from huggingface_hub import snapshot_download
        cache = os.path.join(config.STT_MODEL_DIR, "whisper")
        if os.path.isdir(cache):
            return True
        return False
    except Exception:
        return False


class STTEngine:
    """识别引擎封装：Vosk / faster-whisper 二选一，缺依赖自动回退。"""

    def __init__(self, engine=None, whisper_size=None, log=None):
        self.engine = engine or config.STT_DEFAULT_ENGINE
        self.whisper_size = whisper_size or config.STT_WHISPER_MODEL
        self.log = log or (lambda m: None)
        self._vosk = None
        self._whisper = None
        self._lock = threading.Lock()
        self._error = ""

    @property
    def ready(self):
        return self._load_engine(quiet=True)

    def _load_engine(self, quiet=False):
        """按所选引擎加载模型；失败回退另一引擎并记录原因。"""
        with self._lock:
            if self._vosk is not None or self._whisper is not None:
                return True
            if self.engine.lower().startswith("vosk"):
                if self._load_vosk(quiet):
                    return True
                if not quiet:
                    self.log(f"Vosk 不可用（{self._error}），回退 faster-whisper")
                self.engine = "faster-whisper"
            if not quiet:
                self.log(f"加载 faster-whisper（{self.whisper_size}）…")
            return self._load_whisper(quiet)

    def _load_vosk(self, quiet):
        try:
            if not vosk_model_ready():
                if not ensure_vosk_model(self.log):
                    self._error = "模型缺失/下载失败"
                    return False
            from vosk import Model, KaldiRecognizer
            self._vosk_model = Model(vosk_model_dir())
            self._vosk = KaldiRecognizer
            return True
        except Exception as e:
            self._error = str(e)
            return False

    def _load_whisper(self, quiet):
        try:
            from faster_whisper import WhisperModel
            import os as _os
            # 国内网络用 HF 镜像 + 禁用 Xet 新存储（镜像对 Xet 的 CAS 返回 401）
            if not _os.environ.get("HF_ENDPOINT"):
                _os.environ.setdefault(
                    "HF_ENDPOINT", "https://hf-mirror.com")
            _os.environ["HF_HUB_DISABLE_XET"] = "1"
            self._whisper_model = WhisperModel(
                self.whisper_size, device="cpu", compute_type="int8")
            return True
        except Exception as e:
            self._error = str(e)
            return False

    def recognize(self, pcm, sr=config.STT_SAMPLE_RATE):
        """把 PCM 音频识别为文本（后台线程调用）。失败返回 ""。"""
        import numpy as np
        if not pcm:
            return ""
        if not self._load_engine():
            self.log(f"STT 引擎不可用：{self._error}")
            return ""
        try:
            if self._vosk is not None:
                rec = self._vosk(self._vosk_model, sr)
                # 一次性喂完全部音频后取最终结果（AcceptWaveform 可能返回 False，
                # 此时仍有部分结果；始终用 FinalResult 保证拿到完整文本）
                rec.AcceptWaveform(pcm)
                import json as _json
                res = _json.loads(rec.FinalResult())
                text = (res.get("text") or "").strip()
                return _to_simplified(text)
            # faster-whisper
            arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _info = self._whisper_model.transcribe(
                arr, language="zh", beam_size=1)
            return _to_simplified(
                "".join(s.text for s in segments).strip())
        except Exception as e:
            self.log(f"识别失败：{e}")
            return ""