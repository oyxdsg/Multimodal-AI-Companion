# AI 与检索链路演进方案（LangChain 理念 + 本地 RAG 微量改造）

> 状态：**已全部落地**（随 `desktop-pet` 2.30.0，2026-09-22）
> P0 检索评测 / P1-1 原生工具双通道 / P1-2 结构化重试 / P2-1·2-3 检索排序与语义别名 /
> P2-4 trace/span / P3-1 agent 状态机 / P3-2 统一 retry —— 均已实施并通过全量回归（15/15）+
> 全链路测试（模拟输入驱动真机 API）；检索基线见 `docs/reports/WIKI_AUDIT.md §7`。
> 目标版本：2.30.0
> 相关文档：`DESIGN_AI_PROVIDERS.md`、`DESIGN_NLU.md`、`DESIGN_WIKI_REFINE.md`、
> `docs/reports/WIKI_AUDIT.md`、外部参考 `../RAG方案/mcwiki-agentic-rag`
> 对比对象：LangChain（框架抽象层）· mcwiki-agentic-rag（检索质量层）

## 一、背景与目标

两轮外部对照得出的可改进点，合并成一条演进路线：

1. **对照 LangChain**：我们在「工具调用、结构化输出、可观测性、链编排、重试」五个维度上有可借鉴的理念；
2. **对照 mcwiki-agentic-rag**：我们的 Wiki 检索在「多路召回、融合、重排、评测」上可做微量增强。

**硬约束（决定了"能搬什么"）**：

- **不引入重依赖**。桌宠是打包给用户的 Windows 桌面应用，不能拖入 torch / sentence-transformers /
  faiss / jieba（RAG 方案为此需要 ~270MB 索引 + 数百 MB 模型）。
- **不整体引入 LangChain**。其抽象泄漏、breaking 频繁、依赖重；我们现有的
  「供应商注册表 + 协议适配器 + 流式事件流」（`ai/providers.py`、`ai/protocols/base.py`）
  在「网页版 Cookie 会话 + 模型原生语音」这类非标场景上比 `Runnable/LCEL` 更贴合。
  **只借鉴理念，不替换底座。**
- **不动已跑通的红线**：网页版「服务端记忆、只发当轮」与 API/千问「客户端组装、滑窗 + 提炼」
  两种上下文策略（`ai/backends/session.py` / `ai/backends/messages.py`）保持不变。

目标是**渐进增强**：每一阶段都能独立验证、独立回滚，不做大爆炸式重构。

---

## 二、现状盘点（实测，含行号）

### 2.1 AI 编排层

| 维度 | 现状 | 位置 |
|---|---|---|
| 工具调用 | **纯文本 DSL**：【cmd(params)】→ 正则解析；为它堆了大量容错（漏 `】`、嵌套包装、坐标 `~`） | `game/maid_intent.py:35`（`_INTENT_RE`）、`:37`（`_LOOSE_SCRIPT_RE`）、`:55`（`_unwrap_cmd_wrapper`） |
| 原生 tool_calls | **完全没有**（全项目 grep `tool_calls`/`tools=` 零命中） | — |
| 工具契约 | 已有单一事实来源：`Command`/`Param`（含 type/required/lo/hi） | `nlu/mod_contract.py:42-189` |
| 结构化输出 | 手写 JSON 容错，**失败即静默丢弃，无重试** | `ai/memory.py:80`（`_parse_json`）、`mod_contract.py:256`（`validate_script`） |
| 可观测性 | 仅 `logs/perf.log` 记 AI/TTS 耗时与字数，**看不到一次对话内部的链路** | `core/perf_log.py` |
| 编排 | NLU → 主 AI → DSL → 回执 → 下轮注入，散落在各 handler，靠 `TransientContext` + 全局锁人工串接 | `pet/handlers/nlu_bridge.py`、`game/maid_loop.py:44`、`ai/chat_service.py:88` |
| 重试/退避 | 散落各 client，无统一策略 | `ai/utility.py`（部分回退）、`core/ai_throttle.py`（限流） |

