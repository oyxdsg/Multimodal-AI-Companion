# DESIGN_PERSONALIZE.md — 个性化设置页（方案稿，**未实现**）

> 状态：**仅设计，无代码改动**。占位版本号：无（不实现不占 CHANGELOG）。
> 关联：`DESIGN_LOOP_DEEPENED.md §3.4`（人设按后端分流）、`API_PROMPT_TEST_REPORT.md`（功能性硬约束不可压）。

## 一、目标与来源

来源：WorkBuddy 桌面端「设置 → 个性化」面板。它的字段构成是：

| 分组 | 字段 |
|---|---|
| 顶栏 | 回复风格（下拉）、加载欢迎语（开关）、展示文件变更过程详情（开关） |
| 自由文本 | 自定义指令（0/1500 计数器） |
| 称呼与身份 | AI 对用户的称呼 / AI 的名字 / AI 的人设描述 |

真正值得抄的是三点，与"好不好看"无关：

1. **扁平单页 + 分组小标题**（左栏才分组），不是把十几个配置项平铺成等权导航。
2. **「称呼与身份」三元组**——把散落的"人格相关配置"收成一个语义单元。
3. **自定义指令**——用户能加"对后续所有任务生效"的规则，而不用去改人设文件。

桌宠的目标：补齐「主人称呼」「自定义指令」「说话风格」三个**当前完全不存在**的用户级配置，
并统一落在 system 提示词的同一处。

## 二、现状盘点（代码事实）

| 概念 | WorkBuddy | 桌宠现状 | 存储键 | 入口 |
|---|---|---|---|---|
| AI 的名字 | 有 | **有** | `pet_name_<persona>`（按人格独立） | 设置→宠物 |
| 人设 / 人格 | 有 | **有**（2 个人格，切人格即换 prompt 文件） | `persona` | 设置→宠物 |
| 对用户的称呼 | 有 | **无** | — | — |
| 自定义指令 | 有 | **无** | — | — |
| 说话风格 | 有 | **无**（写死在 `AI_MODES[i]["prompt"]`） | — | — |

**关于"称呼"的现状容易误判**：`game_player_name` 不是称呼，是 *Minecraft 游戏角色名*，
且只在游戏链路注入（`game/processor.py:135`）。闲聊时 AI 手里没有任何"该怎么称呼用户"的信息，
只能吃人设文件里的硬编码——女仆是"主人"、御坂是"伙伴"（`PERSONAS[i]["game_owner"]`）。

## 三、字段设计

| 字段 | QSettings key | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| 主人称呼 | `personalize_owner_title` | str，≤20 | 空 | 空则回退 `PERSONAS[i]["game_owner"]` |
| 说话风格 | `personalize_speech_style` | 枚举 key | `"follow"` | `follow` / `terse` / `clingy` |
| 自定义指令 | `personalize_directive` | str，≤1500 | 空 | 空则整段不注入（不留空标题） |

### 3.1 说话风格

风格文案放 `config.PERSONALIZE_STYLES`，每条约 100–200 字：

- `follow`：跟随人设（默认，**整段不注入**）
- `terse`：简短冷淡——把回复压到 15 字内、少用语气词、不主动追问
- `clingy`：黏人话多——允许 40–60 字、多用撒娇语气词与自称

**风格段必须显式写明"模式规则优先"**：人设/AI_MODES 里有"不超过 30 字"这类硬字数规则，
风格若与之冲突会造成两条 system 指令打架（参考 2.20.0 的教训：功能性硬约束的措辞不能含糊）。

### 3.2 自定义指令（上限 1500 字）

