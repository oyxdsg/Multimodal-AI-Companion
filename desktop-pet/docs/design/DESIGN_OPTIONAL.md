# 插件化架构方案（v3 · 宿主 + 可选插件）

> 状态：**方案稿（只做设计，未落代码）**
> 定位升级：从「缺失降级」升级为「**插件化**」——本体是**插件宿主**，只提供**接口**；
> DeepSeek 网页版、Wiki 知识（整块）、桌宠动画素材 三者都是**可选插件**，**接入但不必须**。
>
> 用户明确：① 目标是缩小本体；② 素材缺失时延用**大肥鱼待机帧**作静态图；③ 插件是可选项，不是必选项。
>
> 相关：`DESIGN_AI_PROVIDERS.md`（供应商注册表）、`DESIGN_WIKI_REFINE.md`（Wiki 提炼）、`DESIGN_UI_LANGUAGE.md`

---

## 〇、一句话架构

```
本体（Host）＝ 插件宿主 + 每个扩展点的「内置最小实现」
                    ↑ 通过稳定接口注册
插件（Plugin，可选）＝ 网页版后端 / 本地 Wiki 提炼 / 完整动画素材包 …
```

- **本体不含任何插件也能完整运行**（启动、聊天、游戏、静态形象）。
- **插件只增强，不奠基**；装不装、装哪个，由用户决定。
- 宿主对具体插件**零硬依赖**：只认接口，不认 `deepseek` / `wiki_refine` 这些名字。

---

## 一、为什么要插件化（体积实测，动机）

以 **git HEAD 跟踪内容**为准（= 别人 clone 会拉到的）：

| 内容 | 体积 | 占比 | 现状 |
|---|---:|---:|---|
| `desktop-pet/assets/`（16 动作 × ~107 帧，1664 PNG） | **248.0 MB** | 72% | 已跟踪 |
| `素材源/`（绿幕视频 + 抽帧，541 文件） | **87.4 MB** | 26% | 已跟踪 |
| `desktop-pet/nlu/`（NLU 模型 + wiki_refine 模型） | 4.8 MB | 1.4% | 已跟踪 |
| 其余全部代码 + 文档 + wasm | **~2.2 MB** | 0.6% | 已跟踪 |
| **本体合计** | **342.4 MB** | 100% | — |
| （未跟踪）`wiki_data/mcwiki.db` 268MB、`games/` 152MB、`.base_frames/` 65MB | 磁盘 | — | 本机数据 |

**结论**：本体 342MB 中 **335MB（98.5%）是美术素材，代码只有 ~2.2MB**。
插件化后本体目标 **≈ 2.6MB**（代码 + 静态图 0.15MB），体积降 **99.2%**。

三条插件化收益：
1. **体积**：大资源全部外置为插件/资源包。
2. **合规**：逆向模块、派生数据不必随主仓库。
3. **可扩展**：第三方能按接口加自己的后端 / 提炼器 / 角色形象，无需改宿主。

---

## 二、项目结构：主体 + 插件并列

### 2.1 本地开发工作区（各单元并列）

```
<workspace>/                          # 本地工作区：主体与插件并列
├── desktop-pet/                      # ★ 项目主体（Host）—— 开源主仓库
│   ├── plugin/                       插件宿主：接口 + loader + registry + 内置默认
│   │   ├── api.py                     扩展点 Protocol + API 版本
│   │   ├── manifest.py                plugin.json 解析 / 校验
│   │   ├── loader.py                  发现 / 加载 / 隔离 / 生命周期
│   │   ├── registry.py                各扩展点注册表 + 按优先级取用
│   │   └── builtin/                   第一方最小插件
│   │       ├── ai_official.py            官方 API / 千问 / 自定义
│   │       ├── wiki_none.py              不注入（依赖大模型自身知识）
│   │       └── assets_static.py          静态图素材（大肥鱼待机帧）
│   ├── plugins/                       插件投放目录（仅 .gitkeep + README，内容 gitignore）
│   ├── assets/_static/idle.png        本体唯一素材：大肥鱼待机帧（≤150KB）
│   └── …                              核心代码（不含任何插件实现）
│
├── deskpet-plugin-deepseek-web/       插件①：DeepSeek 网页版逆封装（独立仓库）
├── deskpet-plugin-wiki-local/         插件②：本地 Wiki 知识库 + 检索 + 提炼 + 建库（独立仓库）
├── deskpet-plugin-assets-official/    插件③：官方完整动画素材包（独立仓库 / 资源包）
└── deskpet-plugin-…                   将来更多插件（TTS / NLU / 新闻源…）
```

### 2.2 发布形态（主体与插件分离）

