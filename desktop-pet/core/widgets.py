"""可复用控件与气泡组件：宠物窗口与聊天窗口共享。

- NoWheelComboBox：禁用滚轮 + 修复宠物 TOPMOST 小窗下下拉框弹出即消失
- AIBubble：从宠物身上冒出的对话气泡（白底圆角 + 尾巴 + 自动消失）

原散落在 pet/window.py / ai/chat.py 的重复定义收敛于此，消除两处维护。
"""

from PySide6.QtCore import QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor
from PySide6.QtWidgets import QComboBox, QLabel, QWidget


class NoWheelComboBox(QComboBox):
    """禁用滚轮切换 + 修复无边框/宠物置顶窗口下下拉框点不到、弹出即消失的问题。

    另外负责下拉箭头的「翻动」：弹出时换成朝上的 chevron，收回时换回朝下。
    用自维护的动态属性 `popupOpen` 驱动 QSS（`QComboBox[popupOpen="true"]::down-arrow`），
    不用 `:on` —— 实测 QComboBox 弹出时虽然会置 State_On，但**不会重绘本体**，
    `QComboBox::down-arrow:on` 的图因此画不出来。
    """

    def wheelEvent(self, event):
        event.ignore()

    def _mark_popup(self, opened):
        """切换 popupOpen 属性并重新 polish，让 QSS 立刻换掉箭头图。"""
        self.setProperty("popupOpen", "true" if opened else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def showPopup(self):
        super().showPopup()
        self._mark_popup(True)
        try:
            win = self.view().window() if self.view() is not None else None
            if win is not None and win is not self.window():
                # 下拉弹层单独置顶、无边框，避免被宠物 TOPMOST 小窗盖住/失活关闭
                win.setWindowFlags(win.windowFlags()
                                   | Qt.WindowStaysOnTopHint
                                   | Qt.FramelessWindowHint)
                win.show()
                win.raise_()
                win.activateWindow()
        except Exception:
            pass

    def hidePopup(self):
        super().hidePopup()
        self._mark_popup(False)


class AIBubble(QWidget):
    """从宠物身上冒出的对话气泡：白底圆角 + 渐变 + 小尾巴指向宠物。
    自动消失，点击关闭，跟随宠物移动。"""

    HIDE_MS = 8000
    MAX_WIDTH = 280
    closed = Signal()      # 用户点击关闭时发出（便于队列立即播下一条）

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._error = False
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(self.MAX_WIDTH)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text, error=False):
        self._error = error
        self._label.setText(text)
        self._label.setStyleSheet(
            "background: transparent; color: rgba(0,0,0,0.88); font-size: 14px;"
            " text-align: center;"
            if not error else
            "background: transparent; color: #FF4D4F; font-size: 14px;"
            " text-align: center;"
        )
        fm = self._label.fontMetrics()
        rect = fm.boundingRect(QRect(0, 0, self.MAX_WIDTH, 100000),
                               int(Qt.TextWordWrap), text)
        pad = 12
        tail = 9
        w = min(max(rect.width() + pad * 2, 60), self.MAX_WIDTH + pad * 2)
        h = rect.height() + pad * 2 + tail
        self._label.setGeometry(pad, pad, w - pad * 2, rect.height())
        self._label.setAlignment(Qt.AlignCenter)
        self.setFixedSize(w, h)
        self._hide_timer.start(self.HIDE_MS)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        tail = 9
        body = QRectF(0.5, 0.5, w - 1, h - 1 - tail)
        path = QPainterPath()
        path.addRoundedRect(body, 8.0, 8.0)
        cx = w / 2
        path.moveTo(cx - 9, h - 1 - tail)
        path.lineTo(cx, h - 1)
        path.lineTo(cx + 9, h - 1 - tail)
        path.closeSubpath()
        if self._error:
            brush = QColor(255, 242, 240)   # #FFF2F0 错误浅底
            pen = QColor(255, 204, 199)     # #FFCCC7 错误描边
        else:
            brush = QColor(255, 255, 255)
            pen = QColor(217, 217, 217)     # #D9D9D9 控件描边
        painter.setPen(QPen(pen, 1))
        painter.setBrush(brush)
        painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event):
        self.hide()
        self.closed.emit()
        super().mousePressEvent(event)