### 2.2 检索层（Wiki）

| 维度 | 现状 | 位置 |
|---|---|---|
| 检索路径 | **单路**：标题精确 > 重定向别名 > 标题包含；标题精确命中即**硬短路**，不再补充 | `plugins/wiki-local/wiki_kb.py:436`（`_search_one`） |
| FTS5 | `pages_fts`（trigram）**已建但检索路径刻意禁用**（怕正文沾边带出版本页） | 建表 `wiki_build.py:1191`；禁用说明 `wiki_kb.py:439` |
| 实体提取 | n-gram 滑窗查表（标题 + 别名、单字上下文守卫、长词优先）；**normal 模式不扫 rare 词表** | `wiki_kb.py:156`（`match_terms`）、`:59`（`_term_index`） |
| 分类 | category：common / rare / version / tech / disambig，检索已按类过滤 | `wiki_build.py:1188` |
| 精炼 | 已有本地精炼小模型选句 + curated + 原文兜底三层 | `wiki_refine/refine.py`、`wiki_refine/curated.py` |
| 评测 | `wiki_audit.py` **只审实体提取**，无检索侧 recall 量化 | `plugins/wiki-local/tools/wiki_audit.py` |

**关键诊断**：Wik 检索的第一瓶颈不是"缺向量语义"，而是
① 没有量化的检索评测（改也不知道好不好）；
② 实体提取有已知漏检（如 `[进度] 怪物猎人`，见 `WIKI_AUDIT.md §四-6`）；
③ 排序是硬短路而非打分，召回与排序都不够精细。

因此本方案的检索部分**不做向量检索**，只做「评测 + 单路打分 + 一路 FTS 融合 + 提取召回」。

---

## 三、总路线图（融合落地顺序）

排序原则：**度量先行 → 低风险高收益 → 中等重构**；每阶段一个可独立发布的版本号。

| 阶段 | 项 | 来源 | 工作量 | 风险 | 前置 |
|---|---|---|---|---|---|
| **P0** | 检索评测集（Recall@k / first_hit_rate） | RAG ① | 小 | 无 | — |
| **P1** | 工具 schema 导出 + 原生 tool_calls（DSL 兜底） | LangChain | 中 | 中 | 契约已在 |
| **P1** | 结构化输出校验 + 失败重试 | LangChain | 小 | 低 | — |
| **P2** | 检索打分排序（替代硬短路） | RAG ③ | 小 | 低 | P0 |
| **P2** | FTS 正文路 + RRF 融合 | RAG ④ | 中 | 中 | P0、P2-1 |
| **P2** | 实体提取召回增强（rare 扫描策略） | RAG ② | 小 | 低 | P0 |
| **P2** | 轻量 trace/span 可观测性 | LangChain | 小 | 低 | — |
| **P3** | agent 循环状态机化 | LangChain | 中 | 中 | P1、P2-4 |
| **P3** | 统一 retry / 退避策略 | LangChain | 小 | 低 | — |

> 与初版建议的差异：把「评测」提前到 P0 作为**度量地基**——
> 因为检索改造（P2）没有基线就无从判断收益；工具调用（原 P0）顺延为 P1。

---

## 四、详细设计

### P0 · 检索评测集（借鉴 `evaluate.py` + `eval/dataset.json`）

**问题**：`wiki_audit.py` 只审「事件文本能否提取出实体」，不回答"提取出实体后能否检索到**正确的页**"。

**方案**（纯脚本，零依赖）：

1. 新增 `plugins/wiki-local/tools/wiki_eval.py` + `eval/retrieval_cases.json`：
   - 用例格式 `{"event": "[击杀] 猪 x1", "expect": "猪"}`（覆盖 item / mob / block / 进度 / 冷门）；
   - 复用 `WIKI_AUDIT.md` 的**归一化标题匹配**口径（去 `（方块）`/`_旧版本` 后缀、去下划线）。
2. 指标对齐 RAG 方案：
   - `first_hit_rate`（至少命中所属页）、`avg_first_hit_rank`、`recall@1/@3/@5`；
   - 分别统计 **normal / lowfreq** 两档，以及 **L1/L2/L3** 各层命中率。
3. 产出 `tools/wiki_eval_report.txt`（UTF-8，脚本自写文件），作为后续 P2 的验收基线。

**验证**：跑一次产出基线数字，写进 `WIKI_AUDIT.md` 新章节。改检索后复跑对比。

---

### P1-1 · 工具 schema 导出 + 原生 tool_calls

> **[已实施 · 2026-09-22]**：`mod_contract.to_openai_tools()` / `to_anthropic_tools()`、
> `CAP_TOOLS` 能力位、`ProtocolAdapter.chat_message(tools)`（OpenAI/Anthropic 适配器）、
> `tests/test_tool_calls.py`（T1–T4 全绿）、真机探针 `tools/probe_tool_calls.py`
> （deepseek-api / qwen 均真实返回合法 `tool_calls`，如"打猪"→`attack(target="minecraft:pig")`）。
> 另外修复了 `ProviderProfile.cap()` 缺失的潜伏 bug。
> **双通道运行时接入（已完成）**：`_ChatWorker` 在「女仆在线 + CAP_TOOLS + 非流式」时走原生
> `chat_message(tools)`，`tool_calls` 经白名单校验 + `clamp` 后由 `MaidLoop.execute` 下发
> （NLU 已本地执行时抑制，与 DSL 通道口径一致）；网页版 / 流式 qwen 保持 DSL 兜底。
> 单测：`test_chat_unit.py` 新增 U11a–U11e（开关 / 全流程 / 无工具回退 / 非法跳过 / NLU 抑制）。

**问题**：模型输出 `【attack(range=10)】` 再正则解析，为此维护大量脆弱容错
（`maid_intent.py:37` 补漏 `】`、`:55` 剥嵌套）。这是全链路最脆的一环。

**方案**（契约已在，主要是"导出 + 双通道"）：

1. **schema 导出**：给 `nlu/mod_contract.py` 增加 `to_openai_tools()` / `to_anthropic_tools()`：
   - 由 `Command.params` 的 `type/required/desc/lo/hi` 映射为 JSON Schema
     （`int`→`integer`+`minimum/maximum`，`pos`→`array[3]`，`item/entity/slot`→`string`）；
   - 排除 `layer == "meta-dryrun"`（`craft_check` 是桌宠内部查询，不下发）。
2. **能力位**：`plugin/contracts.py` 增 `CAP_TOOLS`；`ai/providers.py` 给支持原生 tools 的
   供应商（`deepseek-api` / `qwen` / `anthropic` / `custom`）加位；`deepseek-web` 不加。
   查询走已有两级推导 `providers.supports(pid, mid, "tools")`。
3. **适配器**：`ai/protocols/base.py` 增 `chat_message(messages, tools=...)`，
   **返回完整 assistant message**（含 `tool_calls`）；`openai_chat.py` / `anthropic_messages.py`
   各自实现（RAG 方案 `src/llm.py::chat_message` 已有 OpenAI 兼容参考）。
4. **双通道**：`ai/providers.supports(...,"tools")` 为真 → 原生通道；
   否则（网页版）→ 沿用现有 DSL 文本通道。`maid_intent.parse` 保留为兜底，
   不删除（网页版仍需它）。
5. **一致性守卫**：`tools` 定义必须由 `mod_contract` **生成**，不得手写第二份事实
   （延续该模块"单一事实来源"的既有约束）；`tests/test_nlu.py` 增"schema 与 COMMANDS 逐条对齐"用例。

