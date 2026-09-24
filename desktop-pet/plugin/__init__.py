# -*- coding: utf-8 -*-
"""插件宿主（Host）—— 可选扩展的接入层。

设计见 ``DESIGN_OPTIONAL.md``。两条硬约束：

* **本包只依赖标准库**：不 import 主体内部模块（``pet`` / ``ai`` / ``game`` …），
  这样宿主可离线单测，插件也不会被内部实现绑死。
* **插件只通过** :class:`plugin.api.HostAPI` **注册**：不 import 主体源码。

对外只暴露 :data:`plugin.api.API_VERSION` 与插件契约；加载细节在
:mod:`plugin.loader`，自检入口在 :mod:`plugin.doctor`。
"""

from plugin.api import API_VERSION  # noqa: F401

__all__ = ["API_VERSION"]
