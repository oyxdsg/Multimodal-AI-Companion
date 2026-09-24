# AI 接口通用化（接入任意 AI）· 设计方案

> 状态：**方案稿，未落代码**（本文只做设计，不含改动）
> 目标版本：2.24.0（次版本号 = 新增功能模块）
> 相关文档：`CHANGELOG.md`、`README.md §AI 聊天功能`、`DESIGN_LOOP_DEEPENED.md §3 §3.4`

## 一、要解决的问题

诉求：**AI 接口能接入任意 AI 的 API**（OpenAI / Anthropic / Gemini / 各类聚合平台 / 本地 Ollama、vLLM…），
而不是现在写死的三选一。

### 1.1 现状盘点（实测，含行号）

**已经对的部分**：抽象轴是对的，只有两条——

| 后端 | 上下文策略 | 谁维护历史 |
|---|---|---|
| `deepseek`（网页版） | `SessionBackend`（`ai/backends/session.py`） | **服务端**，客户端只发当轮 |
| `deepseek-api` / `qwen` | `MessagesBackend`（`ai/backends/messages.py`） | **客户端**，滑窗 + 定期提炼 |

**烂的部分**：把「上下文策略」和「传输协议/厂商」两件正交的事，压成了一个字符串三元组。

### 1.2 耦合清单（改造范围 = 这张表）

| # | 耦合点 | 位置 | 后果 |
|---|---|---|---|
| 1 | 后端身份是硬编码元组，**两份** | `ai/client.py:51` `BACKENDS`；`config.py:74` `MESSAGES_BACKENDS` | 加一个后端要改两处，且两者可能不同步 |
| 2 | 凭证是**扁平固定键** | `ai/client.py:65-97`（6 个 getter/setter） | 加 N 个厂商 = N 组键 + N 组存取函数 |
| 3 | `make_client()` / `chat_once()` 是**三段硬编码 if 链** | `ai/client.py:100-143` | 每加一家就要往两个函数里塞分支 |
| 4 | **流式只有千问有**，且泄漏到 UI 层 | `ai/chat.py:516-531` 直接读 `self.opts.get("qwen_model")` | 新后端想流式必须改 `chat.py` |
| 5 | 请求/响应**写死 OpenAI 格式** | `ai/deepseek_api.py:56`、`ai/qwen.py:135` 都取 `choices[0].message.content`；`Authorization: Bearer` | Anthropic / Gemini 原生协议直接不通 |
| 6 | **`_refine_backend()` 写死 `deepseek-api`** | `ai/memory.py:103-115` | ⚠️ **现存 bug**：选千问时，记忆提炼静默回退到 DeepSeek **网页版**会话 → 没登录网页版就永远提炼不了 |
| 7 | **`refine_wiki()` 写死 DeepSeek 网页客户端** | `ai/client.py:211-243` | ⚠️ 同上：Wiki 提炼隐式依赖网页版登录 |
| 8 | `is_messages_backend` 分支 5 处 | `chat_service.py:71`、`ai/chat.py:371/516/1024`、`game/processor.py:293`、`work_handler.py:57` | 这是**行为分支**，判据是"能力"（system 是否每轮重发），却用"厂商名"实现 |
| 9 | 会话状态持久化只服务网页版 | `ai_session_id` / `ai_parent_id`（+ `ai_refine_*` / `ai_condense_*`） | 概念绑定在网页版上，没抽象成"有会话的后端" |
| 10 | 设置界面写死标签与模型常量 | `pet/settings_dialog.py:40-45`（6 个别名）、`:874-881`（"千问 API Key/模型"）、`:1119-1121`、`CHAT_MODELS`/`DS_API_MODELS` | 每加一家 = 一排新控件 |
| 11 | 登录菜单按后端名判分支 | `pet/window.py:411-417` | 同上 |
| 12 | 语音走千问 Key | `voice/tts.py:131/309/406` `load_qwen_key()` | 「对话后端」与「语音后端」被隐式绑定 |
| 13 | 测试与工具写死 `DeepSeekApiClient` | `tests/test_chat_unit.py:169-213`、`tests/test_maid_loop.py:182`、`tools/test_api_modes.py:38`、`tools/test_api_game.py:33`、`tools/e2e_maid_check.py:69/429/455` | 换协议要改 5 个文件 |
| 14 | 识图只有网页版有 | `chat.py` 的 `ref_file_ids` 路径 | 能力门控，需显式化 |

### 1.3 核心判断

> **「任意 AI」的 80% 靠一个通用 OpenAI 兼容适配器 + 可编辑 `base_url` 就能拿到。**
> 因为 OpenAI 的 `/chat/completions` 事实上已成行业方言：DeepSeek、千问（compatible-mode）、Moonshot、
> 智谱、硅基流动、OpenRouter、Ollama、vLLM、LM Studio… 全都兼容。
> 剩下 20% 是**真·异构协议**（Anthropic `/v1/messages`、Gemini `generateContent`）和 **DeepSeek 网页版内部接口**。

所以方案不是"为 20 家写 20 个客户端"，而是：**一份注册表 + 4 个协议适配器 + 一个"自定义"逃生口**。

---

## 二、设计

### 2.1 两个正交轴（本方案的地基）

```
轴 A · 上下文策略   stateless（客户端组装）  ←→  server-session（服务端记忆）
轴 B · 传输协议     openai / anthropic / gemini / deepseek-web
```

现在 `BACKENDS = ("deepseek","deepseek-api","qwen")` 把两轴焊死；拆开后：

- **轴 A 由能力位推导**（`server_memory=True` → SessionBackend，否则 MessagesBackend）
- **轴 B 由适配器实现**（一个协议一个类）

### 2.2 ProviderProfile（唯一事实来源）

新增 `ai/providers.py`：

```python
@dataclass(frozen=True)
class ProviderProfile:
    id: str                     # 稳定标识，写进 QSettings；改显示名不动它
    label: str                  # 设置界面显示名
    protocol: str               # 见 2.3 方言：openai / openai-responses / anthropic / gemini / deepseek-web
    api: str                    # 默认端点（**可选覆盖**，原生协议可为空）
    env: tuple                  # 该厂商密钥的环境变量名（如 ("DEEPSEEK_API_KEY",)）
    auth: str                   # 鉴权形态：bearer / x-api-key / none(网页版走 cookie)
    default_model: str
    models: tuple               # 预置候选模型 → ModelInfo
    group: str                  # 设置界面分组：网页版 / 官方 / 聚合 / 本地 / 自定义
    key_hint: str               # 「去哪儿拿 Key」一行提示

@dataclass(frozen=True)
class ModelInfo:
    """能力挂在**模型**上，不是供应商上（v2 修正，见 §十）。"""
    id: str
    label: str
    attachments: bool           # 图片/PDF —— 旧 vision
    reasoning: tuple            # 思考档位：() = 不支持；("toggle",) 或 ("low","medium","high",...)
    reasoning_kind: str         # "toggle" | "effort" | "budget_tokens"
    context_limit: int
    output_limit: int
    cost: tuple                 # (in, out, cache_read, cache_write)，0 = 未知/免费
```

> v2 修正：结构照抄 opencode 的注册表字段（`attachment` / `reasoning_options` / `limit` / `cost`），
> 这样将来若要接远端 registry 可无损升级。依据见 **§十**。

内置 profile（按 `group`）：

| group | id | protocol | 说明 |
|---|---|---|---|
| 网页版 | `deepseek-web` | `deepseek-web` | 就是现在的 `deepseek`，**id 改名**（见 §4.2 兼容） |
| 官方 | `deepseek` | `openai` | 现有 `deepseek-api` |
| 官方 | `qwen` | `openai` | 现有千问（本来就走的 compatible-mode） |
| 官方 | `anthropic` | `anthropic` | 第一个真异构协议，用来验证接缝 |
| 聚合 | `openrouter` / `siliconflow` / `moonshot` / `zhipu` | `openai` | 只差 base_url 与模型表 |
| 本地 | `ollama` / `vllm` / `lmstudio` | `openai` | `http://127.0.0.1:11434/v1` 等 |
| 逃生口 | **`custom`** | `openai`（可选 anthropic/gemini） | base_url + 模型全部手填 |