- **主体仓库**：不含任何 `plugins/` 内容（`.gitignore` 排除）→ clone ≈ 2.6MB。
- **每个插件**：**独立仓库 + 独立发布**（目录包 zip / pip / Release），彼此不依赖。
- **主 README** 维护"可选插件清单"，指向各插件仓库与安装方法。
- 运行时用户把插件放进 `<install>/desktop-pet/plugins/<id>/`（或 `%LOCALAPPDATA%\DesktopPet\plugins\`）。

### 2.3 插件与主体的契约边界（保证可独立）

- 插件**不 import 主体源码**，只实现宿主暴露的 Protocol；由宿主调用 `register(host_api)` 注入接口对象。
- 插件在 `plugin.json` 声明 `api_version`，宿主校验；接口只增不改。
- 插件自带依赖用 `requires` 自述，宿主只提示不代装。

### 2.4 启动顺序（宿主对插件"尽力而为"，任一失败都不影响启动）

1. 注册**内置插件**（永远可用，保证最小功能闭环）。
2. 扫描内置 `plugin/builtin/`、用户 `plugins/`、以及（可选）已 pip 安装的 entry points。
3. 逐个加载 → 解析 manifest → 按声明类型注册到扩展点；**单插件失败仅记日志并跳过**。
4. 每个扩展点按"优先级"选取**已注册实现**，取不到就用内置默认。

**四大扩展点（本期）**

| 扩展点 | 宿主使用处 | 内置默认 | 可选插件示例 |
|---|---|---|---|
| `ai-provider` | `ai/providers.py` 注册表、`ai/client.py` 工厂 | 官方 API / 千问 / 自定义 | **DeepSeek 网页版** |
| `wiki-knowledge` | `game/processor.py` 的 `{wiki}` 注入 | **不注入**（大模型自答） | **整块 Wiki（本地库+检索+提炼+建库）** |
| `assets` | `pet/skins.py`、`pet/anim.py` | **静态图（大肥鱼待机帧）** | **完整动画素材包 / 角色包** |
| （预留）`tts` / `stt` | `voice/` | 本地 SAPI / 现有引擎 | 未来第三方 |

> 前三个即用户点名的三块；`tts/stt` 作为预留位，本期不实现，但接口留好。

---

## 三、统一插件模型

### 3.1 插件目录布局

```
<plugins_root>/<plugin_id>/
├── plugin.json          # 清单（必需）
├── __init__.py          # 入口（必需，导出 register 或类）
├── …                    # 插件自带资源/代码
```

### 3.2 清单 `plugin.json`

```json
{
  "id": "deepseek-web",
  "name": "DeepSeek 网页版后端",
  "version": "1.0.0",
  "api_version": "1",                 // 宿主插件 API 版本，不匹配则拒绝加载
  "type": ["ai-provider"],            // 可声明多扩展点
  "entry": "__init__:register",       // 入口函数/类
  "requires": ["wasmtime", "playwright"],   // 依赖自述（宿主提示用，不代装）
  "priority": 50,                     // 同扩展点排序（内置 = 0，越大越优先/越靠前）
  "selectable": true,                 // 是否在设置界面出现开关
  "description": "走 chat.deepseek.com 网页内部接口"
}
```

### 3.3 发现与加载

- **内置**：`plugin/builtin/`（随本体，第一方）。
- **用户**：`plugins/<id>/`（仓库内空目录，git 忽略内容）与 `%LOCALAPPDATA%\DesktopPet\plugins\<id>\`（发布版）。
- **pip 插件**：`importlib.metadata.entry_points(group="deskpet.plugins")`（**本期支持**）——第三方把插件打成 pip 包，`pip install` 后宿主自动发现。
- **优先级**：显式目录插件 > pip 插件 > 内置；同 id 冲突时高优先级胜出并记日志。

加载铁律（见第七节）：**任何插件异常都被 `loader` 吞掉并记日志，绝不上抛、绝不阻止宿主启动**。

### 3.4 API 版本与兼容

- 宿主导出 `plugin.api.API_VERSION`；插件在 manifest 声明 `api_version`。
- 不匹配 → 跳过并提示"插件 X 需要宿主 API vN，当前 vM"。
- 接口只增不改（新增可选方法），保证旧插件不碎。

### 3.5 对外开放（第三方插件）

插件 API **本期起对外开放**，第三方可按接口写自己的后端 / 知识源 / 形象包：

- **公共契约**：`plugin/api.py` 是**唯一**对外契约（Protocol + 数据类 + API 版本）；插件**不 import 主体内部模块**。
- **接口参考文档**：新增 `docs/PLUGIN_API.md`——扩展点清单、接口签名、`plugin.json` 字段、生命周期、错误处理、最小示例。
- **示例骨架**：新增 `deskpet-plugin-example`——最小可运行插件，复制即用。
- **两种接入形态**：
  1. **目录包**：解压到 `plugins/<id>/`（适合资源型：素材、知识库）。
  2. **pip 包**：`pip install` 后经 entry_points 自动发现（适合代码型：后端、提炼器）。
- **命名约定**：插件 id 用小写连字符（`deepseek-web`）；pip 包名 `deskpet-plugin-<id>`；Python 模块 `deskpet_plugin_<id>`。
- **安全声明**：插件＝可执行代码、运行在主进程；文档提示"仅安装可信来源"，宿主不做沙箱。

---

## 四、三个扩展点的接口定义

> 接口尽量**薄**：只描述"宿主需要什么"，不暴露内部实现细节。以下为设计签名（落代码时按 `typing.Protocol` 写）。

### 4.1 `ai-provider` —— AI 后端插件

对应现状：`ai/providers.py` 注册表 + `ai/protocols/` 适配器 + `ai/client.py` 工厂。

```python
class AIProviderPlugin(Protocol):
    def register(self, reg: "AIProviderRegistry") -> None:
        """向宿主注册能力位、ProviderProfile、（可选）协议适配器工厂。"""

class AIProviderRegistry(Protocol):
    def add_profile(self, profile) -> None: ...
    def add_adapter_factory(self, protocol: str, factory) -> None: ...
