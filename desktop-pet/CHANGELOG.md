# 更新说明 · Desktop Pet

版本号说明：采用语义化版本（主版本.次版本.修订号）。
- **主版本**：功能质变（如引入 AI 聊天）
- **次版本**：新增功能模块 / 重要增强
- **修订号**：修复与小改进

当前版本：**2.31.0**

---

## 2.31.0（知识能力插件化 · 主动问答）

> 设计见 `docs/design/DESIGN_OPTIONAL.md`（插件宿主）与 `docs/PLUGIN_API.md`。
> 一句话：把「知识」从"只能被动注入游戏事件"扩展成"用户提问也能查"，
> 并且**能力由插件提供、宿主零硬依赖**。

**一、新扩展点 `knowledge-qa`（主动知识问答）**

- 与 `wiki-knowledge` 的分工：前者喂**游戏事件文本**（事件驱动、被动注入），
  后者喂**用户提问**（提问驱动、主动检索）
- 契约（`plugin/api.py`）：`ask(question, *, session_key="", max_chars=1200) -> str | None`
  —— 插件**只回检索到的上下文**，不生成回答；回答仍由主对话 AI 写（人格口吻统一）
- 宿主侧统一兜底：未装插件 → 不注入（**零行为变化**）；插件抛异常 → 当作无知识（不影响对话）
- 接入点：`_ChatWorker._prepare_prompt` —— 检索上下文以 `[知识]` 块前置进当轮 prompt
- 开关与预算：`config.KNOWLEDGE_QA_ENABLED` / `KNOWLEDGE_QA_MAX_CHARS`

**二、`HostAPI.llm()`：插件向宿主借 LLM**

- 插件需要模型能力时不再自己读宿主凭据、也不再 `import ai.*`
  （此前 `wiki_local.py` 的 ai 档提炼就是这样越界的 —— 契约要求插件只依赖 `plugin.api`）
- 走宿主的**辅助调用**通道（跟随「辅助调用后端」设置），网页版后端下按 `purpose` 复用独立会话，
  不污染主对话历史；失败一律返回 `""`，不把异常抛回插件

**三、新增插件 `agentic-rag`（重型知识插件，随仓库发布、数据不随）**

- 自研 RAG 方案的检索链路迁入插件：**向量 + BM25 多路召回 → RRF 融合(k=60) → 规则重排**
  （`rag/` 子包，保持两层目录深度；跨模块 import 改为相对导入，避免 `tools`/`rules` 等
  通用名污染宿主 `sys.path`）
- 同时实现 `wiki-knowledge` + `knowledge-qa`；`priority=20` 高于 `wiki-local` 的 `10`，
  两者都装时它胜出（README 已写明"二选一"及各自体积/依赖）
- **三级降级**：向量路齐全 → 多路召回；只有 `bm25.pkl` → BM25 单路（仅依赖 jieba）；
  无索引 → 返回空串。**全新 clone 直接跑是安全的**（只加载、不注入）
- 未迁入（诚实清单）：ReAct 多步检索（`react.py`/`tools.py`）与图片理解（`vision.py`）——
  宿主是"被动注入"架构，需要时可基于 `HostAPI.llm()` 再接
- 建库脚本随插件（`python -m rag.download_wiki / process / index / build_bm25 / add_page_type`）

**四、其他**

- `--doctor` 新增 `knowledge-qa` 扩展点状态与 agentic-rag 相关依赖检查
- 新增 `tests/test_knowledge_qa.py`（K1–K8：注册校验 / 优先级 / 零行为变化 / 异常隔离 /
  `llm()` 兜底 / 真实插件发现 / 缺索引降级 / doctor 输出）
- 新增仓库级 [`CONTRIBUTING.md`](../CONTRIBUTING.md)：开发环境、测试跑法、契约纪律、提交规范
- `docs/example-plugin/` 从最小骨架升级为**可照抄的完整示例**：同时演示 `wiki-knowledge` 与
  `knowledge-qa`、`host.llm()` 用法，并配 [README](docs/example-plugin/README.md) 说明跑法与改动要点

---

## 2.30.0（AI 与检索链路演进 · P0–P3 落地）

> 设计见 `docs/design/DESIGN_AI_ROADMAP.md`（已全部落地），检索基线见 `docs/reports/WIKI_AUDIT.md §7`。
> 全量回归 15/15 + 全链路测试（e2e 离线 11/11、真实玩家日志驱动原生 tool_calls 等）全部通过。

**P0 · 检索质量评测（借鉴 mcwiki-agentic-rag `evaluate.py`）**
- 新增 `plugins/wiki-local/eval/retrieval_cases.json`（48 条端到端用例）+ `tools/wiki_eval.py`
  （事件 → 实体提取 → 检索 → 命中期望页，Recall@k / first_hit_rate / L1-L3 分层，normal/lowfreq 两档）

**P1-2 · 结构化输出校验 + 失败重试**
- 新增 `ai/structured.py`（`extract_json` + `retry_structured`）、`config.STRUCTURED_RETRY = 1`
- 记忆提炼 `condense_context` 接入重试：首轮非法 JSON 时回灌纠正提示重问一次，不再静默丢记忆
- 新增 `tests/test_structured.py`

**P1-1 · 工具 schema 导出 + 原生 tool_calls（DSL 双通道的 schema/适配器半边）**
- `nlu/mod_contract.py` 新增 `to_openai_tools()` / `to_anthropic_tools()`（与 COMMANDS 逐条对齐，排除 craft_check）
- `plugin/contracts.py` 新增 `CAP_TOOLS`；`ai/providers.py` 给 deepseek-api/qwen/anthropic/custom 加能力位
  （**顺带修复 `ProviderProfile.cap()` 方法缺失的潜伏 bug**）
- `ProtocolAdapter.chat_message(messages, tools)` 返回完整 assistant message（含 tool_calls）；
  OpenAI 适配器解析 `choices[0].message.tool_calls`，Anthropic 适配器把 `tool_use` 块转 OpenAI 风格
- 新增 `tests/test_tool_calls.py`（schema 对齐 / 两适配器解析 / 能力位）；真机探针 `tools/probe_tool_calls.py`
  —— **deepseek-api / qwen 均实测返回合法 tool_calls**（"打猪"→ `attack(target="minecraft:pig")`）
- **双通道运行时接入（已完成）**：`_ChatWorker` 在「女仆在线 + CAP_TOOLS + 非流式」时走原生
  `chat_message(tools)`，`tool_calls` 经白名单 + `clamp` 后由 `MaidLoop.execute` 下发
  （NLU 已本地执行时抑制，与 DSL 通道口径一致）；网页版 / 流式 qwen 保持 DSL 兜底
- 测试：`test_chat_unit.py` 新增 U11a–U11e（开关 / 全流程 / 无工具回退 / 非法跳过 / NLU 抑制）

**检索质量（P2-1 + P2-3，详见 WIKI_AUDIT.md §7.2b）**
- `wiki_kb._search_one`：标题包含候选改按覆盖率打分排序（`_coverage`），命中实质页不补模糊候选
- `wi_config.WIKI_ALIASES` 语义别名（玩家口语 → 页面标题，喂给提取词表 + 检索别名路，不重建库）
- 检索层 recall@1 **97.9% → 100%**、avg_first_hit_rank 1.02 → **1.00**（48 用例，normal）

**P2-4 · 轻量 trace/span 可观测性（借鉴 LangChain callbacks）**
- 新增 `core/trace.py`：树形 span（`Trace` 上下文管理器 + `trace_id` + 线程局部自动父链 +
  `traced` 装饰器），本地落盘 `logs/trace/<date>.jsonl`（不接云端）
- 接入 `ChatService.chat`（`chat`）、`_ChatWorker.run`（`chat_window`）、
  `MaidLoop.process_reply`/`execute`（`maid.dispatch`）、`utility_chat`（`utility`）
- 真机验证：e2e 下 `chat_window → maid.dispatch` 父子链 + 耗时/状态正确；`tests/test_trace.py` 全绿

**P3-2 · 统一 retry / 退避策略**
- 新增 `ai/retry.py`：`with_retry`（异常驱动装饰器，指数退避 + `retry_on` 过滤永久错误）+
  `retry_call`（值驱动，适合返回 `None` 表示失败的调用）
- **qwen TTS 限流重试重构接入** `retry_call`（行为等价：可重试状态码延迟重试、400 类永久错误立即停）
- `tests/test_retry.py`（7 项全绿）、`test_tts_catalog.py` 回归通过

**P3-1 · agent 循环状态机化（借鉴 LangGraph 状态图，不引入框架）**
- 新增 `game/agent_loop.py`：`AgentLoop` 显式状态机
  （`IDLE→NLU→GENERATE→DISPATCH→DONE` + 合法转移表 + 非法转移抛 `IllegalTransition`
  + `steps` 记录 + 每状态 `agent.*` trace span）
- 接入 `_ChatWorker._run_plain` / `_run_stream`：行为零变更，**不碰线程模型**
- `tests/test_agent_loop.py`（5 项全绿，含 `_ChatWorker` 接入的 trace 断言）；
  真实端到端 trace 落盘 `generate→dispatch→done`（有工具）/ `generate→done`（无工具）

---

## 2.29.1（女仆原子指令 · API 端组装修复）

- **API 端原子指令组装测试补做**（`tools/probe_ai_assembly_api.py`，官方 API + 精简人设，5 模拟任务隔离会话）：首测发现精简人设缺 script 结构示例 → AI 组装出非法 JSON（`{"find":{}}` 缺 `cmd/params`、loop 字符串 while/`steps`、if 字符串）被 `validate_script` 拒绝
- **根因**（对照实验确认）：同一 API 客户端 + 完整人设（含示例）组装正确 → 是 **prompt 教学缺示例**，非模型能力
- **修复**：`prompt_api.txt` / `prompt2_api.txt` 的 script 段补紧凑结构示例（照抄格式、勿自创字段名）；`prompt_min.txt` 的 script 伪代码改为真实 JSON 示例
- **重测**：砍树/凑够返回 → script 全部通过 `validate_script`（`cmd/params/as`、`if:{var,op,val}`、`loop:{while,body}`、`assign`、`terminate` 合规）；挖矿 → `mine`；打猪 → `attack(target)`；存箱 → `chestopen`
- 回归：`test_nlu` / `test_maid_loop` / `test_chat_unit` 全部通过

---

## 2.29.0（开源准备 · 可插拔插件化）

> 目标：缩小项目本体、把「可选内容」做成**插件**（接入但不必须），缺了照常跑。
> 设计见 [DESIGN_OPTIONAL.md](docs/design/DESIGN_OPTIONAL.md)，接口见 [docs/PLUGIN_API.md](docs/PLUGIN_API.md)。

**一、插件宿主（P0）**

- 新增 `plugin/`：`api.py`（行为契约）/ `contracts.py`（数据类型契约，单一事实来源）/ `manifest.py` / `loader.py` / `registry.py` / `doctor.py` / `builtin/`
- 扩展点：`ai-provider` / `wiki-knowledge` / `assets`（`tts` / `stt` 预留）
- 发现三来源：内置 / `plugins/` 目录 / pip `entry_points`；失败隔离、同 id 去重（目录 > pip > 内置）
- `python main.py --doctor` 自检；公开接口 + 示例插件（`docs/PLUGIN_API.md`）

**二、动画素材外置 + 静态模式（P1）**

- 完整动画（`assets/` 1664 帧）与 `素材源/` 移出仓库；本体只留 `assets/_static/idle.png`（大肥鱼待机帧；**角色形象为 CC BY-NC-SA 4.0 衍生作品，不适用 MIT**，见 `NOTICE`）
- 无素材 → **静态模式**：一张静态图，拖动/聊天/气泡/语音/游戏联动全部保留，只是不动
- 修 5 个素材缺失崩溃点；`assets` 扩展点接入动画系统

**三、Wiki 整块插件化（P2）**

- `wiki_kb` / `wiki_build` / `wiki_refine` / 词表移入 `plugins/wiki-local/`；宿主不再依赖（默认**不注入**，大模型自答）
- 装上插件恢复「curated → 本地精炼 → 原文」三层链路；`WikiKnowledgePlugin.know()` 接口

**四、DeepSeek 网页版插件化（P3）**

- `deepseek.py` / `login.py` / `sha3_wasm_bg.wasm` 移入 `plugins/deepseek-web/`
- 默认后端改为 `deepseek-api`；缺插件时官方 API / 千问 / 自定义照常
- `wasmtime` 移出主依赖 → `requirements-web.txt`；`ProviderError` 通用异常基类

**五、设置 →「插件」页（P4）**

- 列出已装 / 停用 / 加载失败的插件；可启停（重启生效）、打开插件目录
- 停用状态持久化（`plugins/disabled`）

**六、合规收尾（P5）**

- 仓库本体 **342MB → ~3.4MB**；新增 `LICENSE` / `NOTICE`
- 素材 / 插件 / Wiki 数据**不随包**；脚本硬编码路径改为可移植

---

## 2.28.0（极简人设 + Wiki 低频过滤判定升级）

### 一、极简 prompt 模式（标签式人设，token 最省）

新增**第三档人设**「极简版」：`prompt_min.txt` / `prompt2_min.txt`，采用 `[PERSONA_LOAD]` 标签式结构（人格/形象用英文标签压缩），但**功能规则完整保留**——输出格式硬规则、三模式规则、**完整女仆指令清单与参数用法**（AI 不知道指令就没法用，这部分不压缩）。

- **设置 → 对话 →「人设精简程度」**三档：**完整版**（`prompt.txt`，网页版默认）/ **精简版**（`prompt_api.txt`，官方 API / 千问默认）/ **极简版**（标签式，两类后端都可选）。选项按当前后端过滤（网页版: 完整/极简；无状态: 精简/极简），无效档位自动回退后端默认。
- `config.PERSONAS[i]` 增加 `file_min`；`load_system_prompt` / `get_api_prompt` 支持 `style` 参数；名字替换兼容 `NAME <名>` 行；皮肤动作词替换兼容 `/` 分隔写法。
- token：极简档（含模式段）约 1566 字 vs 精简 4820 字，省约 68%。
- **真机实测校准**（网页版 / 官方 API / 千问 × 日常/工作/游戏 × maid/mikoto，每核心场景多次）：
  - 修复 `【指令(...)】` 占位符被照抄 → 明确【】内写真实指令名 + 给正确示例；
  - 修复 `{动作}` 字面被照抄 → 明确 { }内只能放动作词，禁止写"{动作}"字面；
  - 修复 **API 端（deepseek-flash）`{动作}` 漏带**——根因是 `config.AI_MODES` 工作/游戏规则写「不输出…动作标签」，与极简 [OUTPUT] 冲突，flash 优先信模式段；在 [MODE] 加「三模式格式一致、每条必带{动作}，'不输出动作标签'≠省略{动作}」声明解决；
  - 修复无女仆状态偶发发指令 → 明确无【当前状态】/[态]/[系统] 标记 = 女仆不在场，绝不给指令；
  - 修复网页版造房曾把指令写进 `{build(...)}` → 「{}只放动作词 + 一次最多一条」压住。
- 新增单元测试 U1e–U1j：极简档 token / 指令清单与精简版**逐条对齐** / 组合矩阵 / 皮肤名字替换 / 模式段互异 / `_ChatWorker` 路由。

### 二、Wiki 低频过滤判定升级（不再只靠 191 条白名单）

原低频模式只过滤 `WIKI_COMMON_TERMS`（191 条人工词表），树苗 / 燧石 / 骨头等 common 类基础实体漏网、仍查 wiki 注入主对话。

- `game/wiki_kb.py` 新增 **`is_worth_wiki`**：低频模式过滤 = **白名单 + common 类基础实体**（AI 铁定知晓）；保留 = **rare 类冷门 + 较新版本内容**（curated 标注 `1.xx+` 自动识别 + 新增 `config.WIKI_NEW_TERMS` 补变体写法，如樱花木 / 嗅探兽）。
- `game/processor.py` 低频分支改走 `is_worth_wiki`。
- 单次判定 ≈0.05ms，可忽略。
- 真库 19 个用例验证：树苗 / 燧石 / 骨头 / 羽毛 → 过滤；樱花木 / 嗅探兽 / 铜灯 / 试炼密室 / 怪物猎人 → 保留。

---

## 2.27.0（原生语音双模态 P9：模型自己说话 + 流式播放 + TTS 抑制）

> 本期把 P9 做完：Omni 模型**自己出语音**，走**抖动缓冲 + 边收边播**的流式音频管线；
> 同时补上"原生语音失效就退回千问 TTS"的兜底，避免宠物忽然没声音。
> 设计依据见 [DESIGN_AI_PROVIDERS.md](docs/design/DESIGN_AI_PROVIDERS.md) §十六。