> **这一条是本方案的关键**：`custom` 的存在意味着**以后新增任何 OpenAI 兼容厂商都不用改代码**，
> 用户自己填 URL + Key + 模型名即可。注册表里预置的只是"做了名字和提示的常用项"。

### 2.3 能力位取代厂商名

`is_messages_backend()` 现在做的是"能力判断"（system 是否每轮重发），却写成"厂商名判断"。
改成能力位：

```python
# 传输级能力：挂在 provider 上（决定"信息流怎么走"，与模型无关）
PROVIDER_CAPS = {
    "server_memory",    # 服务端保存历史 → 客户端只发当轮（= 旧 SessionBackend）
    "system_each_turn", # system 每轮重发 → 必须用精简人设（*_api.txt）
    "stream",           # 支持流式
    "search",           # 支持联网搜索（今天只有网页版）
    "session_state",    # 需要持久化 session_id/parent_id
}

# 模型级能力：挂在 ModelInfo 上（同一家不同模型可以完全不同）
#   attachments / reasoning / reasoning_kind / context_limit / cost
```

> **v2 修正（关键）**：原方案把 `vision` / `thinking` 也放在 provider 级，**这是错的**。
> 实证调研显示能力位必须以**模型**为粒度（见 §十）。否则会出现"选了支持视觉的厂商、
> 但实际那个模型不支持"——正是今天"千问后端静默忽略图片"这类问题的成因。

映射：

| capability | deepseek-web | deepseek | qwen | anthropic | custom(openai) |
|---|---|---|---|---|---|
| `server_memory` | ✅ | — | — | — | — |
| `system_each_turn` | — | ✅ | ✅ | ✅ | ✅ |
| `stream` | — | ✅ | ✅ | ✅ | 取决于端点（**默认 ✅，可关**） |
| `search` | ✅ | — | — | — | — |
| ~~`vision`~~ | → 模型级 | | | | |
| ~~`thinking`~~ | → 模型级 | | | | |

> 上表只剩**传输级**能力。`vision` / `thinking` 已下沉为模型级（v2 修正）。
> 例：`deepseek` 这家下 `deepseek-chat` 不支持思考、`deepseek-reasoner` 支持 —— 只有按模型判才对。

改造后：

```python
# config.py —— 函数名保留，语义改成能力查询（12 个调用点零改动）
def is_messages_backend(name=None):
    return not current_profile().cap("server_memory")

# 语义更正的别名，新代码用它
def needs_system_each_turn(name=None):
    return current_profile().cap("system_each_turn")
```

> **这是本方案最省事的杠杆**：5 个行为分支（#8）**一行都不用改**就自动正确了。

### 2.4 凭据：从扁平键到命名空间 + 兼容回退

```python
# 新键
ai_provider                     # 当前 provider id
ai/<id>/key                     # 每家独立
ai/<id>/model
ai/<id>/base_url                # 可选覆盖
ai/<id>/stream                  # 可选能力覆盖
```

新增 `ai/credentials_store.py`（**唯一的 QSettings 读写点**，让 `config.py` 继续保持无 Qt 依赖）：

```python
LEGACY_MAP = {          # 老键 → 新位置，读时回退，保证升级不丢 Key
    "deepseek_api_key":   ("deepseek", "key"),
    "deepseek_api_model": ("deepseek", "model"),
    "qwen_api_key":       ("qwen", "key"),
    "qwen_model":         ("qwen", "model"),
    "ai_session_id":      ("deepseek-web", "session_id"),
    "ai_parent_id":       ("deepseek-web", "parent_id"),
}
```

**升级策略：只读回退，不写迁移**——读 `ai/qwen/key` 不存在就读 `qwen_api_key`；
用户下次在设置里保存时自然落到新键。老键保留不删（可回滚）。

### 2.5 协议适配器

新增 `ai/protocols/`：

| 文件 | 职责 | 备注 |
|---|---|---|
| `base.py` | `ProtocolAdapter` 抽象：`chat(messages)` / `stream(messages)` / `verify()` | 接口极薄，只做"发请求、拿文本" |
| `openai_chat.py` | 通用 OpenAI 兼容客户端 | **吸收** 现有 `DeepSeekApiClient` 与 `QwenClient` 的 chat 半边 |
| `anthropic_messages.py` | Anthropic `/v1/messages` | `system` 顶层字段、`max_tokens` 必填、`x-api-key` + `anthropic-version` |
| `gemini_generate.py` | Gemini `generateContent` | **可延后**，先只做前三个验证接缝 |
| `deepseek_web.py` | 现有 `ai/deepseek.py` 的薄包装 | **内部一行不改**，只是套成 adapter |

**明确拒绝的方案 —— 配置化 JSON 路径映射引擎**
（用一份 JSON 描述"请求体放哪个路径、响应从哪个字段取"来支持任意协议）：
看起来能一次性通吃，实际会造出一层**无法单测、报错看不懂**的间接层。
4 个协议写 4 个真适配器，代码量更小、可断点、可断言请求体。这是有意的取舍。

### 2.6 流式收进适配器

`ProtocolAdapter.stream(messages) -> Iterator[str]`，然后：

- `ai/chat.py:516-531` 的 `qwen_model` 硬编码 → `ai_client.current_model()`
- `is_messages_backend(...)` 分成"能否流式"（`cap("stream")`）+ "是否服务端记忆"
- 千问的句子级流水线（`voice/tts.py::StreamVoice`）保持不动，它只吃 `Iterator[str]`

### 2.7 辅助调用统一（顺手修掉两个真 bug）

现在有两处"用独立会话做第二次调用"的硬编码（#6、#7），都写死 DeepSeek 网页版。
新增 `ai/utility.py`：

```python
def utility_chat(messages, purpose="refine") -> str:
    """辅助调用（提炼/润色/预检）：走当前 provider，不进主历史。

    - provider 有 server_memory（网页版）→ 复用独立持久会话（保留今天的优化）
    - 否则 → stateless 一次性调用
    """
```

然后：
- `ai/client.refine_wiki()` → 改用 `utility_chat`（**Wiki 提炼不再依赖网页版登录**）
- `ai/memory.condense_context()` → 改用 `utility_chat`（**选千问也能提炼了**）

> 这两条是现存的真 bug，不是重构洁癖。修完的收益：只配一个 API Key 的用户，Wiki 提炼与长期记忆
> 第一次真正可用。

---

## 三、分期实施

> 每期都独立可交付、可回滚、可跑测试。**第 0 期是纯重构，零行为变化。**

| 期 | 内容 | 验收 | 风险 |
|---|---|---|---|
| **P0** | 新增 `ai/providers.py` + `ai/credentials_store.py`；`BACKENDS`/`MESSAGES_BACKENDS`/`is_messages_backend` 改为查注册表 | `tests/test_chat_unit.py` 全绿；`is_messages_backend()` 对三个内置返回值与今天**逐一相同**；**调用点零改动** | 低 |
| **P1** | 凭据命名空间 + 老键只读回退；设置界面读写改走 store | 老用户的 `qwen_api_key`/`deepseek_api_key` 仍被正确读出（写单测） | 低 |
| **P2** | `ai/protocols/openai_chat.py` 通用化；`DeepSeekApiClient`/`QwenClient` 的 chat 半边收敛到它 | 请求体与今天**逐字节相同**（golden 断言） | 中 |
| **P3a** | **止血 + 守卫**：给 `【move(pos=~,~,~)】` 补守卫单测固化期望行为；把语音路径改成"先整体剥离再切句" | 单测固化（`[U9]`）；语音不再念指令 | 低 |
| **P3** | 流式剥离重构（单剥离器 `TagStripper` + 句切分前置）+ `ai/chat.py` 去品牌化 + 补流式 DSL 下发 | 千问流式 + 句子级 TTS 流水线行为不变；`[C6]` 流式下发 DSL 通过 | 中 |
| **P4** | `ai/utility.py`；`refine_wiki` / `condense_context` 改走它 | 选 `deepseek`（纯 API Key、不登录网页版）时 Wiki 提炼与记忆提炼**都能跑通** ← 这是新增能力 | 中 |
| **P5** | `anthropic_messages.py` | 用同一个工具能对 Anthropic 说"你好"并拿到回复；`is_messages_backend` 对它也为真 | 中 |
| **P6** | 设置界面重做（见 §4）+ 「测试连接」按钮 | 改 UI 不用启桌宠：`tools/preview_settings_dialog.py` 出图核对 | 中高（控件多） |
| **P7** | `custom` 逃生口 + README/CHANGELOG 同步 | 填任意 OpenAI 兼容 URL + Key + 模型即可用 | 低 |

**建议 P0–P4 一次做完**（内部闭环，用户可见收益是 P4 的两个 bug 修复），P5–P7 作为第二批。

> **落地进度**：P0–P5 见 §十二；**P6–P10 与模型清单校准已落地（2.25.0）见 §十四**；
> **实机校准 + TTS 目录化（2.26.0）见 §十五**。设置界面已支持 5 家提供方 + 自定义端点。

---

## 四、设置界面改造（`pet/settings_dialog.py`）

### 4.1 「基础」页新结构

今天：两排写死的 `DeepSeek 官方 API Key / 模型` + `千问 API Key / 模型`（`:854-881`），
按后端索引显隐（还遗留过"标签不跟着隐藏"的老 bug）。

改后单块：

```
提供方   [ 分组下拉：网页版 / 官方 / 聚合 / 本地 / 自定义 ]   ← 选中即切换
端点     [ https://api.deepseek.com        ]  (预置，自定义项必填)
API Key  [ ••••••••••••••  ]  [获取方式提示]
模型     [ deepseek-chat ▾ ]  (可编辑下拉 = 预置模型 + 手填)
能力     [流式] [识图] [思考]              ← 只读小标签，一眼看清选了什么
         [ 测试连接 ]                       ← 发一条最小请求，复用现有 verify() 模式
```

要点：
- **一个 Key 输入框**取代两排；切提供方时自动换 Key 与模型（各自独立保存，不互相覆盖）
- 「模型」可编辑下拉，解决"预置列表不可能覆盖厂商全部型号"
- 「能力」标签是**只读的自证**：用户能直接看到"这个后端不支持识图"，而不是发图后才失败
- 「测试连接」把今天散在工具里的 `verify()` 变成界面能力

### 4.2 id 改名与兼容

`deepseek` → `deepseek-web` 是更准确的名字，但会打断已存设置。
**做法**：`LEGACY_MAP` 把老键 `ai_session_id/ai_parent_id` 映射到新 provider，
读 `ai_provider` 时若取到老值 `"deepseek"` 就归一化成 `"deepseek-web"`。用户无感。

### 4.3 语音与对话解耦（`voice/tts.py`）—— **2.26.0 已落地**

原先 `load_qwen_key()` 出现在 3 处（`:131/309/406`），且设置界面用
`backend_combo.currentIndex() == 2` 判断"要不要显示音色下拉"——
等于把「语音能选什么」绑死在「对话后端」上：选了 DeepSeek 对话，音色下拉就消失。

**已做**（见 §十五）：

- TTS 的模型 / 音色独立存储：`tts/model`、`tts/voice`（`credentials_store.get_tts/set_tts`），
  带旧键 `qwen_cosy_voice` 只读回退
- 音色下拉的显隐只看**语音引擎**是否为「千问 TTS」，与对话后端无关
- `pet/modes.py` / `pet/bubble.py` 不再直接读旧键

**未做**：把 TTS 抽成可插拔的 `tts_provider`（目前云端 TTS 只有百炼一家实现，
抽象出来只能验证一种实现，属于投机抽象）。第二家落地时再抽，键的形状不用变
（`tts/<provider>/model` 即可）。

> 命名误导已就地更正**文案**：界面从「CosyVoice（千问流式）」改为「千问 TTS（联网，音色多、可配模型）」，
> 因为调的一直是 qwen3-tts 系列。**模式 id 仍叫 `cosy`**——改 id 会让老用户的
> `ai_voice_mode` 失效并掉回「关闭」，代价大于收益。

---

## 五、单一事实来源（同步本次评价里指出的问题）

本方案落地时**顺手把这几条钉死**（都是"同一事实多处表述"的重灾区）：

| 事实 | 唯一来源 | 其它地方 |
|---|---|---|
| provider 注册表 / 能力位 | `ai/providers.py` | 文档只引用，不复述 |
| 凭据键名 | `ai/credentials_store.py` | 其他地方**禁止**出现字面量 |
| 指令集 | `nlu/mod_contract.py` | 已有约定，保持 |
| 版本号 | `config.py::VERSION` | README/CHANGELOG 引用 |

并且按评价里的建议，**把这些做成断言**（新增 `tests/test_providers.py`）：

1. 注册表完整性：id 唯一、`default_model ∈ models`、protocol 合法
2. **老键回退**：`qwen_api_key` 仍能解析到 provider `qwen`
3. **能力位一致性**：对每个内置 provider，`is_messages_backend()` == `not cap("server_memory")`
4. **跨协议请求体 golden**：OpenAI 体 / Anthropic 体各一份断言（把 `test_maid_loop.py:182` 的
   DeepSeek 单体测试升级成按协议的表）
5. **无硬编码守卫**：`ai/` 下除 `credentials_store.py` 外不得出现 `"qwen_api_key"` 等字面量
6. **版本铁律**：`config.VERSION == CHANGELOG.md` 顶部版本号 ← **当前是错的**（2.23.0 vs 2.23.1）

---

## 六、明确不做（Non-goals）

- ❌ **配置化 JSON 路径映射引擎**（理由见 §2.5，有意取舍）
- ❌ **多 provider 并行 / 自动降级 / 负载均衡**——先只做"选谁用谁"。真需要再谈
- ❌ **改 `SessionBackend` / `MessagesBackend` 的语义红线**（纯对话进历史、瞬时状态不进、绝不整段重发）
- ❌ **动女仆端（SmartMaid）一个字节**——本次是桌宠内部改造，`nlu/mod_contract.py` 契约不变
- ❌ **本地模型管理**（下载/量化/显存）——只做"连自定义 URL"

---

## 七、风险与回滚

| 风险 | 对策 |
|---|---|
| `is_messages_backend` 语义变更导致行为漂移 | P0 用**等价性测试**兜住：三个内置 provider 逐一对比新旧返回值 |
| 老用户 Key 丢失 | 老键只读回退 + 保留不删；P1 单独写回退单测 |
| 设置界面控件膨胀（当前一页最多 12 页之一） | 单块 + 显隐；改 UI **不启桌宠**：`tools/preview_settings_dialog.py` → `png/` 目视核对 |
| 流式改造打断句子级 TTS 流水线 | P3 保持 `Iterator[str]` 契约不变；`StreamVoice` 一行不改 |
| 打包体积/依赖 | Anthropic/Gemini 只需 `curl_cffi`（已有）；**不引新依赖** |

**回滚**：遵循项目铁律——改动前打 `*.bak_<时间戳>` 快照，
`pet/settings_dialog.py` 与 `core/theme.py` 已有 `tools/restore_wbs_ui.py` 的防呆范式可复用。
**禁用 `git checkout`**。

---

## 八、待拍板

> P0–P4 是内部闭环重构，**不依赖任何一条决策，可直接开工**；下表只影响 P5 之后的走向。

| # | 决策 | 我的建议 |
|---|---|---|
| 1 | Anthropic / Gemini 原生协议**是否要**？还是"OpenAI 兼容 + 自定义 URL"就够 | **P5 只做 Anthropic**，Gemini 延后。理由：需要一个真异构协议来验证接缝是否成立，一个就够 |
| 2 | 是否保留 DeepSeek 网页版 | **保留**。它是唯一的 `server_memory` 实现 + 唯一识图，且免费；但要把它明确标成"网页版（非官方 API）"，避免用户混淆 |
| 3 | 语音是否跟着通用化 | **不在本方案内**，但**必须解耦**（§4.3 只改"取 Key 的来源"） |
| 4 | 是否要"辅助调用用便宜模型"（主对话强模型 + 提炼/润色用弱模型） | **值得做，但放到 P8**。这是真实成本优化点，且现在 `_refine_backend` 已在隐式尝试这件事 |
| 5 | 要不要"从 URL 探测模型列表"（Ollama/vLLM 有 `/v1/models`） | **P7 做**，一个 GET 就能省掉手填模型名 |

## 九、已知限制（如落地，需在 README 标注）

- `custom` 提供方只知道"这是 OpenAI 兼容"，**不保证**流式/识图/思考真的可用——
  能力标签对 `custom` 显示为"未验证"，需用户自行用「测试连接」确认
- 各厂商的**错误码语义不统一**（限流/欠费/模型名错误），只做统一文案 + 保留原始 message
- 网页版 DeepSeek 的内部接口随时可能变（`X-Client-Version` 已是 2.4.0），
  它是本方案里唯一"不稳定"的 provider

---

## 十、按 opencode 实证修正（v2）

> 依据：`RESEARCH_OPENCODE_PROVIDER.md`（对本机 `opencode-ai@1.18.16` 的实测，
> 含 222 家供应商 / 7864 模型的全量统计与二进制字符串取证）。
> 我不会照抄它的规模（那是通用工具的打法），但**它的分层与字段设计是对的**。

### 10.1 七处修正

| # | 原方案 | 实证后的修正 | 依据 |
|---|---|---|---|
| 1 | 能力位挂在 **provider** | **下沉到 model** | 7864 个模型各自声明 `attachment`/`reasoning`/`tool_call`；同家不同模型能力不同 |
| 2 | `thinking: bool` | **`reasoning` + `reasoning_kind`（toggle / effort / budget_tokens）+ 取值集** | `reasoning_options` 三类语义实测分布 3437 / 1313 / 677 |
| 3 | base_url 必填可覆盖 | **`api` 可选**（原生协议留空） | 222 家里只有 196 家带 `api` |
| 4 | 单个 `openai` 协议 | **留方言子变体位** | 二进制里 `openai-compatible-api` / `-chat` / `-api-gateway` 并存（`/chat/completions` vs `/responses`） |
| 5 | 静态凭证读取 | **有序回退链**：显式 auth → 设置项 → **环境变量**（`env[]` 在注册表里声明）→ 注入 header | 抽到的 auth 解析函数原文 |
| 6 | 注册表只有内置 | **内嵌默认 + 远端刷新**（含失败降级） | 注册表既内嵌在二进制，又每 60 分钟从 `models.dev` 刷新；失败只记日志 |
| 7 | 未涉及 | **思考档位按 `provider/model → variant` 存储** | `state/model.json` 实测 `"variant":{"opencode-go/deepseek-v4-flash":"high"}` |

### 10.2 三处补充（原方案漏掉但有价值）

1. **模型带 `limit` 与 `cost`** —— 顺手能给桌宠加"上下文预算"控制（今天是 `HISTORY_WINDOW` 轮数硬编码，
   换成 token 预算更准），以及将来"主对话用强模型、提炼用便宜模型"的成本优化（对应 §八 待拍板第 4 条）。
2. **密钥脱敏** —— 请求日志里对 `Authorization` / `x-api-key` / `Cookie` 一律打 `***`。
   桌宠目前有 `logs/` 诊断日志体系，接多厂商后**必须**加这条。
3. **`env[]` 声明密钥环境变量名** —— 可以直接做"检测到你机器上已有 `DEEPSEEK_API_KEY`，要导入吗"。

### 10.3 两处明确不采纳

| opencode 做法 | 不采纳的理由 |
|---|---|
| **28 个 npm 包，按注册表的 `npm` 字段动态加载** | 它自己是 Node 生态，`npm install` 即可；桌宠是 Python + **PyInstaller 打包**，引 Node 依赖会砸掉分发。而实证表明 **openai-compatible 一家就占 81.5%、前 4 个协议覆盖 88.7%**，手写 4 个适配器足够 |
| **222 家 / 7864 模型的全量注册表** | 那是通用工具的需求；桌宠只需"预置 10 家常用 + `custom` 逃生口"。**但字段照抄**，将来想接远端 registry 可无损升级 |

### 10.4 对分期的影响

- **P0 不变**（纯重构，零行为变化）——修正 1~3 落在 P0/P2 的数据结构里
- **P2 扩一点**：`openai_chat.py` 要支持方言（`/chat/completions` + 预留 `/responses`）
- **P6 扩一点**：设置界面不是"思考开关"，而是**按当前模型动态渲染**（effort 下拉 / toggle / 不可用）
- **新增 P8**：注册表字段补齐 `limit`/`cost`，为 token 预算与"辅助调用用便宜模型"打底
- **不做**：远端 registry 同步（`models.dev` 那套）——桌宠不需要 7864 个模型；
  若将来要做，字段已对齐，属增量功能

---

## 十一、四个关键问题的设计裁定

> 这一节是把 §十 的原则落到具体机制上。每条都给出**代码依据**（行号/正则）与**代价**。
> 值得注意的是：四个问题里有三个的根因是同一个——**能力与形态必须数据化，且粒度必须是"模型"**。

### 11.1 原生「文本 + 语音」双模态模型

**现状前提**：全项目假定"模型只出文本"，语音一律由 `voice/tts.py` 事后合成（off / edge / local / cosy 四引擎）。

原生音频模型（OpenAI audio 系、Gemini native-audio、Qwen-Omni 这类）**不是 TTS 的替代品，而是绕过了我们的文本控制层**：

| 形态 | 谁在说话 | 我们的 TTS | 标签可控性 |
|---|---|---|---|
| `text-only`（今天全部） | 模型出文本 → 我们合成 | 必需 | ✅ 可剥（文本在手） |
| `native-audio` | 模型直接出音频 | **必须关闭** | ❌ 音频已生成，**无法事后剥离** |
| `omni-dual` | 同时给文本 + 音频 | 关闭 | ⚠️ 文本可剥，音频不可 |

**裁定：默认关闭，列为 P9；需要用户明确拍板。**

四条设计约束：

1. **注册表已预留**：模型级 `modalities.output` → `("text","audio")`，另加 `audio_voices` / `audio_formats`。
   （这就是 §十 采纳的 opencode 字段，他们 7864 个模型的 `modalities.output` 已实测存在）
2. **标签处理必须"前置于生成"**：音频无法剥离 → 选原生语音时改用**语音专用 prompt 段**：
   收窄为**零动作标签**（`{动作}` 由本地情绪/规则驱动），并在 system 里禁止输出 `【指令】`。
   这不是妥协而是**本来更干净**——README 早已写明"语音场景要求 30 字内、标签不读出"，语音通道不适合承载结构化批注。
3. **`stream()` 必须从 `Iterator[str]` 升级为事件流**：否则音频会和文本挤在同一通道。
   ```
   StreamEvent = TextDelta | AudioDelta | ThinkDelta | Done
   ```
   （`ThinkDelta` 顺带解决 11.3 的"思考片段不能进显示/语音"）
4. **需要新的流式音频播放管线**：PCM 增量 → 抖动缓冲 → 边收边播。
   现有 `voice/tts.py` 的 `_play_pcm_blocking(pcm, rate)` 是**整段阻塞播放**，**不能复用**。

> 代价盘点：音色/情感一体的收益 vs 标签不可控 + 新增播放管线 + 原生音频计费更贵。
> 所以它不该是默认路径，而是一个**显式开关**。

### 11.2 流式下动作标签 / 指令的剥离（含两个已定位缺陷）

**现状链路**（`ai/chat.py`）：

```
_run_stream (522) → self.delta.emit(piece) → _on_delta (1068)
     ├─ 显示：self._update_ai_bubble(parse_ai_output(self._stream_full)[0])   # 1072
     └─ 语音：self._maybe_stream_speak(delta)                                 # 1074
```

即**显示层和语音层各自独立做剥离**，这是第一个结构性问题。下面两个是它的直接后果。

#### 缺陷 1（显示层，肉眼可见）

`parse_ai_output` 的动作标签正则是 `[\[{]([^\]\}]{1,10})[\]}]`（`ai/client.py:17`）——
**要求闭合符存在**。流式中途 `self._stream_full` 可能是 `"好的{开"`，正则不匹配 → **原样返回** →
玩家先看到 `好的{开`，等 `}` 到了才消失。**半个标签会闪现。**

#### 缺陷 2（语音层，会把指令念出来）★

`_maybe_stream_speak` 的切句正则（`ai/chat.py:1112`）：

```python
re.split(r"(?<=[。！？!?…~；;])", self._stream_speech_buf)
```

**`~` 在终止符集合里，而 DSL 的相对坐标用的就是 `~`**（`mod_contract` 的 `params.pos` 支持 `"~"`；
`HANDOVER §5.1` 也写明"`world:~` 自动纠错为主人坐标"）。

按现有代码推导 `【move(pos=~,~,~)】`：

1. 切句 → `["【move(pos=~", ",~", ",~", ")】"]`（在三个 `~` 后各切一刀）
2. 前三段作为"完整句"进入 `parse_ai_output` → **都没有 `】` → 剥离失败 → 原样返回**
3. → **逐段送去 TTS，把指令念出来**

> 这是**代码推导**（正则 + 参数契约都是实的），尚未跑真机复现。
> **建议先补一条单测把 `【move(pos=~,~,~)】` 的期望行为固化下来，再改代码**——
> 符合本项目"先立守卫再动刀"的既有习惯（`check_leak.py`、`tests/test_nlu.py::test_no_leak`）。
>
> **临时止血（2 行）**：`_maybe_stream_speak` 里先把累积缓冲整体 `parse_ai_output` 再切句，
> 而不是先切句再逐句剥。但根治要等下面的重构。

#### 正确设计：**一个剥离器，两个下游**

```
adapter.stream() → TagStripper.feed(delta) → (safe_text, captured_tags)
                            ├→ 显示层（直接贴，不再自己剥）
                            └→ 句切分器 → TTS 队列
```

- **状态机 + holdback 缓冲**：见到 `{` / `【` 进入"疑似标签"态，**未闭合则压住不向下游发送**；
  闭合符到达 → 整段吞掉；超上限仍无闭合 → 判定为普通文本放行
- **上限分开设**：`{...}` 内部 ≤10 字（现正则即 `{1,10}`）→ 回退 12 字够用；
  `【...】` 可带长参数（`【attack(target=minecraft:pig,range=10)】`）→ 96 字 **+ 超时兜底**
  （约 300ms 无新增即先放行，否则"标签里卡住"会导致整句不显示）
- **必须在句切分之前**：这样"标签内含 `~` / 句末标点"根本不会被切坏
- **一轮一个实例**，断流/报错时 `flush()`，防止最后一个标签之后的正文被吞掉
- **输出必须带 `captured_tags`**（不是简单丢弃）：
  动作标签 → 喂动画；DSL → 喂 `MaidLoop`
  → **顺手修掉已知缺口**：`ai/chat.py:537` 那行 `trace("DSL", "（千问流式后端不接 DSL —— 已知缺口）")`
  说明流式路径今天**完全不下发指令**，而剥离器天然拿到了完整指令，正好一次补齐

### 11.3 思考强度怎么调

**现状是一个 bool，但两条后端的语义完全不同**：

| 后端 | 实现 | 位置 |
|---|---|---|
| 网页版 | `thinking_enabled` **请求参数** | `ai/chat.py:494` |
| DeepSeek 官方 API | **换模型** ← `model = model or (self.model if not thinking else "deepseek-reasoner")` | `ai/deepseek_api.py:64` |

真实世界至少有**三种传输语义**：

| 语义 | 例子 | 参数形态 |
|---|---|---|
| `model_switch` | DeepSeek 官方 API | 改 `model` 名 |
| `request_param` | 网页版（bool）／ OpenAI（`reasoning_effort`）／ Anthropic（`thinking.budget_tokens`） | 请求体字段 |
| `none` | 千问 turbo | 无 |

**设计**：模型级声明 + 适配器侧一个函数 `apply_reasoning(prepared_request, variant)`：

```python
reasoning=("off", "on")                       # 取值集；() = 不支持
reasoning_kind="toggle" | "effort" | "budget_tokens"
reasoning_transport="model_switch" | "request_param"
reasoning_default="high"
reasoning_map={"off": "deepseek-chat", "on": "deepseek-reasoner"}   # model_switch 用
```

**存储**：`ai/<provider>/<model>/variant`（按 provider+model 存，因为取值集是模型相关的；
换模型读不到就回落 `reasoning_default`，切回来能恢复上次档位）。

**四个坑**：

1. **`model_switch` 不能和「模型下拉」并存**。否则用户会困惑"我明明选了 reasoner，思考开关又是关的"。
   建议**把 `deepseek-chat` / `deepseek-reasoner` 列成模型下拉里的两个独立项，思考开关隐藏**——
   别让一个开关偷偷改掉模型，否则 `logs/perf.log` 里记录的模型名与实际不符。
2. **Anthropic 的 `thinking` 与 `temperature` 互斥** —— `apply_reasoning` 必须清掉 `temperature`，
   这是协议级硬约束，要写进适配器而不是靠记。
3. **`budget_tokens` 有下限**（真实 API 要求 ≥1024）→ 注册表要带 `min` / `max`。
4. **开思考后首字延迟显著变大**（网页版 SSE 已按 `THINK` / `RESPONSE` 分发）。
   → **THINK 通道必须永不进显示、永不进 TTS**，且语音应在**第一个 RESPONSE 片段**到达时才开始。
   这条与 11.2 同源，所以 `ThinkDelta` 要在事件流里单列（见 11.1 第 3 条）。

### 11.4 思考 / 联网开关的 UI：动态还是写死

**裁定：能力与取值集动态（数据），参数名与线格式写死（代码）。边界就在这一句。**

| 内容 | 来源 | 性质 |
|---|---|---|
| 是否支持思考 / 联网 | 注册表（模型级 `reasoning*`、provider 级 `search`） | **动态** |
| 思考有哪些档位、档位名 | 注册表 `values` | **动态** |
| 控件形态（复选框 / 下拉 / 数字框） | 由 `reasoning_kind` 推导 | **动态** |
| `reasoning_effort` vs `thinking.budget_tokens` vs `thinking_enabled` | 适配器 | **写死（代码）** |

**必须有第三态**——`custom` provider 无法预知能力：

| 态 | 来源 | UI |
|---|---|---|
| ✅ 支持 | 注册表声明 | 正常控件 |
| ❌ 不支持 | 注册表声明 | 隐藏，或灰掉 + 一行说明 |
| ❓ **未知** | custom / 注册表无此字段 | **显示控件 + "未验证"小标**，允许手动开关，存 `ai/<id>/caps_override` |

其余四条：

- **位置**：紧挨「模型」下拉下方一行，**随提供方/模型切换即时重渲染**；
  切换后若已存档位不在新取值集内 → **回落默认并提示**（避免"存着 high 但新模型只有 toggle"）
- **不做统一"高级"折叠区**：这两个开关的正确形态依赖模型，放模型旁边最不容易误操作
- **只读能力标签行**：模型下面显示 `[流式] [识图] [思考:高]`，把"动态"这件事**可视化**给用户看
- **可发现性**：这是今天最缺的一环——现在用户要发一张图才知道千问后端不支持识图

### 11.5 三个问题的共同根因

11.1 / 11.3 / 11.4 表面是三个问题，根因是同一个：

> **能力与形态必须数据化（而不是写在 if 里），且粒度必须是「模型」而不是「供应商」。**

这正是 §十 的 v2 修正。按此调整分期：

| 期 | 变更 |
|---|---|
| **P3**（原「流式收进 adapter」） | **扩为**：`stream()` 事件流化 + `TagStripper` 单剥离器 + 修 11.2 两个缺陷 + 补流式 DSL 下发 |
| **P9**（新增） | 原生语音双模态（默认关，含流式音频播放管线） |
| **P10**（新增） | 思考档位：`reasoning*` 模型级声明 + `apply_reasoning` + 按 kind 动态 UI + 三态覆盖 |

> P9 / P10 都可延后；**P3 的两个缺陷建议尽快修**（其中一个会把指令念给用户听）。

---

## 十二、落地记录（P0–P5 已落地，2026-09-20）

