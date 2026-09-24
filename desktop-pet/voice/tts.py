"""语音模块：支持多种合成方式，由用户选择。

- edge：微软 Edge TTS（联网，音质好，首音约 2 秒）
- local：Windows 自带 SAPI 离线语音（即时出声，音质一般，无需联网）
- cosy：千问 TTS（阿里云百炼，联网，音色多、可配模型，配合千问流式文字实现低延迟播报）
  —— 模式 id 仍叫 `cosy`（改 id 会让老用户设置失效），界面文案是「千问 TTS」；
  可选模型与音色来自 `voice/tts_catalog.py` 的实时目录，不硬编码。

所有任务经单一 worker 线程串行处理，避免多线程冲突。
StreamVoice 提供句子级流水线：begin/enqueue/end 逐句合成顺序播放。
"""

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import asyncio
import hashlib
import os
import queue
import tempfile
import threading
import time

import edge_tts
import pygame

from PySide6.QtCore import QObject, Signal, QTimer

# Edge 音色：晓晓（温柔女声）
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
# 本地 SAPI 语速
LOCAL_RATE = 190
# 预设台词语音缓存目录（千问 TTS 每句仅合成一次，之后播放本地文件）
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_cache")


def _load_rate():
    """从 QSettings 读取语音语速倍数（0.5-2.0，默认 1.25 倍速）。"""
    try:
        from PySide6.QtCore import QSettings
        v = int(QSettings("DesktopPet", "PetWindow").value("voice_rate", 125))
        return max(0.5, min(2.0, v / 100.0))
    except (TypeError, ValueError):
        return 1.25


def _edge_rate_str():
    """Edge TTS 的 rate 参数，如 1.25 倍 → '+25%'。"""
    n = round((_load_rate() - 1.0) * 100)
    return f"+{n}%" if n >= 0 else f"{n}%"


def _get_proxy():
    """读取 Windows 系统代理（Clash 等），Edge TTS 走代理更稳定快速。"""
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


def _cache_path(text, voice, model=""):
    """缓存键必须含**模型**：同一音色换 TTS 模型后音频不同，只按 (音色,文本) 会串。"""
    h = hashlib.md5(f"{model or '-'}|{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(_CACHE_DIR, h + ".wav")


def _write_wav(path, pcm_bytes, sample_rate):
    import wave
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)


def _play_wav_file(path):
    """sounddevice 播放本地 wav 文件（带音量与语速）。"""
    import numpy as np
    import sounddevice as sd
    import wave
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            frames = w.readframes(w.getnframes())
        if sw != 2 or not frames:
            return
        arr = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        if nch > 1:
            arr = arr.reshape(-1, nch)
            arr = arr.mean(axis=1)
        vol = _load_volume()
        if vol != 1.0:
            arr *= vol
            np.clip(arr, -32768, 32767, out=arr)
        sd.play(arr.astype(np.int16),
                samplerate=int(rate * _load_rate()))
        sd.wait()
    except Exception:
        pass


def play_cached(text, voice=None, model=None):
    """播放预设台词：本地缓存优先；未缓存则用千问 TTS 合成并缓存（每句仅一次 API）。

    返回是否成功播放。voice/model 为空时取设置值（`tts/voice` / `tts/model`）。
    """
    text = (text or "").strip()
    if not text:
        return False
    from ai.qwen import (COSYVOICE_SAMPLE_RATE, DEFAULT_VOICE, synth_sentence,
                         tts_model, tts_voice)
    if not voice:
        voice = tts_voice() or DEFAULT_VOICE
    if not model:
        model = tts_model()
    path = _cache_path(text, voice, model)
    if not os.path.exists(path):
        try:
            from ai.client import load_qwen_key
            pcm = synth_sentence(text, load_qwen_key(), voice, model)
            if not pcm:
                return False
            _write_wav(path, pcm, COSYVOICE_SAMPLE_RATE)
        except Exception:
            return False
    _play_wav_file(path)
    return True


def _load_volume():
    """从 QSettings 读取语音音量（0.0-1.0，默认 0.8）。"""
    try:
        from PySide6.QtCore import QSettings
        v = int(QSettings("DesktopPet", "PetWindow").value("voice_volume", 80))
        return max(0.0, min(1.0, v / 100.0))
    except (TypeError, ValueError):
        return 0.8


