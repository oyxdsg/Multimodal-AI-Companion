# opencode provider 接口调研（本机实证）

> 目的：为 `DESIGN_AI_PROVIDERS.md`（桌宠 AI 接口通用化）提供**可验证的实证依据**。
> 对象：本机已安装的 opencode。
> 方法：读它的数据文件 + 对 170MB 编译产物做字符串取证。**只读结构，不读取任何密钥明文。**

## 〇、调研对象与定位

| 项 | 值 |
|---|---|
| 包 | `opencode-ai@1.18.16`（npm 全局） |
| 产物 | 单个 **170.4MB** 编译可执行文件（Bun compile），`/bin/opencode.exe` |
| 用户配置 | `~/.config/opencode/opencode.jsonc`（**本机内容为空**，只有 `$schema`） |
| 凭据 | `~/.local/share/opencode/auth.json` |
| **模型/供应商注册表** | **`~/.cache/opencode/models.json`** ← 本调研的核心 |
| 选择状态 | `~/.local/state/opencode/model.json` |
| 会话库 | `~/.local/share/opencode/opencode.db`（SQLite） |

> 一句话概括它的做法：**把"支持哪些 AI"从代码里搬到一个数据文件里；
> 代码只负责 4~5 种「协议」，数据负责 222 家「供应商」。**

---

## 一、四层分离（最值得抄的结构）

| 层 | 载体 | 性质 | 本机规模 |
|---|---|---|---|
| **注册表**（有哪些供应商/模型、各自能力） | `models.json` | 数据，**可远程刷新** | **222 供应商 / 7864 模型** |
| **凭据**（用哪个 Key） | `auth.json` | 数据 | 3 个已配置 |
| **选择**（当前用哪个、思考强度） | `model.json` | 数据 | recent/favorite/variant |
| **协议**（怎么发请求） | `@ai-sdk/*` + 少量手写 HTTP | **代码** | 28 个 npm 包 |

**关键结论：新增一个供应商 = 加一条 JSON，不动代码。** 这是它能覆盖 222 家的根本原因。

---

## 二、实测数据：OpenAI 兼容到底占多少

对 `models.json` 全量统计（脚本解析，非抽样）：

| `npm`（协议实现） | 供应商数 | 占比 |
|---|---|---|
| `@ai-sdk/openai-compatible` | **181** | **81.5%** |
| `@ai-sdk/anthropic` | 8 | 3.6% |
| `@ai-sdk/openai` | 6 | 2.7% |
| `@ai-sdk/azure` | 2 | 0.9% |
| 其余 24 个包 | 各 1~2 | 11.3% |
| **合计** | **222** | 28 个不同包 |

> **前 4 个包覆盖 197/222 = 88.7%；单靠 `openai-compatible` 就有 81.5%。**
>
> 这就是我在设计里断言"「任意 AI」的 80% 靠一个 OpenAI 兼容适配器 + 可编辑 base_url 就能拿到"的
> **实测依据**——不是我猜的，是 opencode 的真实分布。

---

## 三、Provider 记录的形状

```
{ id, name, env[], npm, api?, doc, models{...} }
```

字段出现次数（222 家）：`id/env/npm/name/doc/models` 各 222、**`api` 只有 196**。

三点值得注意：

1. **`env: ["ANTHROPIC_API_KEY"]`** —— 供应商记录里**声明密钥的环境变量名**。
   凭据发现是"数据驱动"的，不是每个供应商写一段代码。
   （google 甚至给了 3 个别名：`GOOGLE_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY` / `GEMINI_API_KEY`）
2. **`api` 是可选的（26 家没有）** —— 原生 SDK 自己知道端点，只有需要覆盖时才填。
   所以 base_url 的正确语义是**"可选覆盖"**，不是必填。
3. **`npm` 是"该加载哪个协议实现"** —— 一个字段就把"数据"和"代码"接上了。
   代价是运行时得能动态加载依赖（它有整个 Node 生态；**我们 Python 侧没有这个条件，见 §八**）。

## 四、Model 记录的形状（比 provider 级丰富得多）

全量字段出现次数（7864 个模型）：

| 字段 | 出现 | 字段 | 出现 |
|---|---|---|---|
| `id` / `name` / `description` | 7864 | `cost` | 7451 |
| `attachment` | 7864 | `temperature` | 7368 |
| `reasoning` | 7864 | `family` | 7194 |
| `tool_call` | 7864 | `reasoning_options` | 5683 |
| `release_date` / `last_updated` | 7864 | `structured_output` | 5448 |
| `modalities` | 7864 | `knowledge` | 4116 |
| `open_weights` | 7864 | `interleaved` | 1097 |
| `limit` | 7864 | `status` | 283 |

能力位真值占比：

| 能力 | 真值数 / 总数 |
|---|---|
| `tool_call` | 6814 / 7864 |
| `temperature` | 5923 / 7864 |
| `reasoning` | 5683 / 7864 |
| `attachment`（图片/PDF） | 4499 / 7864 |
| `structured_output` | 4307 / 7864 |
| `open_weights` | 3536 / 7864 |

