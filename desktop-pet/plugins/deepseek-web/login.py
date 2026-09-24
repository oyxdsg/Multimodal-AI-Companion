"""独立登录进程：由主程序以子进程方式启动。

通过 playwright 驱动系统 Edge，使用【持久化 profile】自动获取 DeepSeek 登录凭证：
1. 先无头静默检查该 profile 是否已登录，已登录则直接获取，无窗口打扰；
2. 未登录则弹出 Edge 窗口等待用户登录，登录成功后自动检测并获取；
3. 支持用户点「我已登录」按钮强制完成（检测兜底）。

token 检测同时覆盖 localStorage 与 Cookie 两种存储方式。

用法：python ai/login.py <edge路径> <输出json路径>
"""

import json
import os
import sys
import time
import traceback

_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "login_debug.log")


def _log(msg):
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def _write(out_path, result):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


def _profile_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "DesktopPet", "edge_profile")
    os.makedirs(d, exist_ok=True)
    return d


def _extract_token(value):
    if not value or value == "null":
        return None
    if isinstance(value, str) and value.startswith("{"):
        try:
            obj = json.loads(value)
            v = obj.get("value")
            if isinstance(v, str) and v and v != "null" and len(v) > 20:
                return v
        except Exception:
            return None
        return None
    if isinstance(value, str) and value and value != "null" and len(value) > 20:
        return value
    return None


def _cookies_map(context):
    try:
        out = {}
        for c in context.cookies("https://chat.deepseek.com"):
            out[c["name"].lower()] = c["value"]
        return out
    except Exception:
        return {}


def _detect_token(page, context):
    try:
        raw = page.evaluate("localStorage.getItem('userToken')")
        t = _extract_token(raw)
        if t:
            return t
    except Exception:
        pass
    ck = _cookies_map(context)
    return _extract_token(ck.get("usertoken", ""))


def _cookie_header(context):
    return "; ".join(
        f"{k}={v}" for k, v in _cookies_map(context).items()
        if k != "usertoken"
    )


def main():
    edge_path = sys.argv[1]
    out_path = sys.argv[2]
    force_path = out_path + ".force"
    profile = _profile_dir()
    result = {"ok": False, "msg": "未知错误"}
    _log(f"=== 登录进程启动 edge={edge_path} ===")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _log("playwright 未安装")
        result = {"ok": False, "msg": "未安装 playwright，请运行：pip install playwright"}
        _write(out_path, result)
        return

    try:
        with sync_playwright() as p:
            _log("静默检查登录状态…")
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                executable_path=edge_path,
                headless=True,
                args=["--disable-gpu"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://chat.deepseek.com",
                      timeout=60000, wait_until="domcontentloaded")
            token = _detect_token(page, ctx)
            if token:
                result = {"ok": True, "token": token,
                          "cookies": _cookie_header(ctx)}
                ctx.close()
                _log("已登录，静默获取 token 完成")
                _write(out_path, result)
                return
            ctx.close()
            _log("未登录，准备弹出窗口等待用户登录")

            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile,
                executable_path=edge_path,
                headless=False,
                args=["--disable-gpu"],
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://chat.deepseek.com",
                      timeout=60000, wait_until="domcontentloaded")
            _log("窗口已打开，等待用户登录…")

            deadline = time.time() + 5 * 60
            token = None
            last_diag = 0.0
            while time.time() < deadline:
                if os.path.exists(force_path):
                    _log("用户点击「我已登录」，强制检测")
                    token = _detect_token(page, ctx)
                    if token:
                        _log("强制检测成功")
                    break
                token = _detect_token(page, ctx)
                if token:
                    _log("检测到登录成功")
                    break
                if time.time() - last_diag >= 5:
                    last_diag = time.time()
                    try:
                        raw = page.evaluate("localStorage.getItem('userToken')")
                    except Exception:
                        raw = "<evaluate失败>"
                    _log(f"轮询中 localStorage={str(raw)[:50]} "
                         f"cookies={list(_cookies_map(ctx).keys())}")
                time.sleep(1)

            if token is None:
                _log("等待登录超时或未检测到 token")
                result = {"ok": False,
                          "msg": "未检测到登录成功。请在 Edge 窗口中确认已登录，"
                                 "然后点击「我已登录」按钮完成。"}
            else:
                result = {"ok": True, "token": token,
                          "cookies": _cookie_header(ctx)}
                _log("已获取 token，登录完成")
            ctx.close()
    except Exception as e:
        _log("发生异常：\n" + traceback.format_exc())
        result = {"ok": False, "msg": f"自动登录失败：{e}"}

    _log("写入结果")
    _write(out_path, result)


if __name__ == "__main__":
    main()