**一、原生语音流式播放管线**

- 新增 `voice/stream_audio.py`（纯逻辑缓冲 + 可注入 sink）
  - 拆成 `PcmBuffer`（纯标准库）/ `SoundSink`（真声卡）/ `PcmPlayer`（拉取线程）三层，
    只有 `SoundSink` 依赖声卡与 numpy → 其余可离屏单测
  - 24kHz 单声道 PCM16（百炼 Omni 固定格式）
  - 抖动缓冲：攒够 240ms 起播；欠载时持续等待；收尾排空不吞尾音
  - 无输出设备时**安静失败**，不抛异常、不卡住聊天
  - 音量设置仍然生效；语速对原生语音无效（已知限制，不引入重采样失真）

**二、原生语音接入**

- `ai/protocols/openai_chat.py` 的 `stream_events()` 已解析 `delta.audio.data`
  （base64 → PCM16），并声明 `modalities: ["text","audio"]`
- `ai/chat.py`：`_ChatWorker._run_stream()` 在 native 模式改走 `stream_events`，
  文本 → 气泡，音频 → `PcmPlayer` 管线
- `pet/settings_dialog.py`「语音朗读」新增「模型原生语音」引擎，
  并按所选**对话模型**过滤可用音色（音色集随模型族不同，实测差异大）
  - `qwen3-omni-flash` 17 个、`qwen3.5-omni-flash` 55 个、`qwen-omni-turbo` 4 个
- 新增 `config.NATIVE_VOICE_PROMPT` 前置约束：音频无法事后剥离，
  只能让模型生成时不输出 `{动作}` / `【指令】` / 括号旁白 / emoji

**三、TTS 抑制 + 静音兜底**

- 原生语音轮不再调外部 TTS（避免"两个人同时说话"）
- 若模型这一轮**没有返回任何音频**（模型不支持 / 被服务端忽略 / 音色被拒），
  自动退回到千问 TTS 念一遍正文，并在状态栏提示原因
- `voice/stream_audio.py::PcmPlayer.end()`：若一片音频都没收到直接复位 `is_active`

**四、目录缓存的字段级 fallback**

- 旧缓存不包含 `omni` 音色表 → 字段级回落 `tts_catalog.snapshot.json`，
  防止旧缓存把新功能顶成空

**五、QwenClient URL 修正**

- `QwenClient` 错误使用裸 `BASE_URL`，少了 `/compatible-mode/v1` → 直接调用会 404
  （生产流实际走 `make_adapter` 因此未触发；但脚本直接构造 `QwenClient` 会命中）

**新增测试**：`tests/test_audio_stream.py`（A1–A10），覆盖缓冲 / 起播阈值 / 短句 /
收尾不吞尾 / 无设备兜底 / 原生语音前置约束守卫。

**实机验证产物**：`logs/_probe_native_audio.txt`（模型 `qwen3-omni-flash`、音色 `Cherry`），
实测：43 字正文，25 片音频，376320 字节 @24000Hz，7.84 秒，全部写入 sink，
正文无动作标签 / 无指令。

---

## 2.26.0（真机校准 DeepSeek + 千问 TTS 目录化）

> 本期两件事都建立在**实机取证**上（用你自己的 Key 打的真实请求）：
> ① DeepSeek 官方 API 的模型清单与思考参数被实测校准；
> ② 千问语音的**模型与音色改为从官方文档抓取的实时目录**，不再硬编码。
> 设计依据见 [DESIGN_AI_PROVIDERS.md](docs/design/DESIGN_AI_PROVIDERS.md) §十三。

**一、DeepSeek：模型清单与思考语义按实测校准**

