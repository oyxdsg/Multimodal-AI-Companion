# -*- coding: utf-8 -*-
"""wiki-local 插件入口：注册本地 Wiki 知识实现。"""

from wiki_local import WikiLocal


def register(host):
    host.add_wiki_knowledge(WikiLocal())
    host.log("本地 Wiki 知识库已注册")
