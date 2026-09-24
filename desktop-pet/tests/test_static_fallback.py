# -*- coding: utf-8 -*-
"""静态模式（无动画素材不崩）测试（P1，见 DESIGN_OPTIONAL.md）。

- S1 `_list_png` 容错（目录不存在返回 []）
- S2 静态回退：所有动作 key 得到 1 帧静态图，`_frame_size` 有效
- S3 静态帧重建：不叠加、不崩、每个 key 都有帧
- S4 连静态图都取不到时走程序占位兜底

离屏运行（QT_QPA_PLATFORM=offscreen）。运行：python tests/test_static_fallback.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import config  # noqa: E402
from pet.anim import PetAnimationMixin, _list_png  # noqa: E402
from plugin import host as plugin_host  # noqa: E402


def _fail(msg):
    raise AssertionError(msg)


class _Dummy(PetAnimationMixin):
    """只承载动画 Mixin 的轻量对象（不构造整窗）。"""

    def __init__(self):
        self._actions_map = {k: {"dir": v, "label": k}
                             for k, v in config.ACTIONS.items()}
        self.brightness = 0
        self.contrast = 1.0
        self.saturation = 1.0


def test_list_png():
    assert _list_png("C:/no/such/dir/definitely-missing") == []
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "frame_0000.png"), "wb").close()
        open(os.path.join(d, "note.txt"), "w", encoding="utf-8").close()
        assert _list_png(d) == ["frame_0000.png"]
    print("  [S1] _list_png 目录缺失/过滤 OK")


def test_static_fallback():
    d = _Dummy()
    d._load_static_fallback()
    assert getattr(d, "_static_mode", False), "应进入静态模式"
    tw, th = d._frame_size
    assert tw > 0 and th > 0, d._frame_size
    assert set(d.actions) == set(config.ACTIONS), "每个动作 key 都应有静态帧"
    for key, act in d.actions.items():
        assert act["frames"], key
    d._rebuild_frame_cache()
    assert set(d._frame_cache) >= set(config.ACTIONS)
    for key in config.ACTIONS:
        assert d._frame_cache[key], key
    print("  [S2/S3] 静态回退 + 静态帧重建 OK（%dx%d）" % (tw, th))


def test_placeholder_when_no_figure():
    d = _Dummy()
    orig = plugin_host.static_figure
    plugin_host.static_figure = lambda: None
    try:
        d._load_static_fallback()
    finally:
        plugin_host.static_figure = orig
    assert getattr(d, "_static_mode", False)
    assert d._frame_size[0] > 0 and d._frame_size[1] > 0
    assert d.actions
    print("  [S4] 无静态图 → 程序占位兜底 OK")


def test_full_load_without_assets():
    """完整 `_load_actions`：素材目录为空（只有静态图可用）→ 静态模式不崩。"""
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(tmp, "assets")
        os.makedirs(os.path.join(assets, "_static"))
        real_fig = os.path.join(config.ASSETS_DIR, "_static", "idle.png")
        if os.path.isfile(real_fig):
            shutil.copy(real_fig, os.path.join(assets, "_static", "idle.png"))
        orig_a = config.ASSETS_DIR
        orig_b = config.BASE_FRAMES_DIR
        config.ASSETS_DIR = assets
        config.BASE_FRAMES_DIR = os.path.join(assets, ".base_frames")
        try:
            d = _Dummy()
            d._load_actions()
            assert getattr(d, "_static_mode", False), "空素材目录应进入静态模式"
            assert d._frame_size[0] > 0 and d._frame_size[1] > 0
            assert set(d.actions) == set(config.ACTIONS)
        finally:
            config.ASSETS_DIR = orig_a
            config.BASE_FRAMES_DIR = orig_b
    print("  [S5] 完整 _load_actions（无动画目录）不崩、进入静态模式 OK")


def main():
    print("=" * 62)
    print("静态模式测试（P1）")
    print("=" * 62)
    test_list_png()
    test_static_fallback()
    test_placeholder_when_no_figure()
    test_full_load_without_assets()
    print("\n全部通过")


if __name__ == "__main__":
    main()
