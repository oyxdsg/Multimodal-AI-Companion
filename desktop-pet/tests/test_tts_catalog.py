# -*- coding: utf-8 -*-
"""千问 TTS 目录（模型 / 音色）测试（DESIGN_AI_PROVIDERS.md §十三）。

- T1 解析器：只取**非实时**表；音色字段（名 / 描述 / 语种 / 支持模型）抽全
- T2 按模型过滤音色（文档声明了每个音色支持哪些模型）
- T3 离线可用：无缓存时回退随包快照，且**不包含实时专用音色**
- T4 无硬编码守卫：`ai/qwen.py` 不得再写死音色/模型清单
- T5 凭据：`tts/voice` 新键 + `qwen_cosy_voice` 旧键回退（假 QSettings，不碰真配置）
- T6 默认值自洽 + 实时/vc/vd 模型绝不进清单

无框架纯断言，风格同 test_providers.py。**不需要网络**（除 T3 用的快照已随包）。
运行：python tests/test_tts_catalog.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice import tts_catalog as tc

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 合成的两表文档：第一张是实时表（应被跳过），第二张是非实时表
FIXTURE = """
<html><body>
<table>
<tr><td>voice参数</td><td>详情</td><td>支持语种</td><td>支持模型</td></tr>
<tr><td>Anna</td>
    <td><p><strong>音色名</strong>：安娜</p><p><strong>描述</strong>：实时专用（女性）</p></td>
    <td>中文（普通话）、英语</td>
    <td><ul><li><strong>Qwen3-TTS-Flash-Realtime</strong>：qwen3-tts-flash-realtime、
        qwen3-tts-flash-realtime-2025-11-27</li></ul></td></tr>
</table>
<table>
<tr><td>voice参数</td><td>详情</td><td>支持语种</td><td>支持模型</td></tr>
<tr><td>Chelsie</td>
    <td><audio src="https://example.invalid/chelsie.wav" controls></audio>
        <p><strong>音色名</strong>：千雪</p><p><strong>描述</strong>：二次元虚拟女友（女性）</p></td>
    <td>中文（普通话）、英语、日语</td>
    <td><ul><li><strong>Qwen3-TTS-Instruct-Flash</strong>：qwen3-tts-instruct-flash、
        qwen3-tts-instruct-flash-2026-01-26</li>
        <li><strong>Qwen3-TTS-Flash</strong>：qwen3-tts-flash</li></ul></td></tr>
<tr><td>Cherry</td>
    <td><p><strong>音色名</strong>：芊悦</p><p><strong>描述</strong>：阳光积极（女性）</p></td>
    <td>中文（普通话）</td>
    <td><ul><li><strong>Qwen3-TTS-Flash</strong>：qwen3-tts-flash</li></ul></td></tr>
