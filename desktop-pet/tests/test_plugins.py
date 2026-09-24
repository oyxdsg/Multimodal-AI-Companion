# -*- coding: utf-8 -*-
"""插件宿主骨架测试（P0，见 DESIGN_OPTIONAL.md）。

无框架纯断言，风格同 test_providers.py。

- P1 清单校验（合法 / 缺字段 / 未知 type / entry 格式 / api_version 不符）
- P2 注册表优先级与"暂存后合并"语义
- P3 内置插件与扩展点齐全（无插件也能用的最小闭环）
- P4 目录插件发现与加载
- P5 失败隔离（坏插件不影响宿主与其它插件）
- P6 同 id 去重（目录 > 内置）
- P7 pip entry_points 发现
- P8 --doctor 输出

运行：python tests/test_plugins.py
"""

import importlib.metadata as md
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugin import api
from plugin import manifest as M
from plugin.loader import load_all
from plugin.registry import HostRegistry


def _fail(msg):
    raise AssertionError(msg)


def _write_plugin(root, pid, types_, register_src, *, priority=0,
                  api_version=1, entry="plugin:register"):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    man = {"id": pid, "name": pid, "version": "0.0.1",
           "api_version": api_version, "type": list(types_),
           "entry": entry, "priority": priority}
    with open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(register_src)
    return d


# ---------------- P1 清单校验 ----------------

def test_manifest_validation():
    good = M.parse({"id": "x", "type": ["assets"], "entry": "p:r"})
    assert good.id == "x" and good.types == ("assets",)
    assert M.sort_key(good) == 0

    bads = [
        {},                                                    # 空
        {"id": "x"},                                           # 缺 type/entry
        {"id": "x", "type": ["nope"], "entry": "p:r"},         # 未知 type
        {"id": "x", "type": ["assets"], "entry": "nocolon"},   # entry 无冒号
        {"id": "x", "type": ["assets"], "entry": "p:r", "api_version": 99},
    ]
    for b in bads:
        try:
            M.parse(b)
            _fail("应拒绝非法清单：%r" % (b,))
        except api.ManifestError:
            pass
    # 来源基准分：目录 > pip > 内置
    d = M.parse({"id": "x", "type": ["assets"], "entry": "p:r"}, source="dir")
    p = M.parse({"id": "x", "type": ["assets"], "entry": "p:r"}, source="entrypoint")
    b = M.parse({"id": "x", "type": ["assets"], "entry": "p:r"}, source="builtin")
    assert M.sort_key(d) > M.sort_key(p) > M.sort_key(b) == 0
    print("  [P1] 清单校验与来源基准分 OK")


# ---------------- P2 注册表优先级 ----------------

def test_registry_priority():
    reg = HostRegistry()

    class W:
        def __init__(self, tag):
            self.tag = tag

        def know(self, t, **k):
            return self.tag

    reg.add_wiki_knowledge("low", 0, W("low"))
    reg.add_wiki_knowledge("high", 200, W("high"))
    reg.add_wiki_knowledge("mid", 100, W("mid"))
    assert reg.wiki_source() == "high"
    assert reg.wiki_knowledge().know("") == "high"

    # 空注册表 → 无实现（= 不注入）
    empty = HostRegistry()
    assert empty.wiki_knowledge() is None
    assert empty.assets() is None

    # 参数校验
    for bad_call in (
        lambda: reg.add_ai_provider("p", 0, None),
        lambda: reg.add_ai_provider("p", 0, types.SimpleNamespace(id="x")),
        lambda: reg.add_wiki_knowledge("p", 0, object()),
        lambda: reg.add_assets("p", 0, object()),
    ):
        try:
            bad_call()
            _fail("应拒绝非法注册")
        except api.HostAPIError:
            pass
    print("  [P2] 注册表优先级与参数校验 OK")


# ---------------- P3 内置插件 ----------------

def test_builtin_extensions():
    res = load_all(plugin_dirs=[], use_entry_points=False)
    assert not res.errors, "内置插件不应出错：%s" % res.errors
    ids = set(res.registry.ai_profile_ids())
    assert {"deepseek-api", "qwen", "anthropic", "custom"} <= ids, ids
    assert res.registry.wiki_knowledge() is not None, "缺 wiki 默认实现"
    assert res.registry.assets() is not None, "缺 assets 默认实现"
    # 内置 wiki 是"不注入"
    assert res.registry.wiki_knowledge().know("") in (None, "")
    # 内置三插件都在
    for pid in ("ai-official", "wiki-none", "assets-static"):
        assert res.plugin(pid) is not None, pid
    print("  [P3] 内置三插件 + 三扩展点最小闭环 OK")


# ---------------- P4 目录插件 ----------------

def test_dir_plugin():
    src = ("class W:\n"
           "    def know(self, t, **k):\n"
           "        return 'X'\n"
           "def register(host):\n"
           "    host.add_wiki_knowledge(W())\n")
    with tempfile.TemporaryDirectory() as tmp:
        _write_plugin(tmp, "my-wiki", ["wiki-knowledge"], src)
        res = load_all(plugin_dirs=[tmp], use_entry_points=False)
        assert res.plugin("my-wiki") is not None, res.errors
        assert res.registry.wiki_source() == "my-wiki"
        assert res.registry.wiki_knowledge().know("") == "X"
    print("  [P4] 目录插件发现 / 加载 / 覆盖内置 OK")


# ---------------- P5 失败隔离 ----------------

def test_failure_isolation():
    good = ("class A:\n"
            "    def actions(self):\n"
            "        return None\n"
            "    def static_figure(self):\n"
            "        return None\n"
            "def register(host):\n"
            "    host.add_assets(A())\n")
    bad = "def register(host):\n    raise RuntimeError('boom')\n"
    with tempfile.TemporaryDirectory() as tmp:
        _write_plugin(tmp, "good", ["assets"], good)
        _write_plugin(tmp, "bad", ["assets"], bad)
        # 只有告警：坏清单不进 errors 也算隔离
        res = load_all(plugin_dirs=[tmp], use_entry_points=False)
        assert res.plugin("good") is not None, res.errors
        assert res.plugin("bad") is None
        assert any("bad" in o for o, _ in res.errors), res.errors
        # 坏插件不得污染注册表（assets 仍来自 good）
        assert res.registry.assets_source() == "good"
    print("  [P5] 失败隔离（坏插件不影响宿主）OK")


# ---------------- P6 同 id 去重 ----------------

def test_same_id_dir_overrides_builtin():
    src = ("from types import SimpleNamespace\n"
           "def register(host):\n"
           "    host.add_ai_provider(profile=SimpleNamespace(id='dir-prof', protocol='openai'))\n")
    with tempfile.TemporaryDirectory() as tmp:
        _write_plugin(tmp, "ai-official", ["ai-provider"], src)
        res = load_all(plugin_dirs=[tmp], use_entry_points=False)
        # 目录插件与内置同 id → 目录胜，最终只有 dir-prof
        assert res.registry.ai_profile_ids() == ["dir-prof"], res.registry.ai_profile_ids()
    print("  [P6] 同 id 去重（目录 > 内置）OK")


# ---------------- P7 entry_points ----------------

class _PipWiki:
    def know(self, t, **k):
        return "PIP"


class _FakeEP:
    def __init__(self, obj):
        self.name = "pip-wiki"
        self.value = "fake_pkg:register"
        self._obj = obj

    def load(self):
        return self._obj


def test_entry_points():
    obj = types.SimpleNamespace(
        MANIFEST={"id": "pip-wiki", "type": ["wiki-knowledge"],
                  "entry": "fake:register"},
        register=lambda host: host.add_wiki_knowledge(_PipWiki()),
    )
    orig = md.entry_points
    md.entry_points = lambda group=None: [_FakeEP(obj)] if group == "deskpet.plugins" else []
    try:
        res = load_all(plugin_dirs=[], use_entry_points=True)
    finally:
        md.entry_points = orig
    assert res.plugin("pip-wiki") is not None, res.errors
    assert res.registry.wiki_knowledge().know("") == "PIP"
    print("  [P7] pip entry_points 发现 OK")


# ---------------- P9 停用 ----------------

def test_disabled_plugins():
    src = ("class W:\n"
           "    def know(self, t, **k):\n"
           "        return 'X'\n"
           "def register(host):\n"
           "    host.add_wiki_knowledge(W())\n")
    with tempfile.TemporaryDirectory() as tmp:
        _write_plugin(tmp, "my-wiki", ["wiki-knowledge"], src)
        res = load_all(plugin_dirs=[tmp], use_entry_points=False,
                       disabled=["my-wiki"])
        assert res.plugin("my-wiki") is None
        assert any(m.id == "my-wiki" for m in res.disabled), res.disabled
        # 停用后不注册 → wiki 回落到内置 wiki-none
        assert res.registry.wiki_source() == "wiki-none", res.registry.wiki_source()
    print("  [P9] 插件停用（发现但不注册）OK")


# ---------------- P10 契约单一事实来源 ----------------

def test_contracts_single_source():
    """plugin.contracts 是数据契约的单一事实来源（宿主与之对齐）。"""
    from plugin import contracts as c
    from core.action_key import ActionKey
    # 动作 key 约定与 ActionKey 一致（不含特殊 SEQUENCE）
    keys = tuple(k.value for k in ActionKey if k.name != "SEQUENCE")
    assert keys == c.ACTION_KEYS, (keys, c.ACTION_KEYS)
    # ai.providers 的常量/类型来自 contracts（re-export，非各自定义）
    from ai import providers as p
    assert p.ProviderProfile is c.ProviderProfile
    assert p.ModelInfo is c.ModelInfo
    assert p.ProviderError is c.ProviderError
    assert p.ProviderUnavailable is c.ProviderUnavailable
    assert p.PROTOCOLS == c.PROTOCOLS
    assert p.CAP_SERVER_MEMORY == c.CAP_SERVER_MEMORY
    print("  [P10] 契约单一事实来源（contracts <-> core/ai）OK")


# ---------------- P8 doctor ----------------

def test_doctor_output():
    from plugin.doctor import run_doctor
    lines = []
    rc = run_doctor(out=lines.append)
    text = "\n".join(lines)
    assert rc == 0
    for kw in ("宿主 API 版本", "ai-provider", "wiki-knowledge", "assets"):
        assert kw in text, kw
    print("  [P8] --doctor 输出 OK")


def main():
    print("=" * 62)
    print("插件宿主骨架测试（P0）")
    print("=" * 62)
    test_manifest_validation()
    test_registry_priority()
    test_builtin_extensions()
    test_dir_plugin()
    test_failure_isolation()
    test_same_id_dir_overrides_builtin()
    test_entry_points()
    test_disabled_plugins()
    test_contracts_single_source()
    test_doctor_output()
    print("\n全部通过")


if __name__ == "__main__":
    main()
