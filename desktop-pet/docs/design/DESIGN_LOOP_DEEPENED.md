# 通讯闭环深化设计（DESIGN_LOOP v0.3 · 已实施）

> 状态：**已实施**（四阶段全部落地，随 `desktop-pet` 2.19.0）。
> **2.20.0 增补**：人设按后端分流（网页版全长 / API 精简）+ 模式声明改走 system，见 **§3.4**；
> §4.2–§4.4 已按当前实装更新（女仆指令**全域可用**）。
> 配套 `DESIGN_LOOP.md`（v0.2 思想稿）。
> 三个已拍板决策：
> 1. **范围**：先深化设计，确认后再分期实施。
> 2. **接入 DeepSeek 官方 API**（有 Key）——用 `MessagesBackend`（客户端传 messages）实现「瞬时状态不进历史」。
> 3. **暂共用主会话**——不做独立 `maid_chat` 会话（修正 1 暂缓），靠 MessagesBackend 的 `ephemeral` 轮次隔离。

---

## 0. 结论先行（一句话）

**给主对话加一个"官方 API 通道"，AI 回复可以夹带 `【DSL 指令】`，桌宠解析后下发给女仆；女仆的实时状态/事件/回执以「当轮注入、不进历史」的方式喂给 AI；再加全局软限流与通道去重。** 物理链路已通（WS + 文件），本稿只解决逻辑层。

**后端信息流差异（本稿最重要的前提，贯穿全部设计）**：

| 维度 | **网页版（SessionBackend）** | **官方 API / 千问（MessagesBackend）** |
|---|---|---|
| 记忆机制 | **服务端自动记忆**（`session_id` + `parent_message_id` 链） | **无状态**，每次请求都要自带完整上下文 |
| 客户端发什么 | 只发**当轮 prompt**，服务端自己记住历史 | 每次组装 messages（system + 历史 + 当轮）|
| 历史提炼 | **不做**（历史在服务端，我们拿不到也不该管） | **必须做**：滑动窗口 + 定期提炼，**绝不把全量历史发给 AI** |
| 瞬时状态 | 压缩成极短行随当轮发出（会被服务端记住） | 本轮组装即弃（`ephemeral`），物理不进历史 |
| 人设注入 | **只在会话首条**（用全长人设） | **每轮重发 system**（用精简人设，§3.4） |
| 模式声明 | 塞进 user 消息 | **写进 system**，每轮刷新 |

> ⚠️ 这是用户明确强调的红线：**网页版靠服务端自动记忆，客户端不提炼历史；API 端每次必须自己带上下文，所以要自己做历史管理——滑动窗口 + 每隔 N 轮提炼一次，绝不整段重发历史。**

---

## 1. 现状盘点（代码核对结论）

### 1.1 已具备（直接复用）

| 能力 | 位置 | 说明 |
|---|---|---|
| WS Server + 感知合并 + 指令下发 | `game/maid_link.py` | `send_command / request / query_craft / query_status / wait_task_done`，`cancel_previous` 已参数化 |
| 事件/回执入队 | `maid_link.poll / drain_events / drain_replies` | 已从 asyncio 线程送主线程 |
| 女仆 Handler | `pet/handlers/maid_handler.py` | 1s tick、8s 事件冷却、`.maid_command.json` 调试口 |
| 文件通道 | `game/mod_data.py` + `game_handler.py` | `deskpet/maid/*.jsonl` 窗口读取（已含子目录扫描） |
| NLU 自动执行 | `nlu/router.py` + `nlu_bridge.py` | 意图识别 → 契约填参 → `send_command` 阻塞等回执 |
| 主对话封装 | `ai/chat_service.py` | 全局串行锁 + 人格注入 + DeepSeek 网页版持久会话 |
| wiki 提炼独立会话 | `ai/client.py::refine_wiki` | 独立 sid/pid 持久化（可照抄模式） |

### 1.2 缺失（本稿要补）

| # | 缺口 | 本稿章节 |
|---|---|---|
| 1 | AI 不会主动发指令（无 DSL、prompt 无指令章节） | §4 |
| 2 | 瞬时状态不进主对话历史 | §5（官方 API + ephemeral） |
| 3 | command_result 不回喂 AI | §6 |
| 4 | 文件/WS 双通道去重 | §7 |
| 5 | 全局软限流（AICallThrottle） | §8 |
| 6 | 播报分级（模板/AI 润色/AI 决策） | §9 |
| 7 | 任务态 vs 对话态：`cancel_previous` 默认**排队** | §10 |

---

## 2. 目标数据流（端到端）

