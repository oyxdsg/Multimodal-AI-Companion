# -*- coding: utf-8 -*-
"""AI 凭据与供应商状态的**唯一 QSettings 读写点**。

为什么要独立一个模块
--------------------
1. `config.py` 必须保持**无 Qt 依赖**（只 import os/re/sys），所以凡是要读 QSettings
   的都收在这里；config 只调 `ai.providers` 的纯函数。
2. 原先是**扁平固定键**（`qwen_api_key` / `deepseek_api_key` / `deepseek_api_model` …），
   加一家厂商就要加一组键 + 一组存取函数。现在改成命名空间：

       ai_provider               当前供应商 id
       ai/<id>/key               密钥
       ai/<id>/model             模型
       ai/<id>/base_url          端点覆盖（可空 = 用注册表默认）
       ai/<id>/stream            流式能力覆盖（capabilities override）
       ai/<id>/caps_override     通用能力覆盖标记（三态里的「未知」态用）
       ai/<id>/session_id        有会话的后端（网页版）用
       ai/<id>/parent_id
       ai/<id>/variant/<model>   思考档位（按 provider+model 存）

**升级策略：只读回退，不写迁移。**
读 `ai/qwen/key` → 不存在就读旧键 `qwen_api_key`；老键**保留不删**（可回滚），
用户下次在设置里点「确定」时自然落到新键。
"""

from PySide6.QtCore import QSettings

from ai import providers as prov

_ORG = "DesktopPet"
_APP = "PetWindow"

#: 旧 provider id 存键（`ai_backend`）里的历史值由 prov.canonical 归一化
_KEY_PROVIDER = "ai_provider"
_LEGACY_KEY_PROVIDER = "ai_backend"

#: (provider_id, field) → 旧键名（只读回退）
LEGACY_FIELDS = {
    ("deepseek-api", "key"): "deepseek_api_key",
    ("deepseek-api", "model"): "deepseek_api_model",
    ("qwen", "key"): "qwen_api_key",
    ("qwen", "model"): "qwen_model",
    ("deepseek-web", "session_id"): "ai_session_id",
    ("deepseek-web", "parent_id"): "ai_parent_id",
}

_STORE = None


def store():
    """共享的 QSettings（与既有实现同 org/app，保证读到同一份数据）。"""
    global _STORE
    if _STORE is None:
        _STORE = QSettings(_ORG, _APP)
    return _STORE


# ---------------------------------------------------------------- 原始读写

def raw_get(key, default=""):
    v = store().value(key, None)
    return default if v is None else v


def raw_set(key, value):
    store().setValue(key, value)


def raw_remove(key):
    store().remove(key)


# ---------------------------------------------------------------- 当前供应商

def provider():
    """当前供应商 id（已归一化）。旧键 `ai_backend` 的值回退兼容。"""
    v = store().value(_KEY_PROVIDER, None)
    if v in (None, ""):
        v = store().value(_LEGACY_KEY_PROVIDER, None)
    return prov.canonical(v)


def set_provider(pid):
    pid = prov.canonical(pid)
    store().setValue(_KEY_PROVIDER, pid)
    # 同步写老键：万一用户回滚到旧版本，后端选择依然正确
    store().setValue(_LEGACY_KEY_PROVIDER, _legacy_provider_value(pid))


def _legacy_provider_value(pid):
    """规范 id → 旧版本认识的值。`deepseek-web` 在旧版本里叫 `deepseek`。"""
    for old, new in prov.LEGACY_IDS.items():
        if new == pid:
            return old
    return pid


# ---------------------------------------------------------------- 字段

def _field_key(pid, field):
    return "ai/%s/%s" % (prov.canonical(pid), field)


def get_text(pid, field, default=""):
    """读字段；新键没有则回退旧键。"""
    key = _field_key(pid, field)
    v = store().value(key, None)
    if v is None or v == "":
        legacy = LEGACY_FIELDS.get((prov.canonical(pid), field))
        if legacy:
            v = store().value(legacy, None)
    return default if v is None else str(v)


def set_text(pid, field, value):
    store().setValue(_field_key(pid, field), value if value is not None else "")


def get_key(pid):
    return get_text(pid, "key", "").strip()


def set_key(pid, value):
    set_text(pid, "key", (value or "").strip())


def get_model(pid, default=""):
    p = prov.profile(pid)
    raw = get_text(pid, "model", "") or default or prov.default_model(p.id)
    # 历史模型 id 归一化（deepseek-chat → deepseek-flash）：
    # 否则设置界面选不中、perf 日志也会记下与实际不符的模型名
    return prov.resolve_model(p.id, raw)


def set_model(pid, value):
    set_text(pid, "model", value or "")


def get_base_url(pid, default=""):
    """端点：用户覆盖优先，其次注册表默认。"""
    return get_text(pid, "base_url", "") or default or prov.profile(pid).api


def set_base_url(pid, value):
    set_text(pid, "base_url", (value or "").strip())


# ---------------------------------------------------------------- 能力覆盖（「未知」态）

def get_flag(pid, name, default=False):
    v = store().value(_field_key(pid, name), None)
    if v is None:
        return default
    return str(v).lower() not in ("false", "0", "")


def set_flag(pid, name, value):
    store().setValue(_field_key(pid, name), bool(value))


def has_override(pid, name):
    return store().value(_field_key(pid, name), None) is not None


# ---------------------------------------------------------------- 会话状态

def session(pid):
    """返回 (session_id, parent_id)；parent_id 为 None 表示无。"""
    sid = get_text(pid, "session_id", "")
    raw = get_text(pid, "parent_id", "")
    try:
        parent = int(raw) if str(raw) not in ("", "None") else None
    except (TypeError, ValueError):
        parent = None
    return sid, parent


def set_session(pid, session_id, parent_id):
    set_text(pid, "session_id", str(session_id or ""))
    if parent_id is None:
        store().remove(_field_key(pid, "parent_id"))
    else:
        set_text(pid, "parent_id", parent_id)


def clear_session(pid):
    store().remove(_field_key(pid, "session_id"))
    store().remove(_field_key(pid, "parent_id"))


# ---------------------------------------------------------------- 思考档位

def variant(pid, mid, default=""):
    return get_text(pid, "variant/%s" % (mid or ""), default)


def set_variant(pid, mid, value):
    set_text(pid, "variant/%s" % (mid or ""), value or "")


# ---------------------------------------------------------------- 语音合成（TTS）

# TTS 与"对话供应商"是**两条独立的轴**：同一家可以只用对话、只用语音，或两者都换
# （正是这一点让原来的 `load_qwen_key()` 把语音绑死在对话后端上显得别扭）。
# 云端 TTS 目前只有百炼一家实现，所以键是扁平的 `tts/model` / `tts/voice`；
# 将来接第二家再升级成 `tts/<provider>/…`，字段形状不变。
# 音色老键 `qwen_cosy_voice` 只读回退（升级不丢用户已选音色）。

_TTS_LEGACY = {"voice": "qwen_cosy_voice"}


def get_tts(field, default=""):
    """读 TTS 设置（新键 → 老键回退）。"""
    v = store().value("tts/%s" % field, None)
    if v is None or v == "":
        legacy = _TTS_LEGACY.get(field)
        if legacy:
            v = store().value(legacy, None)
    return default if v is None else str(v)


def set_tts(field, value):
    store().setValue("tts/%s" % field, "" if value is None else value)
