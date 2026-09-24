# -*- coding: utf-8 -*-
"""插件发现与加载。

发现三个来源（优先级：目录 > pip > 内置，见 ``DESIGN_OPTIONAL.md`` §3.3）：

1. **内置**：``plugin/builtin/*.py``（第一方，随本体）。
2. **目录**：``<plugins_root>/<id>/plugin.json``（用户投放；发布版还含 ``%LOCALAPPDATA%``）。
3. **pip**：``importlib.metadata.entry_points(group="deskpet.plugins")``。

**铁律：任一插件失败都不影响宿主与其它插件**——解析 / import / ``register()``
全部单独 try/except，失败记入 :attr:`LoadResult.errors` 并跳过，绝不上抛。
插件本次注册写在独立暂存 registry，成功后才合并，故中途失败不会污染宿主。
"""

import importlib
import importlib.util
import os
import re
import sys
import traceback

from plugin import api
from plugin.manifest import MANIFEST_FILENAME, load_from_dir, parse, sort_key
from plugin.registry import HostRegistry, make_host_api

ENTRY_POINT_GROUP = "deskpet.plugins"


# ---------------------------------------------------------------- 结果

class LoadResult:
    def __init__(self):
        self.registry = HostRegistry()
        self.plugins = []      # list[PluginManifest] 成功加载
        self.errors = []       # list[(origin, message)] 隔离掉的失败
        self.entries = []      # list[dict(manifest, register, origin, declined)]
        self.disabled = []     # list[PluginManifest] 用户停用（发现但不注册）
        self.log_lines = []

    def log(self, msg):
        self.log_lines.append(str(msg))

    def plugin(self, pid):
        for m in self.plugins:
            if m.id == pid:
                return m
        return None


# ---------------------------------------------------------------- 默认目录与日志

