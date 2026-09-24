# 全链路测试方案：桌宠 × AI × 游戏 × 用户通讯

> 版本：v1.1（2026-09-20；v1.0 为 2026-09-12）
> 范围：`desktop-pet`（桌宠）↔ AI 提供方（网页版 / 官方 API / 千问 / Claude / 自定义）↔ `SmartMaid`（游戏女仆）↔ 用户通讯 的完整闭环。
> 目标：让「用户 → 桌宠 → AI → 女仆 → 回执 → 用户」每一条边都有可自动化的验证，回归可一键跑、真机可复现。
> 配套设计：`DESIGN_LOOP_DEEPENED.md`（通讯闭环）、`DESIGN_AI_PROVIDERS.md`（AI 接口通用化）、`DESIGN_NLU.md`（本地意图识别）、`DESIGN_WIKI_REFINE.md`。
>
> **v1.1 改动**：2.24.0–2.27.0 把「三后端」重构成**供应商注册表 + 协议适配器**，本文档中
> 所有「三后端」措辞与 L0 用例清单据此更新；新增三个 L0 测试文件登记（见 §三）。

---

## 一、链路全景与节点

```
用户 ─(聊天/语音/图片)─► 桌宠 ChatWindow / STT
        │ ① NLU 本地预处理（意图→直接执行 or 提示，含递归合成判断）
        ▼
   _ChatWorker ──► ② AI 提供方（网页版 / 官方 API / 千问 / Claude / 自定义）
        │       人格注入 + 记忆 + 女仆瞬时状态 + [系统]回执注入
        ▼
   ③ AI 回复 ──► 动作标签剥离 + 【DSL 指令】解析
        │                        │
        ▼                        ▼
   ④ 气泡/TTS ──► 用户       ⑤ MaidLoop → WS → SmartMaid 女仆执行
                                    ▲            │
                                    └──⑥ 回执/感知事件──┘
                                 （另：文件遥测 deskpet/maid/*.jsonl 旁路）
```

| 节点 | 组件 | 关键文件 |
|---|---|---|
| ① 输入与 NLU | 聊天窗 / STT / `NluRouter` | `ai/chat.py`、`pet/handlers/stt_handler.py`、`nlu/router.py` |
| ② AI 对话 | 供应商注册表 + 协议适配器 + `MessagesBackend/SessionBackend` | `ai/providers.py`、`ai/protocols/*`、`ai/credentials_store.py`、`ai/stream_strip.py`、`ai/client.py`、`ai/deepseek.py`、`ai/deepseek_api.py`、`ai/qwen.py`、`ai/backends/*` |
| ③ DSL | `MaidLoop.process_reply` | `game/maid_intent.py`、`game/maid_loop.py` |
| ④ 反馈 | 气泡 / TTS / 消息队列 | `pet/bubble.py`、`voice/tts.py`、`core/message_queue.py` |
| ⑤ 下发 | WS Server + 女仆指令 | `game/maid_link.py`、`pet/handlers/maid_handler.py` |
| ⑥ 回执感知 | 感知合并 / 遥测 / 事件播报 | `game/maid_link.py`、`game/mod_data.py`、`pet/handlers/game_handler.py` |
| 模组侧 | SmartMaid 女仆：任务/感知/WS Client | `SmartMaid` 仓库 |

## 二、分层策略

| 层 | 名称 | 依赖 | 耗时 | 烧 token | 跑法 |
|---|---|---|---|---|---|
| **L0** | 单元层 | 无 | 秒级 | 否 | `python tests/test_*.py` |
| **L1** | 集成层 | mock AI + mock 女仆 | 秒级 | 否 | `python tests/test_chat_chain.py` |
| **L2** | 半真机层 | 起桌宠 + Fake 女仆(WS) | 分钟级 | 否 | `python tools/chain_smoke.py` |
| **L3** | 真机端到端 | 起桌宠 + 起游戏 + 真实 AI | 5-10min | 少量 | `python tools/e2e_full_chain.py` |
| **L4** | 手工验收 | 人工 | 不定 | 是 | 手工清单 |

原则：**低层能抓的绝不上高层跑**（省 token、省时间）；每一层通过后才进下一层；真机只验证「只在真机才成立的边」（真实 AI 输出、真实女仆执行、真实时序）。

## 三、现有资产盘点

### 已覆盖（复用，不重写）