```
用户说话/打字
  ├─► 聊天窗 / 语音 → ChatService（官方 API 优先，网页版兜底）
  │        └─► 本地 NLU 预处理（已存在）：识别「指挥女仆干活」→ 直接执行 + 把事实给大 AI
  │        └─► 组装 messages：system(人设 + 当前模式) + 记忆(facts) + 历史(纯净) + [瞬时状态 + 用户消息]★
  │              （网页版 system 只在首条注入、用全长人设；API 每轮重发 system、用精简人设 —— §3.4）
  │        └─► DeepSeek 官方 API 调用 → 完整回复
  │              └─► 解析回复：剥离 {动作标签}（已有）、提取【DSL 指令】★
  │                    └─► 女仆指令下发（maid_link.send_command，默认排队）
  │                    └─► 回执 command_result → 存为「待注入上下文」★（不触发新调用）
  │              └─► 展示气泡/语音（已有）
  └─► 游戏侧事件（文件窗口 / WS event / perception）
        └─► 通道去重（共享指纹）★
        └─► 播报分级：模板播报（不调 AI）/ AI 润色 / AI 决策★
              └─► AI 决策（低血/被围）→ 可夹带【DSL】→ 下发（允许抢占）★
```

`★` = 本次新增。`ephemeral` 轮次（AI 决策/播报润色/游戏互动）**不回写**主对话历史。

---

## 3. 后端抽象：MessagesBackend（官方 API）+ SessionBackend（网页版）

### 3.1 设计（两种后端的信息流差异是核心）

`ai/client.py` 的 `backend_name()` 目前是 `deepseek`（网页版）/ `qwen`。**新增** `ai_backend = "deepseek-api"`（官方 API，OpenAI 兼容）。

**记忆机制完全不同，两个后端的职责边界必须分开**：

| | **SessionBackend（网页版）** | **MessagesBackend（官方 API / 千问）** |
|---|---|---|
| 谁记历史 | **服务端**（`session_id` + `parent_message_id` 链） | **客户端**（本地 `ctx.history` 列表） |
| 客户端发什么 | 只发**当轮 prompt**（服务端自己串上下文） | 每次组装 messages：system + 记忆 + 窗口历史 + 当轮 |
| 历史提炼 | **不做**（历史在服务端，我们够不着、不该管） | **必做**：滑动窗口 + 定期提炼（§5.3），**绝不整段重发** |
| 瞬时状态 | 压缩成极短行随当轮发出（会被服务端记住） | 本轮组装即弃（`ephemeral=True`），物理不进历史 |
| **人设注入时机** | **只在会话首条**（`ai/deepseek.py` 判 `parent_message_id is None`）→ 用**全长人设** | **每轮作为 `messages[0]` 重发** → 用**精简人设**（§3.4） |
| **模式声明载体** | 塞进 user 消息（服务端记住，只发变化时） | **写进 system**，每轮随 system 自动刷新 |

### 3.4 人设按后端分流（2.20.0）

> 起因：两条后端的人设**注入次数不对称**（上表最后两行），却长期共用同一份全长人设
> （`prompt.txt` 6923 字 / `prompt2.txt` 7078 字）。网页版只在首条注入一次、成本可忽略；
> 官方 API **每轮重发**，等于每轮都在重烧这份 token，随轮数线性增长。

**分层与文件**

| | 网页版（SessionBackend） | 官方 API / 千问（MessagesBackend） |
|---|---|---|
| 人设文件 | `prompt.txt` / `prompt2.txt`（全长） | `prompt_api.txt` / `prompt2_api.txt`（精简） |
| 每轮 system 成本 | 一次性（首条） | 4587 / 5235 字（**省 34% / 26%**） |
| 模式声明 | user 消息里「现在切换到 x、模式名」 | system 末尾追加 `【当前模式】+ 该模式规则` |

**接线**

```python
# config.py
persona_prompt_file(info, backend)     # backend 走 MessagesBackend → info["file_api"]，否则 info["file"]
load_system_prompt(persona, pet_name, backend=None)
    # backend=None → 自动探测当前后端；backend=True → 强制精简（get_api_prompt 用）
get_api_prompt(persona, mode_id, pet_name)   # 精简人设 + 【当前模式】+ 该模式规则
is_messages_backend(name=None)               # 自动探测，模式声明分流的判定依据
```

> **2.24.0 修订（AI 接口通用化后）**：`config.MESSAGES_BACKENDS` **已删除**，
> `is_messages_backend()` 改为查供应商注册表的能力位 `prov.cap(pid, CAP_SERVER_MEMORY)` ——
> 「哪些后端无状态」不再有两份硬编码。本节其余论述（两种信息流的不对称、人设按后端分流、
> 模式声明的归属）**仍然有效**，只是下表里「官方 API / 千问」这一列现在泛指**所有无状态提供方**
> （DeepSeek 官方 API、千问、Anthropic Claude、自定义 OpenAI 兼容）。详见
> `DESIGN_AI_PROVIDERS.md §12 / §14`。

