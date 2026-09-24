# 用户 · 游戏 · 桌宠 通讯闭环 深化设计稿

> 状态：**待审阅**（v0.2）
> ⚠️ **已被 `DESIGN_LOOP_DEEPENED.md`（v0.3，已实施）取代** —— 本文仅作思想稿/历史留存，
> 结论以 v0.3 为准（v0.3 另含 2.20.0 的「人设按后端分流」增补，见其 §3.4）。
> 目标：把"用户 → 桌宠 AI → 游戏 → 桌宠 AI → 用户"的决策闭环打通，同时控制 token 与峰值频率。
> 前置：物理通道（WS + 文件）已通，本文只解决**逻辑层**问题。
> v0.2 相对 v0.1 的变化：三个决策点已定案 + 三处修正（会话隔离 / cancel_previous / 双节流器）。

---

## 零、总览

### 0.1 闭环全貌

```
用户 ──说/打字──► 桌宠 AI ──指令 DSL──► 游戏女仆
  ▲                  │                    │
  │                  │◄── perception ─────┤
  │                  │◄── event ──────────┤
  │                  │◄── command_result ─┤
  │                  ▼                    │
  └──气泡/语音──── 桌宠 ──反馈入上下文────┘
```

### 0.2 七个缺口与方案索引

| # | 缺口 | 方案 | 章节 |
|---|---|---|---|
| 1 | AI 不知如何/如何发指令 | 函数式 DSL + prompt 章节 + 解析器 | §四 |
| 2 | perception 是否注入 AI | 三层上下文 + 时效数据不进历史 | §三 |
| 3 | command_result 是否回喂 | 作为系统消息注入，不触发新调用 | §三.5 |
| 4 | 文件与 WS 通道去重 | 主 WS、文件为回放；事件指纹去重 | §五.3 |
| 5 | 用户直接游戏命令 vs AI 上下文 | WS command_result 统一同步 | §三.5 |
| 6 | 主动播报 vs AI 对话边界 | 分级：模板播报 / AI 润色 / AI 决策 | §五.4 |
| 7 | 任务态 vs 对话态互斥 | 单任务 + 冲突策略 + cancel_previous | §四.4 |

### 0.3 决策点定案（v0.2）

| 决策点 | 选项 | 定案 | 说明 |
|---|---|---|---|
| 决策 1：DSL 外层符号 | A `【】` / B `<<>>` / C JSON | **A** | 与 `{}` 动作标签风格一致，中文括号视觉突出；已核实与现有 `_ACTION_TAG_RE`（只匹配 `[`/`{`）不冲突 |
| 决策 2：DeepSeek 官方 API | A 接入 / B 不接入 | **A** | 实现真正的"状态不进历史"，token 再降 30%；新增 `ai/backends/messages.py`（MessagesBackend） |
| 决策 3：AI 主动决策边界 | A 低血+被围 / B +任务建议 / C 不主动 | **A**（默认，可后续改 B） | 仅 `low_health(<30%)` 与 `surrounded(3+ 敌对 10 格内)`；任务完成**只播报不决策** |

---

## 一、设计原则

1. **瞬时数据不进历史**——状态/事件寿命仅一轮，物理上不累积。
2. **记忆分层**——长期记忆结构化沉淀，近期对话滑动窗口，瞬时状态当轮注入。
3. **双后端能力差异吃在适配器里**——业务层只面对统一抽象。
4. **指令走文本 DSL**——两种后端都支持，不依赖 function calling。
5. **关闭思考模式**——省 20~25% token，快 2~5 秒，格式更稳。
6. **限流优先于扩容**——峰值撞限流比平均超支更伤体验。

---

## 二、v0.2 三处修正（相对 v0.1）

### 修正 1（关键）：女仆闭环使用独立 AI 会话，不复用共享主对话

现状 `ChatService` 是工作/游戏/新闻**共用一个人格+历史**的全局会话（`ai/chat_service.py:19`）。若把 `command_result` 注入、瞬时状态塞进它，会污染正常聊天/工作历史，也会让"记忆提炼"误提炼游戏状态。

