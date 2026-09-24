# 本地预处理（NLU）：意图识别 + 自动执行

> 状态：**已实施**（随 `desktop-pet` **2.18.0**）
> 目标：在把用户输入交给大 AI 之前，先用**本地小模型 + 领域规则**判断「用户是不是在指挥女仆干活」，
> 是的话直接调工具执行（如自动合成），并把「原话 + 是否已执行 + 配方/背包明细」一并交给大 AI 润色回应。

---

## 一、为什么不是「纯规则」，也不是「生成模型」

用户原话示例（语音输入）：

> 帮我合成 **一把稿子**

「稿子」是 ASR 错字，实际是「镐子」。如果交给大 AI 自己判断，会消耗一次完整对话的 token 与延迟；
而纯规则引擎对「帮我合成 / 合诚 / 用三个铁锭两根木棍做个铁搞子」这类口语变体很快就不够用。

因此拆成两件事，各用最合适的工具：

| 子问题 | 方案 | 理由 |
|---|---|---|
| **这句话想干什么？** | **训练一个意图分类器**（26 类） | 句式高度口语化、变体无穷 → 学习比枚举可靠；且是有界分类，样本可自动合成 |
| **要操作的物品是什么？** | **词典 + 拼音模糊匹配**（非模型） | 物品名是有限集合、要求**零误配**；生成模型会「编」出不存在的物品，而编辑距离可解释、可测 |
| **材料够不够 / 缺什么？** | **模组侧 dry-run 查询**（权威） | 配方与背包只有游戏内是唯一事实来源，含 mod 配方 |

> 一句话：**意图靠模型，槽位靠词典，事实靠游戏。**

---

## 二、总体数据流

```
用户输入（聊天 / 语音）
        │
        ▼
┌──────────────────────────── desktop-pet 工作线程 ────────────────────────────┐
│ ① 意图识别（nlu/intent.py，纯 numpy）                                        │
│    字符 1/2/3-gram → EmbeddingBag → 隐层 → softmax → 26 类 + 置信度           │
│    置信度 < 0.55 → 放弃干预，原样给大 AI                                      │
│                                                                              │
│ ② 槽位抽取（nlu/slots.py）                                                   │
│    物品名（词典 + 拼音模糊 + 角色感知挑选）、数量、坐标、目标生物、槽位        │
│                                                                              │
│ ③ 执行前检查（nlu/router.py）                                                │
│    需要女仆？连着吗？物品 id 拿到了吗？坐标/目标给了吗？                       │
│                                                                              │
│ ④ 执行（craft 走专用链路）                                                   │
│    craft_check（干跑，不消耗材料）→ 材料够 → craft 指令 → 等回执              │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   ▼
        【本地预处理】用户原话：「帮我合成一把稿子」
        意图：合成（craft，置信度 0.99）
        槽位：物品：《镐子》→ minecraft:wooden_pickaxe（语音模糊匹配）；数量：1
        配方：任意(橡木木板 / 云杉木板 / …) ×3（现有 5，✅）+ 木棍 ×2（现有 0，❌ 缺2）
        执行：❌ 材料不够，没有合成
        还差：木棍 缺 2
        （以上为本地程序的事实结果，请据此回应用户，不要重复报技能名）
                                   │
                                   ▼
                            大 AI 润色后回复用户
```

**关键取舍**：本地程序负责「事实」，大 AI 负责「语言」。注入文本里显式写了
「不要重复报技能名」，避免人格与工具语气打架。

---

## 三、意图体系（26 类）

与模组 `/maidtasks` 的 24 条指令**逐字对齐**（`nlu/taxonomy.py` 是单一事实来源），
外加 `status` / `cancel` / `chat` 三类非任务型意图。

> **26 怎么来的**：模组的 AI 指令是 **24 条**（不含 `cancel`/`status`），
> 加 `status` / `cancel` / `chat` 三类非任务型意图 = 27 类，
> 再减去已按 §11.1 规范**合并进 `collect` 的 `pickup`** → **26 类**。
>
> 另外两条「契约有、意图无」的指令（用户不会「说」它们）：
> `craft_check`（桌宠侧干跑查询）与 `pickup`（已合并进 `collect`）；
> 而 `transfer` / `chestopen` / `chestput` / `chesttake` 是为模组槽位能力补的**桥接意图**。

| 分类 | 意图 | 自动执行 | 必需槽位 |
|---|---|---|---|
| 战斗 | attack / guard / feed / eat | ✅ | target 或 range |
| 采集生产 | mine / farm / collect / **craft** / smelt | craft·smelt·collect ✅，mine·farm 需坐标 | item 或 pos |
| 建造 | build / place / break / use | ❌（需坐标） | pos |
| 物品容器 | equip / store / drop / transfer / chestopen / chestput / chesttake | 除 chestopen 外 ✅ | item / from / to |
| 移动姿态 | move / look / sit / stop | sit·stop ✅，move·look 需坐标或目标 | pos / target |
| 非任务型 | status / cancel / **chat** | ✅（chat ❌） | — |

`chat` 是**负样本主力**（占语料 ~1/4）：闲聊绝不能触发动作，这是产品红线。

---

## 四、模型与训练

### 4.1 语料：自动合成，不需要人工标注

`nlu/synth.py` 用「意图模板 × 槽位词典 × 口语变体 × 语音错字噪声」扩增：

* 约 300 条人工模板（每个意图 5~15 条句式）
* 物品 200+ / 生物 50+ / 数量 20+ / 位置 20+ 词槽
* 前缀（帮我/麻烦/赶紧…）、后缀（吧/谢谢/好不好…）、语境补语
* **同音错字噪声**（50% 样本）：按拼音把字换成同音字，模拟 ASR 错误（`镐子 → 稿子/高子/搞子`）

一次生成 **52,000 条**（26 类 × 2000），用固定随机种子 → 完全可复现。

### 4.2 特征：字符 n-gram

中文短句的意图由**动词**主导，不需要分词：字粒度 1/2/3-gram 已足够，
且免去分词器依赖。归一化去掉标点与全角差异。

```
"帮我合成一把稿子" → 帮我 / 帮 / 我 / 合 / 成 / 一把 / ... / 帮我合 / 我合成 / 合成一 ...
```

### 4.3 模型：纯 numpy 的小网络

`nlu/model.py`：`EmbeddingBag(均值池化) → 64 维隐层(ReLU) → softmax(27)`，
自己实现前向/反向/Adam。选它的原因：

* **训练与推理同一份代码**，不存在「训练用 torch、部署用别的」的漂移
* 桌宠运行时**零新增依赖**（numpy 本来就是必需项）
* 权重导出 float16 压缩 → **1.35 MB**

### 4.4 实测指标（`nlu/data/report.json`）

**两个口径一起看**（只看合成验证集会自我欺骗）：

| 口径 | 数值 | 说明 |
|---|---|---|
| 合成验证集 | 97~98% | 与训练语料同源，**不能当产品指标** |
| **人工测试集（真实口吻）** | 见 `report.json` 的 `manual_eval` | 208 条，含对抗样本 / 复合句 / 极短句 / 闲聊诱饵 |
| **高置信自动执行档** | 准确率 **≥96.8%** | 真正会动手的那一档 |
| **闲聊误激活** | **0 条** | 红线：闲聊被判成可自动执行指令 |

> **关于「人工测试集没有明显提升」这件事**：本轮把语料从「主题模板」扩到
> 「边界讲清楚 + 对抗样本」后，人工测试集反而略降（见下）。根因是**对抗语料权重过高**，
> 把决策边界拉向对抗样本、偏离真实分布——所以权重做成了参数（`--hard-weight`），
> 默认保守，并保留 `hard_weight=0`（完全不用）这一档供对比。
> 更重要的是：**剩余错例大多不是模型能力问题，而是标签口径问题**（见 §11.1）。

**统计显著性**：208 条样本的准确率标准误约 **±2.1%**（binomial）。
比较两个模型请用多随机种子的均值，单次差异小于 4% 不要下结论
（`--seed` 可复现；本次对照实验用了 3 个种子）。

阈值设计（`nlu/intent.py`）：`≥0.85` 且意图允许 → 自动执行；`0.55~0.85` → 只作为提示交给大 AI；
`<0.55` → 完全不干预。**宁可漏执行，不可乱执行。**

---

## 五、槽位抽取：拼音模糊 + 角色感知

### 5.1 物品名四段匹配（`ItemIndex.resolve`）

| 段 | 判据 | 分数 | 例 |
|---|---|---|---|
| ① 字面精确 | 子串命中 | 1.00 | 铁镐 / 工作台 |
| ② 拼音精确 | 无声调音节序列对齐（滑窗） | 0.95 | **稿子 → 镐子**、合诚 → 合成 |
| ③ 近音容错 | 允许一个音节增/删/换 | 0.80 | 铁**搞子** → 铁镐 |
| ④ 首字母 | 拼音缩写 | 0.70 | tg → 铁镐（键盘输入） |