- **模式声明分流**（原来 3 处无条件注入「现在切换到 x、模式名」）：
  `ai/chat.py::_ChatWorker._prepare_prompt`（仅网页版）、
  `game/processor.py::_switch_decl`（API 早退返回空串）、
  `pet/handlers/work_handler.py::_tip_worker`（API 不拼前缀）。
  API 侧改由 `MessagesBackend.build(..., mode_id=)` 把模式段写进 system —— 不再污染 history
  （塞进 user 消息会被写进历史、被反复重放，既费 token 又干扰对话）。
- **人格切换**「把整段人设塞进 user 消息」的补丁**仅网页版保留**
  （`chat_service.chat` / `ChatWindow._send`）：DeepSeek 多轮会话 system 只注首轮，必须靠 user 补；
  API 每轮重发 system，人设已随 system 刷新，无需污染 history。

**⚠️ 压缩人设的铁律（两轮实测换来的）**

**功能性硬约束的措辞不能跟着压缩**，否则模型会漏。第一轮压缩后实测暴露 3 个缺陷
（动作标签写成 `[]`、整条漏标签、把占位符「指令名」照抄成 `【指令名(attack(...))】`）；
第二轮精修又暴露 3 个结构性问题（女仆指令被误写成游戏模式限定、无跨模式范例、模式差异缺失）。
精简版必须显式保留：

1. 「必须用 `{}` 包裹（不是 `[]`、不是 `()`）」+ 正误示例；
2. 「**每条回复都必须带一个 `{动作}`，不得省略**（驱动桌宠动画），工作模式短句也要带」；
3. 指令格式正误对照（`【attack(range=10)】` ✓ / `【指令名(...)】` ✗）；
4. **「女仆指令（全域可用，不限游戏模式）」** —— 条件写窄会让 AI 在非游戏模式不敢发指令；
   判定口径是"消息里带 `[态]`/`[系统]`"，而这两种状态在**所有模式**都会注入；
5. **跨模式指令范例**（日常/工作/游戏各若干条）；
6. **「模式差异」段** —— 三模式共用同一套输出格式硬规则，模式只改「说什么」不改「怎么写」。
   （`config.AI_MODES` 的工作模式原文写「不输出动作标签」，与人设「必须带 `{动作}`」直接冲突，
   必须由这一段显式仲裁。）

**回归守卫**：`tests/test_chat_unit.py::[U1d] test_api_prompt_global_maid_and_modes` 逐条钉住上述条款。
**实测报告**：`API_PROMPT_TEST_REPORT.md`（18 条真实 API 调用；精修后 18/18 带 `{动作}`、
游戏模式 6/6 主动发指令且参数全合规）。

```python
class ContextBuilder:
    def build(self, ctx, memory, transient, user_msg) -> dict: ...
    def append_history(self, ctx, user_msg, assistant_msg, ephemeral=False): ...
    def request(self, messages, **kw) -> str: ...   # 实际调后端

class SessionBackend(ContextBuilder):
    """DeepSeek 网页版：服务端自动记忆，只发当轮 prompt。
    - build() 返回的就是「当轮一条 prompt 字符串」（可能拼上极短状态行），
      不含任何历史 —— 历史由服务端靠 session_id + parent_message_id 链自动串起。
    - 无本地历史列表，无提炼（服务端自己记，客户端不碰历史）。
    - 例：request({"prompt": "[态] 生命18 任务guard 目标zombie\n" + user_msg})
    - 瞬时状态压缩为极短行拼进当轮 prompt（[态] 生命18 任务guard）。"""

class MessagesBackend(ContextBuilder):
    """官方 API / 千问：客户端维护 ctx.history。
    - build() 返回完整 messages 数组（system + 记忆 + 窗口历史 + 当轮）。
    - 历史超窗自动触发提炼（§5.3），只保留「记忆 + 最近 N 轮」，不重发全量。
    - ephemeral=True 的轮次（游戏/决策/播报）不进历史，避免污染。"""
```

### 3.2 MessagesBackend 的 ephemeral 语义（核心）

```python
def append_history(self, ctx, user_msg, assistant_msg, ephemeral=False):
    if ephemeral:            # 游戏事件 / AI 决策 / 播报润色 —— 不进历史
        return
    ctx.history.append(Turn(user=user_msg, assistant=assistant_msg))
    self._maybe_condense(ctx)   # 超窗/超轮 → 触发提炼（§5.3）
```

- **历史只存纯净对话**（用户/AI 原文），瞬时状态永不进入。
- `build()` 组装：`system(人格) + memory(facts+summary) + history[-WINDOW:] + [user_msg + transient 状态]`，仅最后一轮是「含状态的当轮」，调用后 `append_history(ephemeral=...)` 按需写回。
- **绝不做**「把整个 `ctx.history` 都发给 AI」——那是无状态 API 上 token 爆炸的根源。历史管理见 §5.3。

### 3.3 官方 API 客户端