**方案**：照抄 `refine_wiki` 的独立会话模式（`ai/client.py:142-199`，独立 sid/pid 持久化），为女仆闭环建**第二个独立会话**：

```
主对话    ChatService.chat()      ← 正常聊天/工作/新闻（现状不动）
女仆会话  ChatService.maid_chat() ← 游戏联动专用（新增）
           ├ 独立 sid/pid 持久化（ai_maid_session_id / ai_maid_parent_id）
           ├ 独立人格注入（切人格时才带完整 prompt）
           └ 三层上下文只在这里组装
```

**入口切换**：桌宠已有"AI 模式"（正常/工作/游戏，`ai/client.py:default_mode()`）。当 `模式=游戏`（或游戏联动激活）时，聊天输入走 `maid_chat`；否则走 `chat`。

### 修正 2：`cancel_previous` 默认改"排队"

- `MaidLink.send_command(..., cancel_previous=...)` 显式传参，**不再默认 true**
- `maid_handler.send_command()` 对外默认 `cancel_previous=False`（排队语义，与 §四.4 对齐）
- DSL 解析出 `cancel_previous=true` 时才透传抢占

### 修正 3：两个节流器分开

| 节流器 | 控制 | 现状 |
|---|---|---|
| `BroadcastThrottle`（播报节流） | 同一事件指纹不重复播报 | 即现有 `MAID_EVENT_COOLDOWN=8`（`maid_handler.py:177`）+ 去重集 |
| `AICallThrottle`（AI 调用节流） | 30s≤5 / 5min≤35 / 1h≤300 | **新增**，只作用于"AI 润色/AI 决策"两次调用 |

模板播报（不调 AI）只过 `BroadcastThrottle`，不会被 8s 冷却误杀。

---

## 三、上下文与记忆架构（缺口 2 / 3 / 5）

### 3.1 三层数据模型

```
┌──────────────────────────────────────────────┐
│  MemoryStore（长期记忆 · 永久）               │
│  facts + summary，结构化，按需注入             │
├──────────────────────────────────────────────┤
│  ChatContext（近期对话 · 滑动窗口 10~15 轮）  │
│  纯净原文，超窗口压缩迁移至 MemoryStore        │
├──────────────────────────────────────────────┤
│  TransientContext（瞬时状态 · 1 轮）          │
│  每轮重算，不进历史（或极短形态进）            │
└──────────────────────────────────────────────┘
```

**铁律**：`TransientContext` 永不写入 `ChatContext.history`。

### 3.2 数据结构

```python
@dataclass
class Turn:
    user: str
    assistant: str

@dataclass
class Fact:
    key: str           # "user_preference" / "promise" / "relationship"
    value: str
    expire: str | None = None   # ISO 日期，None = 永久

@dataclass
class MemoryStore:
    facts: list[Fact]
    summary: str
    updated: str

@dataclass
class ChatContext:
    system_prompt: str
    history: list[Turn]      # 纯净，无状态无事件
    persona: str
    topic_id: str

@dataclass
class TransientContext:
    status_full: str         # 完整形态（MessagesBackend 用，~250 token）
    status_compact: str      # 极短形态（SessionBackend 用，~20 token）
    events: list[str]        # 最近 1~3 条事件摘要
```

### 3.3 记忆提炼（复用 Wiki 提炼模式）

**触发**：
- 每 30 轮后台提炼一次
- 用户主动说"记住这件事"
- 用户点"🔄 新会话"（先提炼再开新会话，不丢记忆）
- 压缩迁移时

**提炼 prompt**（独立会话，关思考）：
```
从以下对话中提炼需要长期记住的信息，输出 JSON：
- facts: 用户偏好、约定、关系进展、重要事件（key-value，可带 expire）
- summary: 100 字以内的关系与近期状态概述
不要提炼：具体坐标、临时血量、瞬时敌人、日常寒暄。
对话：{最近 N 轮}
```

**实现**：复用 `ai/client.py:refine_wiki` 的独立提炼会话模式（`_REFINE_SESSION`），新增 `_MAID_MEMORY_SESSION` 独立会话 + `logs/memory.json` 结构化落盘（facts + summary，可读可改）。

### 3.4 上下文组装（ContextBuilder）

```python
class ContextBuilder:
    def build(self, ctx, memory, transient, user_msg) -> dict:
        raise NotImplementedError

class MessagesBackend(ContextBuilder):
    """DeepSeek 官方 API / 千问：客户端传 messages"""
    def build(self, ctx, memory, transient, user_msg):
        messages = [{"role": "system", "content": ctx.system_prompt}]

        # 长期记忆（每轮注入，但很小）
        if memory.facts or memory.summary:
            messages.append({"role": "system",
                             "content": self._render_memory(memory)})

        # 近期对话（纯净）
        for turn in ctx.history[-WINDOW:]:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})

        # 本轮：完整状态 + 事件 + 用户消息（不进历史）
        current = self._compose(transient.status_full,
                                transient.events, user_msg)
        messages.append({"role": "user", "content": current})
        return {"messages": messages, "thinking": False}

    def append_history(self, ctx, user_msg, assistant_msg):
        ctx.history.append(Turn(user=user_msg, assistant=assistant_msg))

class SessionBackend(ContextBuilder):
    """DeepSeek 网页版：服务端维护会话"""
    def build(self, ctx, memory, transient, user_msg):
        if self._should_compress(ctx, memory):
            self._compress_and_migrate(ctx, memory)

        # 状态必须极短（进历史），事件更短
        content = f"{transient.status_compact} {user_msg}".strip()
        return {"chat_session_id": self.session_id, "message": content,
                "thinking": False}

    def _compress_and_migrate(self, ctx, memory):
        # 1. 提炼 memory（复用 §3.3）
        # 2. 开新会话，注入 memory 摘要 + 窗口内原文
        # 3. 更新 session_id
```

### 3.5 command_result 回喂（缺口 3 + 5）

`command_result` 回来后：

1. **更新内部状态**（`maid_link` 记录最近任务）
2. **注入女仆会话作为系统消息**（下一次对话的上下文）：

```
[系统] 女仆任务完成：attack（击杀 zombie×2，用时 8s）
[系统] 女仆任务失败：mine（目标位置无矿物）
```

**约束**：
- **不触发新一轮 AI 调用**（避免刷屏 + 烧 token）
- 只作为下一轮上下文
- 用户直接在游戏里 `/maidtasks` 的命令也走同一路径 → 缺口 5 解决

---

## 四、指令协议（缺口 1 / 7）

### 4.1 DSL 语法（决策 1 = A）

```
【attack(range=10)】
【mine(pos=[100,-60,-50], range=4, count=5)】
【transfer(from=mainhand, to=0, count=1)】
【guard(range=12, cancel_previous=true)】
【stop】
```

| 规则 | 说明 |
|---|---|
| 外层 `【】` | 与 `{}` 动作标签区分（已核实 `_ACTION_TAG_RE` 只匹配 `[`/`{`，不冲突） |
| 指令名 | 24 个 `/maidtasks` 指令之一 |
| 参数 | `name=value`，逗号分隔 |
| 坐标 | `[x,y,z]` 绝对 / `~` 主人脚下 |
| 物品 | `item=iron_pickaxe`（可省 `minecraft:`） |
| 槽位 | `slot=mainhand` / `slot=0` |
| 数量 | 一次最多一条，放回复最末尾 |
| `cancel_previous` | 可选；true = 抢占女仆当前任务 |

### 4.2 Prompt 章节（插入现有 prompt"输出规范"之后，`prompt.txt` / `prompt2.txt`）

```markdown
## 女仆指令

你回复的最末尾可以附一条指令，格式：【cmd(param=value, ...)】
- 一次最多一条，放在所有文字和 {动作} 标签之后
- 指令会被系统执行，玩家只看到你的自然语言回复
- 不确定时不要发指令，先问清楚

可用指令：
- 战斗：attack(range=10) / guard(range=10) / stop
- 移动：move(pos=[x,y,z]) / look(pos) / pickup / collect(range=4)
- 采集：mine(pos, range=4, count=N) / farm(pos, range=4)
- 建造：build(pos, height=N) / place(pos) / break(pos) / use(pos)
- 物品：equip(item=x) / store / drop(count=N) / transfer(from=slot, to=slot, count=N)
- 箱子：chestopen(pos) / chestput(count=N) / chesttake(slot=N, count=N)
- 制作：craft(item=x, count=N) / smelt(item=x, count=N)
- 其他：feed / eat / sit / status

坐标：用 [x,y,z] 绝对坐标；不确定时用 ~（主人脚下）。
你的消息里可能带【当前状态】或 [态] 开头的信息，那是女仆的实时状态。
```

**行为约束**（写进 prompt）：
- 玩家没明示"去做某事"时**不要**主动发指令（例外：低血/被围，见 §五.4 AI 决策）
- 需要坐标但不知道时，**问玩家**或退化为 `move(pos=~)`
- 冲突指令让玩家决定，不自行切换

### 4.3 解析器（新增 `game/maid_intent.py`）

```python
INTENT_RE = re.compile(r'【(\w+)\((.*?)\)】')

WHITELIST = {"attack","guard","stop","move","look","pickup","collect",
             "mine","farm","build","place","break","use","equip","store",
             "drop","transfer","chestopen","chestput","chesttake",
             "craft","smelt","feed","eat","sit","status"}

def parse(text):
    m = INTENT_RE.search(text)
    if not m: return None, text
    cmd, args_str = m.group(1), m.group(2)
    if cmd not in WHITELIST:
        log("IntentParseError", f"unknown cmd: {cmd}")
        return None, text
    params = parse_params(args_str)
    if params is None:
        log("IntentParseError", f"bad params: {args_str}")
        return None, text
    clean = INTENT_RE.sub('', text).strip()
    return {"cmd": cmd, "params": params}, clean
```

**失败处理**：静默丢弃 + 写 `crash.log`，回复照常显示。

### 4.4 冲突与互斥（缺口 7，含修正 2）

| 情况 | 策略 |
|---|---|
| 女仆正在执行任务 A，AI 发来 B | 默认**排队不抢占**，回执提示"女仆正忙"（`cancel_previous` 默认 false） |
| 用户明确说"停下" / "先做 B" | AI 在 DSL 里带 `cancel_previous=true` |
| 用户直接游戏 `/maidtasks cancel` | `command_result` 同步到 AI 上下文（§3.5） |
| AI 主动决策（低血/被围） | 允许抢占（`cancel_previous=true`） |

---

## 五、频率与限流（缺口 4 / 6）

### 5.1 通道频率分层

| 通道 | 内容 | 频率 | 触发 |
|---|---|---|---|
| WS `event` | hurt/enemy/task/danger | **即时** | 事件发生 |
| WS `perception` | 增量 diff | **2s**（原 750ms） | 有变化才发 |
| WS `perception` full | 全量基线 | **30s** | 或用户发消息时 |
| 文件 jsonl | 20s 窗口 | **保留 20s** | WS 断线回放 |
| AI 上下文注入 | 状态 + 记忆 | **每轮** | 用户/事件触发 |
| 主动播报 | AI 润色 | **8s 冷却** | 事件驱动 |
| AI 主动决策 | — | **30s 冷却** | 低血/被围 |

### 5.2 全局软限流（AICallThrottle，新增）

```
30 秒内最多 5 次 AI 调用。
超出 → 合并成一次"批量播报"（如"女仆击杀 3 只僵尸，获得铁锭×2"）。
```

实现：`ai/chat_service.py` 加滑动窗口计数器 + 合并队列，只作用于 `maid_chat` 链路（不波及主对话）。