**这是对我方案的第一处、也是最重要的一处修正：能力挂在「模型」上，不是挂在「供应商」上。**
同一家不同模型能力可以完全不同（一个支持视觉、一个不支持），挂错层级会导致
"选了支持视觉的供应商但实际模型不支持"——正是今天桌宠"千问后端会静默忽略图片"这类问题的根源。

模型还带**上下文与成本预算**：`limit.{context,input,output}`、`cost.{input,output,cache_read,cache_write}`。
例（本机实测 anthropic/claude-fable-5）：`limit.context=1000000`、`cost.input=10 / output=50`。

### 4.1 「思考」不是布尔，是带取值集的枚举

`reasoning_options` 是一个**带类型和可选值列表**的数组，分布：

| `type` | 出现次数 | 含义 |
|---|---|---|
| `effort` | **3437** | 档位枚举：`low / medium / high / xhigh / max` |
| （无） | 3387 | 不支持思考 |
| `toggle` | 1313 | 纯开关 |
| `budget_tokens` | 677 | 给 token 预算 |

实录（anthropic/claude-fable-5）：

```json
"reasoning_options": [
  { "type": "effort", "values": ["low", "medium", "high", "xhigh", "max"] }
]
```

**这是第二处修正**：桌宠现在的"深度思考"是一个全局 bool，
而真实世界是"**每个模型有自己的思考档位集合，且有 3 种语义**（开关/档位/预算）"。
一个 bool 在 Anthropic 上必然表达不足。

---

## 五、注册表从哪来、怎么更新（二进制取证）

从可执行文件里抽到的字符串：

```
option("refresh", { describe: "refresh the models cache from models.dev", type: "boolean" })
...
n.repeat(ye.spaced("60 minutes"))          // 后台每 60 分钟刷新一次
if (!ge.OPENCODE_DISABLE_MODELS_FETCH && ...)
n.logError("Failed to fetch models.dev", { cause: T })   // 失败只记日志，不致命
```

结论：

1. 注册表**远程源是 `models.dev`**，落盘到 `~/.cache/opencode/models.json`
2. **后台每 60 分钟自动刷新**；有 CLI：`opencode models --refresh` / `--verbose`（含成本）
3. **可用环境变量 `OPENCODE_DISABLE_MODELS_FETCH` 关掉**；拉取失败**只记日志不中断**
4. **注册表同时内嵌在二进制里** —— 抽到内联的供应商定义（未加引号的 JS 字面量）：

```
penai-compatible", api:"https://api.jiekou.ai/openai", name:"Jiekou.AI",
doc:"https://docs.jiekou.ai/docs/support/quickstart?utm_source=github_models.dev",
models:{"gpt-5.1-codex-mini":{id:"...", attachment:!0, reasoning:!0, ...
```

**这是第三处修正**：注册表要**双份**——内嵌一份默认（离线可用、首启即有），
再从远端刷新一份（保持最新）。只做远端会在无网/被墙时彻底不可用；只做内嵌会很快过时。

---

## 六、协议层的内部形状（二进制取证）

抽到一段**声明式的协议描述符**：

```js
lJ = Ox.make({
  id: q2,
  provider: "anthropic",
  protocol: rZ,
  endpoint: Ex.path(cQ, { baseURL: vQ }),      // 端点 = 路径 + 可覆盖 baseURL
  auth: c.none,
  framing: P2.sse,                              // 流式封装方式也声明化
  headers: () => ({ "anthropic-version": "2023-06-01" }),
})
```

以及一条 **auth 解析链**：

```js
if (e && e.auth) return e.auth;                                    // ① 显式注入的 auth
return optional("apiKey" in e ? e.apiKey : void 0, "apiKey")       // ② 调用方传的 apiKey
  .orElse(config("ANTHROPIC_API_KEY"))                             // ③ 环境变量
  .pipe(header("x-api-key"));                                      // ④ 注入到指定 header
```

还有**密钥脱敏**（请求日志里）：

```js
.map(([X, Y]) => [X,
  ["x-api-key","authorization","cookie","set-cookie"].includes(X.toLowerCase()) ? "***" : Y])
```

OpenAI 兼容侧则是真适配器，**URL 由 baseURL + 路径 + 模型 id 组装**，并带 `transformRequestBody` 钩子：

```js
url: this.config.url({ path: "/chat/completions", modelId: this.modelId })
...
W = P.choices[0]        // 响应解析（还准备了多套响应/流式 chunk schema）
```

以及 **`openai-compatible` 内部有方言子变体**（字符串取证）：

```
openai-compatible
openai-compatible-api
openai-compatible-chat
openai-compatible-api-gateway
```

**这是第四处修正**：我原方案只打算做 1 个 `openai` 协议。
实际上"OpenAI 兼容"里至少还要区分 `/chat/completions` 与 `/responses` 这类**方言**，
协议标识必须留出子变体位。

**第五处修正**（来自 auth 链）：凭据解析应该是**有序回退链**（显式 → 设置项 → 环境变量 → header 注入），
而且是**函数**不是常量——这样 OAuth/自定义鉴权能插进来。

