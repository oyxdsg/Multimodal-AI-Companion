# -*- coding: utf-8 -*-
"""``python main.py --doctor``：宿主与插件自检（不开 GUI）。

报告：宿主 API 版本、插件目录、已加载插件、被隔离的失败、各扩展点的最终可用
实现、关键依赖安装情况、补装指引。设计见 ``DESIGN_OPTIONAL.md`` §九 V-DOCTOR。
"""

import os

from plugin import api
from plugin.host import disabled_ids
from plugin.loader import default_plugin_dirs, load_all

_DEPS = (
    ("PySide6", "GUI（必需）"),
    ("numpy", "动画帧图像处理 / 音频（必需）"),
    ("curl_cffi", "AI 官方协议 / TTS / 天气 / 建库（必需）"),
    ("wasmtime", "DeepSeek 网页版 PoW（可选，网页版插件）"),
    ("playwright", "自动登录 / 修仙桥接（可选）"),
    ("jieba", "agentic-rag 插件：BM25 分词（可选）"),
    ("sentence_transformers", "agentic-rag 插件：向量嵌入（可选，重）"),
    ("faiss", "agentic-rag 插件：向量索引（可选，重）"),
)


def _dep_ok(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _wiki_desc(impl, source):
    if impl is None:
        return "无实现（不注入知识）"
    if source == "wiki-none":
        return "不注入（依赖大模型自身知识）"
    return "已启用知识注入 [%s]" % (source or "?")


def _qa_desc(impl, source):
    if impl is None:
        return "无实现（提问时不注入检索上下文）"
    return "已启用主动知识问答 [%s]" % (source or "?")


def _assets_desc(impl, source):
    if impl is None:
        return "无实现（使用宿主默认）"
    try:
        fig = impl.static_figure()
    except Exception as e:
        return "调用异常：%s [%s]" % (type(e).__name__, source or "?")
    if fig:
        return "静态图存在 [%s]" % (source or "?")
    return "未提供静态图（需自备素材）[%s]" % (source or "?")


def run_doctor(out=print):
    """返回进程退出码（0=正常）。``out`` 可注入以便测试捕获。"""
    res = load_all(disabled=disabled_ids())
    reg = res.registry

    out("桌面宠物 · 插件自检（--doctor）")
    out("=" * 52)
    out("宿主 API 版本：v%s" % api.API_VERSION)
    out("")

    out("插件目录：")
    for d in default_plugin_dirs():
        out("  [%s] %s" % ("存在" if os.path.isdir(d) else "无  ", d))
    out("")

    out("已加载插件：%d" % len(res.plugins))
    for m in res.plugins:
        out("  [%-9s] %-14s %-20s v%s"
            % (m.source, m.id, ",".join(m.types), m.version or "?"))
    if res.disabled:
        out("")
        out("已停用：%d（在设置 →「插件」里关闭）" % len(res.disabled))
        for m in res.disabled:
            out("  - %s（%s）" % (m.id, ",".join(m.types)))
    if res.errors:
        out("")
        out("加载失败（已隔离，不影响运行）：%d" % len(res.errors))
        for origin, msg in res.errors:
            out("  - %s：%s" % (origin, msg))
    out("")

    out("扩展点：")
    ids = reg.ai_profile_ids()
    out("  ai-provider     : %s"
        % (", ".join(ids) if ids else "（无）"))
    out("  wiki-knowledge  : %s" % _wiki_desc(reg.wiki_knowledge(),
                                              reg.wiki_source()))
    out("  knowledge-qa    : %s" % _qa_desc(reg.knowledge_qa(),
                                             reg.knowledge_qa_source()))
    out("  assets          : %s" % _assets_desc(reg.assets(),
                                               reg.assets_source()))
    out("")

    out("依赖检查：")
    for mod, desc in _DEPS:
        out("  [%s] %-12s %s" % ("已装" if _dep_ok(mod) else "未装", mod, desc))
    out("")

    out("提示：可选插件放入下列任一目录后重启生效：")
    for d in default_plugin_dirs():
        out("  " + d)
    return 0