新增 `ai/deepseek_api.py`（OpenAI 兼容）：

```python
class DeepSeekApiClient:
    def __init__(self, api_key, model="deepseek-chat"): ...
    def chat(self, messages, thinking=False) -> str:
        # POST https://api.deepseek.com/chat/completions
        # body: {"model":..., "messages":..., "stream": false, "thinking": ...}
```

> 设置项：`deepseek_api_key` / `deepseek_api_model`（`deepseek-chat` 默认）。配置在「聊天 ⚙ → AI 后端」，与千问同模式（填 Key 才可用）。

### 3.4 接入 ChatService

`ai/chat_service.py::chat()` 改为后端路由：

```
ai_backend == "deepseek-api" → MessagesBackend（官方 API）+ 本地 ctx.history + 提炼
ai_backend == "qwen"         → MessagesBackend（千问）+ 本地 ctx.history + 提炼（统一复用 MessagesBackend）
ai_backend == "deepseek"     → SessionBackend（网页版，现状不动：服务端自动记忆、无提炼）
```

- **共用主会话**：`ChatService` 只有一个 `ctx`（`ai/context.py::ChatContext`），聊天/语音/游戏/新闻/工作都走它。游戏轮次用 `ephemeral=True` 不污染历史（这比「独立会话」改动更小且达到同样隔离效果——用户决策 3）。
- **提炼只发生在 MessagesBackend**；SessionBackend（网页版）不提炼、不维护本地历史——它把记忆完全交给服务端。

---

## 4. 女仆指令 DSL（缺口 1）

### 4.1 语法（决策 1 = `【】`）

```
【attack(range=10)】
【mine(pos=[100,-60,-50], range=4, count=5)】
【transfer(from=mainhand, to=0, count=1)】
【guard(range=12, cancel_previous=true)】
【stop】
```

- 指令名：`/maidtasks` 的 24 个 + 白名单（与 `nlu/mod_contract.py` 对齐，单一来源）。
- 坐标 `[x,y,z]` 绝对 / `~`（主人脚下）；物品 `iron_pickaxe`（可省 `minecraft:`）；槽位 `mainhand`/`0`。
- **一次最多一条，放在回复最末尾**；`cancel_previous=true` 才允许抢占。

### 4.2 Prompt 章节（四份人设文件都要有）

> **2.20.0 起人设按后端分两份**（§3.4），因此「女仆指令」章节**四份都要写**：
> 全长版 `prompt.txt` / `prompt2.txt`，精简版 `prompt_api.txt` / `prompt2_api.txt`。
> 精简版还必须额外带上「跨模式指令范例」与「模式差异」段。

在「输出规范」后新增「【女仆指令】」章节（当前文案见 §4.4），约定：
- 回复末尾可附一条 `【指令名(参数=值)】`，放在所有文字和 `{动作}` 标签之后。
- **全域可用，不限游戏模式** —— 只要消息里能感知到女仆存在（带 `[态]`/`[系统]`）即可发，
  不要写成"游戏模式下才允许"（那会让 AI 在非游戏模式不敢发指令）。
- 不确定时不要发指令，先问清楚。
- 带 `[态]`/`[系统]` 开头的是女仆实时状态/任务结果，据此回应。

### 4.3 解析器 `game/maid_intent.py`

```python
INTENT_RE = re.compile(r"【\s*(\w+)\s*(?:\((.*?)\))?\s*】", re.S)   # 位置无关
WHITELIST = {...mod_contract.COMMANDS...}   # 白名单单一来源（27 条）

def parse(text) -> dict | None:
    """返回 {'cmd':..., 'params':..., 'cancel_previous':...}；未知/畸形 → 静默丢弃 + 写 crash.log。
    注意：返回的是 dict，不是 (cmd, params) 元组。"""
```

- 解析顺序：**先剥 `{}` 动作标签（已有 `parse_ai_output`），再提取 `【】` DSL**。
- **正则位置无关**：指令写在 `{动作}` 之前也能解析（`tools/probe_dsl_order.py` 验证），
  但按规范应放在之后 —— 顺序是**风格要求**，不是功能约束。
- 下发用 `maid_link.send_command(cmd, params, cancel_previous=params.pop('cancel_previous', False))`。
- 下发结果（成功/失败/女仆未连接）作为 `[系统] 一行` 注入下一轮上下文（§6）。

### 4.4 Prompt 章节文案（当前实装，以 `prompt_api.txt` 为准）

```markdown
【女仆指令】（全域可用，不限游戏模式）
任何时候，只要消息里能感知到女仆存在（带【当前状态】/【态】/[态] 的实时状态，或 [系统] 任务回执），
就可在回复末尾附一条指令。日常/工作/游戏模式都适用。
- 主人提到女仆、背包、或让你"拿/打/挖/做"，或状态里看到她在忙，都算感知到女仆在场
- 一次最多一条，放在所有文字和{动作}之后；指令由系统执行，主人只看到你的自然语言回复
- 输出顺序必须是：文字 → {动作} → 【指令】。{动作}之前不要写指令
- 只能用下列指令名，不要自创（fly、walk 都不存在）；不要写【cmd(...)】，cmd 不是指令名
- 「指令名」三字只是占位符，**不要照抄**。对：【attack(range=10)】；错：【指令名(attack(range=10))】、【cmd(attack(...))】
- 不确定就不发，先问清楚；消息里完全没有状态信息时，一个字都不要写

可用指令：
- 战斗：attack(range=10) / attack(range=10, target=minecraft:pig) / guard(range=10) / stop
- 移动：move(pos=[x,y,z]) / look(pos) / pickup / collect(range=4)
- 采集：mine(pos, range=4, count=N) / farm(pos, range=4)
- 建造：build(pos, height=N) / place(pos) / break(pos) / use(pos)
- 物品：equip(item=x) / store / drop(count=N) / transfer(from=slot, to=slot, count=N)
- 箱子：chestopen(pos) / chestput(count=N) / chesttake(slot=N, count=N)
- 制作：craft(item=x, count=N) / smelt(item=x, count=N)
- 其他：feed / eat / sit / status / cancel

参数：
- target 用英文实体 id（猪=minecraft:pig ...）；不写 target 就打最近的敌对生物
- 坐标必须是 3 个整数 [x,y,z]；不确定时用 ~（主人脚下）

女仆指令范例（学语气，别照抄）：   ← 精简版特有
- 日常"今天好累"→"…{待机}【status】"
- 游戏"后面有僵尸"→"…{兴奋}【attack(range=10)】"

模式差异（同一人设，语气随模式切换）：   ← 精简版特有
- 模式1 日常：可撒娇玩梗；指令可选
- 模式2 工作：一句小知识（40 字内），不撒娇；{动作}仍必带；一般不主动发指令
- 模式3 游戏：主动指挥女仆；指令主战场
- 三模式共用同一套输出格式硬规则，模式只改「说什么」不改「怎么写」
```

---

## 5. 上下文与记忆（缺口 2）

### 5.0 两种后端的历史策略（用户强调的红线）

| | **网页版（SessionBackend）** | **官方 API / 千问（MessagesBackend）** |
|---|---|---|
| 记忆在哪 | 服务端（我们不可见、不可改） | 客户端 `ctx.history`（我们全权负责） |
| 发什么 | 只发当轮 prompt | 每次组装 messages |
| **历史提炼** | **不做**（服务端自动记忆，我们既不必要也无权提炼） | **必做**：滑动窗口 + 定期提炼，**不重发全量** |
| 提炼频次 | 无 | 每 **N 轮**（默认 30）或历史超窗时 |

> 一句话：**网页版把记忆交给服务端（零成本）；API 端必须自己做「滑动窗口 + 提炼」，否则每轮 token 线性增长、很快爆窗。**

### 5.1 数据模型（新增 `ai/context.py`）

```python
@dataclass
class Turn: user: str; assistant: str
@dataclass
class Fact: key: str; value: str; expire: str | None = None
@dataclass
class ChatContext:
    history: list[Turn]          # 纯净对话（仅 MessagesBackend 用；SessionBackend 不维护）
    memory: list[Fact]           # 提炼出的长期记忆（facts）
    summary: str = ""            # 提炼出的对话摘要（100 字内）
    condensed_at: int = 0        # 上次提炼的轮次号
    persona: str = ""
    topic_id: str = ""           # 会话 id（网页版）/ 本地历史标识（官方 API）
```

### 5.2 瞬时状态生成（新增 `game/maid_context.py`）

```python
def build_transient(snap, events=None) -> TransientContext:
    """从 maid_link 的合并快照生成完整 + 极短两种形态。"""
    return TransientContext(
        status_full=...,    # 完整 JSON 摘要（MessagesBackend 用，~250 token）
        status_compact=..., # 极短（SessionBackend 用，~20 token，如 "[态] 生命18 任务guard 目标zombie"）
        events=[...],       # 最近 1~3 条事件中文摘要
        replies=[...],      # 最近 1~3 条指令回执（[系统] 前缀）★
    )
```

### 5.3 MessagesBackend 的历史提炼（核心，官方 API 必需）

**为什么必须提炼**：官方 API 无状态，每轮都要把上下文发给服务端。若直接 `system + 全部 history`，对话越长 token 越大，几轮后爆窗、费钱。因此：

**滑动窗口 + 定期提炼，绝不整段重发**：