| 资产 | 层 | 覆盖 |
|---|---|---|
| `tests/test_nlu.py`（24 项） | L0 | 意图体系 / 契约 / 语料 / 槽位 / 路由分支 / 泄漏守卫 / 分层对账 |
| `tests/test_reconstruction.py` | L0 | ActionKey / mc_log / 皮肤 / GameProcessor / SSE / 图片上传 / 设置 |
| `tests/test_maid_loop.py`（14 项） | L0 | MessagesBackend / SessionBackend / DSL 解析 / MaidLoop / 限流 / 去重 / 排队 |
| `tests/test_providers.py` | L0 | 供应商注册表 / 能力位两级推导 / 凭据命名空间 + 老键回退 / 模型别名归一化 / **版本铁律**（含 `[V3]` id 语义、`[V6]` 老键、`[V7]` 版本=CHANGELOG 守卫） |
| `tests/test_tts_catalog.py` | L0 | 音色目录解析（非实时表 + omni 文档）/ 按模型过滤 / 三级数据源回退 / 默认音色 |
| `tests/test_audio_stream.py` | L0 | 原生语音播放管线（缓冲 / 起播阈值 / 短句收尾 / 无设备兜底 / 增益 / 前置约束守卫 `[A10]`） |
| `tools/e2e_maid_check.py --offline`（11 项） | L0 | API 请求体 / 历史提炼 / 记忆渲染 / 状态注入 / 决策判定 / 节流 |
| `tools/e2e_maid_check.py --online`（5 项） | L3 | 桌宠 WS / 女仆握手 / 感知 / 真实 AI→DSL / 多轮 |
| `SmartMaid/tools/run_e2e_test.py` | L3 | 起桌宠 + 起游戏 + quick-play + 遥测/WS 证据 |
| `SmartMaid MaidAutoTest` | L3 | 进游戏自动执行指令序列（autotest.json） |
| `main.py --smoke` | L0 | 启动即崩检查 |

### 覆盖缺口（本方案要补）

1. **聊天窗真实链路未覆盖**：`_ChatWorker` 的 prompt 组装、DSL 应用、NLU 前置——只有 `maid_loop` 的纯逻辑测试，没测 ChatWorker 集成。
2. ~~**三后端路由未测**~~ ✅ **已补**：`test_providers.py` 覆盖注册表/能力位，`test_chat_unit.py [U4]` 覆盖 `make_client` 路由分支。
3. **消息队列优先级/上限/去重未测**：`core/message_queue.py`。
4. **用户→AI→气泡 的 UI 链路无自动化**（L2/L4 覆盖）。
5. **真实聊天窗 → 女仆 DSL 闭环未自动化**：现有 `e2e_maid_check O4` 用 `.maid_command.json` 调试口**绕过**了聊天链路，没验证「聊天窗发的消息真的会让 AI 回复夹带 DSL 并下发」。
6. **千问流式后端**：DSL 已接（2.23.1 起 `_run_stream` 剥离 `【】` 并交 `_apply_dsl`），但**原生语音路径的 DSL/动作剥离**仍只有 L0 守卫，缺真机多轮验证。
7. **NLU 自动执行 → 真机女仆**只测了逻辑（FakeBridge），真机未闭环。
8. **原生语音（2.27.0）**：`stream_events` 解析 `delta.audio` 有 L0 覆盖，真机只跑过单轮探针（`logs/_probe_native_audio.txt`），**多轮 / 长文本 / 断流**未验。

## 四、各层用例

### L0 单元层（本轮实施）

**新增 `tests/test_chat_unit.py`（或并入现有）**：

| # | 用例 | 断言 |
|---|---|---|
| U1 | `_ChatWorker._prepare_prompt` 组装顺序 | 模式声明在前、女仆上下文（[系统]回执/[态]状态）在 NLU 前置之前、图片提示只在有图时 |
| U2 | `_apply_dsl` 三分支 | 有 `maid_loop`→下发并返回 clean；`maid_loop=None`→原样；异常→原样不抛 |
| U3 | `_upload_images` 降级 | 全失败→emit 错误；部分失败→跳过；无图→空 |
| U4 | `make_client` 多后端路由 | mock `_STORE`/凭证：任一注册表里的提供方 id → 对应客户端（`qwen`→QwenClient、`deepseek-api`→DeepSeekApiClient、`deepseek-web`→DeepSeekClient、`anthropic`/`custom`→OpenAI 兼容或 Anthropic 适配器） |
| U5 | `PetMessageQueue` 调度 | 优先级插队、相邻去重、上限丢最低优先级、`_render_idle` 忙时不派发 |
| U6 | `_run_plain` 全流程 | FakeClient 返回含 DSL → `_apply_dsl` 结果进 `dsl_inject`，`finished` 收到 clean 文本 |

**递归合成（模组侧逻辑，L0 在桌宠侧为契约层）**：
- 桌宠侧 `test_router_via_craft`（已完成）：`craft_check` 返回 `via_craft` → 判定可合成 → 下发 `craft`。

### L1 集成层（本轮实施）

**新增 `tests/test_chat_chain.py`**：FakeBackend（固定回复，可切换含/不含 DSL）+ FakeMaidLoop（记录下发 + 返回回执）+ FakePreprocess（NLU 结构化文本）。

| # | 场景 | 断言 |
|---|---|---|
| C1 | 用户消息 → NLU 前置 → AI 回复含 `【attack】` | `maid_loop.process_reply` 被调、下发 `attack`、回执进下一轮注入 |
| C2 | 女仆未连接（`maid_loop=None`） | DSL 不解析、文本原样、不抛异常 |
| C3 | AI 回复无 DSL | 原样透传、不下发 |
| C4 | 千问流式后端 | 走 `_run_stream`、不接 DSL（已知缺口，标记为预期行为） |
| C5 | 多轮回执注入 | `drain_replies` 内容出现在下一轮 `_prepare_prompt` 的女仆上下文 |

### L2 半真机层（下一轮实施）

- `tools/fake_maid.py`：纯 Python `websockets` 连 `ws://127.0.0.1:21420`，按协议发 `hello/perception/event/command_result`，记录下行 `command/speak/animation`。
- `tools/chain_smoke.py`：起桌宠（`main.py`，测试模式）→ Fake 女仆连上 → 断言：
  - 桌宠 `maid_handler` 收到感知/事件 → 播报 → 气泡日志；
  - 指令下发到 Fake 女仆正确落账；
  - 断 WS → 文件遥测回放不重复（EventDedup）。

### L3 真机端到端层（下一轮实施）

`tools/e2e_full_chain.py`（扩展现有 `run_e2e_test.py` 基建：HMCL 启动命令 / quick-play / token 守门）。

- **A 环境**：桌宠监听 + 游戏 quick-play 进存档 + `/summonmaid` + WS 握手 + 官方 API Key 可用。
- **B 用户→AI→女仆 对话闭环**：脚本驱动真实聊天窗新会话发「去打僵尸」→ 断言真实 AI 回复含 `【attack(range=..)】` → 桌宠下发 → 女仆回执 → 下一轮上下文含 `[系统] 女仆任务完成`。连发 3 轮验证上下文连续。
- **C NLU 自动执行闭环（含递归合成）**：发「帮我合成一把稿子」→ 本地识别 craft → `craft_check`（递归：背包只有原木无木棍仍判可合成）→ 下发 `craft` → 回执 → AI 润色。**真机验证递归合成**（见 §五）。
- **D 感知/播报闭环**：女仆受伤/发现敌人 → 遥测 jsonl → 桌宠播报 → 气泡；低血触发 AI 主动决策 → 下发 DSL（允许抢占）。
- **E 收敛与降级**：快速事件不刷屏（去重+限流）；断 WS 走文件回放不重复；切千问后端对话正常（可选，标记千问流式不接 DSL 为已知限制）。

### L4 手工验收层

视觉（气泡贴合/动画/音画同步）、交互（拖拽/点击/动作菜单）、皮肤切换、网页版自动登录、图片识图（真实传图看颜色）、语音按住说话、TTS 四引擎试听（Edge / 本地 / 千问 TTS / 模型原生语音）。

## 五、递归合成专项（本轮已实现，纳入测试）

> 模组 `CraftExecutor` 已支持递归配方解析（2026-09-12）：背包缺的直接材料可由其他配方逐层合成补足；带「展开链 visited + 深度上限」防循环（铁块↔铁锭不死递归）。