def _play_pcm_blocking(pcm_bytes, sample_rate=24000, volume=None):
    """用 sounddevice 阻塞播放一段 16-bit 单声道 PCM（支持流式句级播报，带语速）。"""
    import numpy as np
    import sounddevice as sd
    try:
        if not pcm_bytes:
            return
        if volume is None:
            volume = _load_volume()
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if arr.size == 0:
            return
        if volume != 1.0:
            arr *= volume
            np.clip(arr, -32768, 32767, out=arr)
        sd.play(arr.astype(np.int16),
                samplerate=int(sample_rate * _load_rate()))
        sd.wait()
    except Exception:
        pass


class VoiceModule(QObject):
    """语音合成 + 播放。speak(text, mode) 异步执行，不阻塞 UI。
    mode: "edge" / "local" / "cosy"。"""

    _ready = Signal(str)        # Edge mp3 临时文件路径（兼容保留，不再用于播放）
    speaking = Signal(bool)     # True=开始说话，False=结束
    started = Signal(int)       # 每个语音任务真正开始播放前发序号（音画同步起点）
    done = Signal(int)          # 每完成一个语音任务，发任务序号（供音画同步）

    _shared = None
    _inited = False

    def __init__(self, parent=None):
        super().__init__(parent)
        if not VoiceModule._inited:
            try:
                pygame.mixer.init()
            except Exception:
                pygame.mixer.init(frequency=44100, size=-16, channels=2)
            VoiceModule._inited = True
        self._engine = None
        self._queue = queue.Queue()
        self._task_seq = 0
        threading.Thread(target=self._worker, daemon=True).start()

    @classmethod
    def shared(cls):
        """全局共享实例：所有调用方共用一个队列，保证语音全排队、不叠声。"""
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def enqueue(self, text, mode="edge", voice=DEFAULT_VOICE, source="语音",
                model=None) -> int:
        """入队一段朗读，返回任务序号（供外部做音画同步）；不打断当前播放。

        `model` 只对 mode="cosy" 有意义：留空则用设置里的 TTS 模型
        （设置界面的「试听」需要临时指定模型才会传它）。
        """
        text = (text or "").strip()
        if not text:
            return -1
        self._task_seq += 1
        self._queue.put((text, mode, voice, self._task_seq, source,
                         time.time(), model))
        return self._task_seq

    def speak(self, text, mode="edge", voice=DEFAULT_VOICE, source="语音",
              model=None):
        """把文本转成语音并播放（异步、排队串行，不打断正在播放的语音）。"""
        self.enqueue(text, mode, voice, source, model)

    def speak_cached(self, text, voice=None, source="口头禅", model=None):
        """播放预设台词（本地缓存优先，千问 TTS 仅首句合成一次）。"""
        self.enqueue(text, "cached", voice, source, model)

    def stop(self):
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    # ---------- worker 线程 ----------

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            text, mode, voice, seq, source, t_enqueue, model = item
            t_start = time.time()
            ok = False
            try:
                if mode == "local":
                    ok = self._speak_local(text, seq)
                elif mode == "cosy":
                    ok = self._speak_cosy(text, voice, seq, model)
                elif mode == "cached":
                    ok = self._speak_cached(text, voice, seq, model)
                else:
                    ok = self._speak_edge(text, voice, seq)
            except Exception:
                ok = False
            if not ok and mode != "local":
                # 合成/播放失败 → 回退本地 SAPI 朗读，保证内容不被吞掉
                try:
                    self._speak_local(text, seq)
                except Exception:
                    pass
            # 性能日志：TTS 合成+播放耗时（含失败回退后的耗时）
            tts_ms = (time.time() - t_start) * 1000
            queued_ms = (t_start - t_enqueue) * 1000
            try:
                from core.perf_log import log
                log("TTS", source, len(text), tts_ms=tts_ms, queued_ms=queued_ms)
            except Exception:
                pass
            # 退出时窗口/信号源可能已销毁，emit 会抛 RuntimeError —— 忽略即可
            try:
                self.done.emit(seq)
            except RuntimeError:
                pass

    def _speak_edge(self, text, voice, seq):
        """Edge：worker 线程内合成 + 阻塞播放，保证排队串行不覆盖。
        started 在合成完成、即将出声时发出（声音开始=画面开始）。
        走系统代理（Clash 等）可显著提升稳定性与速度。
        成功返回 True，合成/播放失败返回 False（触发本地回退）。"""
        self.speaking.emit(True)
        path = ""
        ok = False
        try:
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            asyncio.run(edge_tts.Communicate(
                text, voice, rate=_edge_rate_str(),
                proxy=_get_proxy()).save(path))
            if os.path.getsize(path) > 0:
                self.started.emit(seq)   # 声音开始点
                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(_load_volume())
                pygame.mixer.music.play()
                ok = True
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
        except Exception:
            ok = False
        finally:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            self.speaking.emit(False)
        return ok

    def _speak_cosy(self, text, voice, seq, model=None):
        """千问 TTS 合成一句话并播放（阻塞）；成功返回 True。"""
        self.speaking.emit(True)
        ok = False
        try:
            from ai.qwen import (COSYVOICE_SAMPLE_RATE, synth_sentence,
                                 tts_model, tts_voice)
            from ai.client import load_qwen_key
            pcm = synth_sentence(text, load_qwen_key(),
                                 voice or tts_voice(), model or tts_model())
            if pcm:
                self.started.emit(seq)   # 声音开始点
                _play_pcm_blocking(pcm, COSYVOICE_SAMPLE_RATE)
                ok = True
        except Exception:
            ok = False
        finally:
            self.speaking.emit(False)
        return ok

    def _speak_cached(self, text, voice, seq, model=None):
        """播放预设台词缓存（阻塞）；未缓存时合成一次并落盘。成功返回 True。"""
        self.speaking.emit(True)
        ok = False
        try:
            self.started.emit(seq)   # 声音开始点（缓存命中则即刻出声）
            ok = play_cached(text, voice, model)
        except Exception:
            ok = False
        finally:
            self.speaking.emit(False)
        return ok

    def _speak_local(self, text, seq=None):
        """本地 SAPI 朗读；成功返回 True。seq 非空时在出声前发 started。"""
        self.speaking.emit(True)
        ok = False
        try:
            if self._engine is None:
                import pyttsx3
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", int(LOCAL_RATE * _load_rate()))
            self._engine.setProperty("volume", _load_volume())
            if seq is not None:
                self.started.emit(seq)   # 声音开始点
            self._engine.say(text)
            self._engine.runAndWait()
            ok = True
        except Exception:
            ok = False
        finally:
            self.speaking.emit(False)
        return ok