### 5.3 通道去重（缺口 4）

**主次分明**：
- **WS `event` 为主**——实时性来源
- **文件 jsonl 为回放**——仅 WS 断线时读

**去重指纹**：`(event_type, target, tick//20)`。同指纹 60 秒内只处理一次。

`maid_handler`（WS）与 `game_handler`（文件）共享一个 `dedup_set`。

**例外**：`task_started` / `task_done` 不参与去重（每次都要播报）。

### 5.4 播报分级（缺口 6，含修正 3）

| 级别 | 事件 | 处理 | 节流器 |
|---|---|---|---|
| **模板播报** | 任务开始/完成、普通击杀 | 不调 AI，直接气泡 | 仅 BroadcastThrottle |
| **AI 润色** | 建筑完成、里程碑、发现新生物 | 调 AI 润色 | Broadcast + AICallThrottle |
| **AI 决策** | 低血（<30%）、被围（3+ 敌人） | 调 AI 决策 + 可主动发指令 | Broadcast + AICallThrottle |

**AI 决策边界**（决策 3 = A，初期只允许两种）：
- `low_health`：女仆血量 <30%
- `surrounded`：3+ 敌对在 10 格内

---

## 六、Token 预算（合并所有优化）

### 6.1 每轮增量构成

| 项 | 进历史 | Token |
|---|---|---|
| 人格 system | ✅ 首轮 | 1200（一次） |
| 长期记忆 | ✅ 每轮 | 100 |
| 对话历史 | ✅ 窗口 10 轮 | ~1500 |
| 状态行（MessagesBackend 完整） | ❌ | 250 |
| 状态行（SessionBackend 极短） | ✅ 但很短 | 20 |
| 事件摘要 | ❌ | 30 |
| 用户消息 | ✅ | 50 |
| AI 回复 | ✅ | 100 |

### 6.2 1 小时总账（关思考 + 分层 + 限流）

| 强度 | 调用 | 输入 | 输出 | 合计 |
|---|---|---|---|---|
| 轻度 | 55 | 35K | 5.5K | **40K** |
| 中度 | 125 | 80K | 12.5K | **92K** |
| 重度 | 260 | 165K | 26K | **190K** |

对比原始估算（110K / 250K / 520K）：**降 63%**。

### 6.3 峰值保护

| 窗口 | 上限 | 超出动作 |
|---|---|---|
| 30 秒 | 5 次 | 合并批量播报 |
| 5 分钟 | 35 次 | 降级为模板播报 |
| 1 小时 | 300 次 | 提示用户休息 |

---

## 七、改动清单与排期

### 7.1 文件清单

| 序 | 文件 | 类型 | 内容 |
|---|---|---|---|
| 1 | `ai/context.py` | 新增 | ChatContext / TransientContext / MemoryStore / Turn / Fact / WINDOW |
| 2 | `ai/memory.py` | 新增 | 记忆提炼（复用 refine_wiki 会话模式）+ `logs/memory.json` 持久化 |
| 3 | `ai/backends/messages.py` | 新增 | MessagesBackend（DeepSeek 官方 API / 千问） |
| 4 | `ai/backends/session.py` | 新增 | SessionBackend（DeepSeek 网页版）+ 压缩迁移 |
| 5 | `ai/chat_service.py` | 改 | 新增 `maid_chat()`（女仆独立会话）+ AICallThrottle 软限流；`chat()` 不动 |
| 6 | `ai/client.py` | 改 | 女仆会话 sid/pid 持久化（`ai_maid_session_id/ai_maid_parent_id`）+ 后端路由 |
| 7 | `ai/deepseek.py` | 改 | 拆网页版 / 官方 API 两路径 |
| 8 | `game/maid_context.py` | 新增 | 生成 TransientContext（完整 + 极短） |
| 9 | `game/maid_intent.py` | 新增 | DSL 解析器（含 cancel_previous） |
| 10 | `game/maid_link.py` | 改 | `send_command` 显式 `cancel_previous` 参数；记录最近任务状态 |
| 11 | `pet/handlers/maid_handler.py` | 改 | 新上下文 + 解析指令 + 回执注入 + 播报分级 + 默认排队 |
| 12 | `pet/handlers/game_handler.py` | 改 | 与 maid_handler 共享去重集 |
| 13 | `prompt.txt` / `prompt2.txt` | 改 | 加"女仆指令"章节 |
| 14 | `config.py` | 改 | 窗口大小、限流参数、开关 |
| 15 | `SmartMaid/.../BridgeConfig.java` | 改 | perception 间隔 750ms → 2s |
| 16 | `tests/test_maid_loop.py` | 新增 | DSL 解析、上下文组装、去重 |

