# -*- coding: utf-8 -*-
"""内置插件 · Wiki 知识「不注入」。

设计判断（见 ``DESIGN_OPTIONAL.md`` §4.2）：大模型本身掌握绝大多数 Minecraft
知识，因此 **Wiki 插件拔掉后由大模型凭自身知识作答，是一等模式**。
本插件就是那个"空实现"：``know()`` 恒返回 ``None``，宿主据此不注入知识。

真正的本地 Wiki（检索 + 提炼 + 建库）是可选插件 ``deskpet-plugin-wiki-local``。
"""

MANIFEST = {
    "id": "wiki-none",
    "name": "Wiki 知识：不注入（依赖大模型自身知识）",
    "version": "1.0.0",
    "type": ["wiki-knowledge"],
    "description": "不注入任何本地知识；游戏知识由大模型自答",
}


class _NoWiki:
    def know(self, events_text, *, session_key="", max_chars=480, hints=()):
        return None


def register(host):
    host.add_wiki_knowledge(_NoWiki())
