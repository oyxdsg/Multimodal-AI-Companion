# MC 渠道发布物料（Modrinth / mcmod.cn / 苦力怕论坛）

> 面向 Minecraft 垂直渠道的发布准备。**结论：模组本身是纯代码 MIT，不受角色形象 CC BY-NC-SA 的约束**，
> 可以放心发；但下面「一」的三件事必须先对齐，否则容易被误评。

---

## 一、发布前必须对齐的三件事

### 1. ⚠️ 模组单独装「看不到效果」

这是**最大的转化风险**。`deskpet-mod` 只做一件事：把游戏里发生的事写成 JSON 文件到
`.minecraft/deskpet/`。**真正的体验（说话、播报、点评建筑）在桌宠 App 里。**

MC 渠道用户的心智是「装了就生效」。所以：

* 文案**第一行**就要写明「**配套模组，需配合桌宠程序使用**」；
* 不要用「AI 女仆模组」这类标题党——引来的人 5 分钟后会发现没有女仆，然后留差评。

### 2. ⚠️ 女仆功能**不在**这个模组里

「跟着你跑、替你挖矿打怪、听你说话」这些是**姊妹项目 SmartMaid**（另一个独立仓库，
**目前没有远程仓库、未公开**）。

所以 MC 渠道现阶段**只能讲两件事**：**游戏事件采集** + **建筑识别**。
想讲完整故事，得先把 SmartMaid 也开源出去 —— 而它基于车万女仆（Touhou Little Maid）派生
（包名 `com.tartaricacid.smartmaid`），开源前要先理清派生部分的许可。**这是你的决定，我不擅自动。**

### 3. ✅ jar 已验证可用（2026-09-24 开包检查）

| 项 | 实测 |
|---|---|
| 文件 | `build/libs/deskpet-mod-2.0.0.jar`（0.12 MB，46 个 class） |
| 声明版本 | `"minecraft": "~26.2"`、`"java": ">=25"`、`mixin compatibilityLevel: JAVA_25` |
| 依赖 | `fabricloader >=0.18.4`、`fabric-api` |
| 代码与 jar 是否同步 | 同步（src 下无文件比 jar 新）✓ |
| **是否含图像资源** | **0 张** —— 纯代码 → **可标 MIT** ✓ |

---

## 二、Modrinth（**建议第一个发**：自助、免审核、有长期自然流量）

### 项目信息

| 字段 | 建议填写 |
|---|---|
| Project type | Mod |
| Name | `DeskPet Mod` |
| Slug | `deskpet-mod` |
| Summary（一句话） | `Companion mod for the DeskPet desktop AI pet — it lets your desktop pet see what happens in your world.` |
| License | **MIT** |
| Client | **Required** |
| Server | Not required（数据由本机桌宠程序读取） |
| Categories | `Fabric` · `Utility` · `Mobs`（或 `Adventure`） |
| Links | Source / Issues → <https://github.com/oyxdsg/Multimodal-AI-Companion> |

### Description（可直接粘贴）

