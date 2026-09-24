# wiki-local —— 本地 Minecraft Wiki 知识插件

`wiki-knowledge` 扩展点的实现：把游戏事件里出现的实体换成**本地检索到的 Wiki 知识**注入 prompt，
并先用本地精炼小模型选句压缩，减小主对话上下文。

装与不装的区别：

| | 不装（宿主内置 `wiki-none`） | 装 `wiki-local` |
|---|---|---|
| 游戏知识来源 | 大模型自身知识 | 本地 Wiki 库检索 |
| 延迟 / token | 零延迟、零本地库 | 毫秒级检索 + 本地压缩 |
| 冷门 / 较新版本实体 | 可能记错 | 有据可查 |

> **不装也是一等模式**，不是残缺模式 —— 大模型本身掌握绝大多数 Minecraft 知识。

## 需要自备数据

**本插件只随仓库发布代码，不含数据**（Wiki 数据库约 250MB，且 Wiki 内容为 CC BY-NC-SA，
与主仓库 MIT 不兼容）。全新 clone 后插件会自动降级为「不注入」，不影响桌宠运行。

补齐数据：

```bash
# 全量抓取构建（约 7800 个内容页 + 2 万条别名）
python plugins/wiki-local/wiki_build.py

# 增量更新（按 wiki 最近变更拉取）
python plugins/wiki-local/wiki_build.py --update

# 规则升级后补分类 / 重清理
python plugins/wiki-local/wiki_build.py --classify

# 导出为可读纯文本（便于人工检查库内容）
python plugins/wiki-local/wiki_build.py --export
```

产物落在 `desktop-pet/wiki_data/mcwiki.db`（宿主侧路径，已被 `.gitignore` 忽略）。

`wiki_refine/data/` 下的 curated 人工知识与精炼模型数据同样不入库，由
`wiki_refine/build_curated.py` 等脚本生成 —— 缺失时知识提炼会自动回退到「原文直接注入」。

## 结构

```
wiki-local/
├── plugin.json            # 清单（type: wiki-knowledge, requires: numpy）
├── plugin.py              # register(host) → host.add_wiki_knowledge(...)
├── wiki_local.py          # WikiLocal.know()：对外就这一个方法
├── wiki_kb.py             # 检索：实体提取 + 标题/别名匹配 + 打分排序
├── wiki_build.py          # 建库：抓取 → 清理 → 分类 → 入库
├── wi_config.py           # 常量：别名表、常见/新词表、检索权重
├── wi_ngrams.py           # n-gram 工具（不依赖宿主体）
├── wiki_refine/           # 本地精炼：选句压缩 + curated 三层链路
├── eval/                  # 检索评测用例（48 条）
└── tools/                 # wiki_audit.py / wiki_eval.py（评测与基线）
```

## 接口

宿主**不知道 wiki 的存在**，只做"给事件文本 + 预算，换注入文本"：

```python
def know(self, events_text, *, session_key="", max_chars=480, hints=()) -> str | None:
    """返回可直接拼进 prompt 的知识文本；None / "" 表示不注入。"""
```

实体提取、检索、提炼、过滤、裁剪、去重都在插件内部 —— 所以本插件可以整体替换
（例如换成向量 + BM25 + RRF 的重型实现），宿主零改动。

## 评测

```bash
python plugins/wiki-local/tools/wiki_eval.py     # 检索质量：recall@k / first_hit_rate
python plugins/wiki-local/tools/wiki_audit.py    # 实体提取审计
```

基线数据见 [`../../docs/reports/WIKI_AUDIT.md`](../../docs/reports/WIKI_AUDIT.md) §七
（改检索代码后重跑对比）。测试：`python plugins/wiki-local/tests/test_wiki.py`。

## 许可

插件代码 MIT。Wiki 内容派生自 Minecraft 中文 Wiki（CC BY-NC-SA），**数据不随本仓库分发**，
使用者自行构建、自行遵守其许可。详见根 `NOTICE`。