拼音来自**离线固化的字表** `nlu/data/pinyin.json`（CJK 基本区 20892 字，247 KB），
**运行时不依赖 pypinyin**——多一个字表就少一个部署风险，也让训练期与运行期拼音完全一致。

> 字表必须覆盖**整个常用字区**而不是只覆盖物品名用字：错字本身（「稿」）
> 未必出现在任何物品名里，只覆盖物品名会让这些错字的拼音查不到、匹配直接失效。

### 5.2 两条领域规则（可解释、可单测）

1. **容器降级**：`取箱子里的铁锭` 里「箱子」是地点不是宾语 —— 需要物品的意图下，
   若存在非容器候选，容器词（箱子/熔炉/工作台…）降为备选。
2. **动词邻接**：`用三个铁锭两根木棍做个铁镐` 里，产出物在动词**之后且最近** ——
   合成/烧炼类意图取「最后一个产出动词之后、距离最近」的候选。

### 5.3 数量绑定

`用三个铁锭两根木棍做个铁镐` 里「三个/两根」是**材料**的量，产出是 1。
判据：数量后面紧跟的若是「不是目标物品」的物品名 → 该数量属于材料，跳过。
量词换算支持 `一组=64 / 半组=32 / 一打=12 / 十六个=16`。

---

## 六、模组侧数据契约（SmartMaid）

桌宠通过既有 WebSocket 链路（`ws://127.0.0.1:21420`）向模组要事实。

### 6.1 `item_index`（握手后模组推送一次）

```json
{"type":"item_index","count":1523,
 "items":{"铁镐":"minecraft:iron_pickaxe","工作台":"minecraft:crafting_table", "...":"..."}}
```

物品名 ↔ id 的词典**由模组下发**，而不是桌宠自带。理由：

* 覆盖 **mod 物品**（词典随整合包自动变化，桌宠不用维护）
* 名称跟随**客户端当前语言**（`I18n`），中文/英文包都能匹配
* 桌宠不必打包 `zh_cn.json`，不存在版本同步问题

专用服务端没有语言资源 → 模组返回 null，桌宠自动降级为「认得出名字但执行不了」，**不会误执行**。

### 6.2 `craft_check`（干跑查询，不消耗材料）

```json
{"id":"pet-...","ok":true,"state":"done","step":"checked",
 "result":{"found":true,"craftable":false,"need":1,"crafts":1,"out_per_craft":1,
  "ingredients":[
    {"options":["minecraft:oak_planks","minecraft:spruce_planks", "..."],
     "need":3,"have":5,"ok":true},
    {"options":["minecraft:stick"],"need":2,"have":0,"ok":false}]}}
```

* 候选配方优先取物品自带的 `DataComponents.RECIPES`（产物必然匹配）；
  兜底全量扫描时用「填满候选项的 3×3 假网格」验证产物
* `need` / `have` / `ok` 逐项给出，`options` 支持 tag 类配方（「任意木板×3」）
* 修改文件：`CraftExecutor.check()`（新增）、`MaidAIBridge` 加 `craft_check` 分支

---

## 七、指令层对齐（`nlu/mod_contract.py`）

桌宠侧的「训练」与「执行」都依赖模组指令层，所以把协议层抽成**单一事实来源**：
`nlu/mod_contract.py` 逐条记录指令的**参数名 / 类型 / 默认值 / 取值域 / 前置条件**，
锚定 SmartMaid 提交（当前 `d46056b`）。模组改了指令层就更新锚点并重跑测试。

### 7.1 契约带来什么

* **下发零漂移**：`clamp()` 自动丢弃模组不认识的参数、把数值夹进合法区间。
  以前「把 `slot` 塞给 `equip`」「发 `transfer` 却不给 `from`」这类对齐错误会**直接导致真机失败**，
  现在变成结构性不可能。
* **训练有据**：意图集与语料里的说法都能映射到真实指令，而不是"猜模组大概支持什么"。
* **可回归**：`tests/test_nlu.py` 双向校验——每个意图的指令必须在契约里，
  契约里每条指令必须有意图能产生它（`craft_check` 为桌宠内部查询，豁免）。

### 7.2 模组做不到的语义 → 用已有指令组合出来

对齐过程中发现 5 处「语义 ↔ 模组能力」不匹配，全部在 `router._plan()` 里补齐**（不改模组）**：