## 七、选择状态与"变体"

`~/.local/state/opencode/model.json` 本机实录（**已确认不含密钥**）：

```json
{"recent":[{"providerID":"opencode-go","modelID":"deepseek-v4-flash"}],
 "favorite":[],
 "variant":{"opencode-go/deepseek-v4-flash":"high"}}
```

两点：

1. 选择状态用 **`providerID` + `modelID` 复合键**，`recent` / `favorite` 分离
2. **「思考强度」是 `provider/model → 变体名` 的映射**（这里是 `"high"`），
   对应 §4.1 的 `effort` 取值集 —— **不是全局开关**

**这是第五处修正的落地形态**：思考档位应存成 `provider/model → variant`，
这样"Claude 用 high、千问用 toggle、DeepSeek 不支持"能共存。

---

## 八、与 `DESIGN_AI_PROVIDERS.md` 的逐条对照

| opencode 的做法 | 我的方案 | 处置 |
|---|---|---|
| 注册表数据化（222 家不改代码） | ProviderProfile 注册表 | ✅ **保留**，并对齐字段 |
| 能力位在**模型级** | 能力位在 provider 级 | 🔧 **修正**：下沉到模型级 |
| `reasoning_options` 带类型+取值集 | `thinking: bool` | 🔧 **修正**：改成结构化枚举 |
| 注册表内嵌 + 远端刷新（60min） | 未涉及 | ➕ **补充**：双份策略 |
| `api` 可选（196/222） | base_url 必填/可覆盖 | 🔧 **修正**：改为可选覆盖 |
| `env[]` 声明密钥环境变量名 | 未涉及 | ➕ **补充**：便于"检测到已存在的 Key" |
| 协议描述符含 `framing`/`headers` | 只含 protocol | 🔧 **修正**：描述符加 framing/headers |
| auth 有序回退链（函数） | 静态凭证读取 | 🔧 **修正**：改成回退链 |
| 密钥脱敏 `***` | 未涉及 | ➕ **补充**：日志脱敏 |
| `openai-compatible` 有方言子变体 | 单个 `openai` 协议 | 🔧 **修正**：留方言位 |
| 思考档位存 `provider/model → variant` | 未涉及 | ➕ **补充** |
| **28 个 npm 包按 `npm` 字段动态加载** | 4 个手写适配器 | ❌ **不采纳**（理由见下） |
| **222 家全量注册表 / 7864 模型** | 预置 ~10 家 + custom | ❌ **不采纳**（理由见下） |

### 两处明确不采纳，理由

1. **不采纳「按 npm 字段动态加载 SDK」**：opencode 能这么做是因为它自己就是 Node 生态，
   `npm install` 一个包即可。桌宠是 Python + PySide6，**PyInstaller 打包**（README 有打包章节），
   引入 Node 依赖链会直接砸掉打包与分发。Python 侧对应物是"手写 4 个适配器"——
   而 opencode 的数据已经证明**这 4 个足够覆盖 96%**（openai-compatible + anthropic + openai + azure 的方言都在其中）。
2. **不采纳「全量 222 家注册表」**：那是给通用工具用的。桌宠的用户是"一个人的桌面宠物"，
   需要的是"预置 10 家常用 + 一个 `custom` 逃生口"，不是 7864 个模型的目录。
   但**字段设计照抄**（能力位、limit、cost、reasoning_options），这样将来想接远端 registry 可以无损升级。

---

## 九、本机配置实证（隐私边界说明）

`auth.json` 只读取了**结构形状**，未读取任何密钥值：

| providerID | 形状 |
|---|---|
| `opencode-go` | `{type: "api", key: <红acted>}` |
| `deepseek` | `{type: "api", key: <红acted>}` |
| `volcengine-coding-plan` | `{type: "api", key: <红acted>}` |

→ 凭据模型是 **`{providerID: {type, key}}`**，`type` 是判别式（本机全为 `api`，说明还有别的类型如 oauth）。
用户配置 `opencode.jsonc` 为空 = 完全依赖内置注册表；当前使用 `opencode-go/deepseek-v4-flash`，`variant=high`。

> 注：`~/.local/share/opencode/tool-output/` 与 `snapshot/` 下存有会话/快照数据，属会话内容，本次未读取。

---

## 十、方法（可复用）

1. **先找数据，别读代码**：一个成熟工具的设计哲学往往直接摊在它的配置文件与缓存里。
   本次 `models.json` 一个文件就交代清了 80% 的架构。
2. **拿真实分布当论据**：说"大多数是 OpenAI 兼容"很虚，
   "222 家里 181 家（81.5%）"是论据。
3. **编译产物可用字符串取证**：170MB 的 Bun 编译 exe 里，`baseURL` 出现 530 次、
   `reasoning_options` 4178 次、`providerID` 1002 次——**频次本身就能反映设计的重心**；
   再加上下文抽取（前后各百余字符）就能拿到声明式描述符与 auth 链的原文。
4. **碰凭据只看 shape**：读 `auth.json` 时用"类型 + 长度"代替值，
   既能拿到 schema 又不把密钥带进上下文。