def default_plugin_dirs():
    """内置候选目录：项目内 ``plugins/`` + 用户 ``%LOCALAPPDATA%\\DesktopPet\\plugins``。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dirs = [os.path.join(base, "plugins")]
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        dirs.append(os.path.join(appdata, "DesktopPet", "plugins"))
    return dirs


def default_logger(plugin_id, msg):
    """把插件日志追加到 ``logs/plugin.log``（失败静默，绝不抛）。"""
    try:
        logs = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "plugin.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (plugin_id, msg))
    except Exception:
        pass


# ---------------------------------------------------------------- 候选发现

def _iter_builtin():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin")
    if not os.path.isdir(d):
        return
    for name in sorted(os.listdir(d)):
        if name.endswith(".py") and name != "__init__.py":
            yield _make_builtin_loader(name[:-3])


def _make_builtin_loader(modname):
    origin = "builtin:" + modname

    def load():
        mod = importlib.import_module("plugin.builtin." + modname)
        data = dict(getattr(mod, "MANIFEST", None) or {})
        data.setdefault("entry", modname + ":register")
        man = parse(data, source="builtin")
        register = getattr(mod, "register", None)
        if not callable(register):
            raise api.ManifestError("内置插件缺少 register(host)")
        return man, register
    return origin, load


def _iter_dirs(roots):
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(p):
                continue
            if not os.path.isfile(os.path.join(p, MANIFEST_FILENAME)):
                continue
            yield "dir:" + p, _make_dir_loader(p)


def _make_dir_loader(path):
    def load():
        # 插件目录加入 sys.path，使插件可 `import wiki_kb` 等自带模块
        if path not in sys.path:
            sys.path.insert(0, path)
        man = load_from_dir(path, source="dir")
        fname, attr = man.entry.split(":", 1)
        fp = os.path.join(path, fname + ".py")
        if not os.path.isfile(fp):
            fp = os.path.join(path, fname)
        if not os.path.isfile(fp):
            raise api.ManifestError("entry 指向的文件不存在：%s" % man.entry)
        dyn = "deskpet_plugin_" + re.sub(r"\W", "_", man.id)
        spec = importlib.util.spec_from_file_location(dyn, fp)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[dyn] = mod
        spec.loader.exec_module(mod)
        register = getattr(mod, attr, None)
        if not callable(register):
            raise api.ManifestError("entry 属性不可调用：%s" % man.entry)
        return man, register
    return load


def _iter_entry_points():
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:                       # Python < 3.10 兼容形态
        eps = entry_points().get(ENTRY_POINT_GROUP, [])
    for ep in eps:
        yield "pip:" + getattr(ep, "name", "?"), _make_ep_loader(ep)


def _make_ep_loader(ep):
    def load():
        obj = ep.load()
        register = obj
        mod = None
        if not callable(obj):
            mod = obj
            register = getattr(obj, "register", None)
        else:
            mod = sys.modules.get(getattr(obj, "__module__", ""))
        if not callable(register):
            raise api.ManifestError("entry point 未提供 register(host)")
        man_dict = getattr(mod, "MANIFEST", None)
        if man_dict is None:
            raise api.ManifestError("pip 插件缺少 MANIFEST（需在被导入模块内声明）")
        data = dict(man_dict)
        data.setdefault("entry", (getattr(ep, "value", "") or "pip:register"))
        man = parse(data, source="entrypoint")
        return man, register
    return load


# ---------------------------------------------------------------- 加载主流程

def load_all(plugin_dirs=None, use_entry_points=True, logger=None,
             registry=None, disabled=()):
    """发现并加载全部插件，返回 :class:`LoadResult`（永不抛）。

    ``disabled``：用户停用的插件 id；这些插件仍被发现，但不注册，
    状态记在 :attr:`LoadResult.disabled`。
    """
    res = LoadResult()
    disabled = set(disabled or ())
    if registry is not None:
        res.registry = registry
    if logger is not None:
        res.log = logger
    log = res.log

    roots = default_plugin_dirs() if plugin_dirs is None else list(plugin_dirs)

    candidates = []
    candidates.extend(_iter_builtin())
    candidates.extend(_iter_dirs(roots))
    if use_entry_points:
        candidates.extend(_iter_entry_points())

    # 第一遍：解析清单 + 拿到 register（不执行），失败隔离
    for origin, load in candidates:
        try:
            man, register = load()
            res.entries.append({"manifest": man, "register": register,
                                "origin": origin, "declined": False})
        except Exception as e:
            res.errors.append((origin, _fmt_err(e)))
            log("插件解析失败 %s：%s" % (origin, _fmt_err(e)))

    # 同 id 去重：保留排序键最大者（目录 > pip > 内置），落选者记 declined
    best = {}
    for ent in res.entries:
        pid = ent["manifest"].id
        cur = best.get(pid)
        if cur is None or sort_key(ent["manifest"]) > sort_key(cur["manifest"]):
            if cur is not None:
                cur["declined"] = True
            best[pid] = ent
        else:
            ent["declined"] = True

    # 第二遍：逐个注册（暂存 + 合并，天然回滚）
    for ent in res.entries:
        if ent["declined"]:
            log("插件 %s 被更高优先级来源覆盖，跳过" % ent["manifest"].id)
            continue
        man = ent["manifest"]
        if man.id in disabled:
            res.disabled.append(man)
            log("插件 %s 已被用户停用，跳过" % man.id)
            continue
        try:
            staging = HostRegistry()
            host = make_host_api(staging, man.id, priority=sort_key(man),
                                 logger=default_logger)
            ent["register"](host)
            res.registry.merge(staging)
            res.plugins.append(man)
            log("已加载插件 %s（%s / %s）" % (man.id, man.source,
                                            ",".join(man.types)))
        except Exception as e:
            res.errors.append((ent["origin"], _fmt_err(e)))
            log("插件注册失败 %s：%s" % (man.id, _fmt_err(e)))
    return res


def _fmt_err(e):
    line = traceback.format_exc().strip().splitlines()[-1]
    return line if line else "%s: %s" % (type(e).__name__, e)
