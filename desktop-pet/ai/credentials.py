"""DeepSeek 登录凭证的读写（独立模块）。

凭证只是 QSettings 键（`ai_token`/`ai_cookies`），**不属逆向实现**，故留在本体；
网页版客户端在可选插件 deepseek-web 内，它需要 load_credentials。
独立成模块可消除循环依赖。
"""

from PySide6.QtCore import QSettings

_STORE = QSettings("DesktopPet", "PetWindow")


def load_credentials():
    """读取 DeepSeek 凭证，返回 (token, cookies)。"""
    return (_STORE.value("ai_token", "") or "",
            _STORE.value("ai_cookies", "") or "")


def save_credentials(token, cookies):
    _STORE.setValue("ai_token", token)
    _STORE.setValue("ai_cookies", cookies)


def is_logged_in():
    token, _ = load_credentials()
    return bool(token)