**验证**：
- 离线：`to_openai_tools()` 与 `mod_contract.as_dict()` 逐条对齐的单测；
- 真机：参考 `tools/probe_ai_assembly_api.py` 风格，写 `tools/probe_tool_calls.py`，
  对 `deepseek-api` / `qwen` 各跑一次"打猪 / 合成镐子 / 存箱"，断言收到合法 `tool_calls`。

**风险**：网页版无原生 tools（保留 DSL 即可）；不同厂商的 tools 格式差异（各适配器单独实现）。

---

### P1-2 · 结构化输出校验 + 失败重试

> **[已实施 · 2026-09-22]**：`ai/structured.py`（`extract_json` + `retry_structured`）、
> `config.STRUCTURED_RETRY = 1`、记忆提炼 `condense_context` 接入重试
> （首轮非法 JSON 时回灌纠正提示重问一次，不再静默丢记忆）；
> `tests/test_structured.py`（8 项全绿）。

**问题**：`ai/memory.py:80` 的 `_parse_json`、`mod_contract.validate_script` 失败就**静默丢弃**：
一条记忆提炼失败即永久丢失（2.29.1 还专门修过"AI 组装出非法 JSON 被拒")。

**方案**（小而通用）：

1. 新增 `ai/structured.py`：`parse_json(text, schema=None)` → 容错提取 + 可选的字段校验，
   返回 `(obj, error)`。
2. 在**调用侧**加"校验失败重试一次"：
   - 记忆提炼（`memory.condense_context`）：失败时把 `error` 作为追加指令重问一次；
   - script 组装：`validate_script` 失败时把具体错误回灌模型重试一次。
3. 重试次数/是否开启做成常量（`config.STRUCTURED_RETRY = 1`），默认开。

**验证**：单测注入"首轮非法 JSON、次轮合法"的假客户端，断言最终成功；对比重试前后的提炼成功率。

---

### P2-1 · 检索打分排序（替代硬短路，借鉴 `rerank.py`）

**问题**：`_search_one`（`wiki_kb.py:436`）标题精确命中即 `return`，不补候选；模糊时按 `length(title)` 排序，过于粗糙。

**方案**（纯 Python，几毫秒）：

1. 把 `_search_one` 改为**收集候选 → 统一打分 → 排序取 top-k**：
   - 标题精确 +1.00；重定向别名 +0.60；
   - 标题包含 +0.30 ×（命中核心词覆盖率）；
   - 正文命中 +0.10（若启用 P2-2 的 FTS 路）；
   - 类别惩罚：version/tech 命中 -0.05（沿用 RAG 的"纯语义命中降权"思路）。
2. **保留边界**：`rare` 兜底仍只做标题精确/别名（`fuzzy=False`），不放进打分池，
   避免把正文沾边的版本进度页带出（这是 `wiki_kb.py:439` 原本刻意防的坑）。
3. 权重与阈值集中为常量，便于 P0 评测调参。

**验证**：用 P0 的用例集对比改前/改后 `recall@k` 不下降、`first_hit_rank` 不劣化。

---

### P2-2 · FTS 正文路 + RRF 融合（借鉴 `retrieve.py` + `rrf.py`）

**问题**：`pages_fts` 已建却闲置；而"实体在正文里出现、但标题不含该词"的页面会漏召。

**方案**（不重建库）：

1. **正文路**：用现有 `pages_fts`（trigram）跑一次查询，**强制 category 过滤**
   （只 `common`/`rare`），挡住"正文沾边带出版本页"。
2. **融合**：标题路（P2-1 打分）+ 正文路，用 RRF（`score = Σ 1/(60 + rank)`）融合；
   RRF 只依赖排名，两路分数尺度无需归一化（`rrf.py` 核心 5 行）。
3. **定位**：正文路是**补充召回**，标题路主导排序 → 提 recall 而不让无关页上位；
   若评测显示劣化，可用开关一键回退到纯标题路。

**验证**：P0 用例集上 `recall@3/@5` 提升且 `first_hit_rank` 不变即为达标。

