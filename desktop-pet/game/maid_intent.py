# -*- coding: utf-8 -*-
"""女仆指令 DSL 解析器：从 AI 回复里提取【cmd(params)】并下发给女仆。

背景
----
- 桌宠 AI 回复可以夹带一条指令（格式：【cmd(param=value, ...)】），
  由本模块解析后经 ``MaidLink.send_command`` 下发给 SmartMaid 模组。
- 这是「用户 → 桌宠 AI → 女仆」闭环的 AI 侧出口：
  用户在聊天里说「去打僵尸」，AI 回复末尾带 【attack(range=12)】，
  桌宠解析 → 下发 → 女仆执行 → 回执注入下一轮上下文。

设计
----
- 白名单**单一来源** = ``nlu.mod_contract.COMMANDS``（与 NLU 本地预处理共用同一份契约），
  排除 ``craft_check``（桌宠内部查询，AI 不应下发）。
- **脚本指令**：【script({...json...})】—— Atomic Command Protocol 的复合指令流。
  括号内是**完整 JSON params**（steps 数组 + vars + fail/max_steps），steps 内的指令
  用 ``mod_contract.SCRIPT_ALLOWED`` 校验、参数逐条 ``script_clamp``。
  script 默认 ``cancel_previous=true``（复合任务接管女仆当前任务）。
- 参数清洗复用 ``mod_contract.clamp``：自动丢弃模组不认识的参数、数值夹进合法区间。
- ``cancel_previous`` 是 DSL 层控制项（是否抢占女仆当前任务），**不是**模组指令参数，
  解析时单独取出，不回传给模组。
- 坐标：``[x,y,z]`` 绝对（逗号分隔整数）/ ``~``（主人脚下，透传）。
- 解析失败：静默丢弃 + 写 ``logs/crash.log``（不打断正常聊天展示）。
"""

import json
import os
import re

from nlu import mod_contract

# 外层指令：【cmd(params)】或【cmd】（无参指令）。
# 容错：容忍「【指令：attack(...)】」这种 AI 偶发把「指令：」写进【】的格式（剥掉前缀）。
_INTENT_RE = re.compile(r"【\s*(?:指令[：:])?\s*(\w+)\s*(?:\((.*?)\))?\s*】", re.S)
# 未闭合 script 容错：AI 偶发漏掉 script 的 】（JSON 内无 `)`，第一个 `)` 即 JSON 结束）
_LOOSE_SCRIPT_RE = re.compile(r"【\s*script\s*\(([\s\S]*?)\)")
# 坐标数组：[x,y,z]
_POS_RE = re.compile(r"\[\s*([-\d.~]+)\s*,\s*([-\d.~]+)\s*,\s*([-\d.~]+)\s*\]")
# 单参数：name=value（值可能是引号字符串 / 数字 / 无引号标识符）
_PARAM_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|(\[.*?\])|([^\s,]+))", re.S)

# AI 可下发的指令（排除桌宠内部查询 craft_check）
WHITELIST = frozenset(
    name for name, c in mod_contract.COMMANDS.items()
    if c.layer in ("basic", "integrated", "meta"))


class MaidIntentError(Exception):
    """DSL 解析/校验失败（消息面向日志，不打断聊天）。"""


def _unwrap_cmd_wrapper(args_str):
    """容错：把 `cmd(attack(range=10))` 的嵌套串剥成真实指令。

    识别「外层指令名 + 括号 + 内层真实指令」，返回 (真实cmd, 解析后的 dict)；
    不满足嵌套形态时返回 None。
    """
    if not args_str:
        return None
    inner = _INTENT_RE.search("【" + args_str + "】")
    if not inner:
        return None
    n_cmd = inner.group(1)
    if n_cmd not in WHITELIST:
        return None
    try:
        params = _parse_params(inner.group(2) or "")
    except MaidIntentError:
        return None
    miss = mod_contract.missing_required(n_cmd, params)
    if miss:
        return None
    params, _dropped = mod_contract.clamp(n_cmd, params)
    cancel = bool(str(params.pop("cancel_previous", False)).lower()
                  in ("true", "1", "yes"))
    return n_cmd, {"cmd": n_cmd, "params": params,
                   "cancel_previous": cancel}


def _parse_pos(text):
    """解析 [x,y,z] → ['x','y','z']（保留 ~ 相对），失败返回 None。"""
    m = _POS_RE.search(text)
    if not m:
        return None
    return [m.group(1), m.group(2), m.group(3)]