| 语义 | 模组实际能力 | 组合方案 |
|---|---|---|
| `equip` 指定槽位（戴头盔） | `equip` 只能到**主手** | `transfer(from=inv:<背包装备所在格>, to=head)`，格号用背包快照反查 |
| `drop` 指定物品 | `drop` 只丢**主手** | `transfer(from=inv:<格>, to=world:<女仆坐标>)` |
| `transfer` 只说目标槽位 | `from`/`to` **都必填** | 用背包快照反查该物品所在格补出 `from` |
| `chestput` / `chesttake` | 要求容器**已打开** | 自动前置 `chestopen`（模组会在 4 格内定位最近容器），**并等它真正跑完** |
| `place` / `use` / `build` / `chesttake` 指定对象 | 模组不接受该参数 | 列为契约里的 `advisory`（仅作语义提示，不发送）。（`attack` 的 `target` 已升级为**真实参数**：模组支持打指定实体类型，用于获取食物） |

> 「等 chestopen 跑完」这一条很容易漏：模组的 `chestopen` 是**异步任务**，
> 回执只说「已受理」，容器记为「已打开」是在任务 tick 里完成的。所以必须轮询
> `status` 直到任务结束（`MaidLink.wait_task_done`），否则 `chestput` 一定报
> 「女仆还没打开箱子」。

### 7.3 模组侧补全：主人坐标

`OwnerSense` 原本只有 `dist`，导致「在我脚下放方块」「到我这儿来」无法定位。
已补 `pos`（与 `SelfSense` 一致的三元组）：

```json
"owner": {"name": "Steve", "pos": [12, 64, 14], "dist": 3.2, ...}
```

相对位置词据此换算：`owner` → 主人坐标；`maid` → 模组的 `"~"`；拿不到主人坐标时**不执行**，
只提示（「拿不到你的坐标」），绝不瞎放。

### 7.4 意图边界与复合句规则

真实语句评测暴露的错例，绝大多数不是「模型不行」，而是**相邻意图边界没定义**。
现已在 `taxonomy` 文档里明确规则，并在 `synth.DISAMBIG` / `synth.COMPOUND` 里写成对抗语料：

* **边界对**：break↔mine（一个指定方块 vs 附近挖一片）、build↔place（一片 vs 一个）、
  look↔move（看 vs 过去）、equip↔transfer（装备 vs 槽位互换）、store↔transfer、
  cancel↔stop（取消任务 vs 否定当前动作）、status↔chat、attack↔guard、smelt↔craft、chestopen↔move
* **复合句主意图约定**：① 否定/停止优先 ② 最终产出优先 ③「顺便」引导的动作弱化 ④ 核心目的优先

### 7.5 两个准确率，别看错

| 口径 | 含义 | 说明 |
|---|---|---|
| **合成验证集** | 与训练语料同源 | 天然虚高（实测 99~100%），**不能当产品指标** |
| **人工测试集** | 真实口吻 + 对抗样本 + 复合句 | `tools/eval_manual.py`，`--manual-test` 已接入训练流程一起报 |

---

## 八、与桌宠的接线点

| 位置 | 改动 |
|---|---|
| `nlu/`（新子包） | taxonomy / mod_contract / synth / features / model / train / intent / slots / router / build_pinyin |
| `pet/handlers/nlu_bridge.py`（新） | 把 `MaidHandler` 适配成 router 需要的 Bridge 协议（便于单测替换）；新增 `inventory_slots` / `maid_pos` / `owner_pos` / `query_status` / `wait_task_done` |
| `pet/handlers/maid_handler.py` | 无改动（bridge 从外部包装） |
| `game/maid_link.py` | 新增 `item_index()` / `inventory_counts()` / `inventory_slots()` / `maid_pos()` / `owner_pos()` / `request()`（等回执）/ `query_craft()` / `query_status()` / `wait_task_done()`；处理 `item_index` 消息；`_pending` 回执唤醒 |
| `pet/modes.py` | `nlu_router()` / `nlu_preprocess()`（懒加载 + 失败静默回退），perf 日志 |
| `ai/chat.py` | `ChatWindow(preprocessor=…)` + `_ChatWorker` 在前置注入结构化文本 |
| `pet/handlers/stt_handler.py` | 语音链路同样前置 |
| `pet/window.py` | 打开聊天窗时注入 `self.nlu_preprocess` |
| `config.py` | `NLU_ENABLED` / `NLU_AUTO_THRESHOLD` |
| `tools/eval_manual.py` | 人工测试集评测（真实口吻，产品指标） |
| SmartMaid 模组 | `ItemIndexPayload`（新）/ `CraftExecutor.check()` / `MaidAIBridge` 的 `craft_check` 分支 / `MaidWsClient` 握手后推送 / **`OwnerSense` 补 `pos`** |

**线程约定**：预处理会阻塞等女仆回执，因此**只在工作线程调用**（聊天 worker / 语音 worker），
绝不在 Qt 主线程调用。回执等待用 `threading.Event`，`stop()` 时会唤醒所有等待者。