</table>
</body></html>
"""


# ---------------- T1 解析器 ----------------

def test_parse_doc():
    cat = tc.parse_doc(FIXTURE, live_models=("x",))
    ids = [v.id for v in cat.voices]
    assert ids == ["Chelsie", "Cherry"], ids
    assert "Anna" not in ids, "实时表的音色不该被收进来"

    ch = cat.voice("Chelsie")
    assert ch.name == "千雪", ch.name
    assert ch.desc == "二次元虚拟女友（女性）", ch.desc
    assert ch.langs == ("中文（普通话）", "英语", "日语"), ch.langs
    assert ch.sample.endswith("chelsie.wav"), ch.sample
    assert ch.label() == "千雪（Chelsie） · 二次元虚拟女友（女性）", ch.label()

    # 组名（Qwen3-TTS-Instruct-Flash）不得被误当成模型 id
    assert "qwen3-tts-instruct-flash" in cat.models, cat.models
    assert all(m.startswith("qwen") for m in cat.models), cat.models
    assert cat.models == ("qwen3-tts-flash", "qwen3-tts-instruct-flash",
                          "qwen3-tts-instruct-flash-2026-01-26"), cat.models
    assert cat.live_models == ("x",)
    print("  [T1] 解析器：非实时表 + 音色字段 + 组名不误匹配 OK")


# ---------------- T2 按模型过滤音色 ----------------

def test_voice_filtering():
    cat = tc.parse_doc(FIXTURE)
    flash = [v.id for v in cat.voices_for("qwen3-tts-flash")]
    instr = [v.id for v in cat.voices_for("qwen3-tts-instruct-flash")]
    assert flash == ["Chelsie", "Cherry"], flash
    assert instr == ["Chelsie"], instr          # Cherry 不支持 instruct 模型
    assert [v.id for v in cat.voices_for("")] == ["Chelsie", "Cherry"]
    assert cat.voice("Cherry").supports("qwen3-tts-flash") is True
    assert cat.voice("Cherry").supports("qwen3-tts-instruct-flash") is False
    # 默认音色必须落在该模型支持的音色里
    assert cat.default_voice("qwen3-tts-instruct-flash") in instr
    print("  [T2] 按模型过滤音色 OK（模型不同 → 可用音色不同）")


# ---------------- T3 离线快照 ----------------

def test_snapshot_fallback():
    saved_path, saved_memo = tc._CACHE_PATH, tc._live["cat"]
    try:
        tc._CACHE_PATH = os.path.join(BASE, "voice", "_no_such_cache.json")
        tc._live["cat"] = None
        cat = tc.catalog()
        assert cat.source == "snapshot", cat.source
        assert len(cat.voices) >= 40, len(cat.voices)
        assert len(cat.models) >= 5, cat.models
        # 实测：Anna 只在实时清单里，非实时清单不该有它
        assert cat.voice("Anna") is None, "Anna 是实时专用音色，不该出现在非实时目录"
        assert cat.voice("Chelsie") is not None
        assert cat.voice("Chelsie").name == "千雪"
    finally:
        tc._CACHE_PATH, tc._live["cat"] = saved_path, saved_memo
    print("  [T3] 离线回退随包快照 OK（>=40 音色，且不含实时专用音色）")


# ---------------- T4/T6 无硬编码 + 默认自洽 ----------------

def test_no_hardcoded_and_defaults():
    import re

    # ---- 源码守卫（不需要导入，薄环境也跑）----
    src = open(os.path.join(BASE, "ai", "qwen.py"), encoding="utf-8").read()
    assert "VOICES = [" not in src, "音色清单不得再写死在 ai/qwen.py"
    # 没有任何"列表字面量"同时含 Chelsie 与 Cherry（写死清单的指纹）
    for m in re.finditer(r"\[[^\[\]]{0,200}\]", src):
        body = m.group(0)
        if "Chelsie" in body and "Cherry" in body:
            raise AssertionError("发现写死的音色清单：%s" % body)

    try:
        import ai.qwen as qwen
    except Exception as e:      # 薄环境（缺 numpy / curl_cffi）只跳过导入相关断言
        print("  [T4] 无硬编码源码守卫 OK；导入相关断言跳过（%s）" % e)
        return

    assert not hasattr(qwen, "VOICES"), "旧 VOICES 常量应已移除"
    cat = tc.catalog(reload=True)
    assert qwen.tts_model() in cat.models, (qwen.tts_model(), cat.models)
    ids = qwen.voice_ids()
    assert len(ids) == len(cat.voices_for(qwen.tts_model())), len(ids)
    assert ids == tuple(v.id for v in tc.voices(qwen.tts_model()))
    assert qwen.synth_sentence("", "k") is None, "空文本应直接返回 None"
    assert qwen.synth_sentence("你好", "") is None, "无 Key 应直接返回 None"

    # 清单里绝不能出现实时 / vc / vd 模型（同一端点调它们会 400）
    for mid in cat.models:
        assert "realtime" not in mid, mid
        assert "-vc" not in mid and "-vd" not in mid, mid
    for v in cat.voices:
        for mid in v.models:
            assert "realtime" not in mid and "-vc" not in mid and "-vd" not in mid, \
                (v.id, mid)

    # 默认值自洽
    assert cat.default_model() in cat.models
    assert cat.default_voice(cat.default_model()) in [
        v.id for v in cat.voices_for(cat.default_model())]

    flash_n = len(cat.voices_for("qwen3-tts-flash"))
    base_n = len(cat.voices_for("qwen-tts"))
    assert flash_n > base_n, \
        "实机：qwen3-tts-flash 音色远多于 qwen-tts，这正是必须按模型过滤的理由"
    print("  [T4] 无硬编码守卫 + 默认自洽 OK（%d 音色 / %d 模型；"
          "flash 可用 %d 个 vs qwen-tts 仅 %d 个）"
          % (len(cat.voices), len(cat.models), flash_n, base_n))


# ---------------- T6 限流重试 ----------------

def test_tts_retry():
    """限流（429）应重试一次；400 这类永久错误立即放弃，不白等。

    依据：实机连续快速合成 7 句时第 7 句被打回（静置后同参数立刻成功）。
    不重试的话，流式播报会掉到本地 SAPI 机器人音，用户听起来像"换了个人"。
    """
    try:
        import ai.qwen as qwen
    except Exception as e:
        print("  [T6] 跳过（缺 numpy / curl_cffi）：%s" % e)
        return

    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._p = payload or {}
            self.content = b""

        def json(self):
            return self._p

    class _Sess:
        def __init__(self, codes):
            self.codes = list(codes)
            self.calls = 0

        def post(self, *a, **k):
            self.calls += 1
            return _Resp(self.codes.pop(0) if self.codes else 200)

        def get(self, *a, **k):
            return _Resp(500)

    saved_sess = qwen._TTS_SESSION
    saved_delay = qwen._TTS_RETRY_DELAY
    try:
        qwen._TTS_RETRY_DELAY = 0
        kw = dict(voice="Chelsie", model="qwen3-tts-flash")
        # ① 429 → 重试；第二次 200 但无音频 → 仍失败，但确实调了两次
        s = _Sess([429, 200])
        qwen._TTS_SESSION = s
        assert qwen.synth_sentence("你好", "k", **kw) is None
        assert s.calls == 2, s.calls

        # ② 400（永久错误）→ 不重试
        s = _Sess([400, 200])
        qwen._TTS_SESSION = s
        assert qwen.synth_sentence("你好", "k", **kw) is None
        assert s.calls == 1, s.calls

        # ③ 显式 retry=False → 不重试
        s = _Sess([429, 200])
        qwen._TTS_SESSION = s
        assert qwen.synth_sentence("你好", "k", retry=False, **kw) is None
        assert s.calls == 1, s.calls
    finally:
        qwen._TTS_SESSION = saved_sess
        qwen._TTS_RETRY_DELAY = saved_delay
    print("  [T6] TTS 限流重试策略 OK（429 重试一次 / 400 立即放弃 / 可关）")


# ---------------- T5 凭据 ----------------

class _FakeStore:
    """内存版 QSettings（绝不动真实配置）。"""

    def __init__(self, init=None):
        self.d = dict(init or {})

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):
        self.d[key] = value

    def remove(self, key):
        self.d.pop(key, None)


def test_tts_credentials():
    try:
        from ai import credentials_store as creds
    except Exception as e:
        print("  [T5] 跳过（无 PySide6）：%s" % e)
        return
    saved = creds._STORE
    try:
        # ① 只有旧键（老用户）→ 能读出音色
        creds._STORE = _FakeStore({"qwen_cosy_voice": "Cherry"})
        assert creds.get_tts("voice") == "Cherry", creds.get_tts("voice")
        assert creds.get_tts("model", "") == ""
        # ② 写入落到新键，且旧键保留（可回滚）
        creds.set_tts("voice", "Momo")
        creds.set_tts("model", "qwen3-tts-instruct-flash")
        assert creds._STORE.d.get("tts/voice") == "Momo"
        assert creds._STORE.d.get("tts/model") == "qwen3-tts-instruct-flash"
        assert creds._STORE.d.get("qwen_cosy_voice") == "Cherry", "旧键不得被删"
        # ③ 新键优先
        assert creds.get_tts("voice") == "Momo"
        # ④ 缺省值
        assert creds.get_tts("nope", "d") == "d"
    finally:
        creds._STORE = saved
    print("  [T5] TTS 凭据 tts/model+tts/voice 与旧键回退 OK")


def main():
    print("=" * 62)
    print("千问 TTS 目录（模型 / 音色）测试")
    print("=" * 62)
    test_parse_doc()
    test_voice_filtering()
    test_snapshot_fallback()
    test_no_hardcoded_and_defaults()
    test_tts_retry()
    test_tts_credentials()
    print("\n全部通过")


if __name__ == "__main__":
    main()