def _parse_script(args_str):
    """解析 【script({...json...})】→ {"cmd":"script","params":...,"cancel_previous":...}。

    括号内必须是合法 JSON 对象（steps 数组 + vars/fail/max_steps 等）；steps 内每条指令
    用 ``mod_contract.SCRIPT_ALLOWED`` 校验、参数逐条 ``script_clamp``（含 if/loop 递归）。
    script 默认 ``cancel_previous=true``（复合任务接管女仆当前任务）。
    失败返回 None（调用方静默丢弃）。
    """
    if not args_str or not args_str.strip():
        return None
    try:
        parsed = json.loads(args_str)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    cancel = parsed.pop("cancel_previous", True)
    cancel = bool(cancel) if isinstance(cancel, bool) else False
    valid, err = mod_contract.validate_script(parsed)
    if err is not None:
        _log_parse_error("script 校验失败: %s" % err)
        return None
    return {"cmd": "script", "params": valid, "cancel_previous": cancel}


def _parse_params(args_str):
    """解析参数串 → dict；畸形时抛 MaidIntentError。"""
    params = {}
    for m in _PARAM_RE.finditer(args_str):
        name, q1, q2, arr, bare = m.groups()
        if name in params:
            raise MaidIntentError("重复参数: %s" % name)
        if q1 is not None or q2 is not None:
            params[name] = q1 if q1 is not None else q2
        elif arr is not None:
            pos = _parse_pos(arr)
            if pos is None:
                raise MaidIntentError("坐标格式错误: %s" % arr)
            params[name] = pos
        else:
            v = bare
            if re.fullmatch(r"-?\d+", v or ""):
                params[name] = int(v)
            elif re.fullmatch(r"-?\d+(\.\d+)?", v or ""):
                params[name] = float(v)
            else:
                params[name] = v
    return params


def parse(text):
    """从 AI 回复中提取第一条有效指令。

    :param text: AI 回复全文（含文字 / {动作}标签 / 【指令】）
    :return: ``(cmd_dict | None, clean_text)``
        - cmd_dict: ``{"cmd", "params", "cancel_previous"}``；无指令/解析失败为 None
        - clean_text: 去掉【指令】后的纯文本（供气泡显示 / 动作标签解析）
    未知/畸形指令一律静默丢弃（写 crash.log），不打断聊天。
    """
    if not text:
        return None, (text or "")

    # 容错：AI 偶发漏掉 script 的 】闭合 → 独立提取（JSON 内无 `)`，第一个 `)` 即 JSON 结束）
    loose = _LOOSE_SCRIPT_RE.search(text)
    while loose:
        after = text[loose.end():]
        if after.lstrip().startswith("】"):
            # 已被 _INTENT_RE 闭合覆盖，跳过交给下面的常规循环
            loose = _LOOSE_SCRIPT_RE.search(text, loose.end())
            continue
        parsed = _parse_script(loose.group(1))
        if parsed is not None:
            clean = (text[:loose.start()] + after).strip()
            return parsed, clean
        loose = _LOOSE_SCRIPT_RE.search(text, loose.end())

    clean = text
    for m in _INTENT_RE.finditer(text):
        cmd = m.group(1)
        if cmd == "script":
            # 复合指令流：括号内是完整 JSON params（steps/vars/fail/max_steps）
            parsed = _parse_script(m.group(2))
            if parsed is None:
                _log_parse_error("script 解析或校验失败")
                clean = clean.replace(m.group(0), "").strip()
                continue
            clean = clean.replace(m.group(0), "").strip()
            return parsed, clean
        if cmd not in WHITELIST:
            # 容错：AI 偶发输出【cmd(attack(range=10))】这种嵌套包装，
            # 剥掉外层 cmd() 后按真实指令重新解析；否则静默丢弃。
            nested = _unwrap_cmd_wrapper(m.group(2))
            if nested is not None:
                n_cmd, n_params = nested
                if n_cmd in WHITELIST:
                    return n_params, clean.replace(m.group(0), "").strip()
            _log_parse_error("未知指令: %s" % cmd)
            clean = clean.replace(m.group(0), "").strip()
            continue
        try:
            params = _parse_params(m.group(2) or "")
        except MaidIntentError as e:
            _log_parse_error("参数解析失败 [%s]: %s" % (cmd, e))
            clean = clean.replace(m.group(0), "").strip()
            continue
        # cancel_previous 是 DSL 控制项，单独取出（不传给模组）
        cancel = params.pop("cancel_previous", False)
        cancel = bool(str(cancel).lower() in ("true", "1", "yes"))
        # 缺必填参数 → 丢弃（宁可不下发，不发畸形请求）
        miss = mod_contract.missing_required(cmd, params)
        if miss:
            _log_parse_error("缺少参数 [%s]: %s" % (cmd, "、".join(miss)))
            clean = clean.replace(m.group(0), "").strip()
            continue
        params, _dropped = mod_contract.clamp(cmd, params)
        clean = clean.replace(m.group(0), "").strip()
        return {"cmd": cmd, "params": params,
                "cancel_previous": cancel}, clean
    return None, clean


def _log_parse_error(msg):
    """写 crash.log（静默丢弃，不打断聊天）。"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "logs", "crash.log"),
                  "a", encoding="utf-8") as f:
            f.write("\n===== MaidIntentParseError =====\n%s\n" % msg)
    except Exception:
        pass
