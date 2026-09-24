# -*- coding: utf-8 -*-
"""生成内置静态图 ``assets/_static/idle.png``（大肥鱼待机帧）。

从 idle 动作目录取一帧 → 裁透明边 → 缩放到 ``config.TARGET_HEIGHT`` → 保存。
该图是**本体唯一随包的美术资源**，完整动画外置后由它充当静态形象。

用法：python tools/make_static_figure.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap


def _content_box(img):
    """用 alpha 通道估算内容包围盒。"""
    small = img.convertToFormat(QImage.Format_ARGB32).scaled(
        96, 96, Qt.KeepAspectRatio, Qt.FastTransformation)
    data = small.bits().tobytes()
    sw, sh = small.width(), small.height()
    stride = small.bytesPerLine()
    import numpy as np
    arr = np.frombuffer(data, np.uint8).reshape(sh, stride)
    alpha = arr[:, 3:sw * 4:4]
    ys, xs = np.nonzero(alpha)
    if xs.size == 0:
        return None
    sx, sy = img.width() / sw, img.height() / sh
    return (int(xs.min() * sx), int(ys.min() * sy),
            int((xs.max() - xs.min() + 1) * sx),
            int((ys.max() - ys.min() + 1) * sy))


def main():
    idle_dir = os.path.join(config.ASSETS_DIR, config.ACTIONS["idle"])
    files = sorted(f for f in os.listdir(idle_dir) if f.lower().endswith(".png"))
    if not files:
        print("未找到 idle 帧：%s" % idle_dir)
        return 1
    # 取中段一帧（首帧可能有入场姿态，中段更稳定）
    src = os.path.join(idle_dir, files[len(files) // 2])
    img = QImage(src)
    if img.isNull():
        print("无法读取：%s" % src)
        return 1
    box = _content_box(img)
    if box:
        img = img.copy(box[0], box[1], box[2], box[3])

    h = int(config.TARGET_HEIGHT)
    w = max(1, round(img.width() * h / img.height()))
    scaled = img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    out_dir = os.path.join(config.ASSETS_DIR, "_static")
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "idle.png")
    # 用 QImage 直接存（无需 QApplication / QPixmap），保留 ARGB 透明
    ok = scaled.save(dst, "PNG")
    size_kb = os.path.getsize(dst) / 1024.0 if os.path.isfile(dst) else 0
    print("已生成 %s（%dx%d，%.1f KB）源帧=%s"
          % (dst, scaled.width(), scaled.height(), size_kb, files[len(files) // 2]))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
