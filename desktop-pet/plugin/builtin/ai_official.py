# -*- coding: utf-8 -*-
"""内置插件 · 官方 AI 供应商（非网页版）。

P0 阶段：把现有 ``ai.providers`` 里**非网页版**的供应商记录注册进插件宿主，
证明 ``ai-provider`` 扩展点可用。网页版（``deepseek-web``）暂由宿主内置数据
提供，待 P3 再拆为独立插件（``deskpet-plugin-deepseek-web``）。

注意：本插件是**第一方**，这里 import 主体是允许的例外（它随本体发布）；
第三方插件应只依赖 ``plugin.api``，不 import 主体内部模块。
"""

MANIFEST = {
    "id": "ai-official",
    "name": "内置 AI 供应商（官方 API / 千问 / 自定义）",
    "version": "1.0.0",
    "type": ["ai-provider"],
    "description": "DeepSeek 官方 API / 千问 / Anthropic Claude / 自定义 OpenAI 兼容端点",
}


def register(host):
    from ai import providers as P
    n = 0
    # 用 builtin_profiles()（静态内置）而非 profiles()（会触发插件加载 → 递归）
    for prof in P.builtin_profiles():
        host.add_ai_provider(profile=prof)
        n += 1
    host.log("注册 %d 个内置 AI 供应商" % n)
