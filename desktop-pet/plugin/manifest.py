# -*- coding: utf-8 -*-
"""插件清单（``plugin.json``）解析与校验。

清单字段见 ``DESIGN_OPTIONAL.md`` §3.2。解析**绝不抛未捕获异常给主流程**：
非法清单变 :class:`plugin.api.ManifestError`，由 loader 隔离并记日志。
"""

import json
import os
from dataclasses import dataclass, field

from plugin import api

MANIFEST_FILENAME = "plugin.json"


@dataclass(frozen=True)
class PluginManifest:
    id: str
    types: tuple
    entry: str
    api_version: int = api.API_VERSION
    name: str = ""
    version: str = ""
    requires: tuple = ()
    priority: int = 0
    selectable: bool = True
    description: str = ""
    #: 来源：builtin / dir / entrypoint（由 loader 填，用于排序与展示）
    source: str = ""
    #: 插件根目录（目录插件）；内置/pip 插件为空
    path: str = ""
    #: 原始清单（便于 doctor 展示，不做修改）
    raw: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "api_version": self.api_version, "types": list(self.types),
            "entry": self.entry, "requires": list(self.requires),
            "priority": self.priority, "selectable": self.selectable,
            "description": self.description, "source": self.source,
            "path": self.path,
        }


def _as_str_list(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise api.ManifestError("字段类型应为字符串数组：%r" % (value,))


def parse(dict_like, *, source="", path=""):
    """把清单 dict 解析成 :class:`PluginManifest`；非法即抛 ManifestError。"""
    if not isinstance(dict_like, dict):
        raise api.ManifestError("清单必须是 JSON 对象")

    pid = str(dict_like.get("id") or "").strip()
    if not pid:
        raise api.ManifestError("缺少 id")

    types = _as_str_list(dict_like.get("type") or dict_like.get("types"))
    known = [t for t in types if t in api.TYPES]
    if not known:
        raise api.ManifestError(
            "type 缺失或不含已知扩展点（已知：%s）" % ", ".join(api.TYPES))

    entry = str(dict_like.get("entry") or "").strip()
    if not entry:
        raise api.ManifestError("缺少 entry（形如 module:attr）")
    if ":" not in entry:
        raise api.ManifestError("entry 应为 module:attr 形式：%r" % entry)

    raw_api = dict_like.get("api_version", api.API_VERSION)
    try:
        api_version = int(raw_api)
    except (TypeError, ValueError):
        raise api.ManifestError("api_version 必须是整数：%r" % (raw_api,))
    if api_version != api.API_VERSION:
        raise api.ManifestError(
            "需要宿主 API v%s，当前 v%s" % (api_version, api.API_VERSION))

    try:
        priority = int(dict_like.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0

    return PluginManifest(
        id=pid,
        types=tuple(known),
        entry=entry,
        api_version=api_version,
        name=str(dict_like.get("name") or pid),
        version=str(dict_like.get("version") or ""),
        requires=_as_str_list(dict_like.get("requires")),
        priority=priority,
        selectable=bool(dict_like.get("selectable", True)),
        description=str(dict_like.get("description") or ""),
        source=source,
        path=path,
        raw=dict(dict_like),
    )


def load_from_dir(path, *, source="dir"):
    """从插件目录读取 ``plugin.json`` 并解析。"""
    fp = os.path.join(path, MANIFEST_FILENAME)
    if not os.path.isfile(fp):
        raise api.ManifestError("缺少 %s" % MANIFEST_FILENAME)
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise api.ManifestError("读取 %s 失败：%s" % (MANIFEST_FILENAME, e))
    return parse(data, source=source, path=path)


#: 来源基准分：目录插件 > pip 插件 > 内置（见 §3.3 优先级）
SOURCE_BASE = {"dir": 200, "entrypoint": 100, "builtin": 0, "": 0}


def sort_key(manifest):
    """同扩展点排序键（越大越优先）：来源基准分 + 插件自带 priority。"""
    return SOURCE_BASE.get(manifest.source, 0) + int(manifest.priority or 0)