**健壮性原则**：模型缺失、numpy 不可用、模组异常、任何输入 → 一律**原样回退给大 AI**，
预处理层永远不能让聊天变差。

---

## 九、验收与实测

### 9.1 功能验收（不看准确率也能验的硬指标）

| 项 | 标准 | 实测 |
|---|---|---|
| 用户原例「帮我合成一把稿子」 | 判成 craft 且解析出「镐子」 | ✅ craft，item=镐子（拼音模糊） |
| 材料不够 | 不执行，回报缺什么缺多少 | ✅ 注入文本含「还差：木棍 缺 2」 |
| 材料够 | 自动合成，回执写进注入文本 | ✅ 下发 `craft{item:minecraft:iron_pickaxe}` |
| 未连接 | 不执行、不报错，只提示 | ✅ |
| 闲聊 | 零干预、不注入 | ✅ |
| 装备到指定槽位 | 组合 `transfer` 而非无效的 `equip{slot}` | ✅ `transfer{from:inv:12,to:head}` |
| 丢指定物品 | 组合 `transfer` 到 `world:<坐标>` | ✅ |
| 存箱子 | 自动前置 `chestopen` 并等其跑完 | ✅ |
| 相对坐标 | 换算成主人坐标 / `~` | ✅ 拿不到主人坐标时**不执行** |
| 长任务坐标 | 只说相对位置不执行 | ✅ 「在我脚下挖矿」→ 提示而非开挖 |
| 回归 | 原有功能不退化 | ✅ `test_reconstruction` / `test_maid_loop` / `test_chat_unit` / `test_chat_chain` 全过、`main.py --smoke` exit=0 |
| NLU 单测 | 26 项（含泄漏守卫、分层对账、collect 半径） | ✅ 有/无 pypinyin 两种环境都过（未装 websockets 时 [8] 跳过） |
| 模组构建 | 改完能编 | ✅ JDK25 Gradle `BUILD SUCCESSFUL` |

### 9.2 准确率（含与上一版模型的对照）

208 条人工测试集（`nlu/data/manual_test.json`）。

**本轮（2026-09-12 · 标签口径规范化后）**：

| 配置 | top-1 准确率 | 自动执行档（≥0.85） | 闲聊误激活 |
|---|---|---|---|
| 合并前模型（27 类，`nlu_backup/report.before_collect_merge.json`） | 87.50% | 127 条 / 97.64% | 0 |
| **本版 · collect/pickup 合并（26 类，对抗 0.25 倍）** | **88.46%** | 134 条 / 96.27% | 0 |

> **口径提醒**：测试集里 8 条 `pickup` 标注已按 §11.1 规范改为 `collect`（语义合并，不是改答案迁就模型），
> 所以两行**不是严格同口径**；差值 0.96pt 也远小于 208 样本的标准误 ±2.1% → **不能宣称提升**。
> 收集类内部可见的实测变化：合并前 `collect` 5/6 + `pickup` 7/8 = **12/14**，合并后 `collect` **13/14**。
> 合成验证集 val_acc 97.44% → **97.99%**。

**历史对照（2026-09-11 那轮，标签口径未定时）**：

| 配置 | top-1 | 自动执行档（≥0.85） | 闲聊误激活 |
|---|---|---|---|
| v3 基线（`.workbuddy/nlu_base`） | 89.90% | 124 条 / 97.58% | 0 |
| 对抗语料关闭 | 84.86% | 117~120 条 / 95.7~96.7% | 0 |
| **对抗语料 0.25 倍（默认）** | **87.74%** | 122 条 / **98.36%** | 0 |
| 对抗语料 1 倍 | 85.58% | ~97% | 0 |

**结论（诚实版）**：
* 那轮 top-1 **没有超过** v3（差 2.16pt，差值标准误约 2.6pt）→ 统计上不可区分；
  真正要紧的「自动执行档」略好（98.36% vs 97.58%），同样在噪声内。
* 那轮的确定收益不在准确率，而在 §7 的指令层对齐（3 个原本**必然失败**的真实 bug）。
* 本轮收益是**语义上的**：`collect`/`pickup` 本来就是同一个 `CollectTask`，
  两个意图的边界**模型不可能学出来**（这是 §11.1 判定的「最大瓶颈是标签口径，不是模型」）。
  合并后边界消失，`range` 改由规则产出。

**仍未解决**（留给下一轮）：
* `store` ↔ `collect` 仍偶有混淆（「把散落的东西**收纳**一下」→ `collect`）——
  两者都带「收」字，语料还需要更多「收纳/归置 vs 捡/收地上的」对照。