```

- **官方 API / 千问 / 自定义** = 内置插件（`builtin/ai_official.py`，包装现有注册表数据）。
- **DeepSeek 网页版** = 可选插件：自带 `client.py`（PoW/上传）、`login.py`、`login_dialog.py`、`sha3_wasm_bg.wasm`，
  在 `register()` 里注册 `server_memory / session_state / search / attachments` 能力位与网页版适配器。
- 宿主解耦前提（否则插件装不进来）：`DEFAULT_PROVIDER` 不得写死插件 id；`make_client` 走工厂注册表；
  通用异常基类 `ProviderError`；登录菜单按"是否有该插件"显隐。

### 4.2 `wiki-knowledge` —— Wiki 知识插件（整块可插拔）

对应现状：`game/wiki_kb.py`（检索）+ `nlu/wiki_refine/`（提炼）+ `game/wiki_build.py`（建库）
+ `wiki_data/mcwiki.db`（数据）+ `game/processor.py` 里的实体提取 / 过滤 / 预算 / 去重。

**设计判断（用户拍板）**：大模型本身掌握绝大多数 Minecraft 知识。因此 **Wiki 是"锦上添花"的插件，
拔掉后由大模型凭自身知识作答，效果依然可用**。所以内置默认是 **「不注入」**——
这是一等模式（不是残缺模式），反而更省 token、零延迟、零本地库。

接口边界：宿主**完全不知道 wiki 的存在**，只做"给事件文本 + 预算，换注入文本"：

```python
class WikiKnowledgePlugin(Protocol):
    def know(self, events_text: str, *, session_key: str = "",
             max_chars: int = 480, hints=()) -> str | None:
        """从游戏事件文本里提取实体→检索本地库→提炼→过滤→按预算裁剪，
        返回可直接拼进 prompt 的知识文本；返回 None/"" = 不注入。
        hints：宿主给的额外检索词（如模组高亮词），插件可选用。"""
```

- **宿主保留**：事件聚合、AI 冷却、模式/玩家身份注入、prompt 组装、消息队列。
- **插件负责**：实体提取（别名 / 单字守卫）、本地库检索、提炼、低频过滤、预算裁剪、知识去重。
- **内置默认**：无插件 → `{wiki}` 置空 / "无"，大模型自答。
- **可选插件**：完整实现（自带建库脚本、db、小模型），装入即恢复"本地毫秒级知识注入"。
- **宿主收益**：不再 import `wiki_kb`/`wiki_refine`，**DB 崩溃与 numpy 硬依赖随插件一起搬走**
  （宿主天然免疫）；268MB db 与 1541 行建库代码彻底离开本体。
- **风险与兜底**：大模型可能记错冷门实体或较新版本内容。若在意，装 Wiki 插件，
  或（网页版后端）开联网搜索；插件数据损坏时自动回落「不注入」，不影响游戏互动。

### 4.3 `assets` —— 素材/动画插件

对应现状：`pet/skins.py`（角色包）+ `pet/anim.py`（帧加载）。

```python
class AssetPlugin(Protocol):
    def actions(self) -> dict[str, list]:   # ActionKey -> 帧序列（可只给部分 key）
        ...
    def static_figure(self) -> Path | None: # 可选：该插件的静态兜底图
        ...