- 注入位置：**人设正文之后、模式段之前**。
- 段落必须带一句**不可被用户覆盖的仲裁句**：
  > 以下为用户的额外要求，在不与上述人设中的格式硬约束冲突时优先遵守。
  > （格式硬约束指：每条回复必须带且只带一个 `{动作}`；女仆指令 `【指令名(参数=值)】` 的书写格式。）

  理由：用户很可能写"不要输出任何标签"，而"每条回复必须带 `{动作}`"是人设的功能性硬约束
  （2.20.0 实测代价极高）。不能让一条用户指令把整条动作链路打断——这是**必须**的护栏，不是可选项。
- 超长处理：按 1500 字截断（UI 侧也限制输入），**不报错**。

## 四、注入点设计（核心结论）

桌宠有三条链路，但 system 组装**只有一个汇聚点**：`config.load_system_prompt`。

| 链路 | 谁组装 system | 谁注入 | 生效时机 |
|---|---|---|---|
| 网页版 · 聊天窗 | `_ChatWorker._system_prompt()` 返回 `opts["prompt"]`（`ai/chat.py:512-520`）；`opts["prompt"]` 由 `settings_dialog` 调 `load_system_prompt` 算出 | `ai/deepseek.py:584` 判 `parent_message_id is None` | **仅会话首条** |
| 网页版 · 游戏/工作/新闻 | `ai/chat_service.py:79` → `load_system_prompt(..., backend=name)` | 同上 | **仅会话首条** |
| 官方 API / 千问 | `ai/chat_service.py:79` + `ai/chat.py:518`（`get_api_prompt`）；`ai/backends/messages.py:43-48` 再拼【当前模式】 | `messages[0]`，每轮重发 | **下一轮立即** |

→ 三条路径最终都经过 `load_system_prompt`，**所以只改这一处**：

```python
def personalize_suffix(owner_title="", speech_style="", user_directive=""):
    """个性化段：主人称呼 + 说话风格 + 自定义指令。三项全空时返回空串。"""

def load_system_prompt(persona=None, pet_name=None, backend=None, personalize=None):
    ...
    return text + (personalize_suffix(**personalize) if personalize else "")
```

三点设计理由：

1. **`personalize=None` 默认不追加**，现有 17 项测试与所有旧调用零改动。
2. **顺序天然正确**：`get_api_prompt` = `load_system_prompt(...)` + 【当前模式】，
   所以拼出来是 人设 → 个性化 → 模式规则，"模式优先"由位置保证，`get_api_prompt` 不用改。
3. **config.py 保持无 Qt 依赖**：`personalize_suffix` 是纯函数，只做拼接与截断；
   QSettings 的读取留在调用方（`ai/chat_service.py` 已有 `self._store`，`ai/chat.py` / `settings_dialog.py` 同理）。
   —— config.py 目前只 import `os/re/sys`，不要为了这个把 PySide6 拖进 config。

## 五、生效时机（必须在 UI 上讲清，不能装作没有）

**网页版与 API 侧行为不同，这是硬事实**：

- 官方 API / 千问：无状态，system 每轮作为 `messages[0]` 重发 → **改完下一轮就生效**。
- 网页版：system 只在会话首条注入 → **改完必须点「开启新会话」**（`ai/chat.py::_new_thread`
  → `ai/client.py::clear_thread_state`），否则旧会话里 AI 完全看不到新指令。

还有一个隐藏坑：`ChatWindow._system_prompt()` 网页版返回的是 **`opts["prompt"]` 这份缓存**，
而 opts 是设置窗保存时算的。所以保存路径里 `_collect_chat_opts["prompt"]` 与
`load_ai_opts["prompt"]` **两处都要带 `personalize` 参数**，只改 QSettings 不改 opts 会拿到旧值。

**建议的 UI 行为**（待决策）：检测到网页版且 system 类字段有变化时，
在保存后提示一句"网页版需开启新会话生效，是否现在开启"，而不是静默保存。

## 六、token 预算（关键不对称）

WorkBuddy 的 1500 字上限对它自己是"每个任务注入一次"；对桌宠的 API/千问后端是**每轮**。

