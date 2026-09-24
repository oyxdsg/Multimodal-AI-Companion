"""工作模式：检测前台窗口，把原始信息交给 AI 判断并生成提示。

- 用 Windows API（ctypes，无第三方依赖）获取前台窗口标题、进程名与路径
- 不做任何预设映射/文件推断，提示完全由 AI 生成
- 仅在 Windows 上可用；其他平台 detect() 返回 None
"""

import ctypes
import os
from ctypes import wintypes

try:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
except (AttributeError, OSError):   # 非 Windows
    _user32 = None
    _kernel32 = None

# 传给 AI 的标题最大长度（避免过长）
_TITLE_MAX = 60


def _get_foreground():
    """返回前台窗口 (标题, 进程PID)；无前台窗口返回 (None, None)。"""
    if _user32 is None:
        return None, None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return title, pid.value


def _process_info(pid):
    """根据 PID 获取 (进程名, 完整路径)；失败返回 ('', '')。"""
    if not pid or _kernel32 is None:
        return "", ""
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "", ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                path = buf.value
                return os.path.basename(path), path
        finally:
            _kernel32.CloseHandle(handle)
    except Exception:
        pass
    return "", ""


def detect():
    """检测前台窗口，返回 {title, process, path}；前台不可用返回 None。

    信息保持原始，不预设软件类型，由 AI 自行判断用户正在做什么。
    """
    try:
        title, pid = _get_foreground()
    except Exception:
        return None
    if not title:
        return None
    process, path = _process_info(pid)
    return {
        "title": title[:_TITLE_MAX],
        "process": process,
        "path": path,
    }