```
每轮 build()：
  messages = [system(人格)]
           + [{"role":"system","content": render_memory(ctx)}]      # facts + summary（恒定小）
           + [{"role":u,"content":t.user},{"role":a,"content":t.assistant}]
             for t in ctx.history[-WINDOW:]                          # 只取最近 WINDOW 轮原文
           + [{"role":"user","content": transient + user_msg}]      # 当轮（含瞬时状态，不进历史）

append_history() 后：
  if len(ctx.history) > CONDENSE_EVERY:
      ctx = _condense(ctx)     # 提炼：把「窗口外的旧历史 + 当前 memory」压成新 facts+summary
```

- `WINDOW`（默认 20 轮）：始终发最近 N 轮**原文**，保证近期对话细节完整。
- `CONDENSE_EVERY`（默认 30 轮）：每攒够 N 轮，把**滑出窗口外的旧对话**与已有记忆合并提炼成新的 `facts + summary`，旧历史丢弃。
- 提炼是**增量**的：不是把全部历史丢给提炼模型（那样提炼本身也爆窗），而是只提炼**本次新增滑出的部分**，与现有 `summary` 合并（`summary = refine(old_summary + 新滑出的对话)`）。
- 提炼用**独立会话**（照抄 `refine_wiki` 模式，`ai/memory.py`），thinking 关、search 关，只做压缩。
- 提炼 prompt（排除瞬时状态）：
  ```
  从以下对话中提炼需要长期记住的信息，输出 JSON：
  - facts: 用户偏好、约定、关系进展、重要事件（key-value，可带 expire）
  - summary: 100 字以内的关系与近期状态概述
  不要提炼：具体坐标、临时血量、瞬时敌人、日常寒暄。
  已有记忆：{memory}
  本次对话：{新增滑出的 N 轮}
  ```

**产出**：`ctx.memory`（facts）+ `ctx.summary` 始终是恒定小的，`ctx.history` 恒 ≤ `CONDENSE_EVERY` 条。每轮发给 AI 的上下文量有上界，不随对话长度线性增长。

### 5.4 共用主会话 + 瞬时状态策略（用户决策 3 的落地）

| 后端 | 瞬时状态去向 | 历史提炼 |
|---|---|---|
| **官方 API（默认推荐）** | 本轮组装即弃，`append_history(ephemeral=True)` | ✅ 滑动窗口 + 每 30 轮提炼 |
| 千问 | 同官方 API（复用 MessagesBackend） | ✅ 同左 |
| 网页版 | 极短行随当轮消息发出（服务端会保留） | ❌ 不做（服务端自动记忆） |

> 因用户选了「暂共用主会话」，DESIGN_LOOP 修正 1（独立 maid_chat 会话）**不实施**；隔离效果靠官方 API 的 ephemeral 轮次达成。

---

## 6. 回执注入（缺口 3）

`command_result` 回来后：
1. `maid_link` 记录最近任务（已有 `_replies`）。
2. 下次 AI 调用组装上下文时，把最近 1~3 条回执作为 `[系统]` 行注入当轮（不进历史）。

```
[系统] 女仆任务完成：attack（击杀 zombie×2）
[系统] 女仆任务失败：mine（目标位置无矿物）
```

**约束**：
- 不触发新一轮调用（不刷屏、不烧 token）。
- 用户直接在游戏里 `/maidtasks` 的命令结果也走同一条注入路径（模组已广播 command_result）。

---

## 7. 通道去重（缺口 4）

- **主次分明**：WS `event` 为主（实时性），文件 jsonl 为回放（仅 WS 断线时读）。
- **共享去重集**：`maid_handler`（WS）与 `game_handler`（文件）共用 `dedup_set`。
- **指纹**：`(event_type, target, tick//20)`，同指纹 60 秒内只处理一次。
- **例外**：`task_started` / `task_done` 不参与去重（每次都要播报）。

---

## 8. 全局软限流 AICallThrottle（缺口 5）

新增 `core/ai_throttle.py`（滑动窗口计数器）：

| 窗口 | 上限 | 超出动作 |
|---|---|---|
| 30 秒 | 5 次 | 合并成一次「批量播报」 |
| 5 分钟 | 35 次 | 降级为模板播报（不调 AI） |
| 1 小时 | 300 次 | 提示用户休息 |

- **只作用于"游戏/女仆闭环链路"**（AI 决策、AI 润色播报、游戏互动），**不波及主对话**（用户主动聊天永远优先）。
- 模板播报（不调 AI）只过 `BroadcastThrottle`（现有 8s 冷却），不受 AI 限流误杀。

---

## 9. 播报分级（缺口 6）

| 级别 | 事件 | 处理 | 节流 |
|---|---|---|---|
| 模板播报 | 任务开始/完成、普通击杀、`in_progress` 建筑 | 不调 AI，直接气泡 | BroadcastThrottle |
| AI 润色 | 建筑完成、里程碑、发现新生物 | 调 AI 润色（`ephemeral=True`） | Broadcast + AICallThrottle |
| AI 决策 | 低血 <30% / 被围（3+ 敌对 10 格内） | 调 AI 决策 + 可发 DSL（允许抢占） | Broadcast + AICallThrottle |