| 后端 | 1500 字自定义指令的代价 |
|---|---|
| 网页版 | 只花一次（首条），可忽略 |
| 官方 API / 千问 | 每轮 +约 1000–1500 token，随轮数线性 |

→ **建议 UI 上按后端给不同的软上限/提示**：API 侧建议 ≤300 字并在文本框下显示"每轮重发，建议精简"。
这是 2.20.0 按后端分离人设（省 34%/26%）同一逻辑的延续——不能因为 WorkBuddy 写了 1500 就照抄。

## 七、冲突与风险清单

| 风险 | 说明 | 处置 |
|---|---|---|
| 自定义指令打断动作链路 | 用户写"不要输出标签"会破坏 `{动作}` 硬约束 | §3.2 的仲裁句（**必做**） |
| 说话风格与人设字数硬规则打架 | "不超过 30 字" vs "黏人话多" | 风格段写明模式优先 + 风格文案内自带字数口径 |
| 网页版静默不生效 | 用户以为改了没生效 | §5 的保存提示 |
| opts 缓存旧值 | 见 §5 隐藏坑 | 两处 `prompt` 都传 `personalize` |
| 游戏链路玩家名优先级 | `processor.py:135` 目前是 `player or game_player_name or game_owner` | 插成 `player or owner_title or game_player_name or game_owner` |

## 八、改动清单（实施时）

| # | 文件 | 动作 | 内容 |
|---|---|---|---|
| 1 | `config.py` | 新增 | `PERSONALIZE_STYLES`、`personalize_suffix()`；`load_system_prompt` 增 `personalize` 参数 |
| 2 | `pet/settings_dialog.py` | 改 | 新增「个性化」页；`load_ai_opts` / `save_ai_opts` / `_collect_chat_opts` 各加 3 键，两处 `prompt` 传 `personalize` |
| 3 | `ai/chat_service.py` | 改 | 从 `self._store` 读 3 键，`load_system_prompt` 两处调用（第 73、79 行）传入 |
| 4 | `ai/chat.py` | 改 | 人格切换补丁（第 1026 行）传 `personalize` |
| 5 | `game/processor.py` | 改 | 第 135 行玩家名优先级插入 `owner_title` |
| 6 | `tests/test_chat_unit.py` | 新增 | `[U1e]`：空 personalize 零变化 / 顺序在【当前模式】之前 / 截断生效 / 仲裁句存在 |
| 7 | `tools/probe_prompt_split.py` | 改 | 打印 API 各轮 system 是否含个性化段 |
| 8 | `CHANGELOG.md` / `README.md` / `DESIGN_LOOP_DEEPENED.md §3.4` | 改 | 功能清单与版本说明 |

**导航分组重排**（可选，独立于上面）：把左栏 12 项按
`宠物（皮肤/画面/台词/环境/新闻）/ 对话（基础/对话/语音/语音输入）/ 个性化 / 联动（工作/游戏）`
四组分，需要把 `_build_ui` 的 `nav_names` 列表改成 `[(组名, [页名...])]` 结构。

## 九、验收口径

- `personalize=None` 时 `load_system_prompt` 输出**逐字节等于**改动前（回归保护）。
- 非空时：个性化段出现在【当前模式】段**之前**；三点全空时不产生任何多余空行。
- 用户写"不要输出任何标签"时，system 里仍带 `{动作}` 硬约束（人工抽查 + 单测断言仲裁句存在）。
- 真机：网页版改指令后**不**开新会话 → AI 行为不变；开新会话 → 生效。
  API 侧改完下一轮即生效。（用 `tools/probe_prompt_split.py` 看组装结果，不必真调 API。）

## 十、明确不做

- 不做「展示文件变更过程详情」——桌宠没有文件变更流。
- 不做「加载欢迎语」——桌宠已有随机台词（设置→台词）与思考态气泡，语义重叠。
- 不把「自定义指令」做成可编辑整段人设——整段人设归人格文件管，用户只加增量。