---

### P2-3 · 实体提取召回增强（借鉴 RAG 的自动扩词）

**问题**：`WIKI_AUDIT.md §四-6` 承认 `[进度] 怪物猎人` 提取不到——normal 模式只扫 common 词表。

**方案**（小改，需 P0 才敢动）：

1. `_term_index` 现已按 category 收词；探讨**让 normal 模式也扫 rare 标题词表**
   （但保留单字上下文守卫，避免误命中），或对"进度/成就"类实体走专门入口。
2. 沿用 RAG 的"从真实标题自动扩词"思路：确认 `_term_index` 的标题 + 别名覆盖
   已达上限后，再考虑补充 `WIKI_NEW_TERMS`。

**验证**：P0 用例集里 `[进度] xxx`、冷门实体类的提取命中率提升，且闲聊误命中仍为 0。

---

### P2-4 · 轻量 trace/span 可观测性

> **[已实施 · 2026-09-22]**：`core/trace.py`（`Trace` 上下文管理器 + `trace_id` +
> 线程局部自动父链 + `traced` 装饰器 + 本地落盘 `logs/trace/<date>.jsonl`）。
> 接入点：`ChatService.chat`（`@traced("chat")`）、`_ChatWorker.run`（`@traced("chat_window")`）、
> `MaidLoop.process_reply`/`execute`（`maid.dispatch`）、`utility_chat`（`utility`）。
> 真机验证：e2e 探针下 `chat_window → maid.dispatch` 父子链 + 耗时/状态正确。
> 单测：`tests/test_trace.py`（5 项全绿）。

**问题**：`perf.log` 只有单条 AI/TTS 耗时，看不到"一次对话内部"（NLU → 主 AI → 工具往返 → 提炼 → TTS）的完整链路。

**方案**（本地落盘，不接云端）：

1. 新增 `core/trace.py`：`Trace` 上下文管理器 + `trace_id`，支持嵌套 span（名称/起止/耗时/状态）。
2. 在关键入口挂 span：`ChatService.chat`、`MaidLoop.process_reply`、`nlu` 前置、`utility_chat`、TTS。
3. 落盘 `logs/trace/<date>.jsonl`，默认按 trace 级别采样（避免流式每 delta 一条）。

**验证**：跑一条"打猪"链路，trace 里能看到完整父子 span；`perf.log` 保持向后兼容。

---

### P3-1 · agent 循环状态机化

> **[已实施 · 2026-09-22]**：新增 `game/agent_loop.py` —— `AgentLoop` 显式状态机
> （`IDLE→NLU→GENERATE→DISPATCH→DONE`，合法转移表 + 非法转移抛 `IllegalTransition`，
> `steps` 记录实际路径，每状态一个 `agent.*` trace span）。
> **接入** `_ChatWorker._run_plain` / `_run_stream`：行为零变更（只记录状态 + trace），
> **不碰线程模型**（状态机纯逻辑，由调用方在自己线程驱动）。
> 验证：`tests/test_agent_loop.py`（5 项全绿，含 `_ChatWorker` 接入的 trace 断言）；
> 真实端到端下 trace 落盘 `generate → dispatch → done`（有工具）/ `generate → done`（无工具）。

**问题**：用户→NLU→主 AI→DSL→回执→下轮的循环散落在多个 handler，
靠 `TransientContext` 与"下轮才注入回执"的隐式时序，可测性差。

**方案**（借鉴 LangGraph 的图/状态机，**不引入 LangGraph**）：

1. 定义显式状态：`IDLE → NLU → GENERATE → DISPATCH → AWAIT_REPLY → INJECT → GENERATE`。
2. 用一个 `AgentLoop` 类集中承载状态转移，handler 只做 IO 适配。
3. 与 P2-4 的 trace 天然配合（每状态一个 span）。

**验证**：把现有 `tools/chat_chain_test.py` 全链路测试改为对状态机断言，覆盖"无工具/工具成功/工具失败/超时"分支。

