# -*- coding: utf-8 -*-
"""agentic-rag 插件入口。

注册两个扩展点，共用同一份检索链路（见 ``rag_local.AgenticRag``）：

* ``wiki-knowledge`` —— 游戏事件驱动的知识注入（与 ``wiki-local`` 同扩展点）
* ``knowledge-qa``   —— 用户提问驱动的检索上下文（宿主拼进当轮 prompt）

索引缺失时两个入口都返回空串（宿主按「不注入」处理），不会影响宿主运行。
"""

import rag_local


def register(host):
    impl = rag_local.AgenticRag(host)
    host.add_wiki_knowledge(impl)
    host.add_knowledge_qa(impl)
    host.log("已注册 wiki-knowledge + knowledge-qa：%s" % rag_local.status())
