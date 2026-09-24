"""Lucide 风格图标（SVG 内嵌渲染，借鉴 shadcn/ui 的图标语言）。

Lucide 为 MIT 许可的开源线条图标集（24×24 viewBox，stroke 描边）。
此处内嵌所需图标的 path 片段，用 QtSvg 渲染成 QIcon，零外部依赖。
用法：icon("settings", 18, "#595959") → QIcon
"""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Lucide 图标 path 片段（fill=none，stroke 由 make_icon 指定）
ICONS = {
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "arrow_left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "message_circle": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>',
    "message_square": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "palette": '<circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/><circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/><circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/><circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "cloud_sun": '<path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m19.07 4.93-1.41 1.41"/><path d="M15.947 12.65a4 4 0 0 0-5.925-4.128"/><path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/>',
    "newspaper": '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>',
    "briefcase": '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/>',
    "gamepad": '<line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/>',
    "sliders": '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>',
    "volume": '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
    "mic": '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
    "cat": '<path d="M12 5c.67 0 1.35.09 2 .26 1.78-2 5.03-2.84 6.42-2.26 1.4.58-.42 7-.42 7 .57 1.07 1 2.24 1 3.44C21 17.9 17 20 16 20s-1-2-2-2-1 2-2 2-5-2.1-5-5.56c0-1.2.43-2.37 1-3.44 0 0-1.82-6.42-.42-7 1.39-.58 4.64.26 6.42 2.26Z"/><path d="M5 14H3"/><path d="M6 10.5 4.5 9"/><path d="M18 14h2"/><path d="M18 10.5 19.5 9"/>',
    "whale": '<path d="M12 3c-4 0-7 2.5-7 6 0 2.2 1.2 4 3 5v3.5a1.5 1.5 0 0 0 3 0V15c.6.1 1.3.1 2 .1s1.4 0 2-.1v2.4a1.5 1.5 0 0 0 3 0V14c1.8-1 3-2.8 3-5 0-3.5-3-6-9-6z"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "paperclip": '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    "image": '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    # 下拉箭头（chevron）。展开态用朝上的同款，配合 QComboBox::down-arrow:on 做翻动。
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_up": '<path d="m18 15-6-6-6 6"/>',
    # 人格：一个干净的「人形」轮廓。原用的 cat 图标元素太多，16px 下糊成一团。
    "user_round": '<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>',
}


def icon(name, size=18, color="#595959", stroke_width=2):
    """渲染指定 Lucide 图标为 QIcon（2x 超采样，任意 DPI 下清晰完整）。

    - 渲染一份逻辑尺寸 size 的 2x 物理像素 pixmap（不含 devicePixelRatio
      hack），Qt 在按钮中以 setIconSize(size) 显示时按需平滑缩放，线条
      清晰无残影；Windows 125%/150% 缩放、高 DPI 屏均不受影响。
    - 用 renderer.render(painter, targetRect) 显式铺满整个 pixmap，避免
      无参 render 时与 devicePixelRatio 坐标错配导致内容被裁到一角。
    - viewBox 四周各留 stroke/2 边距，避免边缘线条被裁掉一半。
    """
    paths = ICONS.get(name)
    if not paths:
        raise KeyError(f"unknown icon: {name}")
    px = size * 2  # 2x 超采样物理像素（dpr=1）
    pad = max(1.0, stroke_width / 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
        f'viewBox="{-pad} {-pad} {24 + 2 * pad} {24 + 2 * pad}" '
        f'fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )
    pix = QPixmap(px, px)
    pix.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pix)
    renderer.render(painter, QRectF(0.0, 0.0, px, px))
    painter.end()
    return QIcon(pix)


def chevron_png_path(color="#59675E", up=False, size=16):
    """生成下拉箭头（v 形 chevron）PNG，供 QSS 的 `image: url()` 引用。

    为什么不用 QSS 的 border 三角 hack：那样只能画出**实心三角**，而且 Qt 会把它
    挤成一个小方块（实测）。WorkBuddy 用的是**描边 chevron**，所以这里落一张 PNG。

    展开态（`up=True`）由 `QComboBox::down-arrow:on` 引用，即点开时箭头翻转朝上。
    2x 物理像素 + stroke 2（等价 24 viewBox 里 2 → 显示 16px 时约 1.33px 描边），
    与设置页导航图标（16px / 同款 stroke）保持同样的视觉分量。
    """
    import os
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = os.path.join(base, "DesktopPet", "ui")
    os.makedirs(d, exist_ok=True)
    name = "chevron_%s_%s_%d.png" % ("up" if up else "down", color[1:], size)
    path = os.path.join(d, name)
    if os.path.exists(path):
        return os.path.normpath(path).replace("\\", "/")
    ic = icon("chevron_up" if up else "chevron_down", size, color)
    pix = ic.pixmap(size * 2, size * 2)
    if not pix.isNull():
        pix.save(path, "PNG")
    return os.path.normpath(path).replace("\\", "/")


def check_png_path(color="#1677FF", px=16):
    """生成「透明背景 + 指定颜色对勾」PNG 到本地应用数据目录，供 QSS 的
    QCheckBox::indicator:checked 的 image: url() 引用（选中为蓝底白勾）。

    返回绝对路径（正斜杠）。文件缓存复用；QSS 无法 data URI，故落盘。
    """
    import os
    import tempfile
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    d = os.path.join(base, "DesktopPet", "ui")
    os.makedirs(d, exist_ok=True)
    name = f"check_{color[1:]}_{px}.png"
    path = os.path.join(d, name)
    if os.path.exists(path):
        return os.path.normpath(path).replace("\\", "/")

    pad = 2.5  # 额外留白，让勾在方形内居中不顶框
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
        f'viewBox="{-pad} {-pad} {24 + 2 * pad} {24 + 2 * pad}" '
        f'fill="none" stroke="{color}" stroke-width="2.6" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{ICONS["check"]}</svg>'
    )
    pix = QPixmap(px, px)
    pix.fill(Qt.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
        pix.save(path, "PNG")
    return os.path.normpath(path).replace("\\", "/")
