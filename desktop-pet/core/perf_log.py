"""性能日志：记录 AI 处理 / TTS 合成播放耗时，形成数据集便于后期测试调试。

日志格式（logs/perf.log，追加写入）：
[时间] AI  | 来源 | chars=字数 | ai_ms=AI处理耗时
[时间] TTS | 来源 | chars=字数 | tts_ms=合成+播放耗时 | queued_ms=排队耗时
"""

import datetime
import os
import threading

_LOCK = threading.Lock()
_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
_PATH = os.path.join(_DIR, "perf.log")


def log(stage, source, chars, ai_ms=None, tts_ms=None, queued_ms=None, note=None):
    try:
        os.makedirs(_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"[{ts}] {stage} | {source} | chars={int(chars or 0)}"]
        if ai_ms is not None:
            parts.append(f"ai_ms={int(ai_ms)}")
        if tts_ms is not None:
            parts.append(f"tts_ms={int(tts_ms)}")
        if queued_ms is not None:
            parts.append(f"queued_ms={int(queued_ms)}")
        if note:
            parts.append(f"note={note}")
        line = " | ".join(parts) + "\n"
        with _LOCK:
            with open(_PATH, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass