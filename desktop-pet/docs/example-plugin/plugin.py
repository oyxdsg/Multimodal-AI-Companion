# -*- coding: utf-8 -*-
"""示例插件 · 最小可运行骨架。

复制本目录到 `desktop-pet/plugins/<你的id>/`，改 `plugin.json` 的 id/name，
即可运行（用 `python main.py --doctor` 确认已加载）。

约定：
* 目录插件由 `plugin.json` 提供清单，本模块只负责 `register(host)`。
* `entry` 写 `文件名:函数名`（不含 `.py`），这里即 `plugin:register`。
* **不要 import 桌宠内部模块**（`pet` / `ai` / `game` …），只用 `host`。
"""


class ExampleWiki:
    """知识扩展点示例：know() 返回要注入 prompt 的文本，None 表示不注入。"""

    def know(self, events_text, *, session_key="", max_chars=480):
        # 真实插件在这里做：实体提取 → 本地检索 → 提炼 → 按 max_chars 裁剪
        return ""


def register(host):
    # 按扩展点注册；下面按需保留其中一行
    host.add_wiki_knowledge(ExampleWiki())
    # host.add_ai_provider(profile=..., adapter_factory=...)   # ai-provider
    # host.add_assets(MyAssets())                              # assets
    host.log("示例插件已注册")
