# -*- coding: utf-8 -*-
"""记忆提炼（MessagesBackend 专用）：把滑出窗口的旧对话压缩成 facts + summary。

只被官方 API / 千问 后端调用（网页版历史在服务端，不做提炼——见 ai.backends.session）。

设计：
- 提炼是**增量**的：只把「本次滑出窗口的旧轮次」与现有 memory/summary 合并，
  而不是把全量历史丢给提炼模型（那样提炼本身也会爆窗）。
- 提炼用**独立会话 / 独立一次性调用**（不污染主对话历史），thinking 关、search 关。
- 后端优先官方 API（有 Key 时），否则回退网页版独立会话（照抄 refine_wiki 模式）。
- 提炼失败静默：调用方（MessagesBackend._maybe_condense）会把旧历史塞回，下次再试。
- 落盘 logs/memory.json（facts + summary），可读可改。
"""

import json
import os
import threading
import time

import config
from ai import context as ctx_mod
from ai import structured

_LOCK = threading.Lock()
_STORE_PATH = None   # 惰性初始化（logs/memory.json）
_last_save = 0.0     # 落盘节流（避免高频写盘）


def _path():
    global _STORE_PATH
    if _STORE_PATH is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _STORE_PATH = os.path.join(base, "logs", "memory.json")
    return _STORE_PATH


def _load_from_disk():
    """启动时把 memory.json 读进 ctx（进程内恢复记忆）。"""
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return (d.get("facts") or [], d.get("summary") or "")
    except Exception:
        return [], ""


def load_memory():
    """公开入口：读取持久化记忆，返回 (list[Fact], summary)。"""
    facts_raw, summary = _load_from_disk()
    facts = []
    for item in facts_raw if isinstance(facts_raw, list) else []:
        try:
            facts.append(ctx_mod.Fact(
                key=str(item.get("key") or ""),
                value=str(item.get("value") or ""),
                expire=item.get("expire"),
            ))
        except Exception:
            continue
    return facts, summary or ""


def _save(ctx):
    global _last_save
    now = time.time()
    if now - _last_save < 5:      # 5s 节流，避免高频写盘
        return
    _last_save = now
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump({
                "facts": [{"key": f.key, "value": f.value,
                           "expire": f.expire} for f in ctx.memory],
                "summary": ctx.summary,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _parse_json(text):
    """从模型输出里容错提取 {facts, summary}；失败返回 None。"""
    if not text:
        return None
    # 先试整段 JSON，再试去掉 markdown 代码块
    candidates = [text]
    if "```" in text:
        import re as _re
        m = _re.search(r"```(?:json)?\s*(.*?)\s*```", text, _re.S)
        if m:
            candidates.append(m.group(1))
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _condense_call(messages):
    """提炼调用统一走辅助调用入口（P4 改造）。

    原先这里**写死 `deepseek-api`**，取不到就回退到 DeepSeek **网页版**独立会话
    —— 结果选了千问的用户，记忆提炼实际依赖"已登录 DeepSeek 网页版"。
    现在走当前供应商：网页版复用独立持久会话，其余走 stateless 一次性调用。
    """
    from ai.utility import utility_chat
    return utility_chat(messages, purpose="condense")


def condense_context(ctx, old_turns):
    """把滑出窗口的旧轮次与现有记忆合并提炼，更新 ctx.memory / ctx.summary。

    :param ctx: ChatContext（原地修改）
    :param old_turns: list[Turn]，本次滑出窗口的旧对话
    :raises Exception: 提炼失败（调用方回退）
    """
    if not old_turns:
        return
    mem = ctx_mod.render_memory(ctx) or "（无）"
    turns_text = "\n".join(
        f"用户：{t.user}\n宠物：{t.assistant}" for t in old_turns)
    prompt = ctx_mod.CONDENSE_PROMPT.format(memory=mem, turns=turns_text)
    # P1-2：结构化输出失败重试——首轮非法 JSON 时把纠正提示追加进 prompt 再问一次
    # （不再「解析失败即永久丢弃这条记忆」；见 config.STRUCTURED_RETRY）
    obj, _text, _att = structured.retry_structured(
        fetch=lambda note: _condense_call(
            [{"role": "user", "content": prompt + note}]),
        extract=structured.extract_json,
        retries=config.STRUCTURED_RETRY,
    )
    if not obj:
        raise RuntimeError("提炼输出无法解析")
    # 合并 facts：同 key 覆盖，新 key 追加
    new_facts = obj.get("facts") or []
    if isinstance(new_facts, list):
        by_key = {f.key: f for f in ctx.memory}
        for item in new_facts:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            by_key[key] = ctx_mod.Fact(
                key=key,
                value=str(item.get("value") or ""),
                expire=item.get("expire"),
            )
        ctx.memory = list(by_key.values())
    summary = (obj.get("summary") or "").strip()
    if summary:
        ctx.summary = summary
    _save(ctx)
