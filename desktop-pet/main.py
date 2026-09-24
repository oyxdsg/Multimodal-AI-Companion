import datetime
import faulthandler
import os
import sys
import traceback

_BASE = os.path.dirname(os.path.abspath(__file__))
_LOGS = os.path.join(_BASE, "logs")
os.makedirs(_LOGS, exist_ok=True)


def _log_line(msg):
    try:
        with open(os.path.join(_LOGS, "startup.log"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] {msg}\n")
    except Exception:
        pass


_log_line(f"启动 PID={os.getpid()} python={sys.executable}")


def _excepthook(exc_type, exc_value, exc_tb):
    """把未捕获异常写入 crash.log，便于定位闪退。"""
    try:
        log = os.path.join(_LOGS, "crash.log")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {exc_type.__name__} =====")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook

# 插件宿主自检：`python main.py --doctor`（不开 GUI、不依赖 Qt）
if "--doctor" in sys.argv:
    from plugin.doctor import run_doctor

    sys.exit(run_doctor())

# 捕获 C++ 层致命崩溃（SIGSEGV / SIGABRT 等）的 Python 调用栈
_fh = open(os.path.join(_LOGS, "faulthandler.log"), "a", encoding="utf-8")
faulthandler.enable(file=_fh, all_threads=True)

from PySide6.QtWidgets import QApplication

from pet.window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 应用全局界面主题（宠物风格，唯一默认主题）
    from core import theme
    theme.apply_theme(app)

    def _on_quit():
        _log_line("Qt 事件循环退出")

    app.aboutToQuit.connect(_on_quit)

    pet = PetWindow()
    pet.show()
    if "--smoke" in sys.argv:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, app.quit)
    rc = app.exec()
    _log_line(f"事件循环结束 rc={rc}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