class StreamVoice(QObject):
    """流式朗读：begin 后 enqueue 的句子按序合成播放（不打断）。

    用于千问流式回复：模型每生成完整一句，立即入队合成播放，
    实现"边生成文字、边朗读"的低延迟语音播报。mode 支持 cosy/edge/local。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._mode = "cosy"
        self._voice = DEFAULT_VOICE
        threading.Thread(target=self._run, daemon=True).start()

    def begin(self, mode="cosy", voice=DEFAULT_VOICE):
        """开始一段流式朗读：清空未播内容并设置合成方式。"""
        self.end()
        self._mode = mode
        self._voice = voice

    def enqueue(self, text):
        """追加一句文本（顺序播放，不打断当前）。"""
        text = (text or "").strip()
        if text:
            self._queue.put((text, self._mode, self._voice))

    def end(self):
        """丢弃尚未合成播放的句子（正在播放的句子不打断）。"""
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        while True:
            item = self._queue.get()
            if item is None:
                continue
            text, mode, voice = item
            try:
                self._speak_one(text, mode, voice)
            except Exception:
                pass

    def _speak_one(self, text, mode, voice):
        vol = _load_volume()
        ok = False
        if mode == "cosy":
            from ai.qwen import (COSYVOICE_SAMPLE_RATE, synth_sentence,
                                 tts_model, tts_voice)
            from ai.client import load_qwen_key
            pcm = synth_sentence(text, load_qwen_key(),
                                 voice or tts_voice(), tts_model())
            if pcm:
                _play_pcm_blocking(pcm, COSYVOICE_SAMPLE_RATE, volume=vol)
                ok = True
        elif mode == "local":
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", int(LOCAL_RATE * _load_rate()))
            engine.setProperty("volume", vol)
            engine.say(text)
            engine.runAndWait()
            ok = True
        else:  # edge：合成 mp3 后阻塞播放，保证逐句顺序
            path = ""
            try:
                fd, path = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                asyncio.run(edge_tts.Communicate(
                    text, voice, rate=_edge_rate_str(),
                    proxy=_get_proxy()).save(path))
                if os.path.getsize(path) > 0:
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.set_volume(vol)
                    pygame.mixer.music.play()
                    ok = True
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                    try:
                        pygame.mixer.music.unload()
                    except Exception:
                        pass
            finally:
                try:
                    if path and os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
        if not ok and mode != "local":
            # 合成/播放失败 → 回退本地 SAPI 朗读
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.setProperty("rate", int(LOCAL_RATE * _load_rate()))
                engine.setProperty("volume", vol)
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass