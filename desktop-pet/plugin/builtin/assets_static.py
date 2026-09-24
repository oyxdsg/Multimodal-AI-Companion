# -*- coding: utf-8 -*-
"""内置插件 · 静态图素材兜底。

对应 ``DESIGN_OPTIONAL.md`` §五：完整动画素材外置后，本体只随包**一张静态图**
（大肥鱼待机帧，已确认完全开源）。本插件声明该静态图的存在，供动画系统在
无动画素材时进入"静态模式"。

P0 阶段只提供接口；真正接管渲染是 P1（``assets`` 扩展点接入 ``pet/anim.py``）。
"""

import os

MANIFEST = {
    "id": "assets-static",
    "name": "内置静态图素材（大肥鱼待机帧）",
    "version": "1.0.0",
    "type": ["assets"],
    "description": "无动画素材时显示的静态形象；完整动画由素材插件提供",
}

_STATIC_REL = os.path.join("assets", "_static", "idle.png")


def _static_path():
    base = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    return os.path.join(base, _STATIC_REL)


class _StaticAssets:
    def actions(self):
        # 本插件不提供动作帧（静态图由 static_figure 表达）
        return None

    def static_figure(self):
        p = _static_path()
        return p if os.path.isfile(p) else None


def register(host):
    host.add_assets(_StaticAssets())