```markdown
**DeskPet Mod** is the companion mod for [DeskPet](https://github.com/oyxdsg/Multimodal-AI-Companion) —
a desktop AI companion app for Windows. It gives your desktop pet a pair of eyes inside your world.

> ⚠️ **This mod does nothing on its own.** It only writes structured data files to your `.minecraft/`
> folder; the actual experience (talking, narrating, commenting on your builds) requires the DeskPet
> desktop app. Install it only if you plan to use that app.

## What it does

### 1. Game event collection
Watches what you do and aggregates it into 20-second windows: blocks broken, mobs killed, items used,
items gained, damage taken, deaths, advancements, dimension changes, chat, distance travelled.
Each window is graded `CRITICAL` / `NORMAL` / `LOW` so the app can decide whether it is worth speaking.

### 2. Building recognition
Remembers the blocks you place, then analyses the result entirely locally:

- 3D structure scan: walls, rooms, cavities, pillars, beams, symmetry, roof type
  (flat / gabled / pointed / dome), silhouette, solidity, material layering
- Colour analysis from **real texture-sampled colours** (1268 blocks mapped), not keyword guesses
- Classification into ~25 categories: house / villa / cabin / matchbox / tower / spire / lighthouse /
  castle / wall / bridge / arch / pool / fountain / statue / pixel art / farm / ranch / estate / underground…
- Style inference: rustic / modern / gothic / japanese / chinese / mediterranean / industrial …
- A 3-dimension score (structure / decoration / colour) plus **targeted, actionable suggestions**
- **In-progress intent prediction** — while you are still building it guesses what you are making and
  how far along you are, so the pet can comment mid-build instead of only at the end

## Requirements

- **Minecraft 26.2** (Fabric)
- **Java 25** (required by the game itself)
- **Fabric API**
- The **DeskPet desktop app** (Windows) for the actual experience

## Where the data goes

Everything is written under `.minecraft/deskpet/`:

| Path | Content |
|---|---|
| `YYYYMMDD-HHMM.jsonl` | 20-second event windows (auto-cleaned after 2 minutes) |
| `buildings/latest.json` | Live building snapshot |
| `buildings/history.jsonl` | Completed buildings, archived |
| `blocks/*.jsonl` | Placed / broken block log (append-only) |
| `state.json` | Current environment state |

## Scope

**Singleplayer / LAN only.** On remote multiplayer servers the server-side events
(kills, damage, block breaking, building capture) are not available to the client, so collection is skipped.

## Privacy

**Everything stays on your machine.** No network requests, no telemetry, no accounts, nothing is uploaded.

## Notes

- This mod ships **no textures, models or artwork** — it is code only.
- It does not alter gameplay: it only observes and writes files.
  Still, please respect each server's mod rules.
```

### Version 信息

| 字段 | 值 |
|---|---|
| Version name | `2.0.0` |
| Version number | `2.0.0` |
| Release channel | Release |
| Game versions | **26.2** |
| Loaders | Fabric |
| Dependencies | Fabric API（required） |
| File | `deskpet-mod-2.0.0.jar` |

> 发布前建议先把 `fabric.mod.json` 的描述补上建筑识别、作者改成真实署名、补 `contact` 链接
> —— 见「五、建议先改的元数据」。

---

## 三、mcmod.cn（MC 百科）

**为什么值得做**：中文 MC 圈搜模组的入口，收录后有长期自然搜索流量。

需要准备：

| 项 | 内容 |
|---|---|
| 中文名 | `DeskPet 桌宠联动` |
| 英文名 | `DeskPet Mod` |
| 支持版本 | `26.2`（Fabric） |
| 前置 | Fabric API |
| 模组关系 | 前置/联动：DeskPet 桌宠程序（站外程序，正文说明） |
| 简介 | 见下 |
| 图标 | 需自行上传（512×512 以上） |

**简介草稿**

> DeskPet 桌宠的配套模组：把你的 Minecraft 世界「讲给」桌面上的 AI 伙伴听。
>
> 它做两件事：**① 把你在游戏里的行为**（挖掘、击杀、获得物品、受伤、死亡、进度、聊天、移动）
> **聚合成事件窗口**；**② 识别你正在建的建筑**——三维结构扫描 + 真实纹理取色的配色分析，
> 分类到高塔/城堡/别墅/农场/雕像等约 25 类，推断风格，并给出针对性的改进建议，
> 还能在你**建造过程中**就猜出你在建什么。
>
> ⚠️ **注意：本模组只是数据采集端，需要配合 DeskPet 桌宠程序（Windows/开源/MIT）才能看效果。**

---

## 四、苦力怕论坛（klpbbs.com，230 万会员）

**为什么值得做**：中文 MC 最大的资源社区，模组区活跃度高。

**发帖建议**

* 版块：Java 版 → 模组（若有「辅助/工具」子版更合适）
* 标题：`[26.2][Fabric] DeskPet 桌宠联动 —— 让你的 AI 伙伴看得见你在游戏里干了什么`
* 正文结构：
  1. **一句话是什么** + 明确的「配套程序」提示
  2. 截图 2~3 张（**建议用游戏内建筑识别效果**，见「六、素材」）
  3. 功能清单（事件采集 / 建筑识别，各 3~5 条）
  4. 安装步骤（Fabric + Fabric API + 丢 mods + 配套程序怎么连）
  5. 数据写到哪（`.minecraft/deskpet/`）
  6. 已知限制（单人有效 / 多人不采集事件）
  7. 链接：GitHub + 论坛附件（jar）
* 附件：直接传 `deskpet-mod-2.0.0.jar`

---

## 五、建议先改的元数据（**需重新构建 jar**）

`src/main/resources/fabric.mod.json` 目前三项偏弱：

```json
"description": "为桌面宠物（DeskPet）提供结构化游戏事件：监听玩家行为并聚合输出到独立目录 deskpet/",
"authors": ["DeskPet"],
```

建议改为：

```json
"description": "DeskPet 桌宠配套模组：采集游戏事件 + 本地建筑识别，输出到 .minecraft/deskpet/",
"authors": ["oyxdsg"],
"contact": {
  "homepage": "https://github.com/oyxdsg/Multimodal-AI-Companion",
  "sources":  "https://github.com/oyxdsg/Multimodal-AI-Companion",
  "issues":   "https://github.com/oyxdsg/Multimodal-AI-Companion/issues"
},
```

并建议加一个 **`"icon": "assets/deskpet-mod/icon.png"`**（Modrinth / mcmod / 论坛都吃图标）。

> 改完要 `.\gradlew.bat build` 重新出 jar（**注意必须用 JDK 25**，见 `README.md §四`）。

---

## 六、素材（**注意许可**）

⚠️ 上一条已确认「大肥鱼/女仆鲸鱼娘」是 **CC BY-NC-SA（禁商用）**。因此商店封面与截图：

| 建议 | 原因 |
|---|---|
| ✅ **用游戏内建筑识别截图 / 建造过程截图** | 不含角色形象，完全避开 NC |
| ✅ 用抽象图标（方块 + 波纹/眼睛意象） | 同上 |
| ⚠️ 桌宠气泡截图（含鲸鱼娘） | **可以放**，但别让它成为**主体**；且该内容不得用于商业目的 |
| ❌ 用鲸鱼娘做商店主图标 | 直接踩 NC，且可能被原作者找上门 |

---

## 七、执行顺序建议

1. 改 `fabric.mod.json` 元数据 + 加 icon → 重新构建 jar
2. 出 2~3 张游戏内截图（建筑识别效果最有说服力）
3. **发 Modrinth**（自助、免审核）
4. 提 **mcmod.cn 收录**
5. **苦力怕论坛**发帖（正文 + 附件）
6. 三处都发完后，再回知乎/B站补一条「已上架 Modrinth」的更新