> 版本 **2.24.0**。执行顺序：P0 → P1 → P2 → **P3a+P3（合并）** → P4 → P5 → 回归 + 文档同步。
> **回归结果：7 个测试文件全绿**（`test_providers` / `test_chat_unit` / `test_chat_chain` /
> `test_maid_loop` / `test_reconstruction` / `test_wiki` / `test_nlu`），
> 设置对话框离屏渲染正常（`tools/preview_settings_dialog.py`）。

### 12.1 新增文件

| 文件 | 作用 |
|---|---|
| `ai/providers.py` | 供应商注册表（唯一事实来源）：`ProviderProfile` / `ModelInfo` / 能力位 / 思考语义 |
| `ai/credentials_store.py` | 唯一的 QSettings 读写点：命名空间 `ai/<id>/*` + 老键只读回退 |
| `ai/protocols/base.py` | `ProtocolAdapter` + `StreamEvent`（`text` / `think` / `audio` / `done`） |
| `ai/protocols/openai_chat.py` | **通用 OpenAI 兼容适配器**（含 CLI 兜底方言位 `chat_path`） |
| `ai/protocols/anthropic_messages.py` | Anthropic 原生 `/v1/messages` |
| `ai/protocols/__init__.py` | `make_adapter(pid)` 工厂（网页版显式拒绝，它在 `ai.client`） |
| `ai/stream_strip.py` | `TagStripper` + `split_speakable` + 标签/切句正则的**规范定义** |
| `ai/utility.py` | 辅助调用统一入口 `utility_chat()` |
| `tests/test_providers.py` | V1–V9 守卫（见 12.4） |

### 12.2 改动文件

| 文件 | 改了什么 |
|---|---|
| `config.py` | `MESSAGES_BACKENDS` **删除**；`is_messages_backend()` 改为查能力位；新增 `needs_system_each_turn()`；`VERSION` → 2.24.0 |
| `ai/client.py` | `BACKENDS` 由注册表推导；`make_client` / `chat_once` 按协议分流；8 个凭据存取函数**签名不变**（薄包装）；`refine_wiki` → `utility_chat`；会话状态改走 store |
| `ai/chat_service.py` | 两处 `name == "deepseek"` → 能力判断；`_ensure_backend` 注释同步 |
| `ai/chat.py` | 接入 `TagStripper`（一个剥离器两个下游）；`is_stream` 改由注册表能力位决定；传图门控改能力位；流式收尾补 DSL 下发 |
| `ai/deepseek_api.py` / `ai/qwen.py` | chat 半边收敛为 `OpenAIChatAdapter` 薄子类（类名与模块常量保留）；千问 TTS 原样不动 |
| `ai/memory.py` | `_refine_backend()` / `_refine_web()` 删除 → `_condense_call()` 走 `utility_chat` |
| `pet/settings_dialog.py` | 后端 idx 映射改用规范 id（3 处）；**顺手修**：`cosy_voice_combo` 从不隐藏导致的空下拉框 |
| `tests/test_chat_unit.py` | U4 改为断言新路由；**新增 U9** 流式剥离守卫 |
| `tests/test_chat_chain.py` | `_opts(backend=...)` 参数化；**新增 C6** 流式下发 DSL |
| `CHANGELOG.md` / `README.md` | 2.24.0 条目；版本号对齐（顺带修掉 2.23.1/2.23.0 错位） |

### 12.3 与计划的四处偏离（都记账）

1. **P3a 与 P3 合并落地**。原计划先"两行止血"再重构，但止血要正确就必须有 holdback
   （否则 `~` 仍会被切坏），所以直接实现了 `TagStripper`。
   守卫测试（`[U9]`）与两个缺陷的修复一并交付。
2. **流式"事件流化"只落地了原语**。`stream_events()` / `StreamEvent` 已在适配器上，
   但 `_run_stream` 仍走 `chat_stream()` 文本流 —— 因为 `QwenClient.chat_stream` 会写
   "窗口内多轮记忆"，切到事件流会改变这个语义；而事件流的真正理由（音频）要到 P9。
3. **`anthropic` 注册为 `selectable=False`**。适配器就绪（`[V5]` `[V9]` 已覆盖），
   但设置界面是**按索引硬编码**的 3 项，此刻若允许选中会把 `anthropic` 显示成 index 0
   （DeepSeek 网页版）而与实际不符。**P6 接入 UI 后再放开**。
4. **P6 / P7 未做** → 用户可见的变化只有 P4 修掉的两个隐性坏功能（见 12.5）。

### 12.4 新增守卫（`python tests/test_providers.py`）

| 编号 | 内容 |
|---|---|
| V1 | 注册表完整性（id 唯一 / 协议合法 / `default_model ∈ models` / 思考档位自洽） |
| V2 | **能力位等价性**：三个内置后端的新旧 `is_messages_backend` **逐一相同** |
| V3 | 旧 id 归一化 —— 钉死"`deepseek` 不得被当成官方 API"（防语义反转 180°） |
| V4 | 凭据命名空间 + 老键回退（**用假 QSettings，绝不碰真实配置**） |
| V5 | 跨协议请求体 golden（OpenAI / Anthropic `thinking`×`temperature` 互斥 / DeepSeek 换模型） |
| V6 | 无硬编码守卫（旧键字面量只允许出现在 `credentials_store.py`） |
| V7 | **版本铁律**（`config.VERSION` == CHANGELOG 顶部；今天是 2.24.0） |
| V8 | 适配器工厂拒收网页版 / 未知协议报错 / 思考控件三态 |
| V9 | 流式 SSE 解析（OpenAI 与 Anthropic，含**跨分片行拼接**、think 与 text 分流） |

`test_chat_unit.py` 新增 `[U9]`（未闭合不外泄 / 跨片补齐 / `~` 不碎 / `flush` / 超时）；
`test_chat_chain.py` 新增 `[C6]`（流式确实下发了 DSL，补掉已知缺口）。

### 12.5 本期真正的用户可见收益

只有两条，但都是**原本坏掉、且静默失败**的：

1. **只配 API Key、未登录网页版的用户，Wiki 提炼第一次真正可用**（原先 `refine_wiki` 写死网页版客户端）
2. **选千问时长期记忆提炼第一次真正可用**（原先 `_refine_backend` 写死 `deepseek-api`，取不到就回退到网页版）

外加一条体验修复：**流式语音不再把 `【指令】` 念出来**（缺陷 2）。

### 12.6 待确认 / 遗留

- **DeepSeek 官方 API 的模型清单未动**：仍是 `deepseek-chat` / `deepseek-reasoner`，
  但本机 models.dev 缓存里该厂商是 `deepseek-v4-pro` / `deepseek-v4-flash` / `deepseek-flash` / `deepseek-v4-flash-vision-exp`。
  **可能是既有清单过时**，但改动会影响你正在用的配置，故**留着等你拍板**。
- P6 设置界面 / P7 `custom`（→ 这两项做完，"接入任意 AI"才对用户可见）
- P8 模型级 `limit`/`cost` → token 预算与"辅助调用用便宜模型"

---

## 十三、原 §八 待拍板（保留，供 P6/P7 决策）

> 内容见上文 §八；下表为 §八 的浓缩重述，方便对照落地进度。

| # | 决策 | 建议 | 状态 |
|---|---|---|---|
| 1 | Anthropic / Gemini 原生协议要不要 | 只做 Anthropic | **已做（P5）** |
| 2 | 是否保留 DeepSeek 网页版 | 保留并标注"非官方 API" | 保留 |
| 3 | 语音是否跟着通用化 | 本期只解耦取 Key 来源 | **已解耦（2.26.0，§十五）**；TTS 注册表仍未抽（§4.3） |
| 4 | 是否"辅助调用用便宜模型" | 值得做 | **已做（P8）** |
| 5 | 是否从 URL 探测模型列表 | 值得做 | 未做（P7 简化版：手填模型名） |

---

## 十四、落地记录（P6–P10 + 模型清单校准，2026-09-20）

> 版本 **2.25.0**。「接入任意 AI」到此**对用户可见**。
> 回归：7 个测试文件全绿；设置界面逐供应商离屏渲染核对
> （`tools/_preview_providers.py` 一次性核对 5 个提供方的布局，**不启桌宠**）。

### 14.1 模型清单按 models.dev 校准（M1）

