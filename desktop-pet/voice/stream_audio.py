# -*- coding: utf-8 -*-
"""模型原生语音的**流式播放管线**（P9，见 DESIGN_AI_PROVIDERS.md §11.1 / §十六）。

背景（全部实机取证）
--------------------
百炼的 Omni 系列模型可以让**模型自己出语音**（`modalities:["text","audio"]`）。
取证结论决定了这里的设计：

1. **只能走流式**。非流式响应里**根本不给 audio 字段**，但 `usage` 里照样有
   `audio_tokens`（实测 75）—— 也就是说音频被生成、被计费、然后被丢掉。
   官方示例也写着 *"Qwen-Omni only supports stream mode"*。
2. 音频落在 `choices[0].delta.audio.data`，base64 编码的**裸 PCM16**。
   流式下连 `format:"wav"` 都不给 RIFF 头（实测首字节仍是 `\xfd\xff`）。
3. 采样率 **24000 Hz / 单声道**，文档明确"不支持自定义输出采样率"。
   → 所以这里**不做重采样**，`语速` 设置对原生语音不生效（见"已知限制"）。
4. 音频是**一小片一小片**到的（实测 20 片 / 295KB）。网络抖动会让片与片之间
   出现空档，直接 `write` 到声卡会**断断续续** —— 所以需要一个抖动缓冲。

结构（为什么拆三层）
--------------------
``PcmBuffer``  纯标准库的字节环形缓冲：**可离线单测**（起播阈值/欠载/溢出/收尾）
``SoundSink``  sounddevice + numpy 的真实出声端（惰性导入，只有它依赖声卡）
``PcmPlayer``  把缓冲喂给 sink 的拉取线程 + 状态机

拆开的原因很实际：**声卡在 CI / 离屏环境里不可用**，但"什么时候该起播、
欠载了怎么办、收尾怎么不吞掉最后一片"这些才是真正会出错的地方。
sink 可注入 → 这些行为用假 sink 就能断言。

已知限制
--------
* 语速（`voice_rate`）对原生语音无效——重采样会引入失真，且模型输出的节奏
  本身是语义的一部分（它按语气决定停顿）。音量仍然生效。
* 只支持 16-bit 单声道 PCM。换供应商若给别的格式，需要在这里加转换。
"""

import threading
import time

#: 百炼 Omni 输出固定 24kHz 单声道 PCM16
SAMPLE_RATE = 24000
#: 每次从缓冲取多少字节喂给声卡（24000Hz × 2B × 0.1s）
BLOCK_BYTES = 4800
#: 攒够这么多才起播：太小会一开始就欠载，太大则首音延迟明显
DEFAULT_PREBUFFER_MS = 240
#: 缓冲上限，防上游灌爆内存（超了丢最旧）
DEFAULT_MAX_BUFFER_MS = 6000


class PcmBuffer:
    """PCM16 字节环形缓冲。**纯标准库、线程安全、可离线单测**。

    刻意不做"自动按帧对齐"——PCM16 是 2 字节一帧，写入端保证偶数长度即可
    （`PcmPlayer.feed` 会兜住奇数长度的情况）。
    """

    def __init__(self, max_bytes=0):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self.max_bytes = int(max_bytes or 0)
        #: 诊断计数
        self.written = 0
        self.read_bytes = 0
        self.underruns = 0
        self.overflows = 0

    def write(self, data):
        """写入一段。超过上限时**丢最旧的**（宁可缺前面，也不要内存无限涨）。"""
        if not data:
            return
        with self._lock:
            self.written += len(data)
            self._buf += data
            if self.max_bytes and len(self._buf) > self.max_bytes:
                drop = len(self._buf) - self.max_bytes
                # 掉帧必须按 2 字节整帧丢，否则后面全部错位成噪声
                drop += drop % 2
                del self._buf[:drop]
                self.overflows += 1

    def read(self, nbytes):
        """取至多 nbytes。不足时**返回现有的全部**（不补零，由调用方决定）。
        真正取不满才算一次欠载——那才是"卡了一下"。
        """
        with self._lock:
            n = min(int(nbytes), len(self._buf))
            out = bytes(self._buf[:n])
            del self._buf[:n]
            self.read_bytes += n
            if n < nbytes:
                self.underruns += 1
            return out

    def available(self):
        with self._lock:
            return len(self._buf)

    def clear(self):
        with self._lock:
            self._buf = bytearray()


class SoundSink:
    """真实出声端（sounddevice）。**只有这个类依赖声卡与 numpy。**

    `write` 会阻塞到设备缓冲有空位 —— 这正是我们要的节流：拉取线程被声卡
    的播放速度自然限速，不需要自己算 sleep 时长。
    """

    def __init__(self, samplerate=SAMPLE_RATE, channels=1):
        self.samplerate = int(samplerate)
        self.channels = int(channels)
        self._stream = None
        self._np = None

    def open(self):
        import numpy as np                     # 惰性：PcmBuffer 单测不需要 numpy
        import sounddevice as sd
        self._np = np
        self._stream = sd.OutputStream(
            samplerate=self.samplerate, channels=self.channels,
            dtype="int16", blocksize=0)
        self._stream.start()

    @property
    def active(self):
        return bool(self._stream is not None and self._stream.active)

    def write(self, pcm_bytes):
        if not pcm_bytes:
            return
        arr = self._np.frombuffer(pcm_bytes, dtype=self._np.int16)
        self._stream.write(arr)

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


def _load_volume():
    """读设置里的音量（0.0-1.0）。读不到就 1.0（不因设置问题静音）。"""
    try:
        from PySide6.QtCore import QSettings
        v = int(QSettings("DesktopPet", "PetWindow").value("voice_volume", 80))
        return max(0.0, min(1.0, v / 100.0))
    except Exception:
        return 1.0