| 场景 | 预期 | 层 |
|---|---|---|
| 背包 3 铁锭 + 1 原木，合成铁镐 | `craft_check` 报 木棍 `ok=true`（`via_craft`）+ `craft_plan`；`craft` 先合成木板/木棍再合成铁镐 | L0（桌宠侧契约已测）+ **L3 C 真机** |
| 背包 1 铁块，合成铁锭 | 判定可合成，直接拆 | L3 真机 |
| 背包 0 铁锭 0 铁块，合成铁锭 | 判定不可合成，且不死循环 | L3 真机 |
| 桌宠 `test_router_via_craft` | `via_craft` → 自动执行 + 注入文本说明补齐来源 | L0 ✅ |

模组侧 `MaidAutoTest` 递归合成 3 场景（✅ 2026-09-12，已实现 `config/smartmaid/autotest.json`）：
- `give` 预置背包（原木×8 + 铁锭×5 → `craft_check`/`craft` 铁镐，断言 `result.craftable=true`）
- 循环配方（无铁块时 `craft_check` 铁锭断言 `craftable=false`，不死循环）
- 直接拆（`give` 铁块×1 → `craft_check` 铁锭断言 `craftable=true` → `craft` 成功）
- `MaidAutoTest` 升级为 tick 状态机：任务型回执自动等待 ~2s 再取下一条；支持 `expect` 回执扁平字段断言（PASS/FAIL 写日志）。

## 六、验收标准

对齐 `DESIGN_LOOP_DEEPENED.md §13` 全部 + 本次新增：

1. 闭环：用户说「去打僵尸」→ AI 回复夹带 `【attack】` → 女仆执行 → 回执进下一轮上下文。
2. 多后端：网页版（只发当轮）/ 无状态提供方（官方 API、千问、Claude、自定义 —— 滑动窗口 + 提炼，不重发全量）/ 千问流式（含原生语音）均对话正常。
3. 隔离：游戏事件/决策轮 `ephemeral=True` 不进用户对话历史。
4. 递归合成：背包只有原木 → `craft_check` 报 `via_craft` → `craft` 真合成出铁镐；循环配方不死递归。
5. 去重/限流：同一事件不重复播报；30s 内 AI 调用 ≤5。
6. 排队：AI 指令默认不抢占女仆任务，除非 `cancel_previous=true`。
7. 降级：断 WS 走文件回放不重复；AI 未配置 Key 时提示而非崩溃；原生语音无音频时回退千问 TTS。
8. 回归：`test_providers / test_tts_catalog / test_audio_stream / test_nlu / test_wiki / test_reconstruction / test_maid_loop / test_chat_unit / test_chat_chain` 全过；`main.py --smoke` exit=0。

## 七、实施顺序与状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | TEST_PLAN 定稿 | ✅ 本文档 |
| P2 | L0：补 ChatWorker / 路由 / 消息队列单测（`tests/test_chat_unit.py`，14 项） | ✅ 2026-09-12 |
| P3 | L1：`tests/test_chat_chain.py`（5 项） | ✅ 2026-09-12 |
| P4 | 模组 `MaidAutoTest` 递归合成 3 场景（状态机 + give + expect） | ✅ 编译通过；**真机跑：c1/c2/c5/c6 PASS，c3 FAIL**（见下） |
| P5 | L2：`fake_maid.py` + `chain_smoke.py` | 待做 |
| P6 | L3：`e2e_full_chain.py`（含递归合成真机） | 待做 |
| P7 | L4：手工清单 | 待做 |
| P8 | **L2.5：真人设 × 真 API 多模式实测**（`tools/test_api_modes.py` / `test_api_game.py`，18 条 + 6 条校验） | ✅ 2026-09-14（2.20.0） |
| P9 | **L0 增补：`[U1b]`/`[U1c]`/`[U1d]` 后端分流与精简人设回归** | ✅ 2026-09-14（2.20.0，`test_chat_unit` 17 项） |
| P10 | **L0 增补：AI 接口通用化**（`tests/test_providers.py`：注册表 / 能力位 / 凭据命名空间 / 模型别名 / 版本铁律守卫） | ✅ 2026-09-20（2.24.0–2.26.0） |
| P11 | **L0 增补：TTS 目录 + 原生语音管线**（`tests/test_tts_catalog.py`、`tests/test_audio_stream.py`） | ✅ 2026-09-20（2.26.0 / 2.27.0） |
| P12 | **L3 补：原生语音多轮真机**（长文本 / 断流 / 与 DSL 同轮） | 待做（现有 `tools/_probe_native_audio.py` 单轮探针已用完删除，需重建） |

---

## 九、会话记录（2026-09-12，全链路测试实施）