- **DeepSeek 官方 API 换掉整份清单**：`deepseek-chat` / `deepseek-reasoner` 已从 models.dev 下架，
  改为 `deepseek-v4-flash`（默认）/ `deepseek-flash` / `deepseek-v4-pro` / `deepseek-v4-flash-vision-exp`
- **思考语义随之改变**：V4 系列用 `reasoning_options`（toggle + effort）声明思考 →
  **是请求参数 `reasoning_effort`，不是换模型**。旧的"换模型"实现与 `THINKING_MODEL` 常量一并删除
- 模型清单**不再写在 `ai/deepseek_api.py`**，改为 `CHAT_MODELS = prov.model_ids("deepseek-api")`
- 每个模型带真实 `context_limit` / `output_limit` / `cost` / `attachments`
- 千问的两个模型在 models.dev 上**仍然有效**，故未改动
- 声明式数据全部来自本机 models.dev 缓存，非手编

> ⚠️ **`reasoning_effort` 字段名未做真机验证**（本次无法调真实 API）。
> 缓解设计：**只在开了思考时才发这个字段**，所以默认路径完全不受影响；
> 若某天开思考报 400，把注册表里这几个模型的 `reasoning_transport` 改成 `""` 即可彻底停发。

### 14.2 P6 设置界面「基础」页重做

`pet/settings_dialog.py`：从"两排写死的 Key/模型 + 按索引显隐"改为**单块数据驱动**：

提供方（分组下拉）· 端点 · **单个** API Key · **可编辑**模型下拉 · 模型说明（名/上下文/价格）·
**只读能力标签** · 思考（动态）· 测试连接。

**顺手修掉三处既有缺陷**：

1. `cosy_voice_combo` 从不隐藏 → 「基础」页挂着一个**空下拉框**（与 2.23.0 修过的"标签不跟着隐藏"同类）
2. `_update_voice_ui` 用 `backend_combo.currentIndex() == 2` 猜"是不是千问" → 改为查能力位 `CAP_TTS`
3. 传图门控与流式判断原先用 `hasattr(client, "upload_image_file")` / `hasattr(..., "chat_stream")`
   —— 加个基类方法就会**静默失效**，已改为注册表能力位

### 14.3 P10 思考控件按模型动态渲染（并删掉全局复选框）

- 「对话」页那个**与模型无关**的「深度思考」复选框**已删除**（它现在按模型渲染，在「基础」页模型旁）
- 三态：`effort` → 档位下拉 ／ `toggle` → 复选框 ／ `budget_tokens` → 复选框 + token 数（下限 1024，关掉即灰）
- 档位按 `provider + model` 分开记忆（`ai/<pid>/variant/<model>`）
- 顺带修一处**参数被压扁**的隐患：`thinking` 原先只传 bool → effort 档到适配器会退化成"开"。
  新增 `thinking_variant` 字符串随 `opts` 传递，`reasoning_effort` 才拿得到 `high` / `max`

### 14.4 P7 / P8

- **P7**：`自定义（OpenAI 兼容）` 与 `Anthropic Claude` 放开 `selectable`；端点必填校验靠
  标签文案 + 「测试连接」+ `make_adapter` 的明确报错（不引入模态弹窗）
- **P8**：新增「辅助调用后端（留空即跟随主对话）」。Wiki 提炼 / 记忆提炼 / 播报润色
  **不进主对话历史**，用弱模型够用 —— 这是实打实的省钱点

### 14.5 P9：流式音频播放管线 + TTS 抑制（2.27.0 完成）

- ✅ 完成：`voice/stream_audio.py`（抖动缓冲 / 拉取线程 / 可注入 sink）+ TTS 抑制
- 关键验证（真机，模型 `qwen3-omni-flash`，音色 `Cherry`）：
  - 流式请求返回 `delta.audio.data`：25 片 / 376320 字节 / 24000Hz，7.84 秒音频
  - `PcmPlayer` 全部写入 sink，无尾音丢失；正文 43 字，无 `{动作}` / 无 `【指令】`
- 兜底：原生语音若未返回音频 → 自动退回到千问 TTS 念正文，状态栏提示原因
- 代码：见 `ai/protocols/openai_chat.py::_apply_audio` / `stream_events`；
  `ai/chat.py::_ChatWorker._run_stream` / `_on_reply`；
  `voice/stream_audio.py`；`config.NATIVE_VOICE_PROMPT`。

### 14.6 能力位只声明"真的实现了"的（值得记住的取舍）

模型数据里 `deepseek-v4-flash` / Claude 全系都写着 `attachment: true`，但
**OpenAI 兼容的图像输入（content parts）与 Anthropic 的图像块都还没实现**。
所以传输级 `CAP_ATTACHMENTS` **不开** —— 界面不显示 `[识图]`，发图走"当前后端不支持"的提示。

> 宁可少显示一个能力，也不要"界面说支持、一发就报错"。
> 这正是**两级能力位**存在的意义：模型级如实声明（供将来实现），传输级只放行已实现的。

### 14.7 新增守卫

`tests/test_providers.py::[V10]`：可选性 / 传图门控（provider 级 vs 模型级）/ 思考三态
（含 `budget_tokens` 的 `min` 下限）/ 音频声明位 / 成本文本。
另：V5 改为断言"思考走请求参数、模型不因思考而变"；`test_maid_loop.py` 改为断言
**清单来自注册表**且旧模型 id 不回流。

### 14.8 未做（诚实清单）

- OpenAI 兼容 / Anthropic 的图像输入 —— 做完才该开 `[识图]`
- `HISTORY_WINDOW`（20 轮）换成 token 预算 —— 风险较高，先不动
- 从端点 URL 探测模型列表（`/v1/models`）—— 现在靠手填
- 「语音提供方」与「对话提供方」的正式解耦（§4.3）—— 只解了取 Key 来源
  → **2.26.0 已解**（§十五）。剩下"把 TTS 抽成注册表"仍未做，理由见 §4.3

---

## 十五、落地记录（实机校准 + TTS 目录化，2.26.0）

> 这一期的所有结论都来自**用真实 Key 打的请求**，不是读文档猜的。
> 证据脚本：`tools/_probe_api.py` / `_probe_tts.py` / `_probe_doc*.py` /
> `_probe_legacy.py` / `_probe_enum.py` / `_tts_e2e.py`（跑完已清理）。

### 15.1 DeepSeek：上一期的清单是错的，实机校准

| 事实 | 上一期（读 models.dev） | 实机（`GET /models` + 真请求） |
|---|---|---|
| 模型清单 | `deepseek-v4-flash` / `deepseek-flash` / `deepseek-v4-pro` / `deepseek-v4-flash-vision-exp` | **只有 `deepseek-flash` 与 `deepseek-v4-pro`** |
| `reasoning_effort` | 标注"**未验证**"，靠 risk 隔离 | **已验证**：非法值 422 并回读权威取值集 `none, minimal, low, medium, high, xhigh, max` |
| 旧模型 id | "已下架" | 仍可调用，但**被服务端别名**到 `deepseek-flash`（响应 `model` 字段会变） |
| 关思考 | 省略字段（推断） | 省略即不推理（实测：无该字段时 usage 无 `reasoning_tokens`，`prompt_tokens` 5；带上后 31 并出现） |

**两个直接后果**：

1. `deepseek-v4-flash` / `…-vision-exp` 是**百炼侧的名字**，在官方 API 上不存在——
   照抄 models.dev 会得到一个"选了就报错"的清单。教训：**榜单数据不能替代该端点自己的 `/models`**。
2. 不归一化旧 id，`perf.log` 会记下"跑的是 `deepseek-chat`"，而实际跑的是 `deepseek-flash`。
   加了 `LEGACY_MODEL_ALIASES` + 读时归一化（`credentials_store.get_model`）。
   注意 `deepseek-reasoner` 别名到的是 **flash 而非 pro**——凭直觉写会写错。

**教训（方法论）**：`/models` 是免费且权威的，任何"接入任意厂商"的功能都该先打它。
错误信息也常是权威来源——这次两个模型的合法取值集就是从 422 的报错文本里读出来的。

### 15.2 TTS：从「写死 1 模型 + 2 音色」到「实时目录 9 模型 + 48 音色」