* 测试集里 `把工作抬放到背包` 标的是 `store`，但按新规范（**点名了物品「工作台」**）应为 `transfer`。
  **本轮刻意没有改这条标注**，以免把分数往上抬——口径要不要对齐由你来定。
* 复合句、`farm`↔`place`、`sit`↔`stop` 的老错例仍在（单标签模型的固有局限，见 §11.2）。
* **置信度 ≥0.50 档的闲聊误激活从 0.00% 变成 2.17%**（≥0.70 起仍为 0.00%）。
  自动执行档（AUTO=0.85）不受影响、仍是 0.00%，HINT=0.55 只做「软提示」不执行，
  所以实际影响很小；但若要彻底干净，可以调高 HINT 阈值或再补一批闲聊负样本。

---

## 十、复现步骤

```bash
cd desktop-pet

# 1) 建隔离环境（只有 numpy + pypinyin，训练用）
python -m venv <venv>
<venv>/Scripts/pip install numpy pypinyin -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 生成离线拼音表（仅构建期需要 pypinyin）
<venv>/Scripts/python -m nlu.build_pinyin

# 3) 训练意图模型（合成语料 → 训练 → 评估 → 导出，约 5~6 分钟）
#    当前随包发布的模型用的就是这个配方（人工/蒸馏语料按固定权重过采样）
<venv>/Scripts/python -m nlu.train --n-per-intent 400 \
    --manual nlu/data/manual_train.jsonl --manual-w 5 \
    --distill nlu/data/distill_all.jsonl --distill-w 12 \
    --manual-test nlu/data/manual_test.json

# 4) 验证
python tests/test_nlu.py
```

> 运行桌宠本身**不需要** pypinyin / torch / sklearn：推理只用 numpy。

---

## 十一、已知限制与后续

### 11.1 标签口径（**已定规范** · 2026-09-12 用户拍板）

真实语句的剩余错例**大多不是模型能力问题**，而是几对意图在**语义上就重叠**。
下面这份规范是**唯一口径**：`taxonomy.py` 的意图边界表、`synth.py` 的对抗样本、
人工测试集的标注，都必须与它一致。

**决策依据是模组源码行为，不是文档描述**（核对 `MaidAIBridge.java` / `MaidTaskCommand.java`）：

| 组 | 模组侧事实 | 决策 |
|---|---|---|
| `collect` ↔ `pickup` | 同一个 `CollectTask`，只差默认半径（8 / 4） | ✅ **合并为 `collect`**，半径改由规则产出 |
| `store` ↔ `transfer` | `store` 只收主手、不收 `item`；`transfer` 任意槽位 | 保留两个，按「有无来源/物品」切分 |
| `break` ↔ `mine` | `break` 单格瞬发；`mine` 一片（range 4 / count 8，带挖掘过程） | 保留两个，按范围切分 |
| `guard` ↔ `move` ↔ `look` | `guard` 会主动打威胁；`move` 走过去；`look` 只转头 | 保留三个，按动词切分 |
| `build` ↔ `place` | `build` 是结构（height/blocks）；`place` 用主手放一格 | 保留两个，按产出物切分 |

**① 收集（合并后只有 `collect`）**

语义统一为「把地上的掉落物收进来」，半径由语句决定：

* 泛指一片 / 附近 / 掉落物（「收集一下」「附近的东西捡了」「把地上的东西收好」）→ **`range=8`**
* 指定就近 / 单个 /「捡起来」（「把这个捡起来」「捡一下那块」）→ **`range=4`**

模组的 `pickup` 指令**仍然合法**（`/maidtasks pickup`、AI DSL 都能用），
但 NLU **不再产出 `pickup` 意图** —— 它只是 `collect(range=4)` 的捷径。

> 实现（`nlu/slots.py::collect_radius`）：**出现范围词（一片/附近/周围/掉落物/都…）→ 8；
> 否则出现单目标词（就近/这个/脚边/捡起来…）→ 4；都没有 → 8**（等价于不传 `range`，
> 用模组默认）。刻意做得简单——半径只是「看多远」，判粗了也不影响意图正确性。
> 反例警示：「地上 / 撒 / 散」这类**位置词不进范围词表**，否则
> 「把地上那个盾拾起来」会被误判成 8。

**② 收纳与换手（`store` / `equip` / `transfer`）**

`store` 存在的意义是**快速切换手持**：把当前主手收走、腾出手，配合 `equip`
拿出要用的武器/工具（挖矿切镐子、打架切剑弓）。三者的边界看**动词在干什么**：