**AI 决策边界**（决策 3 = A，仅两种）：
- `low_health`：`snapshot.self.health < 30%`
- `surrounded`：`nearby` 中敌对生物 ≥3 且距离 ≤10 格

`maid_handler` 每秒 tick 检查这两个条件（**命中后才调 AI**，30s 冷却），AI 决策回复可夹带 `【guard/attack/...】`。

---

## 10. 任务态 vs 对话态：默认排队（缺口 7）

- `MaidLink.send_command(..., cancel_previous=...)` **显式传参**，调用方默认 `False`（排队）。
- DSL 解析出 `cancel_previous=true` 时才透传抢占。
- 用户直接语音/聊天命令（NLU 链路）保持现状默认 `True`？——**统一改默认 `False`**，与「AI 不抢任务」对齐；仅 AI 主动决策（低血/被围）允许抢占。

> 改动点：`maid_handler.send_command` / `maid_link.request` / `nlu_bridge.send_command` 默认值 True → False（与 `query_status` 已用 False 一致）。

---

## 11. 文件清单（改动/新增）

| 序 | 文件 | 类型 | 内容 |
|---|---|---|---|
| 1 | `ai/deepseek_api.py` | 新增 | 官方 API 客户端（OpenAI 兼容，thinking 可选） |
| 2 | `ai/backends/messages.py` | 新增 | MessagesBackend（ephemeral 语义 + **滑动窗口/提炼触发**） |
| 3 | `ai/backends/session.py` | 新增 | SessionBackend（网页版包装：只发当轮、**不做提炼**） |
| 4 | `ai/context.py` | 新增 | ChatContext / Turn / Fact / TransientContext（WINDOW / CONDENSE_EVERY 常量） |
| 5 | `ai/memory.py` | 新增 | 提炼会话（独立 sid/pid，复用 refine_wiki 模式）+ `logs/memory.json` |
| 6 | `ai/chat_service.py` | 改 | 后端路由三选一 + 瞬时状态/回执注入 + ephemeral 轮次 |
| 7 | `ai/client.py` | 改 | `backend_name` 支持 `deepseek-api`；`make_client` 路由 |
| 8 | `game/maid_context.py` | 新增 | 快照 → TransientContext（完整 + 极短） |
| 9 | `game/maid_intent.py` | 新增 | DSL 解析器（白名单单一来源 mod_contract） |
| 10 | `core/ai_throttle.py` | 新增 | AICallThrottle 滑动窗口 |
| 11 | `pet/handlers/maid_handler.py` | 改 | 去重集 + 播报分级 + AI 决策检查 + 默认排队 |
| 12 | `pet/handlers/game_handler.py` | 改 | 与 maid_handler 共享去重集 |
| 13 | `prompt.txt` / `prompt2.txt` | 改 | 加「女仆指令」章节 |
| 14 | `config.py` | 改 | 后端/窗口/提炼轮数/限流/决策阈值/开关常量 |
| 15 | `tests/test_maid_loop.py` | 新增 | DSL 解析、ephemeral、提炼触发、去重、throttle、回执注入 |
| 16 | `README.md` / `CHANGELOG.md` | 改 | 版本 2.19.0 说明 |

**2.20.0 追加（人设按后端分流）**

| # | 文件 | 动作 | 说明 |
|---|---|---|---|
| 17 | `prompt_api.txt` / `prompt2_api.txt` | 新增 | API 精简人设（女仆 / 御坂），含全域女仆指令 + 跨模式范例 + 模式差异段 |
| 18 | `config.py` | 改 | `PERSONAS[i]["file_api"]`；`is_messages_backend` / `persona_prompt_file` / `mode_prompt` / `get_api_prompt`；`load_system_prompt` 增 `backend`（含 `True`=强制精简） |
| 19 | `ai/backends/messages.py` | 改 | `build(..., mode_id=)` 把 `【当前模式】+ 规则` 追加进 system |
| 20 | `ai/chat.py` | 改 | `_prepare_prompt` 模式声明仅网页版；新增 `_system_prompt()` 按后端分流 |
| 21 | `ai/chat_service.py` | 改 | 人格切换补丁仅网页版；`_chat_messages(..., mode_id=)` |
| 22 | `game/processor.py` / `pet/handlers/work_handler.py` | 改 | API 后端不注入模式声明 |
| 23 | `tools/test_api_modes.py` / `test_api_game.py` / `probe_dsl_order.py` / `probe_prompt_split.py` | 新增 | 真实 API 多模式实测 + 三态校验脚本 |
| 24 | `tests/test_chat_unit.py` | 改 | `[U1b]`/`[U1c]`/`[U1d]`；`[U1]`/`[U6]` 补 `backend` |
| 25 | `API_PROMPT_TEST_REPORT.md` | 新增 | 两轮实测报告（缺陷与修法、前后对比、token 账） |