**问题**：`ai/qwen.py` 写死 `TTS_MODEL = "qwen3-tts-flash"` 与 `VOICES = ["Chelsie","Cherry"]`，
实际可用 9 × 48，硬编码只覆盖 4%；且文档新增音色必须改代码。

**数据源取证**（`_probe_doc.py`）：

- `help.aliyun.com/zh/model-studio/qwen-tts-voice-list` 是**服务端渲染的真 `<table>`**
  （实测 234KB HTML / 768 个 `<td>` / `data = 0 处`），**不需要 JS、不需要浏览器**。
  这一点必须先证实——否则"爬文档"就只是把脆弱留在了运行时。
- 文档里**两张同构表**（4 列：`voice参数 | 详情 | 支持语种 | 支持模型`）：
  **实时**（WebSocket）与**非实时**（HTTP）。本模块只取非实时。
  实证支撑：用同一 HTTP 端点调实时模型直接 400
  （`current user api does not support http call`）。

**只取非实时表的实现要点**：不能先过滤 realtime 再判断"这是不是实时表"——
那样实时表会变成空集、判定退化成死代码。正确顺序是
① 用**含** realtime 的原始 id 集合判断表格归属，② 再用**剔除** realtime/vc/vd 的清单建音色。

**音色可用性随模型变化**（这是"必须按模型过滤"的实证）：

| 模型 | 可用音色数 |
|---|---|
| `qwen3-tts-flash` | **48** |
| `qwen3-tts-instruct-flash` | 24 |
| `qwen3-tts-flash-2025-09-18` | 17 |
| `qwen-tts` / `qwen-tts-2025-04-10` | **4** |

**反向验证**：探针里 `Anna` 在 `qwen3-tts-flash` 上返回
`Invalid voice specified…`，而文档的非实时表里**确实没有 Anna**（它只在实时表）——
文档声明与实机行为一致，过滤是对的。这条已固化成守卫 `[T3]`。

### 15.3 限流：一次真实故障驱动的重试

实机连续快速合成 7 句时**第 7 句失败**，静置后同参数立刻成功 → 百炼对密集调用限流。
后果不轻：`synth_sentence` 返回 None → `voice/tts.py` 回退本地 SAPI →
用户听到"女仆忽然变成 Windows 机器人音"。故补**一次有界重试**
（仅 `408/425/429/5xx` 与网络异常；400 这类永久错误立即放弃，不白等）。
守卫 `[T6]`。

### 15.4 落地清单

| 类型 | 文件 |
|---|---|
| 新增 | `voice/tts_catalog.py`（抓取 + 解析 + 缓存 + 快照）、`voice/tts_catalog.snapshot.json`、`tools/gen_tts_snapshot.py`（维护者工具）、`tests/test_tts_catalog.py` |
| 修改 | `ai/qwen.py`（模型/音色数据化 + 限流重试）、`ai/providers.py`（DeepSeek 清单 + `LEGACY_MODEL_ALIASES`）、`ai/credentials_store.py`（`tts/*`）、`voice/tts.py`（model 透传 + 缓存键含模型 + 去掉重复常量）、`pet/settings_dialog.py`（语音页重做）、`pet/modes.py`、`pet/bubble.py`、`config.py`（2.26.0）、`CHANGELOG.md`、`README.md` |

### 15.5 实机产物

`logs/tts_test/*.wav`：6 组「模型 × 音色」真实合成（`qwen3-tts-flash` ×
Chelsie/Momo/Vivian/Ethan/Dylan，`qwen3-tts-instruct-flash` × Serena），
每句 4~5 秒，可直接试听对比音色差异。

### 15.6 仍未做

- **试听用的是合成而非官方示例音频**：文档的非实时表**没有** `<audio>` 示例
  （只有实时表里 5 个音色有），所以只能现合成。若将来需要"零成本试听"，
  得从实时表那 5 个样本里取（覆盖面太小，暂不做）。
- 把 TTS 抽成可插拔注册表（§4.3 说明为什么不抽）
- 云端 TTS 仍只支持百炼一家

---

## 十六、落地记录（P9 原生语音收尾，2.27.0）

### 16.1 实机结论：原生语音**只能走流式**

用百炼 `qwen3-omni-flash` 实测：

| 维度 | 结论 |
|---|---|
| 请求 | 必须 `stream: true`，且声明 `modalities: ["text","audio"]` + `audio: {voice, format:"pcm16"}` |
| 音频位置 | `choices[0].delta.audio.data`，base64 |
| 格式 | **裸 PCM16，无 RIFF 头**（首片前 8 字节 `b'\xfe\xff\xfd\xff\xfd\xff\xfe\xff'`，不是 `b'RIFF'`） |
| 采样率 | 24000 Hz，单声道（不支持自定义） |
| 非流式 | **直接不给 audio 字段**，但 `usage` 照样有 `audio_tokens` —— 会白花钱，所以适配器直接拒绝 |

> 这些事实来自 `logs/_probe_native_audio.txt`（2026-09-20）：
> 43 字正文，25 片音频，376320 字节，7.84 秒，全部进 sink；
> 前置约束有效，正文里无 `{动作}` / 无 `【指令】`。

### 16.2 为什么把播放管线拆三层

`voice/stream_audio.py` 拆成：

1. `PcmBuffer`：纯标准库的字节环形缓冲（线程安全，可离线断言）
2. `SoundSink`：只有它依赖 `sounddevice` + numpy（真声卡）
3. `PcmPlayer`：把缓冲喂给 sink 的拉取线程 + 状态机

这样 CI / 离屏 / 无扬声器环境也能跑单测，不用等“有机器愿意出声”。

### 16.3 原生语音 vs 外部 TTS 的边界

| 场景 | 行为 |
|---|---|
| 用户选「模型原生语音」且模型支持 + 后端走流式 | **不调用**千问 TTS，模型自己出音频，文本进气泡 |
| 模型/后端不支持 | 设置页提示"换一个 Omni 模型" |
| 运行时模型**说支持**但服务端没返回音频 | 退回到千问 TTS 念正文，状态栏提示原因 |
| 预设台词 / 播报 | 仍走千问 TTS（这些不是对话轮，不让 Omni 念） |

### 16.4 音频无法事后剥离 → 只能前置于生成

外部 TTS 可以对文本任意剥离标签。原生语音中，文本和音频是**同源生成**的：
模型一旦生成了 `{动作}`，语音里也会念出“动作 微笑”。
因此新增 `config.NATIVE_VOICE_PROMPT` 作为硬约束：

- 口语短句（≤60 字）
- **不得**输出 `{动作}` / `【指令】`
- **不得**输出括号旁白 / 星号动作 / emoji

> 这是**功能性硬约束**，措辞不可精简。守卫见 `tests/test_audio_stream.py::[A10]`。

### 16.5 目录缓存的字段级 fallback

运行时缓存 `voice/voice_cache/tts_catalog.json` 可能由旧版本写入，不含 `omni` 音色表。
如果整份缓存替换快照，原生音色表会**整块变空**。
所以在 `tts_catalog.catalog()` 里加了字段级 fallback：

```python
if cache 有 voices 但缺 omni:
    从 snapshot 读取 omni / omni_url 并入
    source = "cache+snapshot"
```

UI 状态行会直接显示 `来源：cache+snapshot`（实测截图已验证）。

### 16.6 落地清单

| 类型 | 文件 |
|---|---|
| 新增 | `voice/stream_audio.py`、`tests/test_audio_stream.py`、`tools/_probe_native_audio.py`（用完删） |
| 修改 | `ai/protocols/openai_chat.py`（`stream_events` 解析 audio / `_build_body` 请求 audio）、`ai/protocols/base.py`（`KIND_AUDIO`）、`ai/chat.py`（native 路径 / 静音兜底 / 注入 `NATIVE_VOICE_PROMPT`）、`voice/tts_catalog.py`（字段级 fallback）、`ai/qwen.py`（`COMPAT_BASE_URL` 修正）、`pet/settings_dialog.py`（native 引擎 / 音色下拉 / tts_model+audio_voice 键闭合）、`config.py`（`NATIVE_VOICE_PROMPT` + 版本 2.27.0）、`CHANGELOG.md`、`DESIGN_AI_PROVIDERS.md` |