**风险**：触及现有线程模型（`chat_service` 全局锁 + worker 线程），需保持锁边界不变。

---

### P3-2 · 统一 retry / 退避策略

> **[已实施 · 2026-09-22]**：`ai/retry.py` 提供 `with_retry`（异常驱动装饰器，指数退避 +
> `retry_on` 过滤永久错误）与 `retry_call`（值驱动，适合返回 `None` 表示失败的调用）。
> **qwen TTS 限流重试已重构接入** `retry_call`（行为等价：可重试状态码延迟重试、
> 400 类永久错误立即停）；`tests/test_retry.py`（7 项全绿）、`test_tts_catalog.py` 回归通过。
> 其余适配器调用点可按需接入（网络瞬时错误 / 5xx）。

**问题**：各 client 的重试/回退逻辑分散（`ai/utility.py` 回退、`core/ai_throttle.py` 限流）。

**方案**：抽 `ai/retry.py::with_retry(fn, attempts, backoff, retry_on)` 装饰器，
统一指数退避 + 可重试异常判定；各适配器调用点接入，行为可配置。

**验证**：单测注入"前 N 次失败、第 N+1 次成功"的假调用，断言退避与次数。

---

## 五、明确不做

| 项 | 原因 |
|---|---|
| 整体引入 LangChain / LangGraph | 抽象泄漏 + 依赖重，现底座已更贴合非标场景 |
| 向量检索 / faiss / bge 嵌入 | 桌面应用拖不动（~180MB 索引 + 数百 MB 模型） |
| BM25 全库 / jieba 分词 | 同上；且事件驱动场景 term 已精准，收益有限 |
| ReAct 工具化 Wiki 检索 | 我们的 Wiki 是"被动注入"，不是"问答检索"，不需要 agent 多步检索 |

> **2.31.0 补充说明（决策演进，不是推翻）**：上表"不做"指的是**宿主不去做** ReAct/问答检索。
> 2.31.0 做的是**把能力位留出来**：新增扩展点 `knowledge-qa`（宿主喂提问、插件回检索上下文）
> 与 `HostAPI.llm()`（插件可向宿主借模型）。宿主仍然只做"被动注入"——
> **回答始终由主对话 AI 写**，插件只提供上下文（`agentic-rag` 就是这类插件）。
> 因此这条 Non-goal 依然成立：宿主内不会长出一个 ReAct 循环。
| 多模态 RAG（CLIP / 图片向量） | 识图已由网页版后端提供，无需额外视觉模型 |

---

## 六、验收与回归

- 每阶段独立可发布，CHANGELOG 记录对应版本号；
- **P0 的评测报告是所有检索改造（P2）的前后对照基线**；
- 所有新增能力**默认可用开关关闭**（检索融合、原生 tools、结构化重试），确保可回退；
- 回归：`tests/test_nlu.py` / `test_maid_loop.py` / `test_chat_unit.py` / `test_wiki.py` 全绿；
- 真机：`tools/e2e_maid_check.py`、`tools/chat_chain_test.py` 通过。

---

## 附：外部参考

| 参考 | 借用点 | 对应本方案 |
|---|---|---|
| LangChain | `bind_tools` / 结构化输出 / callbacks / fallbacks | P1-1 / P1-2 / P2-4 / P3-2 |
| LangGraph | 带环 agent 流程建模为状态机 | P3-1 |
| mcwiki-agentic-rag `rerank.py` | 规则重排（实体精确匹配 + 覆盖度） | P2-1 |
| mcwiki-agentic-rag `rrf.py` / `retrieve.py` | 多路召回 + RRF(k=60) | P2-2 |
| mcwiki-agentic-rag `evaluate.py` | Recall@k / first_hit_rate 评测口径 | P0 |
| mcwiki-agentic-rag `nlu_tasks.py` | 从真实标题自动扩实体词表 | P2-3 |