* **收拾 / 收纳类动作**（「收起来 / 收纳 / 归置 / 装回包里 / 手上别攥着了」）
  —— 哪怕提到「主手 / 手上」这种**来源位置**，只要**没点名具体物品** → `store`
  （模组 `store` 本来就只收主手，所以「主手那件」是它天然的宾语）
* **点名了具体物品**（「把铁镐换到副手」「把钻石剑收进背包第 3 格」）
  或**两样东西对调**（「胸甲跟靴子换一下」）→ `transfer`
* **要指定拿到手上**（「切出镐子 / 换成剑 / 拿起弓 / 换上斧头」）→ `equip`

> 一句话：**「收拾」是 `store`，「点名 A 搬到 B」是 `transfer`，「拿到手上」是 `equip`。**
> 泛称（「那件 / 东西 / 家伙什」）**不算**点名；写了物品名才算。
> 反例警示：「把**主手那件**塞回包里」是 `store`（泛称宾语 + 收纳动词）；
> 「把**铁镐**收进背包」是 `transfer`（点名了物品）。

**③ 挖除（`break` / `mine`）**

「砸掉 / 挖掉**这一个**方块」「把这扇门拆了」→ `break`；
带范围、「一片 / 这一带」、矿石、「挖点矿」→ `mine`。

**④ 移动与护卫（`guard` / `move` / `look`）**

含「守 / 护 / 挡 / 保护」→ `guard`（会主动打威胁）；
「过去 / 过来 / 站我旁边 / 跟上」→ `move`；只是「看 / 盯着」人不动 → `look`。

**⑤ 建造与放置（`build` / `place`）**

产出是**建筑 / 结构**（房子 / 塔 / 墙 / 桥 / 喷泉）→ `build`；
产出是**单个方块**（放个火把 / 摆个箱子）→ `place`。

> 规范定完后语料与人工测试集按它重标；`synth.DISAMBIG` 逐对写对抗样本，
> 由 `tests/test_nlu.py::test_no_leak` 保证对抗样本不是从测试集抄来的。
> 改语料后**必须**重跑 `python tools/check_leak.py`。

### 11.2 复合句

单标签模型只出一个意图。目前用「① 否定优先 ② 最终产出优先 ③『顺便』弱化 ④ 核心目的优先」
四条约定把它变成可训练规则（`synth.COMPOUND`），但**真实复合句的表述千变万化**，
靠模板很难覆盖全。两种解法：
* 廉价：把主意图判定**上移到路由层**——切句 → 逐句识别 → 按约定取主意图（规则确定，不依赖模型）
* 彻底：改成多标签 / 序列标注（`nlu/taxonomy.py` 的意图集不用动，换 head 即可）

### 11.3 其他

1. **坐标类长任务**：`mine`/`farm`/`build` 必须给绝对坐标才自动执行（相对位置副作用太大）。
   后续可接模组感知（女仆看向的方块 / 玩家所在格）把「脚下」变成确定坐标。
2. **`craft` 之外只做「直发指令」**：其他意图没有 dry-run 与结果校验，
   回执 ok 即视为成功（任务型指令的异步结果由既有事件通道播报）。
3. **`cancel_previous` 默认 true**：用户显式指令会抢占女仆当前任务。
   若希望「排队不抢占」，改 `nlu/router.py` 的 `send_command` 调用即可。
4. **语料是合成的**：真实分布会漂移。建议用 `logs/perf.log` 里的 NLU 记录 +
   真实对话抽查，把「低置信/判错」的样本补进语料再训一版。
5. **物品词典依赖模组下发**：纯桌面（不开游戏）时只有 wiki 降级词典，
   认得出名字但执行不了 —— 这是有意为之（避免误执行）。
6. **改语料务必跑泄漏审计**：`python tools/check_leak.py`。
   本项目踩过一次——把 27 条测试句抄进训练语料，人工测试集从 89.9% 虚报到 98.1%。

---

## 十二、回滚

* 开关：`config.NLU_ENABLED = False`（或 QSettings `nlu_enabled=false`）→ 完全回到旧行为
* 代码：`nlu/` 为独立子包，删掉后只需回退 `modes.py / chat.py / stt_handler.py / window.py`
  四处接线与 `maid_link.py` 的增量方法；模组的 `craft_check` / `item_index` 是纯增量，不删也不影响旧功能

---

## 十二·五、指令分层：基础 vs 集成（与 MaidTaskCommand javadoc 对齐）