> 本会话做了大量改动，**不少是真机踩出来的**。接手前必读本节。

### 9.1 已实施改动（两个仓库）

**桌宠侧（`desktop-pet`）**：

| 文件 | 改动 | 动机 / 验证 |
|---|---|---|
| `tests/test_chat_unit.py`（新） | `_ChatWorker` prompt 组装 / `_apply_dsl` 三分支 / 图片降级 / `make_client` 路由 / 消息队列 / `_run_plain` / **NLU 已执行抑制 AI DSL(U7)** / NLU 未执行 AI 兜底(U8) | 补齐聊天窗链路单测 |
| `tests/test_chat_chain.py`（新） | 用户→NLU→AI→DSL→女仆→回执注入 闭环集成 | L1 |
| `tests/test_nlu.py` | `via_craft` 递归合成判定 + 用户指令默认抢占 + 语义化告知断言 + FakeBridge 记录 cancel_previous | 见下 |
| `nlu/router.py` | ①`_render_ingredient` 显示 `via_craft`/`craft_plan`；②`_inventory_ready` 感知未就绪提示（equip/drop）；③`_backpack_slot_of`/equip/drop 失败打 `[NLU-DIAG]`（stderr）；④**`_send` 用户指令默认 `cancel_previous=True`（抢占）**；⑤`_describe_executed` 语义化告知 + `_generic_summary` 明确「已自动完成，不要重复执行/发指令」 | ①递归合成透传；②防「感知未就绪误报没有」；③抓反查失败现场；④用户指令优先于 AI 任务；⑤告知 AI（用户核心诉求） |
| `ai/chat.py` `_ChatWorker` | 记录 `_nlu_executed`（preprocess 返回 tuple）；`_apply_dsl` 在 NLU 已执行时**剥离 DSL 但不下发** | 修复「AI 兜底【equip】撤销 NLU 穿甲」 |
| `pet/modes.py` | `nlu_preprocess` 返回 `(注入文本, executed)` | 支撑抑制/告知 |
| `pet/handlers/stt_handler.py` | 适配 tuple | 语音链路 |
| `TEST_PLAN.md`（新） | 本文档 | — |

**模组侧（`SmartMaid`）**：

| 文件 | 改动 | 验证 |
|---|---|---|
| `CraftExecutor.java` | **递归配方解析**：`craft()` 递归补中间材料（原木→木板→木棍）；`check()` 递归判定 + `via_craft`/`craft_plan`；`ingredientNeeds` 聚合（修复 need 低估）；`resolveItem/ensureMaterials` 加 `produces` 产物验证（修复误选配方）；循环防护（visited + 深度 4） | 编译通过；真机 c1/c2/c5/c6 递归合成 PASS |
| `MaidAutoTest.java` | tick 状态机（任务型等待）+ `give` 预置背包 + `expect` 回执断言（PASS/FAIL 日志） | 编译通过；真机 c3 FAIL |
| `PerceptionModule.java` | `snapshotNow()`：强制全量采样（女仆生成用） | 编译通过 |
| `MaidWsClient.java` | 女仆首次注册通道时 `pushFullSnapshot`（reset diff + snapshotNow + 立即发 full） | 编译通过；真机「女仆生成全量感知已推送」出现 |
| `config/smartmaid/autotest.json`（新） | 递归合成 3 场景（give 原木+铁锭 → 铁镐；无铁块铁锭不可合成；give 铁块 → 拆） | 见 c3 FAIL |

### 9.2 真机验证结果与问题

**递归合成 AutoTest 真机（进游戏跑 `autotest.json`）**：
- c1 `craft_check` 铁镐（原木→木板→木棍 递归）→ **PASS**（`craftable=true`，need 聚合 3 铁锭+2 木棍）
- c2 `craft` 铁镐 → **PASS**
- **c3 `craft_check` 铁锭（期望不可合成）→ FAIL**：女仆存档有 69 铁锭，**技术上确实能合成铁块再拆** → `craftable=true`。**是测试场景依赖背包状态**（需先清背包再测，或换必然无解物品）。
- c5/c6 give 铁块后 `craft_check`/`craft` 铁锭 → **PASS**

