# -*- coding: utf-8 -*-
"""重构回归测试（无框架，纯 Python 断言，直接 python tests/test_reconstruction.py 运行）。

覆盖本次重构（2.15.0）的核心风险点，以及 2.17.0 的 DeepSeek V4.1 适配：
1. ActionKey 枚举容错映射（皮肤未知动作 key 过滤）
2. mc_log 事件解析（未改动但作为基线回归）
3. 皮肤 role.json 容错（未知动作 key / 未知标签词被忽略）
4. GameProcessor 游戏事件 → AI 回复组装（mock chat_service，隔离网络）
5. DeepSeek SSE 解析容错（V4.1 未知 fragment 类型丢弃，不混入正文）
6. DeepSeek 图片上传链路与请求体（ref_file_ids / action，mock 网络）
7. 图片编码 + 「快速/专家」设置项已删除

说明：不依赖测试框架；不写用户 QSettings（GameProcessor 用真实只读 store，
AI 会话与 wiki 均已隔离）。
"""
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")


# ---------- 1. ActionKey ----------

def test_action_key():
    from core.action_key import ActionKey
    assert ActionKey.IDLE.value == "idle"
    assert ActionKey.DANCE.value == "dance"
    assert ActionKey.SEQUENCE.value == "sequence"
    # 合法映射
    assert ActionKey.from_value("idle") is ActionKey.IDLE
    assert ActionKey.from_value("think") is ActionKey.THINK
    assert ActionKey.from_value("dance") is ActionKey.DANCE
    # 非法 key 容错返回 None
    assert ActionKey.from_value("unknown_act") is None
    assert ActionKey.from_value("") is None
    assert ActionKey.from_value(None) is None
    # 全部 ACTIONS 键可枚举
    import config
    for k in config.ACTIONS:
        assert ActionKey.from_value(k) is not None, f"动作 key {k} 不在枚举中"
    print("[ok] ActionKey")


# ---------- 2. mc_log 事件解析（基线） ----------

def test_mc_log_parse():
    from game import mc_log
    text = (
        "[12:34:56] [Server thread/INFO] Steve was slain by Zombie\n"
        "[12:34:58] [Server thread/INFO] Steve has made the advancement [A Seedy Place]\n"
        "[12:35:00] [Server thread/INFO] Steve joined the game\n"
        "noise line without timestamp should be ignored\n"
    )
    evs = mc_log.parse_events(text)
    assert any("slain by Zombie" in e for e in evs), evs
    assert any("advancement" in e for e in evs), evs
    assert any("joined the game" in e for e in evs), evs
    assert not any("noise line" in e for e in evs), evs
    # 增量读取
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "latest.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write("[00:00:01] [INFO] A died\n")
        ev1, pos1, _ = mc_log.read_events_increment(p, 0, None)
        assert any("died" in e for e in ev1)
        with open(p, "a", encoding="utf-8") as f:
            f.write("[00:00:02] [INFO] B joined the game\n")
        ev2, pos2, _ = mc_log.read_events_increment(p, pos1, None)
        assert any("joined the game" in e for e in ev2)
        assert not any("died" in e for e in ev2)   # 只读增量
    print("[ok] mc_log 解析")


# ---------- 3. 皮肤 role.json 容错 ----------

def test_skin_tolerance():
    import config
    import pet.skins as skins

    with tempfile.TemporaryDirectory() as td:
        sid = "t_skin"
        p = os.path.join(td, sid)
        os.makedirs(p)
        role = {
            "id": sid,
            "name": "测试皮肤",
            "actions": {
                "idle": {"label": "待机"},
                "think": {"label": "思考"},
                "unknown_key": {"label": "未知"},      # 应被忽略
            },
            "action_tags": {
                "思考": "think",
                "未知": "unknown_key",                  # 目标 key 非法，应被忽略
            },
        }
        with open(os.path.join(p, "role.json"), "w", encoding="utf-8") as f:
            json.dump(role, f, ensure_ascii=False)
        old = config.SKINS_DIR
        config.SKINS_DIR = td
        try:
            lst = skins.list_skins()
            assert any(s == sid for s, _n, _p in lst), lst
            skin = skins.load_skin(sid)
            assert skin is not None
            assert set(skin.actions.keys()) == {"idle", "think"}, skin.actions
            assert skin.action_tags == {"思考": "think"}, skin.action_tags
        finally:
            config.SKINS_DIR = old
    print("[ok] 皮肤容错")


# ---------- 4. GameProcessor（mock chat_service / wiki_kb） ----------

class _FakeStore(dict):
    """内存版 QSettings（只读 value，不落盘，测试隔离用）。"""
    def value(self, key, default=None):
        return self.get(key, default)


class _FakeWiki:
    """假 wiki-knowledge 实现：记录调用并返回固定文本。"""

    def __init__(self, text="", record=None):
        self._text = text
        self.record = record if record is not None else []

    def know(self, events_text, *, session_key="", max_chars=480, hints=()):
        self.record.append({"events": events_text, "hints": list(hints),
                            "max_chars": max_chars})
        return self._text