> 来源：`SmartMaid/src/main/java/.../command/MaidTaskCommand.java` 第 51-67 行 javadoc。
> 这是模组层面的官方分层，桌宠 NLU 的 `taxonomy.layer` 与 `mod_contract.layer` **必须与之一致**。

### 12.1 三层定义

| 层 | 含义 | 模组示例 | 桌宠行为 |
|---|---|---|---|
| **basic** | 基础指令（原子动作）：单一动作，模组直接接受 | `move`/`look`/`break`/`place`/`use`/`equip`/`store`/`drop`/`pickup`/`sit`/`stop` | 单步执行；`equip`/`drop` 指定槽位/物品时**组合 `transfer`**（basic 的增强） |
| **integrated** | 集成指令（任务级）：一个意图可能展开成多步 | `attack`/`guard`/`feed`/`eat`/`mine`/`farm`/`build`/`collect`/`craft`/`smelt` + 桥接 `transfer`/`chestopen`/`chestput`/`chesttake` | 多步编排：容器类前置 `chestopen`、`transfer` 反查 `from`、`craft` 走 dry-run |
| **meta** | 元命令（管理；不产生任务） | `cancel`/`status`（+ 桌宠 `chat`） | 无参数直发；`chat` 不产生任何指令 |

> 桌宠侧另有 `meta-dryrun`：`craft_check` 是桌宠内部查询（不消耗材料），不属于任何意图（用户不会"说" craft_check），只在 router 内部用。

### 12.2 router 的显式分派

`NluRouter._plan()` 按指令分层走三条路径，避免"集成编排逻辑"与"基础原子"互相污染：

```
_plan(res)
  ├─ taxonomy.layer_of(intent) == "basic"      → _plan_basic(res)
  │                                              · equip/drop 指定槽位/物品 → 组合 transfer
  │                                              · 其余 → _fill_params(res, it)
  ├─ taxonomy.layer_of(intent) == "integrated" → _plan_integrated(res)
  │                                              · chestput/chesttake → 前置 chestopen
  │                                              · transfer → 反查 from
  │                                              · 其余 → _fill_params(res, it)
  └─ taxonomy.layer_of(intent) == "meta"       → _plan_meta(res)
                                                  · chat → 不产生指令
                                                  · cancel/status → 无参数直发
```

`_fill_params()` 是通用契约填充（按 `mod_contract` 声明的参数名填 `item/count/range/pos/...`），basic 与 integrated 共用，保证参数命名与取值域零漂移。

### 12.3 集成失败不回退

集成层任一步失败（如 `chestopen` 超时、`transfer` 反查不到 `from`）**不回退到基础原子直接调**，降级为 hint 把已知信息交给大 AI 追问。理由：集成指令的副作用是组合的，回退可能产生不一致状态（如箱子没打开就 chestput 必失败）。

### 12.4 桥接组合（MaidTaskCommand javadoc 未显式列出）

`transfer` / `chestopen` / `chestput` / `chesttake` 是模组协议实际支持但 javadoc 没在「集成 / 基础」段列出的指令。桌宠归到 **integrated**，因为它们要么需要前置状态（`chestopen` provides `opened_container`）、要么需要组合（`chestput` = `chestopen` + `transfer`）。这条决策写进了 `tests/test_nlu.py::test_layer_alignment` 的"桥接归 integrated"断言。

### 12.5 对账守卫

`tests/test_nlu.py::test_layer_alignment` 做四件事，任一不一致就红：
1. taxonomy.layer 与 MaidTaskCommand javadoc 三层完全对齐
2. mod_contract.layer 与 taxonomy.layer 严格一致（chat 例外：意图有、契约无）
3. `craft_check` 在 mod_contract 是 `meta-dryrun`、不在 taxonomy
4. MaidTaskCommand 的集成段（9）+ 基础段（11）+ 元命令（2）必须在两份表里都齐

> **两处允许的不对称**（都在 §11.1 规范里）：
> * `pickup` —— **契约有、意图无**：模组指令仍在（`/maidtasks pickup`、AI DSL 合法），
>   但 NLU 已把它合并进 `collect(range=4)`，不再产出 `pickup` 意图；
> * `craft_check` —— **契约有、意图无**：桌宠内部干跑查询，用户不会「说」它。

---

## 十三、Wiki 知识注入（本地化链路）

意图识别之外，桌宠的 **Wiki 知识注入** 也已本地化：人工提炼的 curated 知识库（1490 条：游戏实体 + 成就/进度）优先命中，长尾走本地精炼小模型，原文/大 AI 会话保底。设计见 [DESIGN_WIKI_REFINE.md](DESIGN_WIKI_REFINE.md)。