**「穿金胸甲」历史问题（最终根因链）**：
1. 早期（13:49/15:20/15:32）「未执行」：部分时刻**感知/item_index 未就绪**（女仆刚连/刚召唤），反查到空背包误报「没有」；15:32:53 那次反查 slots 有金胸甲但失败，**item_id 未记录**（旧桌宠无日志），无法 100% 还原。
2. 15:44:54 起 NLU 正常执行（`item_id=golden_chestplate` 匹配 slots[0]），但**AI 兜底回复夹带【equip】**把金胸甲从胸甲槽拿回主手 → 用户看到「没穿上」。
3. **修复**：NLU 已执行时抑制 AI DSL（`_ChatWorker._apply_dsl`）。
4. **用户指出核心**：不是抑制/兜底，而是 **NLU 处理完必须用 AI 能懂的语言告知「已自动完成、做成了什么」** → 已加 `_describe_executed` 语义化 + 明确「不要重复执行/发指令」。

**「丢钻石剑」问题**：
- 女仆正在低血守护（`guard`，AI 决策），用户指令默认排队（`cancel_previous=false`）被拒 →「正忙」。
- 修复：`_send` 用户显式指令默认抢占（`cancel_previous=True`）。
- 真机：16:51:19 丢钻石剑成功（`Transfer 完成: diamond_sword 到地面`）。

**仍未解决（接手重点）**：
1. **AI 疯狂发无效 DSL**：`crash.log` 有 **57 组** `MaidIntentParseError`（`move` 坐标 `[1,2]` 格式错、未知指令 `fly`、`move` 缺 `pos`）。prompt 鼓励「游戏模式主动指挥女仆」，但 AI 参数质量差。**需：约束 prompt 或让 AI DSL 走更强校验**。
2. **attack 无目标**：真机 `Attack start target=空` → 女仆「无目标」空转。NLU 识别了 attack 但没解析出目标就下发。**需：attack 无目标时不执行/追问**。
3. **c3 测试场景**：需清背包或换物品（见上）。
4. **千问流式不接 DSL**（已知缺口）。
5. **DSL 抑制的副作用**：NLU 已执行时抑制所有 AI DSL，可能误杀 AI 补充指令（如「穿完去打怪」）。当前是安全选择，可后续细化。

### 9.3 教训（务必遵守）

1. **不要反复启动/关闭桌宠进程**——用户自己管理；改动代码后用 `--smoke` + 测试验证即可，真机由用户启动。
2. **改一处先跑全套测试**：`test_nlu / test_chat_unit / test_chat_chain / test_reconstruction / test_maid_loop` + `main.py --smoke`。
3. **`latest.log` 是 GBK**，读取先按字节 gbk/utf-8 双解码。
4. **诊断日志用完即清理**（本次 `maid_link.py` 的诊断已全部移除，`git status` 无该文件改动）。
5. **感知/item_index 未就绪时不要下结论**——女仆刚连/刚召唤有窗口期。
6. **模组构建**：`JAVA_HOME=...\mojang-java-runtime-epsilon` + `gradlew.bat build`；后台启动 + 日志轮询，禁简单超时。

## 八、风险与前置

| 项 | 说明 | 对策 |
|---|---|---|
| 官方 API Key | L3 在线层需要 | 缺省读 QSettings；未配置则跳过并显式标记 |
| HMCL accessToken | 过期会导致游戏卡主菜单 | 复用 `--check-token` 守门；需 GUI 登录一次 |
| 游戏加载时长 | 真机链路 5-10 分钟 | 窗口压小 + quick-play + 单进程跑完 |
| 千问流式不接 DSL | `_run_stream` 已知缺口 | L1 C4 标为预期行为；后续再补流式 DSL |
| 真机日志编码 | `latest.log` 为 GBK | 读取统一先按字节 gbk/utf-8 双解码 |
| 长耗时操作 | 起游戏 / 构建 | 后台启动 + 日志轮询，禁简单超时 |

---

## 十、会话记录（2026-09-14，2.20.0 人设按后端分流）

> 完整实测数据见 **`API_PROMPT_TEST_REPORT.md`**；设计说明见 `DESIGN_LOOP_DEEPENED.md §3.4`。

### 10.1 起因与设计

两条后端的人设**注入次数不对称**（网页版只在会话首条注入、官方 API 每轮重发），
却共用同一份全长人设 → API 每轮都在重烧 6900+ 字。于是：

- 新增精简人设 `prompt_api.txt` / `prompt2_api.txt`（`config.PERSONAS[i]["file_api"]`）；
- 模式声明「现在切换到 x、模式名」从 user 消息**改走 system**（API 侧），不再污染 history；
- 网页版行为完全不变。