class _FakeReg:
    def __init__(self, wiki_impl):
        self._w = wiki_impl

    def wiki_knowledge(self):
        return self._w


def _install_host_wiki(wiki_impl):
    """把 plugin.host.registry 指向只提供指定 wiki 实现的假注册表；返回还原函数。"""
    import plugin.host as host
    orig_reg = host.registry
    orig_cache = getattr(host, "_registry", None)
    host._registry = None
    host.registry = lambda: _FakeReg(wiki_impl)

    def restore():
        host.registry = orig_reg
        host._registry = orig_cache
    return restore


def test_game_processor():
    import game.processor as gp_mod

    class FakeChat:
        def __init__(self):
            self.calls = []

        def chat(self, prompt, source):
            self.calls.append((prompt, source))
            return "你挖到钻石了！"

    fake_chat = FakeChat()
    restore = _install_host_wiki(_FakeWiki(text=""))   # 无插件知识 → 不注入
    try:
        gp_mod.chat_service = fake_chat

        p = gp_mod.GameProcessor()
        p._store = _FakeStore()       # 隔离真实 QSettings（默认 normal 模式）
        p._logged_in = lambda: True   # 只在本测试实例生效

        result = p.process_game(["Steve 挖到了钻石矿石"], ["钻石"], "steve")
        assert result is not None and result["kind"] == "game", result
        assert result["text"] == "你挖到钻石了！"
        assert fake_chat.calls and fake_chat.calls[0][1] == "游戏"
        prompt = fake_chat.calls[0][0]
        assert "钻石" in prompt or "钻石矿石" in prompt or "Steve" in prompt
        assert "GAME_PROMPT" not in prompt  # 已格式化

        # 重复回复去重：再喂同样事件 + fake 返回同样内容 → None
        result2 = p.process_game(["Steve 挖到了钻石矿石"], ["钻石"], "steve")
        assert result2 is None, "相同回复应去重"

        # 未登录 → None
        p._logged_in = lambda: False
        assert p.process_game(["x"], [], "") is None

        # 修仙
        p._logged_in = lambda: True
        fake_chat.calls = []
        xr = p.process_xiuxian(
            [{"title": "突破", "detail": "炼气期 → 筑基期"}],
            {"currentStage": "base", "age": 20, "maxAge": 200})
        assert xr is not None and xr["kind"] == "xiuxian", xr
        assert "筑基" in fake_chat.calls[0][0]
    finally:
        restore()
    print("[ok] GameProcessor（默认模式）")


def test_wiki_extension_point():
    """processor 经 wiki-knowledge 扩展点取知识：装了注入、没装不崩。"""
    import game.processor as gp_mod

    rec = []

    class FakeChat:
        def __init__(self):
            self.calls = []

        def chat(self, prompt, source):
            self.calls.append((prompt, source))
            return "樱花木是 1.20 的新方块呢！"

    fake_chat = FakeChat()
    restore = _install_host_wiki(
        _FakeWiki(text="樱花木：1.20 新增的木材", record=rec))
    try:
        gp_mod.chat_service = fake_chat
        p = gp_mod.GameProcessor()
        p._store = _FakeStore()
        p._logged_in = lambda: True

        result = p.process_game(["事件：获得了樱花木"], ["樱花木"], "steve")
        assert result is not None
        prompt = fake_chat.calls[0][0]
        assert "樱花木" in prompt
        assert rec and rec[0]["hints"] == ["樱花木"], rec
        assert fake_chat.calls[0][1] == "游戏"

        # 无 wiki 实现 → 不注入、不崩
        restore()
        restore = _install_host_wiki(None)
        p._game_last_msg = ""
        fake_chat.calls = []
        r2 = p.process_game(["别的事件"], [], "steve")
        assert r2 is not None and r2["kind"] == "game"
    finally:
        restore()
    print("[ok] Wiki 扩展点接线（注入 / 不注入）")





def test_settings_mode_option_removed():
    """「快速/专家模式」设置项已删除；model_type 固定 default。"""
    import config
    assert config.AI_MODEL_TYPE == "default"
    assert not hasattr(config, "IMAGE_MAX_FILES") or config.IMAGE_MAX_FILES > 0
    src = open(os.path.join(BASE, "pet", "settings_dialog.py"),
               encoding="utf-8").read()
    assert "快速模式" not in src and "专家模式" not in src, "模式选项应已删除"
    assert 'setValue("ai_model_type"' not in src, "不再写入 ai_model_type"
    assert 'remove("ai_model_type")' in src, "应清理旧的 ai_model_type 键"
    # 上传/识图能力由可选插件 deepseek-web 提供（其测试见该插件目录）
    print("[ok] 设置项清理")


if __name__ == "__main__":
    test_action_key()
    test_mc_log_parse()
    test_skin_tolerance()
    test_game_processor()
    test_wiki_extension_point()
    test_settings_mode_option_removed()
    print("\n全部测试通过")
