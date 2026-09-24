# -*- coding: utf-8 -*-
"""示例插件 · 最小可运行骨架。

用法：把本目录整个复制到 `desktop-pet/plugins/example-plugin/`，重启桌宠，
用 `python main.py --doctor` 确认已加载；改 `plugin.json` 的 `id` / `name` 即可变成你自己的插件。

约定（完整说明见 `../PLUGIN_API.md`）：

* 目录插件由 `plugin.json` 提供清单，本模块只负责 `register(host)`。
* `entry` 写 `文件名:函数名`（不含 `.py`），这里即 `plugin:register`。
* **只依赖 `plugin.api` 与 `plugin.contracts`**，不要 import 桌宠内部模块
  （`ai.*` / `pet.*` / `game.*` / `core.*`）；需要模型能力时用 `host.llm()`。
* 插件是**可选增强**：宿主不装任何插件也完整可用，所以你的方法失败时要自己兜底
  （返回 `None` / `""`），不要让异常冒到宿主 —— 宿主虽然会兜，但那是最后一道网。
"""


class ExampleWiki:
    """`wiki-knowledge` 扩展点：**游戏事件驱动**的被动知识注入。"""

    def know(self, events_text, *, session_key="", max_chars=480):
        """返回要注入 prompt 的知识文本；`None` / `""` 表示不注入。

        - `events_text`：宿主聚合后的游戏事件（如 ``挖掘:2 橡木原木``），约 20 秒一批
        - `max_chars`：宿主给的预算，超过会被截断（请自己按句裁剪，别交给截断切半句）
        - `session_key`：会话维度键，需要"同一局游戏内累积状态"时用它做缓存键

        真实插件在这里做：实体提取 → 本地检索 → 提炼压缩 → 按预算裁剪。
        """
        return ""


class ExampleQA:
    """`knowledge-qa` 扩展点：**用户提问驱动**的主动检索（2.31.0 新增）。"""

    def ask(self, question, *, session_key="", max_chars=1200):
        """返回检索到的知识**上下文**，不是回答；`None` / `""` 表示无可用知识。

        设计上刻意让插件**不生成回答** —— 回答由主对话 AI 写，这样人格与口吻统一，
        也省掉一次 LLM 调用。所以这里只给"证据"，别把答案写死。

        与 `know()` 的区别：宿主喂进来的是用户的原话，而不是游戏事件。
        """
        return ""


def refine_with_host_llm(host, text):
    """演示 `host.llm()`：插件需要模型能力时**向宿主借**，不要自己读凭据。

    要点：

    * 走宿主的「辅助调用后端」（跟随该设置，未设置则跟随主对话后端）
    * `purpose` 是会话分组键：网页版后端下按它复用**独立持久会话**，不污染主对话历史
    * **失败一律返回 `""`**（不抛异常），所以调用处不必包 `try/except`
    """
    return host.llm(
        [{"role": "user", "content": "把下面这段压成 3 条要点：\n" + text}],
        system="你是信息压缩助手，只输出要点，不要寒暄。",
        purpose="example_refine",
    )


def register(host):
    """入口：只做注册，别在这里做耗时初始化（会拖慢宿主启动）。"""
    host.add_wiki_knowledge(ExampleWiki())
    host.add_knowledge_qa(ExampleQA())

    # 其它扩展点按需启用（同一插件可注册多个）：
    # host.add_assets(MyAssets())                              # assets
    # host.add_ai_provider(profile=p, adapter_factory=factory)  # ai-provider

    # 在 `--doctor` 与 `logs/plugin.log` 里都能看到这行
    host.log("示例插件已注册")
