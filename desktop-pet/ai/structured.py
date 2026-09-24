# -*- coding: utf-8 -*-
"""结构化输出：JSON 提取 + 失败重试（P1-2，借鉴 LangChain `with_structured_output` 思路）。

不引入 Pydantic —— 用「容错提取 + 校验失败回灌重问」这一最轻形态：
首次输出非法 JSON 时，把纠正提示追加进 prompt 再问一次，而非静默丢弃。

用法（如记忆提炼 `ai/memory.py`）：:

    obj, raw, attempts = retry_structured(
        fetch=lambda note: utility_chat([{"role": "user",
                                          "content": prompt + note}]),
        extract=extract_json,
        retries=config.STRUCTURED_RETRY,
    )
    if not obj:
        raise RuntimeError("提炼输出无法解析")
"""

import json
import re

# 容忍被 markdown 代码块包裹的 JSON（```json ... ```）
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)

# 重试时追加的纠正指令：让模型只输出一个 JSON 对象，不要任何解释/前后缀/代码块标记。
_RETRY_HINT = (
    "\n\n【更正】你上一次的输出不是可解析的 JSON。"
    "请只输出一个合法的 JSON 对象（不要任何解释、前后缀或代码块标记）。"
)


def extract_json(text):
    """从模型输出里容错提取 JSON 对象。

    :return: ``(obj | None, error)``；成功时 error 为空串，失败时含原因。
    """
    if not text or not str(text).strip():
        return None, "输出为空"
    text = str(text)
    candidates = [text]
    m = _CODE_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1))
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj, ""
    # 兜底：从含前后缀的文本里截出第一个平衡的 { ... } 对象
    obj = _bracket_json(text)
    if obj is not None:
        return obj, ""
    return None, "输出不是合法 JSON（%s）" % _first_line(text)


def _bracket_json(text):
    """截取第一个平衡的 JSON 对象；失败返回 None（含普通标点/换行干扰的容错）。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except (json.JSONDecodeError, TypeError):
                    return None
    return None


def _first_line(text):
    s = (text or "").strip().splitlines()
    return (s[0][:60] if s else "?")


def retry_structured(fetch, extract, retries=1):
    """结构化输出 + 失败重试。

    :param fetch: ``note -> text``。note 为空串表示首轮；非空（重试轮）时，
        调用方把纠正提示追加进 prompt 后再取。
    :param extract: ``text -> (obj, error)``。
    :param retries: 首轮之外的额外尝试次数（对应 `config.STRUCTURED_RETRY`）。
    :return: ``(obj | None, raw_text, attempts)``。
    """
    attempts = 0
    note = ""
    raw = ""
    for _ in range(max(0, retries) + 1):
        attempts += 1
        try:
            raw = fetch(note) or ""
        except Exception:
            return None, raw, attempts
        obj, _err = extract(raw)
        if obj is not None:
            return obj, raw, attempts
        note = _RETRY_HINT
    return None, raw, attempts