### 7.2 排期（四阶段，每阶段可独立验证）

**阶段一：上下文地基**（不改变现有行为）
1. `ai/context.py` + `MemoryStore`
2. `ai/memory.py` 提炼（复用 refine_wiki 模式）
3. `maid_chat()` 独立会话骨架（先不加状态注入，等价现有行为）
4. 跑 `--smoke` 验证主对话不受影响

**阶段二：双后端 + 记忆**
5. `backends/messages.py` + DeepSeek 官方 API 验证
6. `backends/session.py` 包装网页版
7. 后端路由切换（deepseek 网页 / deepseek API / qwen）
8. 对比两后端效果

**阶段三：闭环打通**
9. `maid_context.py` 生成 Transient
10. `maid_intent.py` + prompt 改
11. `maid_handler` 集成：指令下发（默认排队）+ 回执注入
12. 真机 e2e 验证

**阶段四：频率与去重**
13. perception 间隔调整（SmartMaid）
14. AICallThrottle + 事件合并
15. 通道去重（共享 dedup_set）
16. 播报分级（模板 / AI 润色 / AI 决策）

---

## 八、验收标准

| 项 | 标准 |
|---|---|
| 闭环 | 用户说"去打僵尸" → AI 发 `【attack】` → 女仆执行 → 回执进上下文 → 下一轮 AI 知道结果 |
| Token | 中度场景 1 小时 ≤100K |
| 峰值 | 30s 内 AI 调用 ≤5 次 |
| 记忆 | 用户跨会话仍被"记得"（facts 注入） |
| 时效 | 状态/事件不进历史（MessagesBackend）或极短（SessionBackend） |
| 去重 | 同一事件不重复播报 |
| 会话隔离 | 游戏模式聊天不污染主对话历史（修正 1） |
| 默认排队 | AI 指令不抢占女仆当前任务，除非 `cancel_previous=true`（修正 2） |

---

## 九、风险与待确认

| # | 风险/待确认 | 说明 | 对策 |
|---|---|---|---|
| 1 | **DeepSeek 官方 API 凭据** | 需要 API key，成本独立于网页版会话 | 待确认用户是否有可用 key；决策 2 已定案接入 |
| 2 | **网页版多会话并发** | 同一账号并行请求多会话（主对话 + 提炼 + 女仆会话）可能被网页版限流 | refine_wiki 已证明单进程内可行；女仆会话必要时排队 |
| 3 | **决策 3 边界** | 定案 A（仅低血+被围） | 后续扩 B（任务完成建议）只需改一处白名单 |
| 4 | 女仆会话人格切换 | 与主对话共享 persona 设置，切换时需重新注入完整 prompt | 复用 `reset_persona` 模式，按独立会话各自维护 `_last_persona` |
| 5 | 记忆提炼误提炼瞬时状态 | 提炼 prompt 已显式排除坐标/血量/敌人 | §3.3 prompt 约束 + 抽检 |
| 6 | `MaidTaskManager` 排队语义 | 模组侧目前同一时刻只执行一个任务，`cancel_previous=false` 时新指令会被拒（回执"女仆正忙"） | 依赖回执注入让 AI 感知"正忙"，不自行重试 |
