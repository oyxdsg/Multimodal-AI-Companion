# 桌宠插件 API（v1）

> 面向第三方插件作者。宿主是 `plugin/` 包；**公共契约是 `plugin.api`（行为接口）
> 与 `plugin.contracts`（数据类型）**。设计背景见 [`../DESIGN_OPTIONAL.md`](design/DESIGN_OPTIONAL.md)。

## 一、总览

桌宠本体是**插件宿主**。它自己提供每个扩展点的**内置最小实现**，
从而"一个插件都不装"也能完整运行；插件只做增强，装不装由用户决定。

- 宿主启动时发现插件 → 调用其入口 `register(host)` → 插件用 `host.add_*` 注册实现。
- 宿主按**扩展点 + 优先级**选取实现。优先级：**目录插件 > pip 插件 > 内置**。
- **单个插件失败绝不影响宿主或其它插件**（解析 / import / 注册全程隔离）。

## 二、扩展点

| type | 用途 | 宿主在哪用 | 内置默认 |
|---|---|---|---|
| `ai-provider` | AI 后端（供应商 + 协议适配器） | AI 对话 | 官方 API / 千问 / 自定义 |
| `wiki-knowledge` | 游戏模式知识注入（事件驱动） | 游戏联动 | 不注入（大模型自答） |
| `knowledge-qa` | **主动知识问答**（提问驱动，2.31.0） | 聊天 / 语音提问 | 不注入 |
| `assets` | 动画素材 / 角色形象 | 动画系统 | 静态图（大肥鱼待机帧） |
| `tts` / `stt` | 语音合成 / 识别（**预留**） | 语音 | — |

### 2.1 `wiki-knowledge`

```python
class WikiKnowledgePlugin(Protocol):
    def know(self, events_text, *, session_key="", max_chars=480):
        """返回注入 prompt 的知识文本；None / "" 表示不注入。"""
```

宿主只做"给事件文本 + 预算，换注入文本"，**不知道 wiki 的存在**。
实体提取、检索、提炼、过滤、裁剪、去重都由插件内部完成。

### 2.2 `assets`

```python
class AssetPlugin(Protocol):
    def actions(self):
        """返回 {动作key: [帧, ...]}；None 表示不提供动作。"""
    def static_figure(self):
        """返回静态兜底图路径；None 用宿主默认。"""
```

可以只提供**部分**动作 key，其余由宿主用静态图打底。

### 2.3 `ai-provider`

通过 `host.add_ai_provider(profile=..., adapter_factory=None)` 注册。
`profile` 字段对齐 `ai.providers.ProviderProfile`（`id` / `label` / `protocol` /
`api` / `models` / `caps` …）；`protocol` 用字符串（`"openai"` / `"anthropic"` /
`"deepseek-web"` 等）。自带协议时再给 `adapter_factory(provider_id, profile)`。

### 2.4 `knowledge-qa`（主动知识问答 · 2.31.0）

与 `wiki-knowledge` 的分工 —— **都是"要知识"，但触发方式不同**：

| | `wiki-knowledge` | `knowledge-qa` |
|---|---|---|
| 宿主喂什么 | **游戏事件文本**（"挖掘:2 橡木原木…"） | **用户的自然语言提问** |
| 宿主在哪调 | 游戏联动（事件聚合后） | 聊天 / 语音提问（每轮对话） |
| 语义 | 被动注入、事件驱动 | 主动检索、提问驱动 |

```python
class KnowledgeQAPlugin(Protocol):
    def ask(self, question, *, session_key="", max_chars=1200):
        """返回可注入 prompt 的知识上下文；None / "" 表示无可用知识。"""
```

**插件只回"检索到的上下文"，不生成回答** —— 回答始终由主对话 AI 写，
这样人格与口吻统一，也省一次 LLM 调用。插件内部可以用 `host.llm()` 自行做
多步检索 / 重排 / 压缩（Agentic）。

宿主侧的保证：

- **未装插件 → 不注入**，行为与没有这个功能时完全一致（一等模式，不是残缺模式）
- **插件抛异常 → 当作"没有知识"**，绝不影响对话（宿主统一 `try/except` 兜底）
- 注入总量由 `config.KNOWLEDGE_QA_MAX_CHARS` 约束，可整体关闭（`KNOWLEDGE_QA_ENABLED`）

> 同扩展点多个实现按优先级取最高。实测例子：`agentic-rag`（多路召回重型版，`priority=20`）
> 与 `wiki-local`（轻量词法版，`priority=10`）同时安装时，前者胜出。

## 三、插件清单 `plugin.json`

放在插件目录根（目录插件），或作为模块内 `MANIFEST` 字典（pip / 内置插件）。

```json
{
  "id": "my-plugin",
  "name": "我的插件",
  "version": "1.0.0",
  "api_version": 1,
  "type": ["wiki-knowledge"],
  "entry": "plugin:register",
  "requires": ["numpy"],
  "priority": 0,
  "selectable": true,
  "description": "一句话说明"
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 唯一标识（小写连字符） |
| `type` | ✅ | 扩展点数组，见第二节 |
| `entry` | ✅ | 入口，`文件名:函数名`（目录插件）；pip 插件为 `模块:函数名` |
| `api_version` | ✅ | 主机 API 版本，当前 `1`；不等则拒绝加载 |
| `name` / `version` / `description` | | 展示用 |
| `requires` | | 依赖的 Python 包名（宿主只提示、不代装） |
| `priority` | | 同来源内的排序，越大越优先（默认 0） |
| `selectable` | | 是否在设置界面出现开关（默认 true） |

## 四、入口 `register(host)`

插件入口是一个函数 `register(host)`，`host` 为 `plugin.api.HostAPI`：

```python
def register(host):
    host.add_wiki_knowledge(MyWiki())
    host.log("已注册")
