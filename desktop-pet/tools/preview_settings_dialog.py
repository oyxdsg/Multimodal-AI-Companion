"""不启动桌宠，直接把「设置」对话框渲染成 PNG，用于改 UI 后目视核对。

为什么需要它：桌宠是常驻进程，改设置界面后如果要看效果就得启停主程序，
既慢又干扰用户。本脚本离屏构造 SettingsDialog 自己截图，全程不碰键盘鼠标、
不出现窗口（WA_DontShowOnScreen）。

用法（在 desktop-pet 目录下，**必须用系统 Python**，只有它有 PySide6）：
    python tools/preview_settings_dialog.py                # 默认 4 页
    python tools/preview_settings_dialog.py 3 7 14         # 指定导航行号
    python tools/preview_settings_dialog.py --out D:\\tmp   # 指定输出目录

输出：png/设置_<行号>_<页名>.png，PNG 底色铺成中性灰绿，方便看清圆角与投影。

踩坑备忘（改这里之前先读）：
  * 不要用 QT_QPA_PLATFORM=offscreen 取字体 —— offscreen 平台字体数为 0，
    中文全部渲染成方框，看着像坏了其实不是。用 WA_DontShowOnScreen 走真实平台。
  * QScrollArea.setWidget() 会强制给内部控件打开 autoFillBackground，
    必须在 setWidget 之后再关，否则页面按调色板自绘、盖掉卡片的白色。
"""

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QListWidget

import config
from core import theme


class _PetStub:
    """喂给 SettingsDialog 的假 pet：只为让宠物页能构建出来。"""

    brightness = 0
    contrast = 1.0
    saturation = 1.0
    _chatline_enabled = True
    _chatline_freq = 1
    _env_enabled = True
    _manual_city = ""
    _news_enabled = True
    _news_freq_min = 60
    _news_feeds = [{"name": "央视新闻", "url": "https://example.com/feed.xml"}]
    _work_enabled = False
    _work_freq_min = 30
    _game_type = 1
    _game_log_path = ""
    _game_freq = 5
    _game_source = 0
    _game_normal_summary_min = 60
    _game_chat_respond = True
    _xiuxian_game_dir = ""

    def _start_xiuxian(self):
        pass

    def _stop_xiuxian(self):
        pass

    def apply_pet_settings(self, opts):
        pass


def main():
    args = [a for a in sys.argv[1:]]
    out_dir = os.path.join(BASE, "png")
    if "--out" in args:
        i = args.index("--out")
        out_dir = args[i + 1]
        del args[i:i + 2]
    rows = [int(a) for a in args if a.isdigit()] or [1, 7, 9, 14]
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication([])
    theme.apply_theme(app)

    from pet.settings_dialog import SettingsDialog

    dlg = SettingsDialog(pet=_PetStub(), chat=None)
    dlg.resize(900, 690)
    dlg.setAttribute(Qt.WA_DontShowOnScreen, True)   # 布局生效但不上屏
    dlg.show()
    app.processEvents()

    nav = dlg.findChild(QListWidget, "settingsNav")
    for row in rows:
        if row < 0 or row >= nav.count():
            print("跳过越界行号 %d（共 %d 行）" % (row, nav.count()))
            continue
        nav.setCurrentRow(row)
        app.processEvents()
        label = nav.item(row).text() or "组"
        path = os.path.join(out_dir, "设置_%02d_%s.png" % (row, label))
        canvas = QPixmap(dlg.size())
        canvas.fill(QColor("#C9D2C6"))
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, dlg.grab())
        painter.end()
        canvas.save(path)
        print("已输出 %s" % path)

    dlg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