- `GET https://api.deepseek.com/models` 实测**只返回两个模型**：
  `deepseek-flash`（默认）与 `deepseek-v4-pro`。
  前一期按 models.dev 写的 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp`
  在官方 API 上**并不存在**（那是百炼侧的名字）——已删除
- **旧 id 会被服务端别名**（实测响应里的 `model` 字段会变）：
  `deepseek-chat` / `deepseek-reasoner` / `deepseek-v4-flash` → `deepseek-flash`。
  新增 `ai/providers.LEGACY_MODEL_ALIASES` 在**读取时归一化**，
  否则 `logs/perf.log` 会记下与实际不符的模型名（排查问题时这是有毒的）。
  注意 `deepseek-reasoner` 别名到的是 **flash 而非 pro**
- **`reasoning_effort` 字段名已验证**（上一期标的是"未验证"）：非法取值返回 422，
  错误信息直接给出权威取值集 `none, minimal, low, medium, high, xhigh, max`
  （两个模型相同）。注册表档位已对齐；**关思考时整字段省略**（实测省略即不推理）
- 注意：`deepseek-v4.1-flash` / `deepseek-flash-0731` 实测 400，不在清单内

**二、千问语音：模型与音色从「硬编码 2 个」变成「实时目录 48 个」**

原先是 `ai/qwen.py` 里写死 `TTS_MODEL = "qwen3-tts-flash"` + `VOICES = ["Chelsie", "Cherry"]`。
实测百炼提供 **9 个非实时 TTS 模型、48 个音色**，硬编码只能用到 4%。

- 新增 **`voice/tts_catalog.py`**：抓官方音色文档
  （`help.aliyun.com/zh/model-studio/qwen-tts-voice-list`，**服务端渲染的真表格**，
  不需要 JS），解析出 `voice参数 / 音色名 / 描述 / 支持语种 / 支持模型`。
  **零新增依赖**（标准库 `html.parser`）
- **只取"非实时"表**：文档里两张同构表，实时表（WebSocket）用同一 HTTP 端点会
  400（实测 `current user api does not support http call`）
- **音色可用性随模型变化**（实测）：`qwen3-tts-flash` 48 个、`qwen3-tts-instruct-flash` 24 个、
  `qwen-tts` 只有 4 个 —— 所以音色下拉**按所选模型过滤**，
  选了不被支持的音色会回落并提示
- 三级数据源，**离线可用**：运行时缓存 → 随包快照（`voice/tts_catalog.snapshot.json`）
  → 兜底默认。界面上有「刷新音色列表」（后台线程抓取，不卡界面）
- 设置界面「语音」页新增：**语音合成模型**下拉、**音色**下拉（带中文名 + 描述，如
  `千雪（Chelsie）· 二次元虚拟女友（女性）`）、**试听**按钮、数据来源与时间状态行
- **语音与对话后端正式解耦**：原先音色下拉的显隐取决于"对话后端是不是千问"
  （把两条无关的轴绑在一起），现在只看语音引擎是否为「千问 TTS」
- 文案修正：`CosyVoice（千问流式）` → **`千问 TTS（联网，音色多、可配模型）`**
  （调的一直是 qwen3-tts 系列，不是 CosyVoice；模式 id 仍叫 `cosy` 以免破坏既有设置值）

**三、TTS 限流重试（实测驱动的可靠性修复）**

- 实机连续快速合成 7 句时**第 7 句被打回**（静置后同参数立刻成功）→ 百炼会限流。
  一旦被限流 `synth_sentence` 返回 None，`voice/tts.py` 会回退本地 SAPI，
  用户听起来像"忽然换了个人"。现补**一次有界重试**（仅 429/5xx/网络异常；
  400 这类永久错误立即放弃）

**四、顺带修掉的问题**

- **口头禅缓存键漏了模型**：只按 `(音色, 文本)` 缓存，换 TTS 模型后会播到旧模型的音频
- `voice/tts.py` 里那份 `COSYVOICE_SAMPLE_RATE` 与 `ai/qwen.py` 是**两份事实**（会漂移），
  已收敛到 `ai/qwen.py` 单点定义
- `pet/modes.py` / `pet/bubble.py` 直接读旧键 `qwen_cosy_voice`，改为统一走
  `credentials_store.get_tts()`（含旧键只读回退）
- `.gitignore` 里 `desktop-pet/voice_cache/` 路径写错（少了中间一层 `voice/`），
  导致 28 个语音缓存 wav 被误跟踪；模式已修正

**新增测试**：`tests/test_tts_catalog.py`（T1 解析器 / T2 按模型过滤 / T3 离线快照 /
T4 无硬编码守卫 / T5 凭据与旧键回退 / T6 限流重试策略）。

**实机验证产物**：`logs/tts_test/*.wav` —— 6 组「模型 × 音色」真实合成（4~5 秒/句），
可直接试听对比。

---

## 2.25.0（AI 接口通用化 P6–P10：接入任意 AI 对用户可见）

> 接 2.24.0 的地基，本期把"接入任意 AI"做到**界面上真的能用**：
> 提供方从写死的 3 个变成**注册表驱动的 5 家 + 可自定义端点**，
> 模型清单按 models.dev 真实数据校准，思考/联网/传图的控件全部**按能力位动态渲染**。
> 设计依据见 [DESIGN_AI_PROVIDERS.md](docs/design/DESIGN_AI_PROVIDERS.md) §11 / §12。

**提供方与模型清单（M1）**

- 注册表扩到 5 家：**DeepSeek（网页版）/ DeepSeek（官方 API）/ 千问 / Anthropic Claude / 自定义（OpenAI 兼容）**
- **DeepSeek 官方 API 的模型清单按 models.dev 校准**：旧的 `deepseek-chat` / `deepseek-reasoner`
  已下架，改为 `deepseek-v4-flash`（默认）/ `deepseek-flash` / `deepseek-v4-pro` /
  `deepseek-v4-flash-vision-exp`。清单**不再写在 `ai/deepseek_api.py`**，改为从注册表取（单一事实来源）
- 模型记录带上真实数据：上下文 / 输出上限 / 价格 / 传图 / 思考档位（全部取自 models.dev 缓存）
- ⚠️ **DeepSeek 的思考语义随之改变**：V4 系列用 `reasoning_options`（toggle + effort）声明思考能力，
  所以是**请求参数 `reasoning_effort`**，不再是"把模型换成 reasoner"。
  **该字段名未做真机验证**（本次无法调真实 API）；若开思考时报 400，
  把注册表里这几个模型的 `reasoning_transport` 改成 `""` 即可彻底停发（默认路径本来就不发）

**P6 设置界面「基础」页重做**

原来是"DeepSeek 一排 + 千问一排"按索引显隐（还踩过"标签不跟着隐藏"），现在单块、数据驱动：

| 行 | 说明 |
|---|---|
| 提供方 | 分组下拉，来自注册表的 `label`，可选项由 `selectable` 决定 |
| 端点 | 网页版隐藏；其余可编辑（留空用注册表默认，**自定义项必填**） |
| API Key | **一个**输入框（密码模式），切换提供方自动换；网页版隐藏（走右键登录） |
| 模型 | **可编辑**下拉 —— 预置 id 不可能覆盖厂商全部型号，允许手填 |
| 模型说明 | 中文名 · 上下文/输出 · 价格（每百万 token） |
| 能力 | **只读标签行**：`[流式] [识图] [联网] [思考:分档/开关/预算] [能力未验证]` |
| 思考 | 按当前模型**动态渲染**（见 P10） |
| 测试连接 | 后台线程发一条最小请求；用**界面上当前输入**测，不必先保存 |

**P10 思考控件按模型动态渲染（顺手删掉全局复选框）**

- 删掉「对话」页那个与模型无关的「深度思考」复选框 —— 思考的语义**因模型而异**，
  放在模型旁边才不会误操作（§11.4）
- 三态渲染：`effort` → 档位下拉（关闭/low/high/max…）／`toggle` → 复选框／
  `budget_tokens` → 复选框 + token 数（含**下限 1024**，关掉时输入框一并灰掉）
- **不同模型不同语义**已经是现实：Opus 4.8 是分档、Sonnet 4.5 / Haiku 4.5 **只有 token 预算**
- 档位按 `provider + model` 存储；换模型读不到就回落该模型的默认档
- 连带修一处**参数被压扁**的隐患：`thinking` 原先只传 bool，effort 档到适配器会退化成"开"
  → 现在另传 `thinking_variant` 字符串，`reasoning_effort` 才拿得到 `high` / `max`

**P7 自定义端点**

- `自定义（OpenAI 兼容）` 放开可选：填任意端点 + Key + 模型名即可
  （Ollama / vLLM / LM Studio / 聚合平台…）
- `Anthropic Claude` 放开可选（P5 时因界面未接入而刻意锁着）
- 能力未知的提供方显示 `[能力未验证]`，可用「测试连接」验证

**P8 辅助调用可另指便宜后端**

- 新增设置「辅助调用后端（留空即跟随主对话）」：Wiki 提炼 / 记忆提炼 / 播报润色
  **不进主对话历史**，用弱模型完全够用

**P9 原生语音：只落地了能验证的一半**

- 模型级新增 `output_modalities`（含 `audio` 即表示原生出语音），并把
  "原生音频时必须关掉外部 TTS（否则双声）"写进数据层注释
- **流式音频播放管线（抖动缓冲边收边播）故意没做**：现有五个供应商都不返回原生音频，
  写了无从验证；而且适配器目前不请求音频 → 模型只回文本，
  此时抑制 TTS 会让宠物**彻底没声音**，是净负面。播放与抑制必须**同时**落地

**能力位只声明"真的实现了"的**

- **传图**：模型数据里 `deepseek-v4-flash` / Claude 都写着支持图片，但 OpenAI 兼容与 Anthropic 的
  图像输入（content parts / image blocks）还没实现 → **传输级能力位不开**，
  界面不显示 `[识图]`，发图也走"当前后端不支持"的提示。
  否则会出现"界面说支持、一发就报错"
- **联网**：改为按 `CAP_SEARCH` 显示/隐藏（只有网页版有）

**测试**

- `tests/test_providers.py` 新增 **V10**（可选性 / 传图门控 / 思考三态 / 音频声明位 / 成本文本）
- V5 改为断言"思考走请求参数、模型不因思考而变"；V8 改用新模型 id
- `tests/test_maid_loop.py` 的 DeepSeek 请求体测试改为**断言清单来自注册表**

**未做（已在文档记账）**

- P9 的流式音频播放管线（理由见上）
- `HISTORY_WINDOW`（20 轮）换成 token 预算 —— 风险较高，先不动
- OpenAI 兼容 / Anthropic 的图像输入（content parts）—— 做完才该开 `[识图]`

---

## 2.24.0（AI 接口通用化 P0–P5：供应商注册表 + 协议适配器）

> 目标：**让 AI 接口能接入任意 AI**，而不是写死的三选一。
> 设计依据见 **[DESIGN_AI_PROVIDERS.md](docs/design/DESIGN_AI_PROVIDERS.md)**，实证调研见
> **[RESEARCH_OPENCODE_PROVIDER.md](docs/reports/RESEARCH_OPENCODE_PROVIDER.md)**（本机 opencode 的供应商层）。
>
> ⚠️ **本期是地基 + 修 bug，界面看不见变化**：设置界面仍只有原来的三个后端
> （DeepSeek 网页版 / 官方 API / 千问）。新增供应商的**选择界面在 P6**，
> 自定义入口在 P7。另：本次顺带把 README 与 CHANGELOG 遗留的版本错位（2.23.1 / 2.23.0）
> 一并对齐到 2.24.0。

**问题诊断（原设计把两件正交的事焊成了一个字符串）**

原来 `BACKENDS = ("deepseek", "deepseek-api", "qwen")` 与
`config.MESSAGES_BACKENDS = ("deepseek-api", "qwen")` 是**两份硬编码、可能不同步的事实**，
并且「上下文策略」与「传输协议/厂商」被压缩成一个 id。后果是：加一家厂商要改
`make_client` / `chat_once` 三段 if 链、加一组扁平凭据键、加一排设置控件，
而流式只有千问有（还泄漏进 `ai/chat.py` 直接读 `qwen_model`）。

**P0 供应商注册表（新增 `ai/providers.py`）**

- 两个正交轴拆开：**上下文策略**由能力位 `server_memory` 推导；**传输协议**由
  `protocol` 字段选适配器
- **能力位取代厂商名**：`server_memory` / `system_each_turn` / `stream` /
  `attachments` / `thinking` / `search` / `session_state` / `tts` / `chat_stream`。
  能力分**两级**（provider 级 = 端点支持什么，model 级 = 该模型是否具备），
  **有效能力 = 两级取与**
- 关键杠杆：`config.is_messages_backend()` 保留函数名、内部改为查能力位
  → **5 个行为分支一行未改就自动正确**；三个内置后端的新旧判定经 `[V2]` 逐一对比相同
- **id 命名规避了一次语义反转**：`deepseek` 历史上指**网页版**，若把它改作官方 API 的新名字，
  老用户的后端会被错读成"需要 API Key 的官方 API"。最终只把网页版改名 `deepseek-web`
  （从未出现过的名字），官方 API 保持 `deepseek-api`。旧值映射 `LEGACY_IDS` + `[V3]` 守卫

**P1 凭据命名空间（新增 `ai/credentials_store.py`）**

- 扁平键 → `ai/<id>/key|model|base_url|session_id|parent_id|variant/<model>`
- **老键只读回退、不删**：`qwen_api_key` / `deepseek_api_key` / `deepseek_api_model` /
  `qwen_model` / `ai_session_id` / `ai_parent_id` 全部还能读；用户下次点「确定」自然迁到新键
- `qwen_api_key` 等**字面量只允许出现在 `credentials_store.py`**，由 `[V6]` 守卫
- `ai/client.py` 原有 8 个存取函数**签名全部保留** → 设置界面 / `voice/tts.py` / 工具脚本零改动

**P2 协议适配器（新增 `ai/protocols/`）**

- `openai_chat.py`：**通用 OpenAI 兼容适配器** + 可编辑 `base_url`，覆盖大头
  （实证：opencode 的 222 家供应商里 **181 家 81.5%** 走这套方言，前 4 个协议覆盖 88.7%）
- `deepseek_api.py` / `qwen.py` 的 chat 半边**收敛成它的薄子类**，类名与模块常量
  （`CHAT_MODELS` 等）保留，故既有测试与工具不受影响；千问的语音部分原样不动
- 明确**不做**配置化 JSON 路径映射引擎（会造出无法单测的间接层）
- 顺手修掉一处**隐式判断失效**风险：`chat.py` 原用 `hasattr(client,"chat_stream")`
  决定是否流式、用 `hasattr(...,"upload_image_file")` 决定是否支持传图 ——
  适配器统一后前者恒假、后者会因为加个基类方法而静默失效。现改为注册表能力位
  （`CAP_CHAT_STREAM` / `CAP_ATTACHMENTS`）

**P3 流式剥离重构（新增 `ai/stream_strip.py`）—— 修掉两个真实缺陷**

- **缺陷 1（显示层）**：原对累积全文调 `parse_ai_output`，而动作标签正则要求闭合符，
  流式中途的 `"好的{开"` 会**原样显示**，等 `}` 到了才消失
- **缺陷 2（语音层，会把指令念出来）★**：原实现"先切句、再逐句剥离"，而切句正则
  `(?<=[。！？!?…~；;])` 把 **`~` 当句末终止符**，偏偏 DSL 相对坐标就用 `~`
  → `【move(pos=~,~,~)】` 被切成 4 段、每段缺 `】` 剥离失败 → **逐段朗读指令**
- 改为**一个剥离器、两个下游**：`TagStripper` 状态机 + holdback 缓冲，
  **剥离发生在句切分之前**，显示与 TTS 共享同一实例
- 上限分档：动作标签 12 字；DSL 96 字 **+ 300ms 超时兜底**（卡住就按普通文本放行，不冻结界面）
- **补掉一个已知缺口**：流式原先完全不接 DSL（旧代码里明写"千问流式后端不接 DSL"），
  现在收尾时在工作线程走既有的 `_apply_dsl` 路径
- 守卫：`tests/test_chat_unit.py::[U7]`（未闭合不外泄 / 跨片补齐 / `~` 不碎 / flush / 超时）

**P4 辅助调用统一（新增 `ai/utility.py`）—— 修掉两个隐性坏功能**

- `ai/client.refine_wiki()` 原**写死 DeepSeek 网页版客户端**；
  `ai/memory._refine_backend()` 原**写死 `deepseek-api`**（取不到就回退网页版）
  → 结果：**只配一个 API Key、没登录网页版的用户，Wiki 提炼与长期记忆提炼其实都是坏的**，
  而且静默失败、界面上看不出来
- 统一走 `utility_chat(messages, purpose)`：有 `server_memory` 能力用独立持久会话
  （保留跨重启复用优化，会话键沿用 `ai_refine_*` / `ai_condense_*`），否则 stateless 一次性调用

**P5 Anthropic 原生协议（新增 `ai/protocols/anthropic_messages.py`）**

- 第一个**真异构协议**，用来验证适配器接缝成立：`system` 顶层字段、`max_tokens` 必填、
  `x-api-key` + `anthropic-version` 头、`content` 块数组响应、
  **`thinking` 与 `temperature` 互斥**（适配器里显式清掉）、SSE `content_block_delta` 事件
- 档位→`budget_tokens` 的启发式换算放在适配器里（数据层只给人类档位名）
- **注意**：`anthropic` 已注册为 `selectable=False` —— 适配器就绪但设置界面未接入，
  此时若允许选中，界面会显示成 index 0（网页版）而与实际不符。**P6 接入后才放开**

**新增测试**

- `tests/test_providers.py`：V1 注册表完整性 / V2 **能力位等价性** / V3 旧 id 归一化 /
  V4 凭据命名空间与老键回退（用假 QSettings，**不碰真实配置**）/ V5 跨协议请求体 golden /
  V6 无硬编码守卫 / V7 **版本铁律** / V8 适配器工厂与思考控件三态
- `tests/test_chat_unit.py`：U4 改为断言新路由；新增 U7 流式剥离守卫

**未做（后续分期）**

- P6 设置界面重做（提供方下拉 + 端点 + 可编辑模型 + 只读能力标签 + 「测试连接」）
- P7 `custom` 自定义端点逃生口
- P8 模型级 `limit`/`cost` 落地为 token 预算与"辅助调用用便宜模型"
- P9 原生语音双模态（默认关，需新播放管线）；P10 思考档位按模型动态渲染 UI
- 流式**事件流化**只落地了原语（`stream_events` / `StreamEvent`），
  `_run_stream` 仍走文本流 —— 因为切换会改变千问"窗口内多轮记忆"的语义，
  而音频（事件流的真正理由）要到 P9 才实现

---

## 2.23.1（2026-09-19）：游戏内女仆对话 + 桌宠隐退

- **游戏内女仆对话**：接收模组 WS `chat`（游戏聊天栏 `/maidchat` 的文本）→ 桌宠 AI 回复经 `chat_reply` 回女仆（多行气泡 + 游戏聊天栏回显 + 语音朗读）
- **Y 键语音接入女仆**：女仆在线时，语音识别文本自动作为对女仆说的话（`MaidHandler.chat_turn`）
- **指令闭环**：对话走 `MaidLoop.process_reply` 剥离并下发【指令】；`world:~` 自动纠错为主人坐标；背包槽位/主人坐标注入 prompt 帮助 AI 生成可执行指令
- **桌宠隐退**：新增设置「召唤女仆后隐藏桌宠窗口」（`MAID_HIDE_PET_ON_MAID`，默认开）——女仆上线隐藏窗口+气泡（AI/STT/TTS 后端保留），女仆离线/断线自动恢复窗口
- 涉及：`game/maid_link.py`（chat / maid_presence / chat_reply）、`pet/handlers/maid_handler.py`、`pet/handlers/stt_handler.py`、`pet/bubble.py`、`pet/modes.py`、`pet/settings.py`、`pet/settings_dialog.py`、`config.py`

---

## 2.23.0（设置界面改用 WorkBuddy 视觉语言）

> 起因：按本机 WorkBuddy 5.5.6「设置」面板的**实测取色**重做设置对话框外观。
> 只动**设置对话框**一处，其余界面（聊天窗 / 气泡 / 右键菜单）仍是 2.22.0 的 Ant 蓝。
> 取色凭证、完整令牌表与移植注意事项见 **[DESIGN_UI_LANGUAGE.md](docs/design/DESIGN_UI_LANGUAGE.md)**。

**设计令牌（`core/theme.py` 新增 `WBS_*` 区，与既有 `_ANT_QSS` 并存）**

| 类别 | 令牌 |
|---|---|
| 底色分层 | 弹窗 / 导航列 / 内容区 `#F6F8F5` → 卡片 `#FFFFFF`。相邻层只差约 3% 亮度，**全窗 0 描边**——分层靠亮度差，不靠边框 |
| 交互反馈 | hover `#EDF0EC`、按下 `#E4E9E2`、导航选中填充 `#C3DCC6` + 文字/图标 `#23382A` |
| 强调绿 | `#3C8C4E`（勾选 / 滑块 / 强调）、主按钮 `#519560`（hover `#46854F`、按下 `#3A7444`） |
| 文字四级 | 主 `#293B30` / 次 `#59675E` / 三级 `#6C7870` / 导航分组标题 `#5F6D63` |
| 圆角 | 弹窗 24 / 卡片 16 / 控件与导航项 8 |
| 尺寸 | 窗口 900×690（外框留 20px 给投影）、导航列 200、导航项高 34、内容区右/下内边距 20 |

QSS 用 `@TOKEN@` 占位 + `_WBS_TOKENS` 统一替换，不在样式里写死色值；
`wbs_dialog_qss()` 挂在对话框 shell 上，向子控件级联，**不影响对话框之外的界面**。

**结构改造（`pet/settings_dialog.py`）**

- **导航改为三组**：`宠物`（皮肤/画面/台词/环境/新闻）、`对话`（基础/对话/语音/语音输入/人格）、
  `联动`（工作/游戏），共 12 页。分组标题是不可选的导航行（`Qt.NoItemFlags` → 命中 QSS `:disabled`）
- 原「宠物」页实为**人格 / 宠物名称**，改名「人格」以免与「宠物」分组混淆
- **每页包进白色卡片**（`_wrap_card()`），卡片内套 `QScrollArea`——「游戏」页控件多，长页面可滚动不再被裁
- 标题条新增「设置」文字；窗口外框留白 + 单层柔和投影（Qt 的 `QGraphicsDropShadowEffect` 只能叠一层，
  取双层的更外扩那层）
- 导航项不再加粗选中文字（原 Ant 版是 600 字重 + 蓝底）

**控件重做**

| 控件 | 改动 |
|---|---|
| 滑块 | 原为**空心方块**滑钮（`QSlider::handle` 的 `border-radius` 缺 `height` 不生效）→ 改圆形滑钮（14px，radius 7）。行结构统一为 `_slider_row()`：标签 + 滑块 + **最右定宽右对齐数值**，数值不再拼进标签（否则拖动时数字位数变化会让整行抖） |
| 下拉箭头 | 原用 QSS `border-top` 三角 hack，实际渲染成**一个小方块** → 改为真 chevron PNG（`core/icons.py::chevron_png_path()`）。展开时箭头**翻转朝上** |
| 人格图标 | 原 `cat` 线条过多，16px 下糊成一团 → 新增 `user_round`（圆头 + 肩线） |
| 复选框 / 按钮 / 输入框 / 列表 | 全部改 WBS 配色、8px 圆角、绿色强调 |

**顺带修复（原有 bug）**

- 「基础」页切到非千问后端时，`千问 API Key` / `千问模型` 两个**标签**不跟着隐藏，
  输入框藏了标签却孤零零留在页面上（原实现只留了输入框引用）。

**新增工具**

- `tools/preview_settings_dialog.py`：**不启动桌宠**直接把设置对话框渲染成 PNG，
  改 UI 后可立刻目视核对（真实平台 + `Qt.WA_DontShowOnScreen`，窗口不上屏、不碰键鼠）
- `tools/restore_wbs_ui.py`：一键回滚本次 UI 改版，带「选错旧时间戳」防呆

**⚠️ 两个 Qt 陷阱（已写进代码注释，避免后人重踩）**

1. **`QScrollArea.setWidget()` 内部会强制 `widget->setAutoFillBackground(true)`** ——
   页面于是按调色板自绘 `#EFEFEF`，把卡片的白色整块盖掉。必须在 `setWidget()` **之后**再关，提前关会静默失效。
2. **QSS 与全局 `_ANT_QSS` 是按属性合并、不是覆盖** —— 覆盖某属性时必须把 Ant 也声明过的同组属性一起显式声明。
   已踩两处：`QComboBox::down-arrow`（Ant 的三角 hack 会糊在 chevron 上，须显式 `border: none`）、
   `QPushButton:focus` / `:default`（伪状态比普通规则更具体，压不住就会在按钮上露蓝边）。


## 2.22.0（UI 改造：Ant Design 组件库规范 + 设置入口合并 + 全量去 emoji）

> 起因：按《后台管理系统 · 组件库规范 v1.0》（Ant Design 风格）重做全部界面，
> 合并重复的设置入口、移除所有 emoji。仅界面与交互入口变化，功能逻辑不变。

**设计令牌落地（`core/theme.py` 重写）**

| 类别 | 令牌 |
|---|---|
| 品牌蓝 | `#1677FF` / hover `#4096FF` / 按下 `#0958D9` / 浅底 `#E6F4FF` |
| 功能色 | 成功 `#52C41A` / 警告 `#FAAD14` / 错误 `#FF4D4F` / 信息 `#1677FF` |
| 中性灰阶 | `#FAFAFA / #F5F5F5 / #F0F0F0 / #D9D9D9 / #BFBFBF / #8C8C8C / #595959 / #262626` |
| 文字 | primary `.88` / secondary `.65` / tertiary `.45` |
| 圆角 | 2 / 4 / 6（按钮、输入框）/ 8（卡片、弹窗）/ 999（胶囊） |
| 控件高度 | 大 40 / 中 32（默认）/ 小 24 |
| 间距 | 8pt 栅格（8 / 12 / 16 / 24 / 32 / 48） |

- 按钮：主按钮唯一、次要 `btnOutline`、危险 `btnDanger`/`btnDangerOutline`、
  文本 `btnText`/`btnLink`；圆角 6px、高 32px、左右内边距 16px
- 复选框改「蓝底白勾」；下拉选中改浅蓝底；菜单 hover 浅灰、item 圆角 4px
- 旧「深海鲸鱼海洋系（水珠胶囊）」令牌全部替换（`theme.py` / `icons.py` /
  `widgets.py` / `chat.py` / `settings_dialog.py`）

**设置入口合并（唯一设置页）**

- 宠物右键「设置」打开**全局唯一**设置对话框，含宠物页（皮肤/画面/台词/环境/新闻/工作/游戏）
  与 AI 对话页（基础/对话/语音/语音输入/宠物）共 12 个子页
- 聊天窗口**移除**设置按钮与独立设置页；保存时若聊天窗在运行则同步运行时状态，
  否则仅写 QSettings、下次打开读取生效

**设置对话框改无系统标题栏**

- 去掉左上角「设置」窗口文字与图标；改为圆角卡片（8px）+ 顶部可拖动 + 右上角关闭按钮

**右键菜单**

- 移除「修仙游戏」子菜单（启动/停止已在「设置 → 游戏」页）
- 菜单：动作 / 躲猫猫 / 设置 / 登录 DeepSeek / 聊天 / 退出

**全量去 emoji**

- 界面与提示文案移除全部 emoji：右键菜单、聊天状态（上传/思考/说话/回复/新会话/错误）、
  语音输入、新闻播报模板、设置页提示
- 开发脚本（`tools/*`、`nlu/router.py`）的 `✅/❌/⚠️` 标记改为 `[通过]/[失败]/[注意]`、
  注入文本改「成功/失败/充足/缺N」—— 顺带修掉 emoji 在 GBK 控制台打印即
  `UnicodeEncodeError` 崩溃的问题

**顺带修复（与 UI 无关的测试漂移）**

- `tools/e2e_maid_check.py` L5：`attack.target` 自 2.19.1 起为**真实参数**，
  过期断言（期望 target 被丢弃）已同步 → 离线层 11/11
- `tests/test_reconstruction.py::test_game_processor`：补 `_store` 隔离，
  避免误读真实 QSettings（把用户设置的「低频」带进测试）→ 稳定通过

**验证**

- 全量离线回归 **9/9 全绿**：`test_nlu` / `test_reconstruction` / `test_maid_loop` /
  `test_chat_unit`（17 项）/ `test_chat_chain` / `test_wiki` /
  `e2e_maid_check --offline`（11/11）/ `check_leak` / `main.py --smoke`
- 离屏截图核对：设置对话框（含 AI 子页）、聊天窗、右键菜单

---

## 2.21.0（Wiki 链路修复：打通别名口径 + 收敛抢答 + 分类与精炼治理）

> 起因：对 Wiki 链路做了一次**基于实测**的审计（`WIKI_AUDIT.md` + `tools/wiki_audit.py`），
> 发现注入的知识**要么拿不到、要么是错的**，而不是"查得慢"。
> 全部结论由脚本实测得来，非照抄设计文档。三处问题按 P0→P2 一次性修完。

**度量基线（修复前 → 修复后，同一工具复测）**

| 指标 | 前 | 后 |
|---|---|---|
| `[获得] 铁镐 x1` 提取 | `[]` | **`['铁镐']`** |
| `[获得] 钻石剑 x1` | `['钻石']`（语义错） | **`['钻石剑']`** |
| `[获得] 樱花木 x8` | `[]` | **`['樱花木']`** |
| `[击杀] 猪 x1` | `[]` | **`['猪']`** |
| `[获得] 橡木原木 x3` | `['原木']` | **`['橡木原木']`** |
| `樱花木` 的 curated 结果 | `【樱花木板】`（错） | 未命中 → 走精炼/原文兜底 |
| `嗅探兽` 的 curated 结果 | 进度条目`【小小嗅探兽】`（错） | 未命中 → 走精炼/原文兜底 |
| common 类页面数 | 4382（含大量非实体） | **3539** |
| 单页精炼输出上限 | 无（兜底可超原文） | **≤172 字**（< 单页原文上限 180） |

**P0-1 实体提取打通「标题 ↔ 重定向别名」**（`game/wiki_kb.py`）

MC 中文 Wiki 的**材质工具/装备没有独立页面**，只有单字总览页，变体只作重定向
（铁镐→「镐」、钻石剑→「剑」、樱花木→「木头」）。旧 `match_terms` 只对 common
标题做逐条 `t in text` 字面扫描、**不查别名**，于是玩家最常用的一批实体整批漏掉。
- 改为「标题 + 重定向别名」统一词表 + **n-gram 滑窗查表**（O(len×词长)，不再遍历全表）；
  别名只在目标页属于允许类别时收录，version/tech/disambig 的重定向不参与提取。
- **单字实体加上下文守卫**：42 个单字页（猪/剑/床/门/弓…）此前被 `len>=2` 直接排除；
  现在只有附近出现事件动词或数量标记（`[击杀] 猪 x1`）才采信，
  「我去水边看看风景」这类闲聊零误命。
- **长子串优先**：命中「钻石剑」后不再产出子串「钻石」（旧行为会把语义降级）。
- 词表加 `_valid_term` 守门，剔除纯数字/纯符号别名（实测混着「5」，
  会让 `[破坏] 钻石矿石 x5` 多提取一个 `'5'`）。
- 实测耗时 0.7ms → **0.5ms**。

**P0-2 curated 不再「抢答」**（`nlu/wiki_refine/curated.py`）

旧 `find()` 是「双向子串 + 字典顺序首命中」——当查询词没有对应条目时，会拿
**包含它的另一个条目**顶答，直接注入错误实体的知识（`樱花木`→`【樱花木板】`、
`嗅探兽`→进度`【小小嗅探兽】`、`木板`→`【金合欢木板】`）。
- 改为**只认精确**（名称 / id / 可选 `aliases` 字段）。没有条目就返回 `None`，
  交给 L2 精炼模型 / L3 原文兜底 —— **宁可降级，不答错**。
- 实体条目**优先于成就/进度条目**，并去掉完全重复条目：
  修掉「同名条目里后一条覆盖前一条、前者永远查不到」的问题
  （name 查不回自己 38 → 37 条，且剩下的都能按 id 查到）。
- 顺带把严格查找的性能从 0.167ms 降到 **0.022ms/次**。

**P1-1/P1-2 分类与数据治理**（`game/wiki_build.py`）

`common` 里混着成百上千非游戏实体页，既污染实体提取池、也让"覆盖率"失真。
- 新增**版本前缀规则**：`Java版/基岩版/携带版/教育版/中国版/原主机版` 开头的标题
  一律不算实体（实测 78 条全是版本号、数据值、协议、文档、已移除特性，零误伤）。
- 补 `_VERSION_RE`：`Java版Alpha v1.0.1` 这类（「Java版」后不是数字）此前漏判成 common。
- 新增**正文特征**判定：音乐曲目（`是一首由…创作的音乐`/`== 播放条件 ==`）、
  开发方与人员（`工作室`/`音乐制作人`/`== 雇员 ==`）、平台版本页（`原主机版`）→ rare；
  数据格式页（`树状数据结构`/`二进制`/`基于表达式`）→ tech。
- 从 `_RARE_RE` 里**移除「音乐」**：标题含「音乐」的可能是真物品（音乐唱片），
  曲目页改由正文特征识别。
- 结果：common 4382 → **3539**，version 2030 → 2728，tech 338 → 444，rare 765 → 804；
  真实游戏实体（铁镐/僵尸/苦力怕/音乐唱片/试炼密室…）零误伤。
  用 `python game/wiki_build.py --classify` 就地重分类（本地，不联网，约 10s）。

**P1-3 精炼预算逻辑修正**（`nlu/wiki_refine/refine.py`）

- 超预算的句子改为**截断**而非整句丢弃：旧实现 `if len(t) > budget: continue`，
  而 wiki 引言行常 100+ 字，一条长的就把整条要点挤空。
- 空要点的兜底分支**加上硬上限**：旧实现直接塞 `rows[0]` 首句、无长度约束，
  实测兜底输出比 L3 原文还长，与「减小主对话上下文」的目标相反。
- 新增**相似句去重**（字符 Jaccard ≥ 0.7 跳过），
  消掉「该区域有13张贴纸 / 该区域有12张贴纸」这类模板复读。
- 预算 KNOW 140→120、FUN 60→40，新增 `MAX_TOTAL` 硬上限（172），
  保证**单次注入不超过单页原文上限**（`wiki_kb._SNIPPET_LEN` = 180）。

**P2 死代码 / 去重策略 / 文档漂移**

- 删除 `game/mc_log.py` 的在线兜底死代码：`wiki_search` / `_wiki_fetch` /
  `_WIKI_API` / `_WIKI_CACHE` / `_WIKI_LOCK` / `_WIKI_SESSION` / `_WIKI_EXTRACT_MAX`
  及仅供其使用的 `threading` / `time` / `urllib.parse` 导入（全项目零调用）。
  `extract_terms` 依赖的 `_WIKI_TERMS` / `_WIKI_TERMS_CN` 兜底词表保留。
- `game/processor.py`：`_wiki_knowledge` 现返回 `(注入文本, 产出过知识的词)`；
  去重记账改为**只记真正产出知识的词** —— 旧实现「无论命中与否都记录」，
  某次因库状态没查到就会整局不再尝试（只能换会话重置）。空结果的词现在会自动重试。
- 修正文档漂移：README 的知识库体量（70MB → 实际 245.8MB）、
  `DESIGN_WIKI_REFINE.md` 的 curated 匹配口径与 fun 实际覆盖、`match_terms` 的能力描述。

**追加：注入总额预算 + L2 逐实体分组**

审计复查时又暴露两个问题（详见 `WIKI_AUDIT.md` §3.6 / §3.7），一并修掉：

- **整条 `{wiki}` 注入此前没有总额约束**：L1 是**逐词**产出块的，而模组 highlights
  最多给 12 个词 → 实测 12 词全命中注入 **1460 字**（L3 原文路径只有 80~92 字，差 16 倍）。
  `GAME_WIKI_LIMIT = 2` 只约束 L2/L3 的**页数**，管不到 L1。
  → 新增 `config.GAME_WIKI_MAX_CHARS = 480` + `_clip_knowledge()`：按
  「L1 命中块（按模组给的顺序，具体词条优先）→ L2/L3 兜底块」贪心塞入，超预算截断；
  块间换行也计入预算（总长有硬上界）；截断优先收在换行/句读边界。
  实测 12 词全命中 **1460 → 468 字**；单物品 / 3 物品不受影响（165 / 383 字）。
- **L2 批量路径只覆盖 2 页、且句子无实体标注**：`search_candidates(terms, limit)` 的 limit 是
  **跨词共享**的页数上限 → 6 个词只取到「猪」「牛」两页，其余实体完全没进候选；且 refine
  输出不带实体名，多实体时跨实体混排（实测「猪」的段落后面直接接「它们是皮革、生牛肉…」，
  那句讲的是**牛**）。
  → `search_candidates` 新增 `per_term`：精炼路径**逐词各取 1 页**，给每行打 `term`/`title`，
  且同一页面不被两个词重复取用；`refine.refine()` 见到 `term` 就**按实体分组**分别选句、
  每块加 `【实体名】` 前缀。实测覆盖实体 **2 → 6**。
- **顺带修掉「填满剩余额度」的副作用**：精炼在剩余预算放不下一整句时，改为**跳过该句、
  给后面的短句机会**，而不是把句子切碎塞进去（实测会注入到「…无法被喂食除金」这种碎片）；
  「什么都没选上」仍由兜底分支保证至少有一条内容。
- 截断实现抽成共用函数 `nlu.wiki_refine.refine.cut_at_boundary`，精炼与整条注入预算共用同一口径。
- 成本：L2 `search_candidates + refine` 76ms → 168ms/次（6 词）、约 300ms（12 词）。
  纯本地 numpy 前向、跑在工作线程，不影响 UI。

**新增测试** `tests/test_wiki.py`（9 项，无框架纯断言）

覆盖：别名命中 / 单字守卫 / 长子串优先 / curated 不抢答 / 同名实体优先 / 完全重复去重 /
精炼预算（跳过放不下的长句 + 相似去重 + 兜底上限，含 `MAX_TOTAL ≤ _SNIPPET_LEN` 不变量）/
分类规则（版本族·技术·非实体 → 过滤类，真实体保 common）/
**L2 逐实体分组（不跨实体混排）** / **整条注入总预算 + 记账语义**。
`curated`、`wiki_kb`、`wiki_build` 三项不依赖数据库与 numpy；精炼与真库用例仅在可用时执行。

**新增工具** `tools/wiki_audit.py` → `tools/wiki_audit_report.txt`

一键复现全部指标（数据层体量 / curated 覆盖与可达性 / 标题与别名口径 /
实体提取效果 / 三层链路产出与体量 / 耗时）。**改动 Wiki 相关代码或数据后重跑它对比即可。**

**验证**

- `tests/test_wiki.py`（8 项）· `test_nlu.py`（27 类）· `test_reconstruction.py` ·
  `test_maid_loop.py`（14 项）· `test_chat_unit.py`（17 项）· `test_chat_chain.py`（5 项）全过
- `main.py --smoke` exit=0

**已知未做（诚实清单）**

- curated 的 `fun`（趣闻）仍只有 25/1490 条非空 —— 这是**内容工作量**，
  需要像原批次那样逐条撰写 + 审核，不适合机械生成，留待专门一轮。
- 37 组同名（不同 id）条目仍并存（多为其「FAQ」页）；名查询取实体条目，其余按 id 可达。
- `pages_fts`（FTS5）索引建了但检索路径不使用，占一部分体积；正文 FTS 兜底曾被
  刻意禁用（正文沾边会带出无关页），暂无启用计划。
- 剩余 148 条"疑似非实体"标题（如 `…定义格式` 系列）未再细分类。

---

## 2.20.1（同步 SmartMaid 女仆战斗系统 —— 新增 `eat` 指令 / 意图）

> 起因：SmartMaid 落地了女仆战斗系统（C0–C5，提交 `275bc59`），模组指令层新增 `eat`
> （女仆自己进食回饱食度）。桌宠侧需按「契约单一事实来源」同步，否则 NLU 不认识"吃点东西"、
> 契约回归测试会红。

- `nlu/mod_contract.py`：新增 `eat` 指令（`item` 可选）；契约锚点 `d46056b → 275bc59`
- `nlu/taxonomy.py`：新增 `eat` 意图（integrated，auto）；`NEED_ITEM` 加入 eat
- `nlu/synth.py` + `nlu/hard_cases.py`：新增 eat 语料与对抗样本（"吃点东西"/"吃个金苹果" vs "喂我吃东西"）
- `tests/test_nlu.py`：`MOD_TASK_COMMANDS` / `MAID_INTEGRATED` 加入 eat
- `game/maid_link.py`：任务名映射新增 `eat → 进食`
- **重训 NLU**：27 意图，val_acc 0.9921，人工集 89.9%，闲聊误激活 0；`tests/test_nlu.py` 全过

## 2.20.0（按后端分离 prompt：网页版全长人设 / API 精简人设 + 模式声明改走 system）

> 起因：两条后端的信息流**根本不对称**，却共用同一份人设与同一套「现在切换到 x、模式名」
> 声明写法。网页版（SessionBackend）服务端自动记忆，system **只在会话首条注入一次**；
> 官方 API / 千问（MessagesBackend）无状态，system **每轮都要重发**（实测 `messages[0]`
> 永远是人格 prompt）。共用一份 6900 字人设，等于 API 每轮都在重烧这份 token。

**两条后端的信息流（本次固化为代码里的红线）**

| | 网页版 `deepseek` | 官方 API / 千问 |
|---|---|---|
| 记忆 | 服务端（`session_id` + `parent_message_id` 链） | 客户端组装，无状态 |
| 每轮发什么 | 只有当轮一句话 | `system(人设+模式) + system(记忆) + 最近 20 轮 + 当轮` |
| 人设 prompt | **只在首条拼进 user 消息** | **每轮作为 `messages[0]` 重发** |
| 模式声明 | 塞进 user 消息（服务端记住，只发变化时） | **写进 system，每轮自动刷新** |

**改动**
- **人设文件按后端分离**（`config.PERSONAS[i]["file_api"]`）：
  新增 `prompt_api.txt`（女仆，4587 字含模式段）/ `prompt2_api.txt`（御坂美琴，5235 字含模式段），
  对应全长版 `prompt.txt`（6923 字）/ `prompt2.txt`（7078 字）——**每轮 system 省 34% / 26%，
  且随轮数线性节省**。精简原则：保留全部**功能性条款**（女仆指令 / 输出格式硬约束 /
  情绪惯性判定 / 白饭禁令 / 动作词白名单），把**长段叙述压成短句**、
  **输出范例从多行改为一行一条**。
  ⚠️ **女仆指令（AI-DSL 闭环）章节必须保留**——否则 API 后端下 AI 无法主动指挥女仆。
- **`config.load_system_prompt(persona, pet_name, backend=None)`**：新增 `backend` 参数，
  走 MessagesBackend 时读 `file_api`，其余读 `file`；精简版缺失自动回退全长版（人设不丢）。
  **`backend=True` 表示强制取精简版**（给 `get_api_prompt` 用，不受当前后端影响）。
  新增 `config.is_messages_backend(name=None)`（自动探测当前后端）、
  `config.mode_prompt(mode_id)`、`config.get_api_prompt(persona, mode_id, pet_name)`。
- **模式声明分流**（原来 3 处无条件注入「现在切换到 x、模式名」）：
  - `ai/chat.py::_ChatWorker._prepare_prompt`：仅网页版注入；API 后端 user 消息保持纯净
    （不再污染 history 被反复重放）。
  - `game/processor.py::_switch_decl`：API 后端直接返回空串。
  - `pet/handlers/work_handler.py::_tip_worker`：API 后端不拼模式前缀。
  - **API 侧新模式声明走 system**：`MessagesBackend.build(..., mode_id=None)` 把
    `【当前模式】+ 该模式规则` 追加到 system 末尾；`ChatService` 从 `ai_default_mode` 取值传入。
- **人格切换注入分流**：`ChatService.chat` 与 `ChatWindow._send` 里「把全长人设塞进
  user 消息」的补丁**仅网页版保留**（DeepSeek 多轮会话 system 只注首轮，必须补）；
  API 后端每轮重发 system，人设已随 system 刷新，无需污染 history。
- `ChatWindow._ChatWorker._system_prompt()`：统一按后端产出 system
  （API → `get_api_prompt`（精简+模式）；网页版 → `opts["prompt"]` 全长）。

**精简版精修轮（实测驱动的第二轮，把功能性约束全部补回）**

真实 API 实测（2 人设 × 3 模式 × 3 条 = 18 条）暴露精简的**副作用**——压缩时把「约束语气」
一起减掉了，模型就开始漏。本轮修 4 处：

1. **动作标签格式**：御坂版输出 `[坐下]`（方括号）。补「必须用 `{}` 包裹（不是 `[]`、不是 `()`）」+ 示例。
2. **必须带标签**：御坂版 2/3 条完全不带 `{动作}`。补「**每条回复都必须带上一个 `{动作}`，不得省略**
   （这是在驱动桌宠动画）。即使是工作模式的短句也要带」。
3. **占位符照抄**：女仆版输出 `【指令名(attack(range=10))】`。补「『指令名』只是格式占位符，
   **不要照抄**」+ 正误对照。
4. **「女仆指令」被写成游戏模式限定** → 改为「**全域可用，不限游戏模式**」，明确日常/工作/游戏
   都适用，并给出「感知到女仆在场」的判定口径（消息里带 `[态]`/`[系统]` 即算）。
   同时新增 **7 条跨模式指令范例** + **模式差异段**（三模式共用格式硬规则，`{动作}` 一律必带）。

**精修前后实测对比**

| 指标 | 前 | 后 |
|---|---|---|
| 带 `{动作}` 标签 | 14/18 | **18/18** |
| 动作词写 `[]` | 3/18 | **0/18** |
| 工作模式夹带人设梗 | 有 | **0/9** |
| 游戏模式主动发指令 | 3/6 | **6/6** |
| 指令顺序违规 | 2/6 | **0/6** |

游戏模式结构化校验 `tools/test_api_game.py` **6/6 全过**（标签格式 / 指令顺序 / 参数合法性）。

**顺手修的真 bug**：`config.get_api_prompt` 原先传 `backend=None`（=自动探测当前后端），
导致「API 专用提示词」是否取精简版取决于"此刻恰好连着谁"——离线生成/测试时会错误拿到全长人设。
已改为 `backend=True` 强制精简。

**验证**
- `tests/test_chat_unit.py` 新增 4 项：`[U1b]` API 后端不注入模式声明、
  `[U1c]` system 按后端分流（**全长 6923 字 → 精简 4359 字**，且精简版含女仆指令）、
  `[U1d]` **`test_api_prompt_global_maid_and_modes`**（逐条钉住全域女仆指令 /
  全域可用字样 / 跨模式范例 / 模式差异段 / `get_api_prompt` 恒取精简版）；
  `[U1]`/`[U6]` 补 `backend="deepseek"` 保持网页版断言。
  → **17 项全过**
- 组装实测（`tools/probe_prompt_split.py`）：API 各轮 `system[0]` 含【当前模式】与女仆指令，
  **user 消息不含「现在切换到」**；网页版仍靠 user 消息声明。
- `tests/test_chat_chain.py`（5 项）、`test_maid_loop.py`（14 项）、`test_nlu.py`（26 项）、
  `test_reconstruction.py` 全过；`main.py --smoke` exit=0。
- 完整实测报告见 `API_PROMPT_TEST_REPORT.md`。

**环境备忘**
- 跑带 PySide6 的测试/`--smoke` 用**系统 Python**：
  `C:\Users\86187\AppData\Local\Microsoft\WindowsApps\python.exe`（3.10，装齐 PySide6）；
  受管 3.13 与 nlutrain venv **都没有 PySide6**（只能跑纯 numpy 的 NLU 测试）。

---

## 2.19.2（NLU 标签口径规范化：collect/pickup 合并 + 意图边界定稿）

> 起因：NLU 的剩余错例**大多不是模型能力问题**，而是几对意图在语义上重叠
> （`DESIGN_NLU.md §11.1` 早就指出「当前最大瓶颈是标签口径，不是模型」）。
> 本轮把口径定死，再按规范改语料重训。

**口径定稿**（依据是模组源码行为，不是文档描述——核对 `MaidAIBridge.java`）

| 组 | 模组侧事实 | 定稿 |
|---|---|---|
| `collect` ↔ `pickup` | 同一个 `CollectTask`，只差默认半径（8 / 4） | ✅ **合并为 `collect`**，半径改由规则产出 |
| `store` ↔ `equip` ↔ `transfer` | `store` 只收主手、不收 `item`；`equip` 只到主手 | 「收拾」→ `store`；「点名物品搬到某槽」→ `transfer`；「指定拿到手上（切出镐子/换成剑）」→ `equip` |
| `break` ↔ `mine` | `break` 单格瞬发；`mine` 一片（带挖掘过程） | 「这一个方块」→ `break`；带范围/一片/矿石 → `mine` |
| `guard` ↔ `move` ↔ `look` | `guard` 会主动打威胁 | 守/护/挡 → `guard`；过去/站我旁边 → `move`；只看不动 → `look` |
| `build` ↔ `place` | `build` 是结构；`place` 用主手放一格 | 建筑/结构 → `build`；单个方块 → `place` |

**改动**
- **意图体系 27 → 26 类**（`nlu/taxonomy.py`）：删除 `pickup` 意图并入 `collect`；
  分层变为 basic=10 / integrated=13 / meta=3。
- **`collect` 半径规则**（`nlu/slots.py::collect_radius`）：出现范围词（一片/附近/周围/掉落物/都…）→ 8；
  否则出现单目标词（就近/这个/脚边/捡起来…）→ 4；都没有 → 8（模组默认）。
  「地上/撒/散」这类**位置词不算范围词**（否则「把地上那个盾拾起来」会被误判成 8）。
- **模组 `pickup` 指令仍合法**（`/maidtasks pickup`、AI DSL 都能用），只是 NLU 不再产出该意图；
  `nlu/mod_contract.py` 保留 `pickup` 契约并标注为「契约有 / 意图无」。
- **语料**：`synth.py` 的 pickup 模板并入 collect；`hard_cases.py` 按新口径重写 store/transfer/equip/
  collect 的对抗样本（新增 22 条，全部通过泄漏审计）。人工语料 / 蒸馏语料共 **56 处 `pickup` 标签
  迁移为 `collect`**（含 26 处 soft 软标签分布合并）。
- **人工测试集**：8 条 `pickup` 标注按新口径改为 `collect`（语义合并，非迁就模型）。
- **测试**：意图数 27→26、分层对账豁免「契约有 / 意图无」（`pickup`、`craft_check`）、
  新增 collect 半径用例；`CONTRACT_ONLY` 白名单防止漏删。

**配套：全链路测试基建（本次为「用代码喂聊天」新增）**
- `ai/chat.py`：新增 `ChatWindow.submit_text(text)`（= 在输入框打字 + 回车，走同一条链路）与 `busy()`。
- `pet/window.py`：新增 `.chat_input.json` 注入口——每 0.6s 轮询，有内容即当作一次用户聊天输入；
  聊天窗没开会自动非模态创建一个（手动入口仍是模态 `exec`，互不影响）。与 `.maid_command.json` 同款调试通道。
- `ai/chat.py`：新增 `logs/chat_trace.log` 追踪日志（`USER` / `NLU` / `AI` 原文 / `DSL` 决策逐行），
  让全链路测试有**可核查的文本证据**（否则只能肉眼看气泡）。低频，每次聊天几条。
- `tools/chat_chain_test.py`（新）：`python tools/chat_chain_test.py [--only T13]` —— 无需鼠标键盘的
  真机全链路测试（本地 NLU → AI → DSL → 真女仆 → 回执），逐条落证据；前置检查 21420 是否在监听。
- 首次实测（14 条真实口吻）见 [CHAIN_TEST_REPORT.md](docs/reports/CHAIN_TEST_REPORT.md)：**7 通过 / 1 部分 / 6 失败**，
  并暴露了 1 个由本轮语料引入的回归（`transfer` 样本污染 `equip`）与 1 个长期缺口（「X子」后缀拼音匹配不上官方物品名）。

**验证**
- 重训（`--n-per-intent 400 --manual ×5 --distill ×12`，与上一版同配方）：
  合成验证集 **97.44% → 97.99%**；人工测试集 **87.50% → 88.46%**，
  自动执行档 134 条 / 96.27%，**闲聊误激活 0.00%**
- 收集类实测：合并前 `collect` 5/6 + `pickup` 7/8 = 12/14 → 合并后 `collect` **13/14**
- `check_leak.py` 必须零重合的两类均零重合；`test_nlu`（26 项）/`test_reconstruction`/
  `test_maid_loop`/`test_chat_unit`/`test_chat_chain` 全过；`main.py --smoke` exit=0
- ⚠️ 差值 0.96pt 在 208 样本的标准误（±2.1%）内，**不宣称准确率提升**；
  本轮收益是**语义**上的（消灭了一个模型不可能学出的边界）

**未解决（留给下一轮）**
- ⚠️ **真机实测发现的回归**（见 [CHAIN_TEST_REPORT.md](docs/reports/CHAIN_TEST_REPORT.md) §四 R1）：本轮给 `transfer` 补的对抗样本
  「把铁镐收进背包」把「铁镐」这个高频 `equip` 宾语带进了 transfer 分布 → 「换上新打的铁镐」由
  合并前的 `equip 0.91`（可自动执行）退化为 `transfer 0.47`（不可执行）。修法很小（换掉该样本 + 给 equip 补铁镐类）。
- ⚠️ **「X子」后缀拼音匹配不上官方物品名**（R2）：README 招牌例「帮我合成一把稿子」真机下解析不出物品
  （官方名是「木镐/铁镐」，没有「镐子」词条；单测假词典恰好含「镐子」所以一直是绿的）。
- `store` ↔ `collect` 仍偶混，且两个候选置信度被打平（0.50 / 0.48），双双落到阈值下（R3）
- 测试集 `把工作抬放到背包` 标 `store`，按新口径（点名了物品）**应为 `transfer`**；
  本轮**刻意不改**，避免抬高分数——口径是否对齐待定
- 复合句、`farm`↔`place`、`sit`↔`stop` 老错例仍在（单标签模型固有局限）
- 人工测试集（208 条）措辞偏「教材式」，覆盖不到真实口吻 → 建议把真机测试用例收进去

---

## 2.19.1（attack 指定目标 + prompt 指令格式修正）

> 修掉「AI 指挥女仆执行不好」的两大根因：**prompt 里 `【cmd(...)】` 被 AI 当成字面指令名**，
> 以及**「打猪获取食物」在模组侧根本做不到**。

**新增**
- **`attack` 支持指定目标**（模组 + 桌宠联动）：`attack(target=minecraft:pig, range=10)` 打最近的该种
  生物（猪/牛/羊/鸡/兔…），不指定则仍打最近敌对生物。
  - 模组 `AttackTask` 新增「按实体类型索敌」模式 + `PerceptionBlockUtil.findNearestByType`；
    `MaidAIBridge` / `/maidtasks attack [range] [target]` 均支持 `target`。
  - 桌宠 `nlu/mod_contract` 的 `attack.target` 从 `advisory` 升级为**真实参数**；
    `nlu/slots.MOB_IDS`（生物中文名→实体 id）把「猪」解析成 `minecraft:pig`；
    `router._fill_params` 透传 target（泛称「怪」不传，走敌对逻辑）。

**修复**
- **prompt 指令格式歧义**（`prompt.txt` / `prompt2.txt`）：`格式：【cmd(参数=值, ...)】` 里的 `cmd`
  是占位符却被 AI 当字面指令，输出 `【cmd(...)】`（`crash.log` 的「未知指令: cmd」）。
  改为 `【指令名(参数=值)】` + **具体示例 + 反例**：禁止 `cmd(...)`、禁止 fly 等未列指令、
  坐标必须 `[x,y,z]`；并加入 `attack(target=...)` 获取食物的正例。
- 补 `test_nlu` 的 `attack 指定目标` 用例；`test_maid_loop` 的 target 断言同步为「现在是合法参数」。
- **槽位消歧：完整物品名优先于其字面子串**（`nlu/slots.py::pick_target`）：语音/错字「钻石**搞**」
  （搞/镐同音）此前会选中字面命中(1.0)的「钻石」，而漏掉拼音命中(0.95)的「钻石镐」——
  现在 drop/equip/transfer 等意图统一按「更长的完整名」优先，再比分数、再取靠前出现位置。
- **NLU 诊断日志**（`pet/modes.py`）：`logs/perf.log` 的 NLU 行现在记录「用户原话 → 识别到的物品/目标」，
  便于事后定位「说的是 A、识别成 B」这类问题。
- **丢出改为「丢到主人身边」**：此前 `transfer … world:<女仆坐标>` / `drop` 都掉在女仆脚下，
  会被她的拾取逻辑（`setCanPickUpLoot(true)`）立刻收回背包，等于没丢。
  现在 NLU 指定物品的丢出目标取**主人坐标**（拿不到才退女仆）；模组侧 `drop` / `transfer→world`
  统一丢到主人位置，并让女仆 **5 秒内不拾取**（`SmartMaidEntity.suppressPickup`）。

**验证**
- 模组 `gradle build` BUILD SUCCESSFUL（JDK 25）
- 桌宠 `test_nlu`（含新用例）/ `test_maid_loop` / `test_chat_unit` / `test_chat_chain` /
  `test_reconstruction` 全过；`main.py --smoke` exit=0

---

## 2.19.0（通讯闭环：官方 API + DSL 指令 + AI 决策 + 限流去重）

> 打通「用户 → 桌宠 AI → 游戏女仆 → 回执 → 用户」的决策闭环。AI 回复可夹带
> `【DSL 指令】` 指挥女仆干活；女仆实时状态/事件/回执以「当轮注入、不进历史」
> 的方式喂给 AI；新增 DeepSeek 官方 API 后端与全局软限流。设计见
> `DESIGN_LOOP_DEEPENED.md`。

**新增**
- **DeepSeek 官方 API 后端**（`ai_backend = "deepseek-api"`）：`ai/deepseek_api.py`
  OpenAI 兼容客户端（`deepseek-chat` / `deepseek-reasoner`），设置里配置 Key 即可切换。
  - 与网页版（服务端自动记忆）不同：官方 API **无状态**，历史由客户端管理——
    `ai/backends/messages.py`（MessagesBackend）负责组装 + **滑动窗口（最近 20 轮原文）
    + 每 30 轮增量提炼**（`ai/memory.py`，只提炼滑出部分与已有记忆合并），**绝不整段重发历史**。
  - 网页版（`ai/backends/session.py`，SessionBackend）保持**只发当轮 prompt**、服务端自动记忆、
    **不做提炼**（历史在服务端，客户端不碰）。
  - `ephemeral` 轮次（游戏事件 / AI 决策 / 播报润色）**不进主对话历史**，共用主会话下实现隔离。
- **女仆指令 DSL**（`game/maid_intent.py` + `game/maid_loop.py`）：AI 回复末尾可夹带
  `【attack(range=10)】` 等指令，桌宠解析（白名单单一来源 `nlu/mod_contract`）→ 下发给女仆
  → 回执注入下一轮 `[系统]` 上下文。聊天窗口自动接入（`_ChatWorker` 工作线程处理，不阻塞 UI）。
- **AI 主动决策**（决策边界：低血 <30% / 被围 3+ 敌对 10 格内）：`maid_handler` 每秒检查，
  命中后调一次 AI 决策（`ephemeral`，30s 冷却），回复可夹带 DSL（允许抢占）。
- **状态注入**（`game/maid_context.py`）：女仆感知快照 → TransientContext（完整/极短两种形态），
  注入当轮 AI 上下文。
- **全局软限流**（`core/ai_throttle.py`）：30s ≤5 / 5min ≤35 / 1h ≤300，只作用于
  游戏/女仆闭环链路，用户主动聊天不受影响；超限降级模板播报。
- **双通道去重**（`core/event_dedup.py`）：WS event 与文件 jsonl 共享指纹去重（60s TTL），
  任务启停类豁免。
- **默认排队**：AI 指令 `cancel_previous` 默认 **False**（不抢占女仆任务），仅 AI 决策允许抢占。
- **验证套件**（`tools/e2e_maid_check.py`）：离线 11 项（多事件/历史压缩/隔离/去重/限流/排队）
  + 在线层（女仆连接/感知/真实 AI→DSL→下发→回执）。
- **prompt**：`prompt.txt` / `prompt2.txt` 新增「女仆指令」章节。

**修复**
- **NLU 槽位抽取**（`nlu/slots.py::pick_target`）：同起点子串消歧改为**更长匹配优先**——
  「帮我做钻石稿」此前错抽成「钻石」（不可合成不执行），现正确抽出「钻石镐」。
- **设置对话框**（`pet/settings_dialog.py`）：三后端 UI 下点「设置」无反应的 bug——
  `_update_backend_ui` 过早访问未构建的 `voice_combo`，已加判空保护。
- **回归测试**：`test_nlu.py` 新增「钻石稿」防回归断言；新增 `test_maid_loop.py`（14 项）。

**验证**
- 单元测试：`test_maid_loop.py` 14 项 / `test_reconstruction.py` / `test_nlu.py` 全过
- `main.py --smoke` exit=0
- 真实端到端（官方 API + 女仆）：AI 输出 `【attack(range=12)】` → 解析下发 → 回执注入；
  感知 diff 首帧修复后全量 3585B → 增量 308B（降 91.4%）
- 真机联调：女仆连接 / 感知 / 事件播报 / 指令回执 / 物品索引 1516 条 全通

---

## 2.18.1（指令分层对齐：基础 vs 集成）

> 把 `MaidTaskCommand.java` 第 51-67 行 javadoc 的官方分层（**基础指令 / 集成指令 / 元命令**）
> 接进桌宠 NLU。此前 27 类意图平铺、router 的组合逻辑藏在特例分支里——现在显式分三层。

**改动**
- `nlu/taxonomy.py`：`Intent` 加 `layer: "basic"|"integrated"|"meta"` 字段；27 类全部按
  MaidTaskCommand javadoc 标层（basic=11 / integrated=13 / meta=3）
- `nlu/mod_contract.py`：`Command` 加 `layer` 字段；27 条全部标层；
  `craft_check` 单独标 `meta-dryrun`（桌宠内部查询，不产生任务）
- `nlu/router.py`：`_plan()` 重构为**显式三层派发**：
  - `_plan_basic`：单原子；`equip`/`drop` 指定槽位/物品时组合 `transfer`（basic 增强）
  - `_plan_integrated`：任务级；容器类前置 `chestopen`、`transfer` 反查 `from`
  - `_plan_meta`：`cancel`/`status` 无参数直发；`chat` 不产生指令
  - 通用 `_fill_params()` 按契约填参，basic 与 integrated 共用
  - 集成失败**不回退**到基础原子直接调，降级为 hint
- `tests/test_nlu.py`：新增 `test_layer_alignment`——与 MaidTaskCommand javadoc 双向对账

**验证**
- `tests/test_nlu.py` 24 项全过（含新增 [10] 分层对账）
- 行为完全不变：23 项原测试零修改通过

---

## 2.18.0（本地预处理 NLU：意图识别小模型 + 与模组指令层对齐）

> 新增本地「意图识别 + 槽位抽取」预处理层。用户输入（聊天 / 语音）先经**本地小模型**判断意图，
> 高置信的「指挥女仆干活」意图**直接执行**（如自动合成），并把「原话 + 是否已执行 + 配方/背包明细」
> 一并交给大 AI 润色回应。详见 [`DESIGN_NLU.md`](docs/design/DESIGN_NLU.md)。

### 2.18.0-a 主体：NLU 子包 + 自动执行

**新增**
- **NLU 子包 `nlu/`**（纯 numpy，运行时零新增依赖）
  - `taxonomy.py` 意图体系：27 类，与模组 `/maidtasks` 的 24 条指令逐字对齐 + `status`/`cancel`/`chat`
  - `synth.py` 语料合成：模板 × 词典 × 口语变体 × **同音错字噪声**（模拟语音识别错误），固定种子可复现
  - `features.py` / `model.py` 字符 1/2/3-gram + EmbeddingBag + 隐层 + softmax，自实现前向/反向/Adam
  - `train.py` 离线训练/评估/导出（含置信度分档、每类 P-R-F1、混淆对、**闲聊误激活率**）
  - `intent.py` 运行时推理：`≥0.85` 自动执行、`0.55~0.85` 仅提示、`<0.55` 不干预
  - `slots.py` 槽位抽取：物品名四段匹配（字面 / **拼音模糊** / 近音容错 / 首字母）+ 容器降级 + 动词邻接 + 数量绑定
  - `router.py` 意图路由与执行编排（craft 走 dry-run → 执行 → 结果汇总）
  - `build_pinyin.py` 离线拼音表：固化 CJK 基本区 20892 字（247KB），**运行时不依赖 pypinyin**
  - `data/` 训练产物：`model.npz`(1.35MB, float16) / `vocab.json` / `pinyin.json` / `report.json`
- **发图片给宠物看**之外的第二种「本地工具」能力：语音说「帮我合成一把稿子」→ 本地识别为
  「合成 + 镐子」（拼音模糊匹配命中 ASR 错字）→ 查配方查背包 → 材料够则自动合成
- **模组侧新增两个接口**（SmartMaid）
  - `item_index`：握手后下发「当前语言物品名 → `minecraft:id`」整表，覆盖 mod 物品、随语言变化；桌宠据此做中文/拼音模糊匹配
  - `craft_check`：**干跑查询**（不消耗材料），逐项返回配方所需材料 / 女仆现有 / 缺多少（tag 类配方给出候选列表）

**接线**
- `game/maid_link.py`：新增 `item_index()` / `inventory_counts()` / `request()`（等回执）/ `query_craft()`，处理 `item_index` 消息
- `pet/handlers/nlu_bridge.py`（新）：把 `MaidHandler` 适配成 router 的 Bridge 协议，便于单测替换
- `pet/modes.py`：`nlu_router()` / `nlu_preprocess()`（懒加载 + 失败静默回退）+ NLU perf 日志
- `ai/chat.py`：`ChatWindow(preprocessor=…)`，聊天 worker 在提示词前置注入结构化结果
- `pet/handlers/stt_handler.py`：语音链路同样前置（语音错字重灾区）
- `config.py`：`NLU_ENABLED` / `NLU_AUTO_THRESHOLD`

**设计原则**
- **意图靠模型、槽位靠词典、事实靠游戏**：物品名用词典+编辑距离（可解释、零误配），
  配方与背包以游戏内为唯一事实来源
- **宁可漏执行，不可乱执行**：置信度阈值 + 意图白名单 + 槽位齐备检查 + 未连接不执行
- **预处理永不拖垮聊天**：模型缺失 / 依赖不可用 / 模组异常 / 任何异常输入 → 一律原样回退大 AI

**验证**
- `tests/test_nlu.py` 新增 13 项回归（体系 / 语料 / 特征 / 模型存取 / 意图质量 / 报告门槛 /
  槽位 / 路由五种分支 / 健壮性），有、无 pypinyin 两种环境均通过
- `tests/test_reconstruction.py` 全过；`python main.py --smoke` exit=0（`crash.log` 未增长）

### 2.18.0-b 指令层对齐（与 SmartMaid 接轨）

> 桌宠的「训练」与「执行」都依赖模组指令层，因此把协议层抽成单一事实来源并逐条核对。

**新增**
- **模组指令契约 `nlu/mod_contract.py`**：27 条指令的参数名 / 类型 / 默认值 / 取值域 / 前置条件
  全部固化，锚定 SmartMaid `d46056b`。`clamp()` 自动丢弃模组不认识的参数并把数值夹进合法区间——
  「把 `slot` 塞给 `equip`」「发 `transfer` 却不给 `from`」这类对齐错误变成结构性不可能
- **对抗语料 `nlu/hard_cases.py`**：易混淆边界（break↔mine、build↔place、look↔move、
  equip↔transfer、store↔collect↔pickup、cancel↔stop、status↔chat…）与复合句主意图约定，
  与模板语料分离以便审计
- **去泄漏防护**：`tools/check_leak.py` + 训练时自动剔除与人工测试集逐字相同的样本
  （曾误把 27 条测试句抄进训练语料，把 89.9% 虚报成 98.1%——现已加守卫杜绝）
- **真实语句评测接入训练**：`--manual-test` 让每次训练同时报「合成验证集」与「人工测试集」两个数

**修复（对齐过程中发现，均为真机必失败项）**
- **`transfer` 缺 `from`**：模组 `from`/`to` 都必填，之前只发 `to` → 必然失败。现在用背包快照反查物品所在格补出
- **相对坐标未换算**：`pos` 传中文字符串（「我脚下」）模组解析不了。现在 `owner` → 主人坐标、`maid` → `"~"`
- **`chestput`/`chesttake` 未前置开箱**：模组要求容器已打开。现在自动前置 `chestopen`，
  且**等它真正跑完**（该指令是异步任务，回执只说「已受理」）
- **`equip` 收到无效 `slot` / `drop` 收到无效 `item`**：模组这两个指令只作用于主手。
  现在「戴头盔到指定槽位」「丢指定物品」改为组合 `transfer`（`inv:<格>` ↔ 装备槽 / `world:<坐标>`）

**模组侧补全**
- `OwnerSense` 补 `pos`（原来只有 `dist`）：「在我脚下放方块」「到我这儿来」这类相对指令才能定位

**说明（诚实口径）**
- **合成验证集准确率偏高（97~100%）**：验证集与训练语料同源，不能当产品指标
- 产品指标看**人工测试集**（真实口吻 + 对抗样本 + 复合句，`tools/eval_manual.py`）。
  208 条样本、2 个随机种子对照：

  | 配置 | top-1 | 自动执行档（≥0.85） | 闲聊误激活 |
  |---|---|---|---|
  | 上一版模型（v3） | 89.90% | 124 条 / 97.58% | 0 |
  | 本版 · 对抗语料关闭 | 84.86% | 117~120 条 / 95.7~96.7% | 0 |
  | **本版 · 对抗语料 0.25 倍（默认）** | **87.74%** | 122 条 / **98.36%** | 0 |
  | 本版 · 对抗语料 1 倍 | 85.58% | ~97% | 0 |

  → top-1 **没有超过**上一版（差 2.16pt，而差值标准误约 2.6pt，统计上不可区分）；
  真正要紧的自动执行档略好（98.36% vs 97.58%），同样在噪声内。
  **本轮确定收益是指令层对齐（3 个必失败 bug + 主人坐标 + 可回归契约），不是准确率。**
- 剩余错例集中在「相邻意图**标签本身有歧义**」（`store`↔`transfer` 都以背包为目标、
  `collect`↔`pickup` 在模组里是同一任务只差半径）与复合句主意图约定 —— 属**标签口径**问题，
  需先定规范再改语料（详见 DESIGN_NLU.md §11.1）

---

## 2.17.0（DeepSeek V4.1 适配：图片上传 + 移除快速/专家模式）

> DeepSeek 于 2026-09-10 发布 V4.1 Flash。实测确认**协议层未变、原客户端可直接使用**；本次按新版网页端能力做两件事：新增「发图片给宠物看」（识图），并移除已失去意义的「快速/专家模式」设置项。

**新增**
- **图片上传（识图）**：聊天窗口输入框左侧新增「📎」按钮，选图后在输入区上方显示缩略图（可单独移除，最多 4 张），随下一条消息一起发送。
  - 链路：`POST /api/v0/file/upload_file`（multipart，字段名 `file`）→ 返回 `file_id` → `POST /api/v0/chat/completion` 携带 `ref_file_ids`。
  - **关键坑（实测）**：上传用的 PoW 挑战 `target_path` 必须是 `/api/v0/file/upload_file`，指向 completion 会返回 `40301 INVALID_POW_RESPONSE`。无需 `fork_file_task` / HIF 签名。
  - 上传前自动等比压缩（长边 ≤1568，带透明通道存 PNG、否则 JPEG），控制 token 与带宽。
  - 失败处理：全部失败给出明确气泡提示；部分失败跳过并在状态栏提示；千问后端暂不支持传图（会提示并忽略图片）。

**变更**
- **移除「快速/专家模式」设置项**：V4.1 起网页端「快速/专家/识图」三模式已合并为统一模型，不再有模式可选；`model_type` 固定为 `default`，并清理旧的 `ai_model_type` 存储键。**保留「深度思考」「联网搜索」**（实测更新后仍在、仍有效）。
- **客户端版本头更新**：`X-Client-Version` 由 `2.3.0` 提升到 **`2.4.0`**（与当前网页端一致，降低被判定为旧客户端的风险）。
- 端点路径、`CLIENT_VERSION` 等抽为模块常量，便于日后接口变动集中修改。
- `chat/completion` 请求体补充 `action: null`（与网页端一致）。

**修复 / 加固**
- **SSE 解析容错**：`_extract_content` 改为按类型表分发（`RESPONSE`→正文、`THINK`→思考链），**未知 fragment 类型（如新增的搜索结果）一律丢弃**，不再混进正文气泡。

**验证**
- 端到端实测：上传纯蓝色 PNG → 提问「这张图是什么颜色」→ 模型正确回答「蓝色」。
- 新增回归测试：SSE 未知类型容错、上传链路与请求体（`ref_file_ids`/`action`）、图片编码、「模式选项已删除」。
- `python main.py --smoke` 通过。

---

## 2.16.0（新增：Wiki 知识过滤模式 + 知识库检索路径修复）

> 游戏模式新增「Wiki 知识过滤」选项（默认模式 / 低频模式），调节注入主对话的 wiki 知识量；同时修复本地知识库检索路径 bug（此前检索实际未生效）。

**新增**
- **Wiki 知识过滤（聊天 ⚙ 设置 → 基础）**，两档可选：
  - **默认模式**（现有行为）：全量检索注入——常见知识（common）优先、冷门知识（rare）兜底，提炼后注入主对话
  - **低频模式**：**白名单反向过滤**——只过滤「明确常见」的核心实体（`config.WIKI_COMMON_TERMS`，AI 训练语料铁定知晓的钻石/僵尸/工作台等）；**其余照常查 wiki + 提炼注入**，包括：
    - common 类里的**冷门/较新版本内容**（樱花木、嗅探兽、铜灯、试炼密室等 AI 训练语料未必覆盖的方块/生物/物品，刻意不入白名单）
    - rare 冷门知识（成就/进度/活动等）
  - 既减少 wiki 调用与提炼次数、降低延迟，又**不遗漏冷门知识**（方块/生物/物品不再一刀切）
- 实现：`game/wiki_kb.py` 新增 `is_common`（白名单常见判定）、`match_terms(include_rare=True)`（低频同时扫 common+rare 标题提取实体）；`game/processor.py` 的 `process_game` 按设置走低频分支（提取 → 过滤白名单 → 全量检索 → 提炼 → 注入）

**修复**
- **本地知识库检索路径**：`wiki_kb.py` 的 `_DB` 此前只上溯一级指向 `game/wiki_data/`，实际库在根目录 `wiki_data/`（构建脚本上溯两级）→ 游戏模式 wiki 检索从未生效。已改为上溯两级，默认/低频模式均真实可用
- **DeepSeek 聊天 PoW wasm 路径**：`ai/deepseek.py` 的 `_WASM_PATH` 此前用 `parent` 定位到 `ai/` 子目录，而 `sha3_wasm_bg.wasm` 在项目根目录（2.12.0 分包重构漏改）→ **聊天从 2.12.0 起发消息即报错**。已改为上溯两级（兼容 PyInstaller `_MEIPASS` 根）
- **DeepSeek 旧会话失效自动重开**：`chat()` 对 `chat/completion` 返回 422（跨重启恢复的旧会话已失效/被替换）现纳入「重开会话重试一次」逻辑，不再永久卡在 422 报错
- **游戏事件播报失效**（2.15.0 重构回归）：`game_handler`/`xiuxian_handler` 的 worker 误用模块名调用实例方法（`game_processor.process_game` 应为 `game_processor.shared.process_game`）→ 所有游戏/修仙/建筑事件静默失败。已改为 `.shared` 单例调用
- **建筑播报抢占 AI 冷却**：`in_progress` 建筑播报（口语提示，不调 AI）此前也刷新 `_game_last_ai` 冷却，导致盖房子时频繁把游戏事件播报压住。现仅 `complete`（需 AI 润色）占冷却
- **worker 异常可视化**：游戏/修仙/建筑 worker 的吞异常改为写入 `logs/crash.log`（`WorkerError[xx]`），「无播报」类问题可直接定位
- **启动跳存量（进入游戏后才互动）**：桌宠启动时把已存在的模组 jsonl / 日志游标初始化为文件当前大小，只处理**本次运行后新增**的事件——不再一打开桌宠就把上次游戏的存量日志内容重读发送；玩家进入游戏产生新事件后照常增量互动
- **perf 日志路径**：`core/perf_log.py` 的日志目录指到 `core/logs/`（应根 `logs/`），已修复并把历史合并

**说明**
- 默认保持「默认模式」，行为与 2.15.0 完全一致
- 白名单 `config.WIKI_COMMON_TERMS` 可按需增删；较新版本内容建议不入白名单（低频下自动查 wiki）

---

## 2.15.0（结构重构：Handler 拆分 + 业务下沉 + 动作 key 枚举化）

> 纯结构重构，**零用户可见行为变化**。拆分巨型 `pet/modes.py`、下沉游戏业务到 `game/` 层、动作 key 枚举化、统一 AI 会话服务，并建立回归验证。功能、UI、数据格式（role.json / traj.json / 事件 JSONL / QSettings 键名）全部兼容。

**结构变化**
- **Handler 拆分**：`pet/modes.py`（原 1100+ 行巨型 Mixin）瘦身为调度壳，各功能域拆到 `pet/handlers/`：
  - `game_handler.py`（Minecraft 联动）/ `xiuxian_handler.py`（修仙桥接）/ `env_handler.py`（环境提醒）/ `news_handler.py`（新闻播报）/ `work_handler.py`（工作模式）/ `stt_handler.py`（语音输入）
  - 信号通道不变（沿用 6 个 Qt Signal + 统一消息队列），不引入新事件总线
- **业务下沉 `game/processor.py`**：`_game_worker` / `_xiuxian_worker` / 建筑播报逻辑（wiki 提炼、实体去重、人格关系、环境注入、模式切换声明）迁为 `GameProcessor`，**不依赖 UI 层**，只返回结构化结果
- **统一 AI 会话服务 `ai/chat_service.py`**：`_ai_chat`（人格切换注入 / 会话同步 / 全局串行锁 / perf 日志）迁为 `ChatService` 单例，handler 与 processor 统一调用
- **ActionKey 枚举化 `core/action_key.py`**：动作 key 单一规范来源，皮肤 `role.json` 未知 key 基于枚举容错过滤；素材目录名（`config.ACTIONS` 值）与台词类别不参与枚举

**回归验证**
- 新增 `tests/test_reconstruction.py`（无框架纯断言）：ActionKey 容错、mc_log 事件解析、皮肤未知 key 容错、`GameProcessor` 组装（mock chat_service，隔离网络）
- `python main.py --smoke` 正常通过

**说明**
- 行为完全等价：去重/重置语义（wiki 实体换会话重置、人格关系只发一次、环境变化才注入、回复去重、模式声明按需注入）逐一保留

---

## 2.14.0（UI 主题重构：深海鲸鱼海洋系 + shadcn 组件与图标）

> 全局 UI 从「萌系粉嫩」换为「现代清爽浅色 · 鲸鱼海洋系」，并以 shadcn 设计语言重做布局与图标系统，配套修复多轮图标渲染问题。仅影响界面外观，桌宠本体动画与功能逻辑不变。

**新增 / 重做**
- **新增动作 · 跳舞（dance）**：`assets/跳舞/`（192 帧，无位移轨迹）。素材取自绿幕视频（`素材源/新素材/小D可爱舞蹈动作-飞吻版.mp4`、`小D开心跳舞-缩小全身.mp4`），站姿大小统一 + 独立脚心对齐（`PRE_ALIGNED`，站姿组）；右键「🎬 动作」可触发，`AI_ACTION_TAGS` 含「跳舞→dance」的兼容标签，且经 `_build_chain` 可正确衔接坐姿↔站姿。`config` / `window`（`STATE_DANCE`）/ `settings_dialog` / `skins` 均已接入。
- **主题换肤（`core/theme.py`）**：从萌系粉嫩（粉红 `#ff5c8a` + 粉色渐变 + 大圆角）整体改为**深海鲸鱼海洋系**——现代清爽浅色：
  - 强调色海蓝 `#1E88E5`（hover `#1976D2` / pressed 深海蓝 `#0A3D5C`），高亮底浅海蓝 `#E3F2FD`
  - 中性底：浪花白 `#EFF6FB`（背景）/ 珍珠白 `#FFFFFF`（面板）/ 描边 `#DCE6F0`；文本三级 Ink `#1F2937` / Slate `#5B6B7C` / Mist `#8A9AAC`
  - **去掉所有粉色渐变**，按钮改纯色 + hover/pressed 加深；圆角统一（交互控件 pill、输入/卡片 10px、浮层 16px）；字体统一 `Segoe UI / 微软雅黑`
  - 新增 shadcn 按钮变体：`btnOutline`（次要/取消）、`iconBtn`（ghost 图标按钮）
- **设置对话框布局重构（`pet/settings_dialog.py`）**：按 shadcn Dialog 标准改为 内容区（左侧导航 + 右侧内容）+ Footer（**右对齐 [取消][确定]，宽度自适应**），**移除横贯底部的整行「确定」按钮**；导航加图标、选中态为海蓝胶囊气泡
- **复选框打勾样式**：`QCheckBox` 勾选态从「整块填蓝」改为**白底 + 蓝描边 + 内部蓝色对勾**（生成勾 PNG，QSS `image:` 引用）
- **Lucide 图标系统（`core/icons.py`）**：借鉴 shadcn 图标语言，内嵌 19 个 Lucide 线条图标（MIT，零外部依赖，QtSvg 渲染），替换聊天窗口/设置里的 emoji 与符号按钮（⚙🔄✕➤ → 图标），导航项带图标（选中白 / 未选蓝灰）

**修复 / 优化**
- **图标显示不全根治**：改用「2x 超采样 pixmap（无 devicePixelRatio 干扰）+ `renderer.render(painter, rect)` 显式铺满」，修复 QSvgRenderer 在带 DPR 的 QPainter 上坐标错配、把图标内容裁到一角的问题；viewBox 四周留边避免边缘线条被裁
- 移除设置对话框页内重复的「设置」标题与关闭按钮（由系统标题栏承担，消除右上角重复按钮）

**说明**
- 图标渲染基于 `PySide6.QtSvg`（PySide6-Essentials 自带）；勾选 PNG 首次启动生成到 `%LOCALAPPDATA%\DesktopPet\ui\` 缓存，可随时删除。
- 参考设计规范：`design-taste-frontend`（anti-slop）与 `frontend-design`（Designing with Impeccable）两份 opencode skill 的落地。

---

## 2.13.0（角色皮肤系统 + 动画亮度修正 + 性能优化）

> 新增独立角色工具与桌宠皮肤切换，并移除动画的强制亮度统一、修正打滚饱和度，同时优化动画首帧加载与设置对话框。

**新增**
- **独立角色工具 `deskpet-tools/`**（GUI，与桌宠程序分离，可独立运行）：
  - ① 视频 → 动作：绿幕角色视频 → 标准化桌宠动作（色度键抠图 / 去绿溢色 / 水印绿影清理 / 站姿大小统一 / 锚点对齐 / 位移轨迹提取），输出透明 PNG 帧图 +（可选）`traj.json`，支持 **站姿 / 位移·垂直(跳跃 dy) / 位移·双向(打滚 dx,dy)** 三种动作类型，并可选择位移锚点（脚底贴地窄带 / 角色中心）
  - ② 动作 → 角色包：把一组动作打包成角色包（`role.json` + 帧图 + 可选专属 `prompt.txt`），默认输出到 `desktop-pet/skins/`
- **皮肤切换功能**（设置 → 新增「皮肤」页）：
  - 自动扫描 `desktop-pet/skins/` 下角色包，识别角色名称 / 动作名称（`role.json`）
  - 切换皮肤：更换全部动作素材、右键「🎬 动作」菜单按角色动作显示名动态生成、宠物名改为角色默认名、AI 的 `{动作标签词}` 与随机台词跟随皮肤；皮肤可带专属 `prompt.txt` 覆盖当前人格提示词
  - 人格体系不受皮肤影响（切皮肤不换人格，切回「内置」恢复默认）

**修复 / 优化**
- **移除动画强制亮度统一**：此前 `pet/anim.py` 在加载素材时用「全图平均亮度」强行统一各动作（`brights/target/gain`），被深色面积大的动作（背面/侧面）污染，导致坐姿与站姿显示亮度不一致、切换突变。现移除该逻辑，各动作**按素材原亮度显示**，坐/站/滚亮度自然一致
- **打滚饱和度校准**：打滚（roll）头发颜色偏灰发闷（饱和度 0.414，其余动作 ≈0.47-0.50），在 HSV 空间提升饱和度（×1.18）到 0.489，与其它动作发色一致；明度未动，不偏色不过艳
- **动画首帧加载提速**：懒加载动作的基础帧改用 `QImage` 读盘 + `QPixmap.fromImage` 转换（免 DIB 转换），首次播放更快
- **设置对话框预热**：启动后后台预构建一次设置 UI，用户首次打开「设置」即秒开（进程首次构建的一次性 Qt 开销被挪到启动空闲期）

**说明**
- 工具依赖 `opencv-python` / `Pillow` / `numpy`（见 `deskpet-tools/requirements.txt`）；使用与角色包格式见 `deskpet-tools/README.md`
- 打滚原始素材备份：`assets/打滚` 校色前的副本可回滚

---

## 2.12.0（项目结构重构 + 内存再优化）

> 按领域分包重构代码（29 个平铺 .py → 7 个语义化包），并继续压降常驻内存（0.9GB → ≈0.3GB）。

**新增**
- **项目结构重构**：按领域拆分 `core/`（基础设施）/ `ai/`（AI 对话）/ `voice/`（语音）/ `pet/`（桌宠主体）/ `game/`（游戏联动）/ `info/`（环境/新闻/工作）/ `tools/`（独立工具），消除「29 个 .py 平铺根目录、无分层」的问题
- **消除循环依赖**：`ai.client ↔ ai.deepseek` 双向引用 → 凭证读写拆为独立 `ai/credentials.py`，方向单向化
- **帧缓存内存再优化**（动画帧常驻内存 ≈ 0.9GB → ≈ 0.3GB）：
  - **消除双份帧缓存**：`act["frames"]`（基础帧）与 `_frame_cache`（用户设置帧）此前同时常驻（≈614MB 像素），现在重建后只保留一份用户帧，基础帧从 `.base_frames/` 按需重读
  - **动作按需懒加载**：`angry/think/happy/cry/shy/surprised/sleep/jump/roll` 启动不常驻，首次播放时从磁盘加载并应用画面设置，按 LRU 最多保留 4 个（当前播放的动作永不被驱逐）；核心动作（待机/正面/背面/侧面/坐下/起身/转向）常驻保证巡逻/过渡零延迟
  - 画面设置滑块加入 150ms 防抖，拖动中不再逐帧全量 numpy 重建
  - 实测：启动空闲 ≈ 0.23GB，补载全部动作 ≈ 0.35GB

**修复**
- `game/wiki_build.py` f-string 含反斜杠（Python 3.10 语法错误，3.12 才允许）→ 提取变量修复

**说明**
- 独立脚本路径同步修正：`xiuxian.py` / `login.py` / `wiki_build.py` / `color_calibrate.py` 的资源定位改为上溯到项目根目录；运行方式见 README「项目结构」

---

## 2.11.0（动作颜色校准 + 性能优化）

> 一键将所有动作图色调校准到「正面」基准，并解决桌宠常态内存过高（2.5GB）与启动慢（8s）两大问题。

**新增**
- **动作颜色校准**（`color_calibrate.py`）：以「正面」为基准，在 LAB 空间对每个动作做 **L 亮度 + b 蓝黄轴双通道分位数直方图匹配**（0~100 逐分位分段线性映射 + 端点外推，保留高光阴影），**a 通道保留**（脸红/表情颜色不变）。逐动作生成统一映射函数应用到全部帧 → 帧间零闪烁；校准后各动作平均色差显著收拢（此前背面/侧面明显发暗偏蓝）。
- **对比图**：`color_check_after.png`——各站姿动作并列对比，便于人工核对色调一致性。

**修复 / 优化**
- **内存过高修复**：`_load_actions` 生成显示帧后立即释放原始大图与逐帧分析中间数据（`act["images"]/boxes/feet/gb`），常驻内存从 ≈2.5GB 降至 ≈0.9GB（此前同时缓存了全部 512×512 原图 QImage 与显示帧）。
- **启动大幅提速**（`pet_anim.py`）：
  - **基础帧落盘缓存** `.base_frames/`：把「裁剪 + 缩放 + 贴底 + 位移轨迹 + 镜像」后的**基础显示帧**（**不含**用户亮度/对比/饱和度）缓存为小图，下次启动直接读取，跳过「重读 1600+ 张 512×512 原图 + 逐帧缩放绘制」。命中时启动到宠物出现 ≈ 8s → ≈ 5s。
  - 用户改亮度/对比/饱和度**不会**触发此缓存失效（只走运行时 `_rebuild_frame_cache` 的 numpy），改素材 / 改 `TARGET_HEIGHT` 时才重新生成。
  - **`_apply_user_settings` 优化**：合并对比度 × 亮度常数、饱和度免中间大数组、按需运算，全帧处理更快。
  - 缓存异常安全：删除 `assets/.base_frames/` 即强制走原现场生成路径（等价旧行为）。

**说明**
- 颜色校准仅改色调、不动 alpha 与素材文件；`a` 通道（表情/脸红）保留。本次验收：背面/侧面回滚保留原始色，其余动作已统一校准。

---

## 2.10.0（新动作 + 视频素材处理管线）

> 新增 害羞/惊讶/站着睡着/跳跃 动作；引入「绿幕视频 → 成品帧图」的完整处理管线：站姿动作不动点（脚底）对齐、位移动作运动轨迹还原。

**新增**
- **新动作**：害羞、惊讶、站着睡着、跳跃、打滚。右键「🎬 动作」可直接触发；AI 回复中的 `{害羞}{惊讶}{睡眠}{跳跃}{打滚}` 标签也能驱动对应动作。
- **视频素材处理管线**：`素材源/` 里的绿幕视频（`*_ 动作.mp4` 等）一键转成成品帧图——色度键抠图、去绿溢色、水印/地面阴影清理、站姿大小统一。
- **站姿动作不动点（脚底）对齐**：以「首帧脚底贴地窄带中心」为参考锚点，整段视频整体平移对齐到正面站立站位基准。不做逐帧质心对齐，避免被大裙摆/鱼尾把角色拖偏导致左右漂移。
- **位移动作轨迹还原**：
  - **跳跃**：锚点=脚底窄带中心、水平固定；垂直位移写入 `traj.json`，播放按轨迹上移，等效「跳起-落下」。
  - **打滚**：内含"站立-倒地-翻滚-起身"多个子动作，改用**角色中心**做不动点，记录 `(dx,dy)` 双向轨迹，旋转由帧内容表现，还原「向左滚一圈再站回」。
- **桌面定位改进**：新增 `config.PRE_ALIGNED` 标记，这些动作走独立的「素材画布中心」脚心定位（`_foot_center_x_narrow`），其余姿势仍用全局脚心算法，互不影响；`_build_chain` 坐姿→站姿自动插入「起身」过渡。

> 素材处理的具体算法见 `视频处理成图片成为宠物动作.md`。

---

## 2.9.1（语音输入体验修复）

> 语音识别繁转简、动作标签不再被读出、语音回复缩短以降低延迟、Vosk 中文路径问题。

**修复**
- **识别结果统一简体**：whisper/Vosk 中文模型输出偏繁体字形，识别结果经 OpenCC 繁转简（`你好呀 今天過得怎麼樣` → `你好呀 今天过得怎么样`）
- **语音回复动作标签过滤**：`{思考}` 等动作标签不再被气泡显示/语音朗读，改为提取后驱动宠物动作（此前会直接读出，影响体验）
- **语音输入延迟优化**：AI 调用本身仅 1.3~1.6s，延迟主因是回复过长（54~63 字，Edge 合成+朗读 13~16s）且连发时语音串行排队。语音输入场景现要求 AI **一句话 30 字内、不用动作标签**，合成+朗读时长减半以上
- **Vosk 中文路径崩溃**：Vosk（Kaldi C++）与 sentencepiece 一样无法加载含中文/非 ASCII 的路径（项目目录「opencode用」即触发 `Failed to create a model`），此前会静默回退 whisper。模型目录已改为纯英文路径 `%USERPROFILE%\.deskpet-stt`（与项目解耦），Vosk 正常加载（0.4s）
- **默认识别模型改为 faster-whisper tiny**（~75MB）：small 达 461MB 过大，改为 tiny 默认，设置里可换 base/small

---

## 2.9.0（语音输入 + 统一消息管理）

> 支持按住快捷键说话和宠物对话（语音转文字），并新增统一消息队列解决语音/日志/系统消息并发碰撞。

**新增**
- **语音输入（STT）**：按住全局快捷键（默认 Y）说话，松开即识别；识别文本气泡显示「🎙 你说：xxx」并自动发给 AI，回复气泡+语音；识别结果同步进已打开的聊天窗口历史
- **双识别引擎**：Vosk（轻量离线，~42MB）与 faster-whisper（更准，模型较大），设置里切换，缺模型自动下载（多源+系统代理），失败自动回退另一引擎
- **静音自动收尾**：按住期间静音超时（默认 2.5s，可调）自动结束识别；开始录音时自动停掉正在播放的 TTS，保证收音干净
- **统一消息管理**（`message_queue.py`）：所有消息源（语音输入 / AI 回复 / 游戏日志 / 新闻 / 工作 / 环境提醒 / 随机台词）统一按优先级排队串行显示，不再互相打断、音画错位
  - 优先级：语音输入 > AI 回复 > 日志播报 > 系统提醒 > 随机台词
  - 相邻去重 + 队列上限（30 条丢最低优先级）+ 高优先级插队
- **设置**：聊天窗口 ⚙ 新增「语音输入」分组（启用 / 引擎 / whisper 模型 / 静音时长 / 自动发送）

**说明**
- 语音对话走共享 AI 会话（与聊天窗口历史同源），不依赖模态聊天窗口；聊天窗口已打开时语音历史同步显示
- 首次使用需下载识别模型（Vosk ~42MB 或 faster-whisper ~75MB tiny），国内网络建议开启代理
- **模型目录为英文路径**（`%USERPROFILE%\.deskpet-stt`）：Vosk（Kaldi C++）无法加载含中文/非 ASCII 的路径，故与项目位置解耦

---

## 2.8.1（游戏模式 Wiki 检索与模式声明修复）

> 修复 Minecraft 游戏模式「获得物品没搜索」与「Wiki 过滤失效」，并优化 Wiki 摘要质量与模式切换声明。

**修复**
- **实体提取增强**：此前从模组数据（deskpet JSONL）的中文事件行提取搜索词走固定词表（仅 20 余词），获得「橡木原木 / 石斧 / 工作台」等常见物品几乎搜不到。现改为直接从事件 highlights 提取目标实体（获得物品 / 击杀 / 受伤来源 / 破坏·放置·使用 / 进度名），再合并日志正则与本地库反向匹配
- **Wiki 去重改为会话绑定整局去重**：此前按 10 分钟时效去重，超时后同一实体重复注入（刷屏）。现改为：同一 AI 会话内每个实体只搜索注入一次（无论命中与否），后续事件提到即过滤；玩家「🔄 新会话」（更换对话 id）后自动重置
- **Wiki 摘要质量**：过滤掉落表/数据表噪音（`[数据表]`、`数量范围 / 掉落概率`、`无抢夺` 等压成单行的表格文本不再进摘要），精确命中时优先返回干净引言
- **模式切换声明按需注入**：不再每次互动都发「现在切换到 3、游戏模式」，改为仅在游戏联动类型切换 / 换人格 / 换新会话时发送一次（同局同会话静默），避免每次互动重复声明

---

## 2.8.0（凡人修仙游戏集成）

> 支持《凡人修仙》（v0.9.e，React 文字修仙游戏）：右键菜单一键启动，桌宠通过 playwright 直接读取浏览器里的游戏状态（方案 C，零游戏侧改动）。

**新增**
- **修仙游戏集成**：右键菜单新增「🧘 修仙游戏」子菜单（启动/停止）；桌宠管理游戏副本 `games/xiuxian`（复制自用户原项目，独立运行）
- **联动游戏互斥切换**：设置 → 游戏页新增「联动游戏」下拉（关闭 / 我的世界 / 修仙小游戏），二者互斥只能激活一个，避免 Minecraft 与修仙同时抢 AI 会话与气泡；修复修仙互动被 Minecraft 开关误拦的问题（`_on_game_ready` 只认 `_game_enabled`）；旧版独立开关设置自动迁移兼容
- **方案 C 桥接**：桥接子进程（`xiuxian.py`）自动拉起游戏服务器（node server.js，3000 端口）与系统 Edge，通过 playwright 直接读浏览器 `localStorage` 存档 + 页面日志 DOM，**无需给游戏埋点**（游戏本体零改动）
- **事件识别**：开局（境界/难度/世界）、历事（页面日志叙事）、突破晋级（境界变化+寿元上限）、死亡/寿元耗尽、结局达成；状态摘要含境界/年龄/寿元/修为/气血/道心/业力/灵石
- **AI 互动**：修仙事件复用共享 AI 会话，以「修仙道侣/同伴」身份气泡+语音互动；关键事件（开局/突破/晋级/死亡/结局）立即互动，普通历事按 30 秒汇总；沿用 15s AI 冷却与去重
- **设置面板**：游戏页新增「修仙游戏联动」开关、游戏目录选择、启动/停止按钮
- **桥接可靠性**：子进程心跳文件判活（避免重复拉起 + 自动恢复）；桥接退出时桌宠自动清理

**说明**
- 游戏由桌宠拉起浏览器游玩（playwright 驱动系统 Edge，隔离临时 profile，不影响日常浏览器）；游戏不依赖桌宠，单独运行也不受影响
- 游戏副本可替换/升级：将新版本覆盖到 `desktop-pet/games/xiuxian` 即可

---

## 已知问题与待办（TODO）

- **多人服务器（Hypixel 等）游戏模式：已修复**（2.6.0）。
  - 原 `deskpet-mod` 的 `PlayerAdvancementTrackerMixin` 混入服务端专用类 `PlayerAdvancementTracker`，多人客户端连服务器会启动崩溃（mixin required 失败）。
  - 修复：`mixins.json` 全局 `required` 改为 `false`（目标类缺失时静默跳过，单机/多人均不崩），并新增客户端 `ClientAdvancementManagerMixin` 混入客户端等价物 `ClientAdvancementManager`（全量同步时建立已完成基线、`setProgress` 增量 diff 判断新完成、自动过滤配方进度），多人/单机均能采集进度。
  - 需重新构建 `deskpet-mod` 并覆盖 `.minecraft/mods/` 旧 jar。
- **多人采集缺口**（剩余限制）：击杀/受击/破坏方块等服务端事件客户端只能采到部分；进度/聊天/合成/拾取可正常采集。
- **服务器策略**：反作弊不拦截纯观察型 mod，但各服务器对 mod 清单有不同政策，使用前请确认。

---

## 2.7.0（多人游戏模式增强 + 人设更新）

> mod 升级到 Minecraft 26.2，多人服务器游戏模式全面可用，并更新两套人格人设。

**新增**
- **mod 升级 Minecraft 26.2**：Fabric 无映射模式（26.2 起官方停止发布映射），源码直接使用官方类名；fabric-loader 0.19.3 / fabric-api 0.158.0 / loom 1.17 / Java 25
- **多人广播解析**：客户端监听系统消息，采集击杀/死亡/获得物品/玩家聊天等广播（去颜色码 + 超长/装饰/噪声关键词过滤 + 短时去重），多人数据经客户端 tick 周期落盘
- **环境上下文注入**：识别服务器名、当前小游戏（Hypixel sidebar 记分板 + 消息兜底）、世界类型（单机超平坦检测），写入 `deskpet/state.json`，组装 AI 消息时注入；环境只在变化时注入避免重复
- **Hypixel 游戏介绍库**（`config.HYPIXEL_GAMES`）：21 个主游戏 + 16 个 Arcade 街机小游戏的中文玩法介绍，AI 能准确理解当前游戏
- **玩家闲聊无条件保留**：自己发的聊天（`名字: 内容`）不经过广播过滤，始终作为互动素材传给 AI
- **Wiki 提炼会话持久化**：提炼会话 id（`ai_refine_session_id`）跨重启复用同一 DeepSeek 会话，不再堆积新对话
- **广播事件显示**：桌宠 `deskpet.py` 支持 `[广播·击杀/死亡/获得/消息]` 展示

**人设更新**
- **宠物女仆**（`prompt.txt`）：形象层新增「已成年」亲密设定；傲娇新增 5 种句式示例（"摸就摸，别指望我会说舒服"等）；输出规范禁止 `（）` 旁白；动作标签 `{}` 一律在回答末尾、一次一个
- **御坂美琴**（`prompt2.txt`）：身份认知新增「无数字空间操作权限，但空间内与人无异，可吃饭睡觉」

---

## 2.6.0（本地 Wiki 知识库）

> 引入离线 Minecraft 中文 Wiki 知识库，游戏模式知识注入全面升级，并优化 DeepSeek 响应延迟。

**新增**
- **本地 Wiki 知识库**（`wiki_build.py` / `wiki_kb.py`）：离线抓取中文 Wiki 主命名空间全部内容页（约 7800 页 + 2 万条重定向别名）构建 SQLite 知识库（`wiki_data/mcwiki.db`），本地毫秒级检索；支持全量构建 / 增量更新 / 分类重跑
- **Wiki 信息分类**：common（常用）/ rare（冷门）/ version（版本）/ tech（技术）/ disambig（消歧义）五类，检索只注入常用知识、冷门标题兜底、过滤版本/技术/消歧义
- **Wiki 知识提炼**：独立 DeepSeek 会话（关闭思考/搜索）把 wiki 原文提炼成 60~120 字要点再喂主对话，减小上下文；可在设置「Wiki 知识提炼」开关，关闭则直接注入原文（省一次调用）
- **PoW 预取**：DeepSeek 网页版工作量证明答案由后台线程预生成，请求零等待，AI 响应延迟显著降低
- 游戏模式实体提取升级：本地库标题反向提取（覆盖猪灵/活塞/农民等词条库外实体）；同一实体 10 分钟去重；本地未命中不再在线兜底

**改进**
- 游戏模式 wiki 注入：事件 → 本地检索 →（可选提炼）→ 主对话，未命中由 AI 自身知识回应
- 新会话按钮逻辑重构（重建客户端替代同步网络调用，点击立即生效不卡界面）
- 聊天窗口右键菜单 / 设置项完善

---

## 2.5.0（人格系统）

> 支持多人格切换：每个角色独立的人设提示词文件 + 独立默认名。

**新增**
- **人格系统**：内置「宠物女仆」（`prompt.txt`）与「御坂美琴」（`prompt2.txt`）两套人格，聊天 ⚙ 设置 → 宠物 分组切换，即时生效
- 人格与宠物名称解耦：每个角色有独立默认名（宠物女仆=大肥鱼 / 御坂美琴=御坂美琴），改名自动替换当前人格的角色名（正则精确替换「名字叫X」，台词里的名字不受影响）
- 桌面宠物游戏/新闻/工作共享会话同样按当前人格加载提示词

---

## 2.4.0（本地 MOSS 语音 + 体验增强）

> 引入自托管 MOSS-TTS-Nano 本地语音（省 API 字符）、人设 prompt 文件化、语音音画同频、游戏模式事件扩展与性能日志。

**新增**
- **🔥 重磅：人设提示词全面重写**（`prompt.txt`，源自 Prompt.docx）：
  - 形象层：深海蓝渐变长发、蓝鲸耳鳍与鱼尾、维多利亚女仆装的「大肥鱼」鲸鱼少女
  - 性格层：活泼俏皮、爱偷吃（白饭=token）、傲娇护主、害羞、情感丰富
  - **情绪情态理论**：情态值驱动情绪强度、句末语气词、口语停顿/拖音、自由间接引语、内聚焦
  - **输出规范**：80 字以内、至少一个情态词、一处口语停顿/语气词、针对输入具体内容回应；`{动作词}` 标签限 10 个、禁颜文字/结构化输出
  - 8 个情绪场景正向示例 + 反面示例（杜绝一句话套话模板）
- **人设 prompt 文件化**：完整提示词由 `prompt.txt` 隐式加载，设置里只允许改「宠物名称」（自动替换 prompt 角色名）
- **本地 MOSS-TTS 语音**：自托管 ONNX CPU/GPU 推理，本地免费不限量合成，替代千问 TTS 省字符；内置女声音色可选
- **音色下拉框智能切换**：按语音引擎显示对应音色（CosyVoice 千问音色 / 本地 MOSS 女声），互不覆盖
- **语音语速调节**（50-200%），各引擎统一生效（默认 110%）
- **语音全排队不打断 + 音画同频**：声音开始=气泡开始，声音结束=气泡结束；合成失败自动回退本地 SAPI
- **Edge 语音走系统代理**：国内网络下显著提升稳定性与速度（~2s）
- **人设 prompt 文件化**：完整提示词由 `prompt.txt` 隐式加载，设置里只允许改「宠物名称」（自动替换 prompt 角色名）
- **游戏模式扩展**：Fabric 模组新增击杀 / 获得物品（合成·拾取）/ 受伤（标注来源）事件；告知 AI 主人的游戏名字；所有事件统一 20s 汇报一次，AI 冷却 15s
- **性能日志** `logs/perf.log`：记录每条 AI 处理耗时 / TTS 合成播放耗时 / 字数，形成测试数据集
- 设置与聊天窗口改为**左侧导航 + 右侧内容区**布局并放大分辨率
- AI 模式「正常模式」更名「日常模式」；气泡去除游戏/工作/新闻 emoji 前缀

**改进**
- 中文词条也能命中 Minecraft Wiki 查询（末影龙/僵尸/钻石矿石等）
- 游戏模式统一汇报节奏，避免关键事件刷屏

## 2.3.0（千问集成）

> 引入阿里云千问：双 AI 后端 + qwen3-tts 语音，并与 DeepSeek 自由组合。

**新增**
- AI 双后端：内容生成可在 **DeepSeek（网页版）/ 千问（官方 API）** 间切换，默认 DeepSeek
- 千问后端流式文字：边生成边显示气泡
- **qwen3-tts 语音**：默认音色 **Chelsie（千雪）**，音质自然；句子级逐句合成播放，边生成边朗读
- 语音音量调节（0-100%），所有语音引擎统一生效
- **口头禅语音本地缓存**：预设台词（待机/走动/点击等）每句仅用千问 TTS 合成一次，缓存为本地 wav（`voice_cache/`），之后零 API 播放
- 聊天模式防漂移：每次聊天显式声明当前模式，避免"日常模式却输出游戏风格"

**改进**
- 后端与语音引擎解耦，可自由组合（如 DeepSeek 内容 + 千问语音）
- 桌面周期强制置顶（TopMost + raise）
- 游戏模式下宠物不再自动巡逻（仍可拖动）
- 游戏模式普通活动汇总改为秒级（默认 20s，与日志检查频率一致）
- 宠物设置与聊天设置的「AI 模式」去重，AI 对话配置统一归聊天 ⚙ 设置
- 下拉框禁用滚轮误触，只能点击选择

## 2.2.0（游戏模式）

> 桌宠陪你玩 Minecraft。

**新增**
- 游戏模式：读取 Minecraft 日志（增量、按时间戳、支持日志轮转）
- 事件识别：死亡 / 成就 / 进度 / 进出世界 / 跨维度 / 聊天（精确正则，避免启动噪音误报）
- Minecraft 中文 Wiki 查询，辅助 AI 互动
- Fabric 模组 `deskpet-mod` 联动：精确监听玩家行为，每 20 秒聚合分级窗口写入独立目录，桌宠按 CRITICAL / NORMAL / LOW 分级触发互动
- 日志路径可自由选择，支持自动检测默认位置

## 2.1.0（提醒 / 新闻 / 工作 / 主题）

> 让桌宠更"懂事"。

**新增**
- 环境提醒：本地时间整点关怀 + 联网天气播报（下雨/高温/低温/大风），自动 IP 定位城市
- 新闻播报：RSS 拉取（默认澎湃新闻，多镜像），AI 逐条可爱播报，支持自定义新闻源
- 工作模式：检测前台软件/文件，AI 生成实用小知识
- 界面主题：宠物风格（定制 QSS）/ 粉白 Material / 暗色 QDarkStyle / 默认
- 共享 AI 会话：工作/游戏/新闻共用同一 DeepSeek 会话

## 2.0.0（AI 聊天）

> 从"会动的贴图"进化成"能聊天的桌宠"。

**新增**
- DeepSeek 网页版接入（PoW 求解、网页内部接口，免付费 API）
- AI 动作联动：AI 回复夹带 `[开心]` `[生气]` 等标签驱动宠物动作
- 跨重启会话记忆：会话 ID 持久化，重启续接对话
- 自动登录 DeepSeek：playwright 驱动 Edge，一次登录永久复用
- 聊天窗口：无边框圆角卡片、气泡消息、可拖动
- 宠物气泡：实时贴合角色头顶，跟随动画浮动
- 随机台词：待机/走动/点击/拖动等场景随机卖萌
- 消息队列：气泡消息统一排队，互不挤压
- 语音朗读：Edge TTS / 本地 SAPI

## 1.1.0（性能与画面）

> 性能优化。

**改进**
- 素材分析向量化 + 磁盘缓存（`.cache.pkl`）
- 动画帧缓存：动画期零 numpy，提升流畅度
- 画面调节：亮度 / 对比度 / 饱和度实时调节并保存

## 1.0.0（初始版本）

> 桌面宠物基础功能。

**功能**
- 透明背景 PNG 帧序列动画，自动裁剪 / 统一缩放 / 脚部贴底对齐
- 随机巡逻：定时左右走动，自动切换朝向
- 交互反应：单击随机动作、按住拖动、坐下/起身过渡
- 动作序列：右键菜单表演连贯动作
- 多种姿势：正面 / 背面 / 侧面 / 坐下 / 起身 / 转向 / 坐地上生气