# -*- coding: utf-8 -*-
"""原生语音流式播放管线测试（P9，`voice/stream_audio.py`）。

跑这个文件**不需要声卡、不需要 numpy、不需要网络** —— 这正是把管线拆成
`PcmBuffer`（纯缓冲）/ `SoundSink`（真声卡）/ `PcmPlayer`（拉取线程）三层的理由：
真正会出错的是"什么时候起播、欠载怎么办、收尾会不会吞掉最后一片"，
而这些用一个假 sink 就能断言。

- A1 `PcmBuffer`：读写 / 帧对齐 / 溢出丢最旧且不破坏帧对齐
- A2 起播阈值：没攒够 prebuffer 之前**不碰声卡**
- A3 欠载：取不满才算一次欠载
- A4 短句：`end()` 后不硬等 prebuffer，照样出声（否则短回复永远没声音）
- A5 收尾不吞尾：喂进去的字节全都要写到声卡
- A6 一片都没喂：`end()` 直接收尾，`is_active` 必须复位（否则外部会误判"还在响"）
- A7 无可用输出设备：**安静失败**，不抛异常、不打断聊天
- A8 `stop()`：立刻中断并释放声卡
- A9 音量增益：设置读不到时按原始音量播（不因设置异常而静音）
- A10 守卫：原生语音的**前置约束**必须存在且被真的注入（音频无法事后剥离）

无框架纯断言，风格同 test_providers.py / test_tts_catalog.py。
运行：python tests/test_audio_stream.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice import stream_audio as sa

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILED = []


def check(tag, cond, msg=""):
    if cond:
        print("  [ok] %s" % tag)
    else:
        print("  [FAIL] %s %s" % (tag, msg))
        FAILED.append(tag)


def eq(tag, got, want):
    check(tag, got == want, "got=%r want=%r" % (got, want))


class FakeSink:
    """假声卡：只记账，不出声。`write` 立即返回（真声卡靠阻塞限速）。"""

    def __init__(self, samplerate=0, channels=1):
        self.samplerate = samplerate
        self.channels = channels
        self.written = 0
        self.chunks = 0
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True
        return self

    @property
    def active(self):
        return self.opened and not self.closed

    def write(self, pcm):
        if pcm:
            self.written += len(pcm)
            self.chunks += 1

    def close(self):
        self.closed = True


class SinkRegistry:
    """收集每次新建的 sink，供断言"声卡到底有没有被打开"。"""

    def __init__(self, boom=False):
        self.sinks = []
        self.boom = boom

    def __call__(self, samplerate=0, channels=1):
        if self.boom:
            raise RuntimeError("no output device")
        s = FakeSink(samplerate=samplerate, channels=channels)
        self.sinks.append(s)
        return s

    @property
    def last(self):
        return self.sinks[-1] if self.sinks else None

    @property
    def total_written(self):
        return sum(s.written for s in self.sinks)


def make_player(reg, prebuffer_ms=100, max_buffer_ms=1000, gain=None):
    return sa.PcmPlayer(prebuffer_ms=prebuffer_ms, max_buffer_ms=max_buffer_ms,
                        sink_factory=reg, gain=gain or (lambda: 1.0))


def wait_until(pred, timeout=3.0, step=0.01):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


def pcm(nbytes):
    """一段可辨认的 PCM：逐字节递增，便于断言"没有错位/丢失"。"""
    return bytes((i * 7 + 3) & 0xFF for i in range(nbytes))


# ---------------- A1 PcmBuffer ----------------

def test_buffer():
    print("A1 PcmBuffer")
    b = sa.PcmBuffer()
    b.write(pcm(10))
    eq("A1a 写入计数", b.written, 10)
    eq("A1b available", b.available(), 10)
    eq("A1c 整取", b.read(10), pcm(10))
    eq("A1d 取空", b.read(10), b"")
    check("A1e 取不满计入欠载", b.underruns >= 1, b.underruns)

    b.write(pcm(6))
    eq("A1f 不足返回现有全部", len(b.read(10)), 6)
    b.clear()
    eq("A1g clear", b.available(), 0)

    # 溢出：丢最旧的，且必须按 2 字节整帧丢（否则后面全部错位成噪声）
    b2 = sa.PcmBuffer(max_bytes=10)
    b2.write(pcm(16))
    check("A1h 溢出丢最旧", b2.available() == 10, b2.available())
    check("A1i 溢出计数", b2.overflows == 1, b2.overflows)
    eq("A1j 保留的是尾部", b2.read(10), pcm(16)[-10:])
    check("A1k 奇数上限也按帧对齐", sa.PcmBuffer(max_bytes=9).max_bytes == 9)
    b3 = sa.PcmBuffer(max_bytes=9)
    b3.write(pcm(16))
    check("A1l 丢帧量为偶数", b3.available() % 2 == 0, b3.available())


# ---------------- A2–A5 起播 / 短句 / 收尾 ----------------

def test_prebuffer_gate():
    print("A2 起播阈值")
    reg = SinkRegistry()
    p = make_player(reg, prebuffer_ms=100)          # 100ms ≈ 4800B
    tiny = p.prebuffer_bytes // 4
    p.begin()
    p.feed(pcm(tiny))
    time.sleep(0.15)
    check("A2a 没攒够不开声卡", not reg.sinks, len(reg.sinks))
    check("A2b 数据留在缓冲里", p.buffer.available() == tiny, p.buffer.available())
    p.feed(pcm(p.prebuffer_bytes))
    check("A2c 攒够后起播", wait_until(lambda: reg.last is not None and reg.last.opened))
    p.end()
    check("A2d 播完自动停", wait_until(lambda: not p.is_active))


def test_short_utterance():
    print("A4 短句（不足 prebuffer 也要出声）")
    reg = SinkRegistry()
    p = make_player(reg, prebuffer_ms=1000)         # 故意设很大
    p.begin()
    p.feed(pcm(400))                                # 远小于 prebuffer
    time.sleep(0.1)
    check("A4a 起播前不出声", not reg.sinks)
    p.end()                                         # 关键：end() 不硬等
    ok = wait_until(lambda: reg.last is not None and reg.last.written >= 400)
    check("A4b end() 后照样出声", ok,
          "sinks=%s" % (reg.total_written,))
    check("A4c 收尾后 is_active 复位", wait_until(lambda: not p.is_active))


def test_tail_not_swallowed():
    print("A5 收尾不吞尾")
    reg = SinkRegistry()
    p = make_player(reg, prebuffer_ms=50)
    p.begin()
    total = 0
    for _ in range(4):
        chunk = pcm(2400)
        total += len(chunk)
        p.feed(chunk)
    p.end()
    ok = wait_until(lambda: not p.is_active and reg.total_written >= total)
    eq("A5a 全部字节都写到声卡", reg.total_written, total)
    check("A5b 诊断显示已播时长", p.stats()["played_ms"] > 0, p.stats())
    check("A5c 未丢帧", p.buffer.overflows == 0, p.buffer.overflows)


def test_silence_no_audio():
    print("A6 一片都没喂")
    reg = SinkRegistry()
    p = make_player(reg)
    p.begin()
    check("A6a begin 后 active", p.is_active)
    p.end()
    check("A6b 直接收尾", not p.is_active)
    check("A6c 不开声卡", not reg.sinks, len(reg.sinks))
    # 幂等：再 end 一次不应炸
    p.end()
    check("A6d end 幂等", True)


def test_no_device():
    print("A7 无可用输出设备（离屏 / 没扬声器）")
    reg = SinkRegistry(boom=True)
    p = make_player(reg, prebuffer_ms=50)
    p.begin()
    p.feed(pcm(2400))
    p.end()
    check("A7a 安静失败", wait_until(lambda: not p.is_active))
    check("A7b 记下原因", bool(p.last_error), p.last_error)
    check("A7c feed 不再抛异常", p.feed(pcm(200)) is None)


def test_stop():
    print("A8 stop()")
    reg = SinkRegistry()
    p = make_player(reg, prebuffer_ms=50)
    p.begin()
    p.feed(pcm(24000))                              # 0.5 秒的量
    check("A8a 已起播", wait_until(lambda: reg.last is not None))
    p.stop()
    check("A8b 立刻停", not p.is_active)
    check("A8c 声卡已释放", reg.last is None or reg.last.closed)
    check("A8d 缓冲已清空", p.buffer.available() == 0)
    # 奇数长度必须补一字节，否则 PCM16 从这一片起全部错位成噪声
    p2 = make_player(SinkRegistry(), prebuffer_ms=100000)
    p2.begin()
    p2.feed(pcm(3))
    eq("A8e 奇数长度补字节", p2.buffer.written, 4)
    p2.stop()


def test_gain():
    print("A9 音量增益")
    reg = SinkRegistry()
    p = make_player(reg, prebuffer_ms=50, gain=lambda: 1.0)
    p.begin()
    p.feed(pcm(2400))
    p.end()
    wait_until(lambda: not p.is_active)
    eq("A9a 增益=1.0 原样写入", reg.total_written, 2400)

    b = sa.PcmBuffer()
    b.write(pcm(4))
    eq("A9b 缓冲不做隐式变换", b.read(4), pcm(4))

    # 缩放路径要 numpy；受管 Python 没有就跳过（不是失败）
    try:
        import numpy  # noqa: F401
    except Exception:
        print("  [skip] A9c 无 numpy，跳过缩放校验")
        return
    p2 = sa.PcmPlayer(prebuffer_ms=50, sink_factory=reg, gain=lambda: 0.5)
    out = p2._apply_gain(b"\x00\x40\x00\xc0")       # 16384 / -16384
    import struct
    vals = struct.unpack("<2h", out)
    check("A9c 增益 0.5 生效", vals[0] == 8192 and vals[1] == -8192, vals)
    p2.stop()


# ---------------- A10 前置约束守卫 ----------------

def test_prompt_guard():
    print("A10 原生语音前置约束（音频无法事后剥离 → 只能前置于生成）")
    import config
    tpl = config.NATIVE_VOICE_PROMPT
    for token in ("{动作}", "【指令】", "语音"):
        check("A10a 模板点名 %s" % token, token in tpl)
    check("A10b 有绝对措辞", "绝对不要" in tpl)
    check("A10c 讲清代价（会被念出来）", "念出来" in tpl)
    check("A10d 给出长度上限", "60 字" in tpl)

    chat_src = open(os.path.join(BASE, "ai", "chat.py"), encoding="utf-8").read()
    check("A10e chat.py 真的注入模板",
          "config.NATIVE_VOICE_PROMPT" in chat_src)
    check("A10f 只在原生语音轮注入", "if self.native_audio:" in chat_src)
    check("A10g 有静音兜底（素材/模型不出声时退回 TTS）",
          "native_used" in chat_src and "已退回千问 TTS" in chat_src)


def main():
    for fn in (test_buffer, test_prebuffer_gate, test_short_utterance,
               test_tail_not_swallowed, test_silence_no_audio,
               test_no_device, test_stop, test_gain, test_prompt_guard):
        fn()
    print("")
    if FAILED:
        print("FAILED: %d 项 —— %s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("全部通过（%d 组）" % 9)
    return 0


if __name__ == "__main__":
    sys.exit(main())