### 10.2 两轮实测（L2.5 新增层）

**第一轮（首测）**：2 人设 × 3 模式 × 3 条 = 18 条真实 API 调用。
链路全对（走对精简人设、system 每轮含【当前模式】、user 消息无「现在切换到」），
**AI-DSL 闭环在 API 后端下确实活着**（游戏模式 3/3 主动发指令、参数全合规、引用了 `[态]` 瞬态）。
但暴露 **3 个精简缺陷**（详见报告 §四）：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 御坂版标签写成 `[坐下]` | 精简时漏了「必须用 `{}` 包裹」 | 补约束句 + 示例 |
| 2 | 御坂版 2/3 条**完全不带**标签 | 「必须带一个 `{动作}`」的强制语气被压缩掉 | 补「**不得省略**（驱动动画）」 |
| 3 | 女仆版照抄占位符 `【指令名(...)】` | 留了规范但删了反例 | 补正误对照 |

**第二轮（精修）**：又暴露 3 个**结构性**问题并根治（报告 §五）：

| # | 问题 | 修法 |
|---|---|---|
| 4 | 「女仆指令」被写成**游戏模式限定** → 非游戏模式不敢发指令 | 改「**全域可用，不限游戏模式**」+ 判定口径 |
| 5 | 无跨模式范例 | 新增 7 条范例（日常/工作/游戏） |
| 6 | 模式差异缺失（工作模式「不输出动作标签」与人设「必须带」冲突） | 新增「模式差异」段仲裁 |

顺手修一个真 bug：`config.get_api_prompt` 传 `backend=None`（自动探测）→ 改为 `backend=True` 强制精简。

### 10.3 精修后实测结果

| 指标 | 前 | 后 |
|---|---|---|
| 带 `{动作}` | 14/18 | **18/18** |
| 用 `[]` | 3/18 | **0/18** |
| 工作模式夹带人设梗 | 有 | **0/9** |
| 游戏模式主动发指令 | 3/6 | **6/6** |
| 指令顺序违规 | 2/6 | **0/6** |

`tools/test_api_game.py` 结构化校验 **6/6 全过**。每轮 system：女仆 6923 → 4587 字（省 **34%**）、
御坂 7078 → 5235 字（省 **26%**）。

### 10.4 教训（增补）

7. **压缩人设时，功能性硬约束的措辞不能跟着压缩**——模型会漏。至少保留：
   格式要求 + 反例 + 「必须/不得」的强制语气 + 触发条件（且条件不能写窄）。
8. **"看起来像废话"的句子可能是功能性的**（如「全域可用」四个字）。已用回归用例
   `[U1d]` 钉死，防止后续再压 token 时顺手删掉。
9. **`backend=None` 的"自动探测"语义容易埋雷**——需要"恒取某变体"时必须显式（`backend=True`），
   否则结果取决于运行时状态，离线/测试会静默拿错。

### 10.5 环境坑（增补）

10. **本机 PowerShell 会吞 stdout**（命令 exit=0 但无输出）→ 让脚本自己写 UTF-8 文件再用 Read；
    `>` 重定向与 `Set-Content -Encoding UTF8`（带 BOM）都不可靠。
11. **中文路径下 PowerShell `Remove-Item` 常失效** → 用 Python `os.remove`。
12. 跑带 PySide6 的测试用**系统 Python**（`WindowsApps\python.exe`），受管 3.13 与 nlutrain venv 都没有。

---

## 十一、会话记录（2026-09-16，2.21.0 Wiki 链路修复）

> 完整数据见 **`WIKI_AUDIT.md`**（修复前后对比 + 未做清单）；复现工具 `tools/wiki_audit.py`。

### 11.1 起因与方法

用户要求「先梳理 Wiki 情况」→ 做了一次**基于实测的审计**：先写审计脚本跑数，
再拿数据说话，而不是照抄设计文档。结果发现 Wiki 的问题**不是慢**（毫秒级），
而是**注入的知识要么拿不到、要么是错的** —— 且文档与实装已有多处漂移。

审计工具 `tools/wiki_audit.py`（→ `tools/wiki_audit_report.txt`）覆盖：
数据层体量 / curated 覆盖与可达性 / 标题与别名口径 / 实体提取效果 /
三层链路产出与体量 / 耗时。**改动 Wiki 代码或数据后重跑它对比即可。**

### 11.2 修复清单（P0→P2，随后追加两条）