```

- **内置 `assets_static`**：只提供**一张大肥鱼待机帧**作为所有动作的 1 帧序列（静态模式）。
- **完整动画素材包 / 角色包** = 可选插件：按动作目录提供帧序列，覆盖内置静态。
- 宿主合并策略：内置静态打底 → 插件按优先级覆盖对应动作 key（**可以只补部分动作**，其余仍静态）。

### 4.4 机制可复用（非本期）

`plugin/` 宿主是**通用**的。将来 NLU 意图模型、TTS 引擎、新闻源等都能照此插件化，接口照开。

---

## 五、内置插件 = 本体的"最小可用闭环"

本体必须自足，因此每个扩展点都要有内置默认：

| 扩展点 | 内置实现 | 效果 |
|---|---|---|
| `ai-provider` | 官方 API / 千问 / 自定义 OpenAI 兼容 | 聊天可用（用户填 Key） |
| `wiki-knowledge` | **不注入**（依赖大模型自身知识） | 游戏知识由大模型自答 |
| `assets` | **静态图：大肥鱼待机帧** | 宠物可见、可对话、不动 |

**静态图规格（按用户指示）**
- 来源：内置 idle 动作（大肥鱼待机）首帧。
- 授权：**大肥鱼待机形象完全开源**，直接随主仓库发布。
- 存放：`assets/_static/idle.png`，压缩到 **≤150KB**（必要时缩到 256px 再放大）。
- 行为：所有动作 key → 该图 1 帧；帧索引恒 0 = 视觉静止；窗口/气泡/菜单/聊天/游戏联动**全部保留**。
- 提示：无动画插件时，设置页 + 首启各一句"当前为静态形象，安装动画素材包即可解锁动作"。

---

## 六、三块可选内容 → 插件（迁移映射）

| 现物 | 体积 | 迁移为 | 本体动作 |
|---|---:|---|---|
| `ai/deepseek.py`+`login.py`+`credentials.py`+`sha3_wasm_bg.wasm` | ~0.1MB + 依赖 | `plugins/deepseek-web/` | 删除主链路硬 import；走 `ai-provider` 接口 |
| `game/wiki_kb.py`+`wiki_build.py`+`nlu/wiki_refine/`+`wiki_data/*`+注入逻辑 | 代码 ~0.2MB + db 268MB | `plugins/wiki-local/` | 宿主默认**不注入**，大模型自答 |
| `assets/` 完整动作目录（1664 帧） | 248MB | 官方素材包 / 角色包（`assets` 插件） | 本体只留 `_static/idle.png` |
| `素材源/` | 87MB | 制作输入，移出仓库 | 仅 `deskpet-tools/` 使用 |
| `wiki_data/mcwiki.db` | 268MB | 运行数据 + 重建脚本 | 缺失 → `{wiki}`="无" |

**必须先修的"不崩"前提**（否则"可选"变"必崩"）：

- 动画：`anim.py:151/295/352/380/413` 裸 `os.listdir`；`window.py:106` `actions["idle"]`；
  `anim.py:191` `_frame_size` 未设；`bubble.py:48/50` `side/turn`；`skins.py:42` `config.ACTIONS[key]`。
- Wiki：宿主移除 `wiki_kb`/`wiki_refine` 依赖后，**DB 崩溃与 numpy 硬依赖随插件一起搬走**
  （宿主天然免疫）；插件内部仍需自修（见本方案 V2 的 B 节）。
- 网页版：`providers.py:84` 默认值写死；`client.py:136/157` 与 `chat.py:52` 直接 import；
  `utility.py:76/84` 直接 import；`window.py:407/421` 登录入口隐式假设。

---

## 七、插件加载容错铁律（"不必须"的工程保证）

1. **加载隔离**：`loader` 对每个插件单独 `try/except`；失败 → 记 `logs/plugin.log` + 跳过，**宿主照常启动**。
2. **注册回滚**：插件 `register()` 中途抛异常 → 撤销其已做的部分注册，保持注册表一致。
3. **无插件可用**：每个扩展点必有内置默认，取不到插件就回内置。
4. **依赖缺失可读提示**：插件声明 `requires`（如 `wasmtime`）；缺依赖 → 提示"插件 X 需要 wasmtime，请 `pip install -r requirements-web.txt`"。
5. **失败不进 `crash.log`**：插件问题是"插件不可用"，不是宿主崩溃；仅在设置页插件列表标红。
6. **补装即生效**：放入插件目录后重启即可用，无需改代码/重装本体。

---

## 八、分期路线图

| 期 | 内容 | 交付 | 验收 |
|---|---|---|---|
| **P0** | `plugin/` 宿主骨架（api/manifest/loader/registry）+ 内置三插件 + `--doctor` + **公共 API 文档 + 示例插件 + entry_points 加载** | 接口可用、内置闭环、可自检、第三方可接入 | I1/I2/I3、V-PLUGIN |
| **P1** | 动画：崩溃点修复 + 静态图（大肥鱼待机帧）+ 完整素材拆为插件 | 本体 **-335MB** | C-V1~V5、V-SIZE |
| **P2** | Wiki：整块（检索+提炼+建库）拆为插件；宿主改为「不注入」默认 | 本体再减 ~0.2MB 代码 + 合规 | B-V1~V2 |
| **P3** | 网页版：默认值/工厂/异常解耦 + 拆为插件 | 无网页版照常 | A-V1~V4 |
| **P4** | 设置界面「插件」页（列表/启停/状态/打开目录） | 用户可管理插件 | I3 |
| **P5** | 合规收尾：`.gitignore`/LICENSE/NOTICE/README/脚本可移植 | 可开源 | V-SIZE |

**建议顺序**：P0（先立宿主）→ P1（体积最大）→ P2 → P3 → P4 → P5。
P0 是纯新增，不动现有行为，风险最低；之后每块逐个"搬到插件接口后面"。

---

## 九、验收

| 编号 | 验收 |
|---|---|
| **I1 闭环** | 删除全部可选插件（只留内置）后，`python main.py` 启动，宠物（静图）可见、能聊天、能进游戏模式 |
| **I2 不崩** | 上述状态 `logs/crash.log` 不增长；空/坏插件的 `plugin.json` 不影响启动 |
| **I3 可见** | 设置页「插件」页列出已装/未装插件与状态；无死控件；给出补装指引 |
| **I4 隔离** | 故意让某插件 `register()` 抛异常 → 宿主与其它插件不受影响 |
| **C-V1** | 只留 `assets/_static/` → 静图启动、可交互 |
| **C-V5** | 放入完整动画插件 → 重启自动恢复动画 |
| **B-V1** | 未装 Wiki 插件 → 游戏模式正常、`{wiki}` 不注入、大模型自答、不崩 |
| **B-V2** | 装入 Wiki 插件 → 恢复本地知识注入；插件数据损坏 → 回落「不注入」、不崩宿主 |
| **A-V1** | 网页版插件缺失 → 4 个非 web 后端可用、默认后端落在可用项 |
| **V-SIZE** | `git clone` 后仓库 ≤ 3MB（不含任何可选插件） |
| **V-DOCTOR** | `--doctor` 报告各扩展点状态：内置可用、已装插件、缺失依赖与补装指引 |
| **V-PLUGIN** | 目录插件与 pip（entry_points）插件均能被发现并注册；同 id 冲突按优先级处理 |

---

## 十、合规

| 项 | 处理 |
|---|---|
| 美术素材（assets 动画、素材源、skins） | 移出主仓库，作可选素材包/插件；本体保留**大肥鱼待机帧**（**已确认完全开源**，随主仓库） |
| Wiki 派生数据（db/seed/curated） | 声明源自 MC 中文 Wiki 及其许可（通常 CC BY-NC-SA）；大文件不入库 |
| 网页版逆封装 | 作可选插件，不入主仓库；README 推荐官方 API/千问 |
| 插件安全 | 插件＝可执行代码，运行在主进程；README 提示"仅安装可信来源" |
| LICENSE / NOTICE | 仓库根与 `desktop-pet/` 各补 |

---

## 十一、决策

**已定**（用户拍板）：

- 项目结构 = **主体 + 插件并列**（`desktop-pet` + `deskpet-plugin-*`）；每个插件独立仓库、独立发布。
- **Wiki 插件完全不随包**；本体默认「不注入」，大模型自答。
- 静态图**延用大肥鱼待机帧**（`assets/_static/idle.png`）；**大肥鱼待机形象完全开源，随主仓库发布**。
- 官方素材包 / 角色包**单独发布**，不进主仓库。
- **`--doctor` 本期做**（随 P0 宿主骨架）。
- **插件 API 对外开放**（第三方可写），**支持 entry_points（pip 安装）**——均本期。
- 当前是**开源准备**阶段（不是立即开源）。

**待拍板**：无（本轮决策已全部拍定）。

---

## 十二、落地记录（P0 · 2026-09-20）

> 范围：**纯新增、零行为变化**。宿主骨架就位、内置最小闭环可用、`--doctor` 可自检、
> 第三方可按接口接入。**尚未迁移任何现有功能**（动画 / Wiki / 网页版仍在原处，P1–P3 再迁）。

**新增文件**

| 文件 | 作用 |
|---|---|
| `plugin/__init__.py` | 宿主包，导出 `API_VERSION` |
| `plugin/api.py` | **唯一公共契约**：扩展点类型、`HostAPI` / `WikiKnowledgePlugin` / `AssetPlugin` Protocol、异常 |
| `plugin/manifest.py` | `plugin.json` 解析与校验、来源优先级 `sort_key` |
| `plugin/registry.py` | 扩展点注册表 + 注入给插件的 `HostAPI` 实现（暂存后合并） |
| `plugin/loader.py` | 发现（内置 / 目录 / entry_points）、加载隔离、同 id 去重 |
| `plugin/doctor.py` | `--doctor` 自检报告 |
| `plugin/builtin/{ai_official,wiki_none,assets_static}.py` | 三个内置插件（第一方最小实现） |
| `plugins/README.md` + `.gitkeep` | 插件投放目录（内容 gitignore） |
| `docs/PLUGIN_API.md` + `docs/example-plugin/` | 第三方接口文档 + 最小示例 |
| `tests/test_plugins.py` | P1–P8 守卫 |

**改动文件**：`main.py`（`--doctor` 分支，在 import Qt 之前）、`.gitignore`（忽略插件内容、保留 README/.gitkeep）。

**验证结果**

| 项 | 结果 |
|---|---|
| `tests/test_plugins.py` | ✅ P1–P8 全通过 |
| `python main.py --doctor` | ✅ 报告 3 内置插件、3 扩展点、依赖检查（无 Qt 启动） |
| `tests/test_providers.py` 回归 | ✅ 全通过 |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长（零行为变化） |
| `.gitignore` 校验 | ✅ 插件内容忽略、README/.gitkeep 入库 |

**当前扩展点状态**

| 扩展点 | 内置实现 | 说明 |
|---|---|---|
| `ai-provider` | `ai-official` | 注册 `deepseek-api / qwen / anthropic / custom`（网页版暂留宿主，P3 迁出） |
| `wiki-knowledge` | `wiki-none` | 不注入（大模型自答） |
| `assets` | `assets-static` | 静态图接口就位；`assets/_static/idle.png` 待 P1 生成 |

**已知待办（P1 起）**：静态图 `idle.png` 尚未生成（doctor 显示"未提供静态图"）；
`ai-official` 尚未剔除网页版；各扩展点**尚未接入主流程**（P1–P3 逐个迁移）。

---

## 十三、落地记录（P1 · 2026-09-20）

> 范围：**动画素材外置 + 静态模式兜底**。`assets` 扩展点接入主流程（动画系统），
> 完整动画与素材源移出仓库跟踪，本体只保留一张静态图。

**新增文件**

| 文件 | 作用 |
|---|---|
| `plugin/host.py` | 宿主运行时：全局注册表惰性加载 + `static_figure()` 便捷查询 |
| `tools/make_static_figure.py` | 生成内置静态图（可复跑） |
| `assets/_static/idle.png` | **本体唯一素材**：大肥鱼待机帧（182×220，52.6KB，完全开源） |
| `assets/README.md` | 素材目录规范 + "如何补回动画" |
| `tests/test_static_fallback.py` | S1–S5 守卫 |

**改动文件**

| 文件 | 改动 |
|---|---|
| `pet/anim.py` | 新增 `_list_png` 安全列目录（替换 4 处裸 `os.listdir`）；`_load_actions` 无有效帧 → `_load_static_fallback()`；新增 `_make_placeholder`（最后兜底）；`_rebuild_frame_cache` / `_ensure_lazy_loaded` 静态分支；`_init_ui` `_frame_size` 兜底 |
| `pet/window.py:106` | `actions["idle"]` → 安全取值（缺 idle 不崩） |
| `pet/bubble.py:48/50` | `actions["turn"/"side"]` → `.get()`（皮肤缺动作不崩） |
| `pet/skins.py:42` | `config.ACTIONS[key]` → `.get(key,key)`，并过滤 `ActionKey.SEQUENCE` |
| `.gitignore` | 忽略 `assets/*`（保留 `_static/` 与 README）与 `素材源/` |

**素材外置（git）**：`git rm -r --cached`（**保留工作区文件**）——
`assets` 跟踪 1664 → **2**（README + 静态图），`素材源` 541 → **0**。

**体积**

| 口径 | P0 前 | P1 后 |
|---|---:|---:|
| 仓库跟踪文件数 | 2482 | **279** |
| 仓库跟踪体积 | 342.4 MB | **7.11 MB（−97.9%）** |

> 余下 7.11MB 中约 4.8MB 是 `nlu/`（含 Wiki 提炼模型 3.2MB）——**待 P2 随 Wiki 插件移出**；
> 纯代码+文档约 2.3MB。终态目标 ≈2.6–3.9MB。

**验证结果**

| 项 | 结果 |
|---|---|
| `tests/test_static_fallback.py` | ✅ S1–S5（含完整 `_load_actions` 无素材目录 → 静态模式） |
| `tests/test_reconstruction.py` 回归 | ✅ 全通过（动画/皮肤/上传） |
| `tests/test_plugins.py` 回归 | ✅ 全通过 |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长 |

**行为**：有动画素材 → 照旧播放；**无动画素材 → 静态模式**（显示大肥鱼待机帧，
拖动/聊天/气泡/语音/游戏联动全部保留，只是不动；连静态图都取不到时用程序占位）。

---

## 十四、落地记录（P2 · 2026-09-20）

> 范围：**Wiki 整块插件化**。宿主完全不再依赖 Wiki（不 import、无词表、无 db）；
> 拔掉插件 → 大模型凭自身知识作答；装上 `wiki-local` → 恢复本地检索 + 提炼注入。

**迁移（物理）**：`game/wiki_kb.py`、`game/wiki_build.py`、`tools/wiki_audit.py`、
`nlu/wiki_refine/`（含 data 模型）→ `plugins/wiki-local/`（**不随主仓库**，gitignore）。

**新增插件文件**：`plugin.json` / `plugin.py` / `wiki_local.py`（`WikiLocal.know`）/
`wi_config.py`（词表等常量）/ `wi_ngrams.py`（n-gram 副本）/ `tests/test_wiki.py` / `tools/wiki_audit.py`。

**改动文件**

| 文件 | 改动 |
|---|---|
| `game/processor.py` | 删除 `import game.wiki_kb` / `nlu.wiki_refine` 与 `_wiki_engine`/`_wiki_knowledge`/`_clip_knowledge`；新增 `_wiki_lookup()` 走 `wiki-knowledge` 扩展点；`process_game` 改调它 |
| `plugin/api.py` | `WikiKnowledgePlugin.know()` 增加 `hints` 参数（接口只增不改） |
| `plugin/builtin/wiki_none.py` | `know()` 同步加 `hints` |
| `plugin/loader.py` | 目录插件加载时把插件目录加入 `sys.path`（使插件可 import 自带模块） |
| `plugin/doctor.py` | Wiki 状态按来源判定（不再用"空输出"猜实现） |
| `pet/settings_dialog.py` | 无 Wiki 插件时隐藏「Wiki 知识提炼 / 过滤」开关 |
| `config.py` | 删除 `GAME_WIKI_LIMIT` / `WIKI_REFINE_ENGINE` / `WIKI_COMMON_TERMS` / `WIKI_NEW_TERMS`（随插件走）；保留 `GAME_WIKI_MAX_CHARS`（预算）与 `WIKI_FILTER_OPTIONS/DEFAULT`（UI） |
| `tests/test_reconstruction.py` | Wiki 测试改为经扩展点（`_FakeWiki` / `_install_host_wiki`） |
| `tools/wiki_audit.py` → 插件 | import 与路径改写 |

**体积**

| 口径 | P1 后 | P2 后 |
|---|---:|---:|
| 仓库跟踪文件数 | 279 | **220** |
| 仓库跟踪体积 | 7.11 MB | **3.43 MB（累计 −99.0% vs 342.4MB）** |

> 余下 ~3.4MB 为代码 + 文档 + 静态图 + NLU 意图模型（`nlu/data`，非本次三块范围）。

**验证**

| 项 | 结果 |
|---|---|
| `--doctor` | ✅ 4 插件加载，`wiki-knowledge: 已启用知识注入 [wiki-local]` |
| `plugins/wiki-local/tests/test_wiki.py` | ✅ 全通过（含真库 curated/match_terms/预算） |
| `tests/test_reconstruction.py` | ✅ 全通过（含「Wiki 扩展点接线：注入 / 不注入」） |
| `tests/test_plugins.py` | ✅ P1–P8 全通过 |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长 |

**行为**：不装 `wiki-local` → `{wiki}` 不注入（大模型自答）；装上 → 恢复三层知识链路
（curated → 本地精炼 → 原文），设置界面出现 Wiki 开关。**宿主代码与仓库均不含 Wiki 实现。**

---

## 十五、落地记录（P3 · 2026-09-20）

> 范围：**DeepSeek 网页版逆封装插件化**。宿主不再 import 逆向代码；默认后端改为官方 API；
> 缺插件时其余 4 个后端照常；装上插件恢复网页版（含登录、识图、联网搜索、会话续接）。

**迁移（物理）**：`ai/deepseek.py` → `plugins/deepseek-web/deepseek_client.py`；
`ai/login.py` → 插件内 `login.py`；`sha3_wasm_bg.wasm` → 插件。
（`ai/credentials.py` **保留在本体**——它只是 QSettings 凭证读写，不属逆向实现。）

**新增插件文件**：`plugin.json` / `plugin.py`（网页版 profile + 客户端工厂）/ `tests/test_deepseek.py`。

**改动文件**

| 文件 | 改动 |
|---|---|
| `ai/providers.py` | 内联 web 记录移除（改由插件注册）；`profiles()`/`profile()`/`selectable_ids()` 合并插件注册；`DEFAULT_PROVIDER` → `deepseek-api`；新增 `ProviderError`/`ProviderUnavailable` 与 `builtin_profiles()` |
| `ai/client.py` | 新增 `make_web_client()`（经扩展点工厂，插件缺失返回 None）；`make_client`/`chat_once` 的 web 分支走它；会话状态 `save/load/clear_thread_state` 改用 `backend_name()`（不再写死默认） |
| `ai/utility.py` | `_web_once` 改用 `make_web_client()` |
| `ai/chat.py` | 删除 `from ai.deepseek import ...` 顶层依赖；`LoginDialog._HELPER` 动态指向插件；手动验证改用 `make_web_client`；`except DeepSeekError` → `except prov.ProviderError` |
| `pet/window.py` | 右键「登录 DeepSeek」按 `prov.exists("deepseek-web")` 显隐；聊天预检按后端分支 |
| `plugin/builtin/ai_official.py` | 改用 `builtin_profiles()`（避免 `profiles()` 递归加载） |
| `requirements.txt` | 移除 `wasmtime` → 新增 `requirements-web.txt`（可选） |
| `tests/` | `test_chat_unit` 的 U4 改为经扩展点工厂；DeepSeek 三项测试迁入 `plugins/deepseek-web/tests/` |

**体积**

| 口径 | P2 后 | P3 后 |
|---|---:|---:|
| 仓库跟踪文件数 | 220 | **218** |
| 仓库跟踪体积 | 3.43 MB | **3.37 MB**（累计 **−99.0%** vs 342.4MB） |

> 本块体积影响小（网页版代码+wasm ≈55KB）；主要意义是**合规**（逆向代码不随包）与**依赖解耦**（`wasmtime` 变可选）。

**验证**

| 项 | 结果 |
|---|---|
| `--doctor` | ✅ 5 插件；`ai-provider: deepseek-web, deepseek-api, qwen, anthropic, custom` |
| `tests/test_providers.py` | ✅ V1–V10（5 供应商含插件 web） |
| `tests/test_chat_unit.py` | ✅ 全通过（U4 经扩展点路由） |
| `tests/test_maid_loop.py` | ✅ 14 项全通过 |
| `tests/test_reconstruction.py` | ✅ 全通过 |
| `plugins/deepseek-web/tests/test_deepseek.py` | ✅ SSE / 上传 / 编码 |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长 |

**行为**：不装 `deepseek-web` → 默认后端 `deepseek-api`，右键无「登录 DeepSeek」，
设置里网页版显示"未安装"；装上 → 恢复网页版全部能力。**宿主代码与仓库不含逆向实现。**

---

## 十六、落地记录（P4 · 2026-09-20）

> 范围：**设置 →「插件」页**（扩展组）：列出已装 / 停用 / 加载失败的插件，可启停、
> 打开插件目录。停用状态持久化，重启生效。

**改动文件**

| 文件 | 改动 |
|---|---|
| `plugin/loader.py` | `load_all(..., disabled=())`：停用的插件**仍被发现但不注册**，记入 `LoadResult.disabled` |
| `plugin/host.py` | 新增 `disabled_ids()`（读 QSettings `plugins/disabled`）；`registry()` 加载时应用 |
| `plugin/doctor.py` | 报告"已停用"插件 |
| `pet/settings_dialog.py` | 新增「扩展 → 插件」页：插件列表（名称/类型/来源/状态 + 启用勾选）、加载失败红字、打开插件目录；`_wiki_plugin_available()` 补齐（P2 遗留） |
| `tests/test_plugins.py` | 新增 `[P9]` 停用守卫 |

**行为**

- 停用插件 → 该插件不注册，其扩展点回落到内置默认（如停用 `wiki-local` → `wiki-none` 不注入）。
- 勾选/取消即写 `plugins/disabled`，**重启桌宠**生效。
- 「打开插件目录」用 `QDesktopServices` 打开 `desktop-pet/plugins/`。

**验证**

| 项 | 结果 |
|---|---|
| `tests/test_plugins.py` | ✅ P1–P9 全通过（含 P9 停用） |
| `tools/preview_settings_dialog.py 16` | ✅ 插件页离屏渲染成功（`png/设置_16_插件.png`） |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长 |

**注**：P4 顺带修复 P2 遗留的 `SettingsDialog._wiki_plugin_available` 缺失（`preview` 才暴露；
`--smoke` 不打开设置故当时未发现）。

---

## 十七、落地记录（P5 · 合规收尾 · 2026-09-20）

> 范围：许可 / 声明 / 文档 / 脚本可移植 / 版本发布，使项目**可开源**。

**新增文件**

| 文件 | 作用 |
|---|---|
| `desktop-pet/LICENSE` / `LICENSE` | MIT 许可（主体代码） |
| `desktop-pet/NOTICE` / `NOTICE` | 第三方组件与数据声明（Lucide / Ant Design / Minecraft Wiki CC BY-NC-SA / 车万女仆 / 可选插件 / 素材） |
| `desktop-pet/requirements-web.txt` | 网页版插件可选依赖（`wasmtime`） |

**改动文件**

| 文件 | 改动 |
|---|---|
| `README.md` | 新增「插件系统」章节；依赖表 `wasmtime` 改可选；打包命令只带静态图与 prompt（不再强制 assets/wasm）；素材与插件说明 |
| `CHANGELOG.md` | 新增 2.29.0 条目（P0–P5） |
| `config.py` | `VERSION` → 2.29.0（满足版本铁律 V7） |
| `plugins/wiki-local/wiki_refine/gen_{advancements,achievements}.py` | 硬编码绝对路径 → 基于 `__file__` 推导 |
| `plugins/wiki-local/tools/wiki_audit.py` | 注释里的绝对 python 路径 → `python` |

**仓库状态（P0 前 → P5 后）**

| 口径 | 开源准备前 | 现在 |
|---|---:|---:|
| 跟踪文件数 | 2482 | **222** |
| 跟踪体积 | 342.4 MB | **3.39 MB（−99.0%）** |
| `assets/` 跟踪 | 1664 | **2**（README + 静态图） |
| `素材源/` 跟踪 | 541 | **0** |
| `plugins/` 跟踪 | — | **2**（README + .gitkeep，内容 gitignore） |

**验证**

| 项 | 结果 |
|---|---|
| `tests/test_providers.py` | ✅ V7 版本铁律（config.VERSION == CHANGELOG 顶部 == 2.29.0） |
| 全套主体测试（9）+ 插件测试（2） | ✅ 全通过 |
| `python main.py --smoke` | ✅ exit=0，`crash.log` 未增长 |
| `git ls-files` | ✅ LICENSE/NOTICE 入库；素材/插件内容/Wiki 数据未入库 |

> **开源准备完成**：三大可选模块（网页版逆封装 / Wiki / 动画素材）全部插件化、不随包；
> 本体从 342MB 降到 3.4MB；含许可、声明、接口文档与自检工具。

**全量验证（复验 · 2026-09-20）**

| 项 | 结果 |
|---|---|
| 主体测试 9 + 插件测试 2 | ✅ 全通过 |
| `python -m compileall .` | ✅ exit=0（无语法错） |
| smoke：带插件 / 无插件 / 无插件+无动画素材 | ✅ exit=0，`crash.log` 未增长 |
| `--doctor`：无插件 / 停用插件 | ✅ 正确降级（内置三插件 / 停用列表） |
| 设置对话框：带插件 / 无插件 | ✅ 离屏渲染成功（Wiki 开关按插件显隐） |

复验中发现并修复：

1. `--doctor` 未把停用列表传给 `load_all` → 停用不生效（`plugin/doctor.py`，已修）。
2. `voice/tts.py` 退出竞态 `RuntimeError: Signal source has been deleted`（既有问题，非本次引入；已给 `done.emit` 加保护）。

---

## 十八、插件规范（生态扩展）

> 为支撑第三方插件生态，把「插件需要知道的数据类型」从主体模块收敛为**公共契约包**，
> 并明确版本 / 命名 / 发布规范。

**问题**：原先 `ProviderProfile` / `ModelInfo` / `ActionKey` 定义在主体（`ai.providers` /
`core.action_key`），第三方插件不得不 import 主体内部，与"插件只依赖插件契约"矛盾。

**收敛**：

- 新增 `plugin/contracts.py`（**单一事实来源，纯数据、无 Qt / config / 主体依赖**）：
  `PROTO_*` / `CAP_*` / `REASONING_*` / `ACTION_KEYS` / `ModelInfo` / `ProviderProfile` /
  `ProviderError`。
- `ai.providers` 改为**从 contracts 导入并 re-export**（`prov.ProviderProfile` 等名字不变，
  **零行为变化**）；`core.action_key` 与 `ACTION_KEYS` 由测试 `[P10]` 守卫一致。
- `deepseek-web` 插件改用 `plugin.contracts`（示范"插件只依赖契约"）。

**规范（写入 `docs/PLUGIN_API.md` §十~§十二）**：

- 插件只依赖 `plugin.api`（行为接口）+ `plugin.contracts`（数据类型）；**不 import 主体内部**。
- 版本：`API_VERSION` 为主版本，不匹配拒绝加载；接口**只增不改**（可选方法/字段）。
- 命名：id `kebab-case`；pip 包 `deskpet-plugin-<id>`；模块 `deskpet_plugin_<id>`。
- 发布：目录包 / pip（`entry_points` 组 `deskpet.plugins`）；建议独立仓库；`requires` 声明依赖。
- 自测：`python main.py --doctor`；禁用：设置 →「扩展 → 插件」。

**验证**：`test_plugins` 新增 `[P10]`（契约单一事实来源）；全套测试 + `compileall` 通过。