> 说明：DESIGN_LOOP v0.2 原文件清单中的 `ai/backends/session.py` 也涉及拆网页版/官方 API 两路径；本稿按「网页版不动、官方 API 新增」落地，风险最小。**提炼是 MessagesBackend 专属**（网页版把记忆交给服务端，不提炼）。

---

## 12. 分期实施计划（每阶段可独立验证）

**阶段一：官方 API 通道（不动现有行为）**
1. `ai/deepseek_api.py` + `ai/backends/messages.py` + `ai/context.py`
2. `ai/chat_service.py` 后端路由（deepseek-api 分支），`ephemeral=True` 先不接
3. 验证：`--smoke` + 手工切官方 API 聊一句

**阶段二：女仆指令闭环**
4. `game/maid_intent.py` + prompt 章节
5. `chat_service` 对 AI 回复做 DSL 解析 → 下发 → 回执注入
6. 验证：聊天让 AI 说「去打僵尸」→ 女仆执行 → 下一轮 AI 知道结果

**阶段三：状态注入 + 记忆 + 提炼**
7. `game/maid_context.py` + 瞬时状态注入（ephemeral）
8. `ai/memory.py` + MessagesBackend 的「滑动窗口 + 每 N 轮提炼」（§5.3）
9. 验证：游戏事件不再污染聊天历史（切换官方 API 后，重启聊天历史干净）；**长对话下每轮发给 AI 的上下文量有上界（不发全量历史）**

**阶段四：限流 / 去重 / 播报分级**
10. `core/ai_throttle.py` + 通道去重 + 播报分级 + AI 决策（低血/被围）
11. `cancel_previous` 默认排队
12. 验证：同一事件不重复播报；30s 内 AI 调用 ≤5

---

## 13. 验收标准

| 项 | 标准 |
|---|---|
| 闭环 | 用户说「去打僵尸」→ AI 回复夹带 `【attack】` → 女仆执行 → 回执进下一轮上下文 |
| 官方 API | 切到 `deepseek-api` 后聊天正常；瞬时状态不进历史 |
| 隔离 | 游戏事件/决策轮次用 `ephemeral=True`，不污染用户对话历史 |
| **历史提炼** | **API 端长对话（>30 轮）后，每轮上下文量有上界、不随轮数线性增长；早期对话被提炼成 facts+summary 且语义不丢** |
| **网页版不提炼** | 切回 `deepseek` 网页版后行为与现状完全一致（服务端自动记忆，客户端不做任何提炼） |
| 去重 | 同一事件（WS + 文件）不重复播报 |
| 限流 | 30s 内 AI 调用 ≤5；超出合并批量播报 |
| 排队 | AI 指令默认不抢占女仆任务，除非 `cancel_previous=true` |
| 回归 | `tests/test_reconstruction.py` / `test_nlu.py` 全过；`main.py --smoke` exit=0 |

---

## 14. 风险与对策

| # | 风险 | 对策 |
|---|---|---|
| 1 | 官方 API 有独立计费 | 设置里只填 Key 才启用；默认仍走网页版；费用可控（个人聊天量级） |
| 2 | 共用主会话下 ephemeral 误用 | 只在「游戏/决策/播报」链路置 ephemeral=True；聊天/语音用户主动轮次恒 False |
| 3 | 网页版仍会残留极短状态行 | 该后端为兜底；主推官方 API 达成完全隔离 |
| 4 | DSL 解析失败/乱发 | 白名单 + 静默丢弃 + crash.log；prompt 明确「不确定不发」 |
| 5 | AI 决策刷屏 | 仅低血/被围两个条件 + 30s 冷却 + AICallThrottle |
| 6 | 记忆提炼误提炼瞬时状态 | 提炼 prompt 显式排除坐标/血量/敌人 |
| 7 | **提炼丢语义**（API 端压缩历史） | 滑动窗口保留最近 20 轮原文 + 增量提炼（只压滑出的、与已有 summary 合并）；提炼用独立会话、关 thinking 省钱 |
| 8 | **提炼线程阻塞主对话** | 提炼在后台线程 + 全局串行锁（ChatService 已有）；提炼失败静默回退为「窗口内原文直接发送」，不丢对话 |

---

## 15. 回滚

- 开关：设置里 `ai_backend` 切回 `deepseek`（网页版）即完全回到现状行为。
- 代码：新增文件均独立（`ai/backends/`、`ai/context.py`、`ai/memory.py`、`game/maid_*.py`、`core/ai_throttle.py`），删除后仅需回退 `chat_service.py / maid_handler.py / game_handler.py / prompt` 四处接线；`prompt` 的「女仆指令」章节删除不影响旧格式。