| 优先级 | 问题 | 修法 | 实测 |
|---|---|---|---|
| P0-1 | 实体提取只扫标题、不查重定向别名 → 最常用的工具/装备拿不到（铁镐→`[]`、钻石剑→`['钻石']`） | 词表改为「标题 + 别名」+ n-gram 滑窗；单字实体加事件上下文守卫；长子串优先 | 铁镐/木镐/钻石剑/樱花木/猪/橡木原木 全对；0.7ms→0.5ms |
| P0-2 | curated 双向子串抢答 → 注入错误实体（樱花木→【樱花木板】、嗅探兽→进度页） | `find()` 只认精确（name/id/aliases）；实体条目优先于成就/进度；去完全重复 | 三例均变为「未命中 → 走 L2/L3」；0.167ms→0.022ms |
| P1-1/2 | common 混入 843 条非实体页（版本族/音乐/开发商/平台版本） | `_VERSION_PREFIX_RE` + `_VERSION_RE` 补版本族 + 正文特征（`_TECH_TEXT_HINT` / `_RARE_TEXT_HINT2`）；`_RARE_RE` 去掉「音乐」 | common 4382→3539，真实体零误伤；`--classify` 就地重分类（10s） |
| P1-3 | 精炼超预算句整句丢弃、兜底无长度上限 | 跳过放不下的句子给短句机会 / 兜底加上限 / 相似句 Jaccard 去重 / `MAX_TOTAL=172` | 单块 114~162 字 |
| P2 | `mc_log` 在线兜底死代码；去重「无论命中与否都记」 | 删死代码；`_wiki_knowledge` 返回 `(文本, 命中词)`，只记产出过的词 | 空结果可重试 |
| 追加① | 整条 `{wiki}` 注入无总额约束（12 词全命中 **1460 字**） | `GAME_WIKI_MAX_CHARS=480` + `_clip_knowledge()` | 1460 → **468 字** |
| 追加② | L2 批量共享页数上限 → 6 词只覆盖 2 个实体；句子无实体名 → 跨实体混排 | `search_candidates(per_term=1)` 逐词取页打 `term`；`refine` 按实体分组加 `【实体名】` | 覆盖实体 **2 → 6**；混排消失 |
| 追加③ | 按剩余预算切碎句子 → 注入半句碎片 | 改为跳过该句；截断抽成共用 `cut_at_boundary()` | 块尾收在句末 |

### 11.3 验证

| 项 | 结果 |
|---|---|
| `tests/test_wiki.py`（**新增，9 项**） | ✅ 别名/单字守卫/长子串优先 · curated 不抢答 · 同名实体优先 · 精炼预算 · 分类规则 · L2 逐实体分组 · 注入总预算 |
| `test_nlu.py`（27 类）/ `test_reconstruction.py` / `test_maid_loop.py`（14 项）/ `test_chat_unit.py`（17 项）/ `test_chat_chain.py`（5 项） | ✅ 全过（exit=0） |
| `main.py --smoke` | ✅ exit=0 |
| `tools/wiki_audit.py` 复测 | ✅ 全部指标达成（见 `WIKI_AUDIT.md §三`） |

### 11.4 教训（增补）

13. **先拿数据再改代码**：这轮所有结论都来自脚本实测，其中 3 个问题
    （L1 无总额上限、L2 只覆盖 2 页、跨实体混排）是**审计过程中新发现的**，
    设计文档里完全没提。文档会漂移，脚本不会。
14. **源码字符串断言太脆**：`assert 'return "\\n".join(parts), hit' in getsource(...)`
    在这轮改结构时直接红了。改为**行为断言**（真调 `_wiki_knowledge` 验预算、
    真跑 `search_candidates + refine` 验分组不混排）。
15. **测试装置别比现实善良**：`match_terms` 的旧单测用了含「镐子」的假词典，
    一直绿；真机（官方库只有「木镐/铁镐」+ 重定向）根本解析不出来。
    Wiki 侧的真库用例要保留（DB 缺失时显式 skip，不要假装通过）。
16. **「截断填满预算」是个陷阱**：为了用满预算去切碎句子，会注入
    「…无法被喂食除金」这种碎片。放不下就跳过、给后面的短句机会更划算。
17. **加预算时要连分隔符一起算**：块之间用 `\n` 拼接，不计入就会超预算
    （实测 483 > 480，被新加的守卫测试逮到）。