```

`HostAPI`：

| 方法 | 说明 |
|---|---|
| `add_ai_provider(*, profile, adapter_factory=None)` | 注册 AI 供应商 |
| `add_wiki_knowledge(impl)` | 注册知识实现（提供 `know`） |
| `add_knowledge_qa(impl)` | 注册主动知识问答实现（提供 `ask`） |
| `add_assets(impl)` | 注册素材实现（提供 `actions` / `static_figure`） |
| `llm(messages, *, system=None, purpose="", thinking=False)` | **借一次宿主的 LLM 调用**（见下） |
| `log(msg)` | 写一行插件日志（落 `logs/plugin.log`） |
| `api_version` | 宿主 API 版本 |

**`llm()` —— 插件需要模型能力时用它**（2.31.0）：

```python
def register(host):
    out = host.llm([{"role": "user", "content": "把这段维基正文压成 3 条要点：…"}],
                   system="你是信息压缩助手", purpose="rag_refine")
```

- 走宿主的**辅助调用**通道（跟随「辅助调用后端」设置，未设置则跟随主对话后端），
  **插件不要自己读宿主凭据、也不要 `import ai.*`**
- `purpose` 是会话分组键：网页版后端下按它复用**独立持久会话**，不污染主对话历史；
  留空按插件 id 分组
- **失败一律返回 `""`**（不抛异常）—— 插件自己兜底，别让模型问题变成插件报错

注册是**暂存后合并**的：`register()` 中途抛异常，该插件已做的注册会被丢弃。

## 五、两种接入形态

### 5.1 目录包（资源型，推荐）

```
plugins/<id>/
├── plugin.json
├── plugin.py
└── …（自带资源）
```

放进 `desktop-pet/plugins/` 或 `%LOCALAPPDATA%\DesktopPet\plugins\`，重启生效。

### 5.2 pip 包（代码型）

`pyproject.toml`：

```toml
[project.entry-points."deskpet.plugins"]
my-plugin = "my_pkg:register"
```

被导入的模块（`my_pkg`）需暴露 `MANIFEST = {...}` 与 `register(host)`；
`pip install` 后自动发现。

## 六、命名约定

| 项 | 约定 | 例 |
|---|---|---|
| 插件 id | 小写连字符 | `deepseek-web` |
| 目录名 | 与 id 同名 | `deepseek-web/` |
| pip 包名 | `deskpet-plugin-<id>` | `deskpet-plugin-wiki-local` |
| Python 模块 | `deskpet_plugin_<id>` | `deskpet_plugin_wiki_local` |

## 七、容错与生命周期

- 加载顺序：解析清单 → 同 id 去重（高优先级胜）→ 逐个注册。
- 任一步失败：记 `logs/plugin.log` 与 `--doctor`，**宿主照常启动**。
- 补装即生效：放入/安装后重启，无需改代码或重装本体。
- 用 `python main.py --doctor` 查看：已加载插件、被隔离的失败、各扩展点最终实现。

## 八、示例

最小骨架见 [`example-plugin/`](example-plugin/)（复制即可用）：含 `wiki-knowledge` +
`knowledge-qa` 两个实现，以及 `host.llm()` 的用法；跑法与改动要点见
[其 README](example-plugin/README.md)。

```bash
cp -r desktop-pet/docs/example-plugin desktop-pet/plugins/my-plugin
python desktop-pet/main.py --doctor      # 确认加载与扩展点归属
```

## 九、安全

插件是**可执行代码**，在桌宠主进程内运行，宿主**不做沙箱**。
请只安装你信任来源的插件。

## 十、公共契约：`plugin.contracts`（数据类型）

插件**只需**依赖两个模块：

- `plugin.api` —— 行为接口（`HostAPI`、扩展点 Protocol、`API_VERSION`）。
- `plugin.contracts` —— 数据类型与常量（`ProviderProfile` / `ModelInfo` / `PROTO_*` /
  `CAP_*` / `REASONING_*` / `ACTION_KEYS` / `ProviderError`）。

**不要 import 主体内部模块**（`ai.*` / `pet.*` / `game.*` / `core.*`）——契约模块是
单一事实来源，宿主自己也从它导入（`ai.providers` / `core.action_key` 与之对齐，
有测试守卫 `[P10]` 防漂移）。

`ai-provider` 插件用 `contracts.ProviderProfile` / `contracts.ModelInfo` 声明供应商；
`assets` 插件的 `actions()` 键取自 `contracts.ACTION_KEYS`。

## 十一、版本与兼容

- `API_VERSION`（当前 `1`）为**主版本**：插件在 `plugin.json` 声明；不匹配则拒绝加载。
- **只增不改**：新增能力以**可选方法 / 可选字段**追加，不破坏旧插件。
- 宿主忽略不认识的 `type`（前向兼容）；插件应容忍宿主新增字段。
- 破坏性变更 → 升 `API_VERSION`，并在本文件记录迁移说明。

## 十二、发布与分发

- **目录包**：`plugins/<id>/`（`plugin.json` + 入口 + 资源），zip 解压即用。
- **pip 包**：`entry_points` 组 `deskpet.plugins`，`pip install` 后自动发现。
- 建议插件**独立仓库**，命名 `deskpet-plugin-<id>`；依赖在 `plugin.json.requires` 声明
  （宿主只提示、不代装）。
- **自测**：`python main.py --doctor` 查看是否被发现 / 注册、各扩展点最终实现；
  加载失败原因见 `logs/plugin.log`。
- **禁用**：设置 →「扩展 → 插件」勾选启停（写 `plugins/disabled`，重启生效）。
