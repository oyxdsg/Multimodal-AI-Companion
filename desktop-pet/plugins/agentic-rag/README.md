# agentic-rag —— 本地 Wiki 知识插件（多路召回重型版）

同时实现两个扩展点，共用一份检索链路：

| 扩展点 | 谁触发 | 给宿主什么 |
|---|---|---|
| **`wiki-knowledge`** | 游戏事件（挖矿 / 击杀 / 获得物品…） | 知识块，拼进游戏互动 prompt |
| **`knowledge-qa`** | **用户提问**（聊天 / 语音） | 参考上下文，拼进当轮 prompt |

```
规则层（need / page_type）→ 多路召回（向量 + BM25 [+ 元数据过滤]）
   → RRF 融合（k=60）→ 规则重排 → 按预算裁剪 → 交给主对话 AI
```

**只检索、不生成**：回答始终由主对话 AI 写 —— 人格与口吻统一，也省一次 LLM 调用。

## 与 `wiki-local` 的关系：二选一

| | `wiki-local`（轻量） | `agentic-rag`（重型，本插件） |
|---|---|---|
| 检索 | 纯词法（标题 / 别名 / 包含）+ 打分排序 | 向量 + BM25 多路召回 → RRF → 重排 |
| 依赖 | 纯 numpy，零重依赖 | faiss + sentence-transformers（带 torch） |
| 索引 | ~250MB（Wiki 库） | ~345MB（Wiki 库 + chunks + faiss 180MB + BM25 82MB） |
| 额外内存 | 低 | 载入 bge 模型约需数百 MB |
| 适用 | 普通用户（**推荐**） | 想要更好召回、且机器扛得住 |

两者都实现 `wiki-knowledge`，**本插件 `priority=20` 高于 `wiki-local` 的 `10`**，
同时安装时本插件胜出。不想要它就停用（设置 →「扩展 → 插件」）。

## 三级降级（任一环缺失都不会影响宿主）

1. `data/vector/` + faiss + sentence-transformers 齐全 → **向量 + BM25 多路召回 + RRF + 重排**
2. 只有 `data/bm25/bm25.pkl` → **BM25 单路**（只依赖 `jieba`；该 pickle 的 metas 自带正文）
3. 索引完全没有 → `know()` / `ask()` 返回 `""`，宿主按「不注入」处理

所以**全新 clone 下来直接跑是安全的**：插件会加载、会注册，只是暂时不注入任何知识。

## 需要自备索引（不随仓库发布）

数据约 345MB，且 Wiki 内容为 CC BY-NC-SA，与主仓库 MIT 不兼容，因此不入库。按顺序重建：

```bash
cd desktop-pet/plugins/agentic-rag

# 0) 装依赖（含建库用的 mwparserfromhell / playwright）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 1) 下载维基正文（Playwright + Edge 绕过 Cloudflare）→ data/raw/   约 56MB
python -m rag.download_wiki

# 2) 解析 + 语义切分（按章节 / 段落）→ data/processed/chunks.jsonl  约 26MB
python -m rag.process

# 3) 向量索引（bge-base-zh-v1.5）→ data/vector/                    约 180MB，约 8 分钟
python -m rag.index

# 4) BM25 索引（jieba）→ data/bm25/bm25.pkl                        约 83MB，约 30 秒
python -m rag.build_bm25

# 5) 给 chunk 打 page_type 标签（元数据过滤用）
python -m rag.add_page_type

# 6) （可选）NLU 微模型：need / page_type 分类，纯 numpy              约 0.5MB
python -m rag.train_nlu both
```

> 模型下载走镜像：脚本已 `setdefault("HF_ENDPOINT", "https://hf-mirror.com")`。
> 单卡 RTX 2060 即可全量建库；只用 CPU 也能建，慢一些。

## 结构

```
agentic-rag/
├── plugin.json          # 清单（type: wiki-knowledge + knowledge-qa；priority 20）
├── plugin.py            # register(host)：两个 add_* 注册同一个实现
├── rag_local.py         # ★ AgenticRag.know() / .ask()（对外就这两个方法）
├── requirements.txt     # 依赖（运行时 + 建库）
├── data/                # 索引（不入库，见上「需要自备索引」）
└── rag/                 # 检索 / 建库代码（自 RAG 方案 mcwiki-agentic-rag 迁入）
    ├── retrieve.py         多路召回 + RRF + 重排
    ├── rrf.py / rerank.py  融合与规则重排
    ├── vector_store.py     faiss 向量库（load / search）
    ├── embed.py            bge 嵌入
    ├── bm25.py             BM25（jieba）
    ├── rules.py            规则层（need / page_type）
    ├── nlu*.py             纯 numpy 微模型
    └── download_wiki / parse / chunk / process / index / build_bm25 / add_page_type / train_nlu.py
```

`rag/` 保持**两层目录深度**：各模块用 `dirname(dirname(__file__))` 定位插件根，
`data/` 就在那里 —— 不要把这些文件挪到插件根下（会指错目录）。

## 未做 / 已知限制

- **Agentic 多步检索（ReAct）未迁入**：原项目的 `react.py` / `tools.py` / `agent.py` 没有搬过来。
  宿主是「被动注入」架构（检索到的上下文交给主对话 AI 写回答），
  多步工具化检索在当前场景收益有限；需要时可用宿主提供的 `HostAPI.llm()` 接上。
- **图片理解（vision）未迁入**：识图目前由 `deepseek-web` 后端提供。
- **首轮延迟**：第一次检索要加载 bge 模型（秒级）与 faiss 索引；之后常驻内存。
- **`ask()` 每轮对话都会跑一次规则层**（便宜、本地），但命中知识时才做嵌入检索。

## 许可与来源

- 插件代码 MIT（与主仓库一致）。
- `rag/` 下的检索 / 建库代码迁自本项目自研的 **RAG 方案（`mcwiki-agentic-rag`）**，同一作者。
- Wiki 内容派生自 **Minecraft 中文 Wiki（CC BY-NC-SA）**，**数据不随本仓库分发**，
  使用者自行构建、自行遵守其许可。详见根 `NOTICE`。