class PcmPlayer:
    """边收边播的 PCM 播放器（抖动缓冲 + 拉取线程）。

    用法::

        p = player()          # 全局单例
        p.begin()             # 一段新的回复开始
        p.feed(pcm_chunk)     # 收到一片就喂进来（线程安全）
        p.end()               # 不会再有数据了 → 播完自动收尾

    `on_start` 在**真正出声前**回调（用于音画同步），`on_done` 在播完/被停后回调。
    两个回调都在**拉取线程**里执行，调用方需自己切回 UI 线程。
    """

    def __init__(self, samplerate=SAMPLE_RATE, prebuffer_ms=DEFAULT_PREBUFFER_MS,
                 max_buffer_ms=DEFAULT_MAX_BUFFER_MS, sink_factory=None,
                 on_start=None, on_done=None, gain=None):
        self.samplerate = int(samplerate)
        self.prebuffer_bytes = int(self.samplerate * 2 * prebuffer_ms / 1000.0)
        self.buffer = PcmBuffer(max_bytes=int(self.samplerate * 2
                                              * max_buffer_ms / 1000.0))
        self._sink_factory = sink_factory or SoundSink
        self._on_start = on_start
        self._on_done = on_done
        self._gain_provider = gain

        self._sink = None
        self._thread = None
        self._stop = threading.Event()
        self._draining = False
        self._started = False
        self._active = False
        self._lock = threading.Lock()
        self.last_error = ""

    # ---------------- 对外 ----------------

    def begin(self):
        """开始一段新回复：停掉上一段、清空缓冲。"""
        self.stop()
        self.buffer.clear()
        self._stop.clear()
        self._draining = False
        self._started = False
        self._active = True

    def feed(self, pcm_bytes):
        """喂入一片音频。线程安全（由网络线程调用）。"""
        if not pcm_bytes or not self._active:
            return
        data = pcm_bytes
        if len(data) % 2:                       # 奇数长度：补一字节静音，保住帧对齐
            data = data + b"\x00"
        data = self._apply_gain(data)
        self.buffer.write(data)
        self._ensure_thread()

    def end(self):
        """不会再有数据。缓冲里剩的照播（短句也能出声），播完自动收尾。"""
        if not self._active:
            return
        self._draining = True
        if self._started or self.buffer.available():
            self._ensure_thread()
        else:
            # 一片音频都没收到（典型：模型只回了文本）→ 直接收尾。
            # 不这么做 `_active` 会一直挂着，直到下一次 `begin()` 才复位，
            # 期间任何 `is_active` 判断都是错的（比如"原生语音到底有没有在响"）。
            self._active = False

    def stop(self):
        """立刻停（丢弃未播内容）。"""
        self._active = False
        self._draining = False
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        self._thread = None
        self._close_sink()
        self.buffer.clear()

    # ---------------- 内部 ----------------

    def _apply_gain(self, data):
        """按音量缩放。设置里没读到时用 1.0，不因设置异常而静音。"""
        g = self._gain_provider() if self._gain_provider else _load_volume()
        if not g or g == 1.0:
            return data
        try:
            import numpy as np
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            arr *= g
            np.clip(arr, -32768, 32767, out=arr)
            return arr.astype(np.int16).tobytes()
        except Exception:
            return data                          # 缩放失败就原样播，别静音

    def _ensure_thread(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        """拉取线程：攒够 prebuffer 起播 → 持续喂 → 收尾。"""
        try:
            while self.buffer.available() < self.prebuffer_bytes and not self._stop.is_set():
                if self._draining:
                    break                        # 短句：不硬等 prebuffer
                time.sleep(0.01)
            if self._stop.is_set():
                return
            if not self._open_sink():
                return
            self._started = True
            if self._on_start:
                try:
                    self._on_start()
                except Exception:
                    pass
            while not self._stop.is_set():
                chunk = self.buffer.read(BLOCK_BYTES)
                if chunk:
                    self._sink.write(chunk)
                elif self._draining:
                    break                        # 排空了且不再有数据 → 收尾
                else:
                    time.sleep(0.01)             # 上游还没喂 → 等一下（算欠载）
        except Exception as e:
            self.last_error = "%s" % e
        finally:
            self._close_sink()
            self._active = False
            if self._on_done:
                try:
                    self._on_done()
                except Exception:
                    pass

    def _open_sink(self):
        try:
            self._sink = self._sink_factory(samplerate=self.samplerate, channels=1)
            self._sink.open()
            return True
        except Exception as e:
            # 没有可用输出设备（如离屏/无扬声器）也要安静失败，不能把聊天打断
            self.last_error = "%s" % e
            self._sink = None
            return False

    def _close_sink(self):
        if self._sink is not None:
            try:
                self._sink.close()
            except Exception:
                pass
            self._sink = None

    # ---------------- 诊断 ----------------

    @property
    def is_active(self):
        return bool(self._active)

    @property
    def is_started(self):
        return bool(self._started)

    def stats(self):
        """给日志/测试用的一行诊断。"""
        return {
            "active": self._active,
            "started": self._started,
            "written": self.buffer.written,
            "played": self.buffer.read_bytes,
            "underruns": self.buffer.underruns,
            "overflows": self.buffer.overflows,
            "played_ms": int(self.buffer.read_bytes / 2.0
                             / self.samplerate * 1000),
            "error": self.last_error,
        }


_PLAYER = None
_PLAYER_LOCK = threading.Lock()


def player():
    """全局单例：与 VoiceModule 一样，全应用共用一条音频输出。"""
    global _PLAYER
    if _PLAYER is None:
        with _PLAYER_LOCK:
            if _PLAYER is None:
                _PLAYER = PcmPlayer()
    return _PLAYER
