# MC 渠道发布物料（Modrinth / mcmod.cn / 苦力怕论坛 / CurseForge）

> 面向 Minecraft 垂直渠道的发布准备。**结论：模组本身是纯代码 MIT，不受角色形象 CC BY-NC-SA 的约束**，
> 可以放心发；但下面「一」的三件事必须先对齐，否则容易被误评。

> **2026-09-24 策略更新（用户拍板）：只发女仆模组 SmartMaid，deskpet-mod 暂缓。**
> 女仆的可投递物料在 [`docs/publish/`](publish/README.md)（`modrinth-smartmaid.md` /
> `mcmod-smartmaid.md` / `klpbbs-smartmaid.md` / `curseforge-smartmaid.md`）；
> **P4 发布收尾已完成**（命令权限 `LEVEL_GAMEMASTERS` + debug 关闭 + 图标 + 重建验证）。
> 本文其余章节保留为 deskpet-mod 的背景与物料（暂缓，但随时可启用）；
> 另：SmartMaid 已开源 <https://github.com/oyxdsg/SmartMaid>。

---

## 一、发布前必须对齐的三件事

### 1. ⚠️ 模组单独装「看不到效果」

这是**最大的转化风险**。`deskpet-mod` 只做一件事：把游戏里发生的事写成 JSON 文件到
`.minecraft/deskpet/`。**真正的体验（说话、播报、点评建筑）在桌宠 App 里。**

MC 渠道用户的心智是「装了就生效」。所以：

* 文案**第一行**就要写明「**配套模组，需配合桌宠程序使用**」；
* 不要用「AI 女仆模组」这类标题党——引来的人 5 分钟后会发现没有女仆，然后留差评。

### 2. ⚠️ 女仆功能**不在**这个模组里

「跟着你跑、替你挖矿打怪、听你说话」这些是**姊妹项目 SmartMaid** —— **已开源**：
<https://github.com/oyxdsg/SmartMaid>（代码 MIT，Emotecraft 表情 GPL-3.0、大肥鱼皮肤 CC BY-NC-SA，
明细见其 `NOTICE.md`）。

所以 MC 渠道**可以讲完整的三件套故事**了：`deskpet-mod`（游戏事件采集 + 建筑识别）→
`DeskPet` 桌宠程序（桌面上的 AI 互动）→ `SmartMaid`（游戏内的 AI 女仆）。
但本模组的定位不变：**它是配套模组**，文案仍以「事件采集 + 建筑识别」为主体，
SmartMaid 和桌宠作为「生态」在结尾安利——不要把女仆写进本模组的功能清单。

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

## Credits

The character art used for this mod's icon / gallery images is a derivative work based on
**「溟月」 by 上善无形** (original character) and **女仆鲸鱼娘 by ZipZipPipe** (Bilibili, secondary design),
used under **[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)** —
attribution required, **non-commercial only**, derivatives share-alike.

> 角色形象：「溟月」© 上善无形 ｜ 女仆版二次设计 © ZipZipPipe（B 站）｜ CC BY-NC-SA 4.0

The mod itself contains **no artwork at all** — it is code only and is licensed **MIT**.

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

## 五、元数据（✅ 已改）

`src/main/resources/fabric.mod.json` 已更新：

```json
"description": "DeskPet 桌宠配套模组：采集游戏事件 + 本地建筑识别，输出到 .minecraft/deskpet/",
"authors": ["oyxdsg"],
"contact": {
  "homepage": "https://github.com/oyxdsg/Multimodal-AI-Companion",
  "sources":  "https://github.com/oyxdsg/Multimodal-AI-Companion",
  "issues":   "https://github.com/oyxdsg/Multimodal-AI-Companion/issues"
},
```

原描述**漏了建筑识别**（本模组最有价值的部分），且 `authors` 写的是 `"DeskPet"` 而非人 —— 都已修正。

**`"icon"` 字段故意没加**，理由见 §六。重新构建：JDK 25 已确认可用（HMCL 运行时，OpenJDK 25.0.1），
构建命令见 `deskpet-mod/README.md §四`。

---

## 六、素材与署名

### 已就绪

| 文件 | 用途 | 尺寸 | 体积 |
|---|---|---|---|
| `docs/images/deskpet-mod-icon.png` | 商店图标（Modrinth / mcmod / 论坛） | 512×512 | 321 KB |
| `docs/images/deskpet-mod-cover.png` | 商店封面 / 帖子头图 | 1781×1002 | 1.4 MB |

**更正此前的说法**：我上一版写「别用鲸鱼娘做商店主图标」，**说重了**。
CC BY-NC-SA 的 NC 禁的是**商业使用**，不是「不能出现」。用在免费开源模组的商店页上
属于**非商业使用，允许**。

### ⚠️ 但署名是硬要求

素材里的角色是**他人作品**：原创 OC「溟月」@上善无形，女仆版二次设计 @ZipZipPipe（B 站），
以 CC BY-NC-SA 4.0 开放二次创作 —— **署名（BY）是使用的前提**。

**每个用到该图的地方，都请带上这一行：**

> 角色形象：「溟月」© 上善无形 ｜ 女仆版二次设计 © ZipZipPipe（B 站）｜ CC BY-NC-SA 4.0

（Modrinth 的 description 里已加好 Credits 段；mcmod 简介、苦力怕帖子正文也要各带一次。）

### 关键取舍：图标**不放进 jar**

`fabric.mod.json` 里故意**没有**加 `"icon"` 字段，因为：

* **放进去** → jar 里就含了一份 CC BY-NC-SA 衍生作品，**与 jar 声明的 MIT 冲突**，
  且整个 jar 会变成「仅限非商业使用」，使用者容易误判；
* **不放进** → jar 保持**纯代码 MIT**，商店图单独按 CC BY-NC-SA 使用 —— 两边都干净。

Modrinth / mcmod.cn / 苦力怕论坛的图标都是**项目级图片**，不要求来自 jar，所以这个取舍**零损失**。

> 如果你确实想在游戏内的模组列表里看到图标，就得接受「jar 变成混合许可」——说一声我加上。

> **2026-09-24 备注（SmartMaid 的不同取舍）**：女仆模组的 jar **本来就带** CC BY-NC-SA 皮肤
> （女仆没有皮肤就不能看），jar 是混合许可、由 `NOTICE.md` 声明——所以它的图标直接进了
> jar（`assets/smartmaid/icon.png`，Mod Menu 可显示），不矛盾。deskpet-mod 的纯代码取舍仅对它自己适用。

### 不要做的

* ❌ 把该图用于**任何变现场景**（B 站激励、公众号流量主、付费专栏、爱发电…）—— 直接违反 NC
* ❌ 去掉署名后转发

---

## 七、执行顺序（2026-09-24 起按「女仆优先」执行）

**SmartMaid（当前主推，物料已齐）：**

1. ✅ P4 发布收尾（命令权限 + debug 关闭 + jar 图标 + 重建验证）
2. ✅ 图标 / 封面（`smartmaid-icon.png` 512×512、`smartmaid-cover-240x150.png`）
3. ✅ 四渠道投递物料（[`docs/publish/`](publish/README.md)，`--project smartmaid` 为脚本默认）
4. ⬜ **发 Modrinth**（等你 token：`python docs/publish/publish_modrinth.py create` → `submit`）
5. ⬜ **苦力怕论坛发帖**（`publish/klpbbs-smartmaid.md`，截图已齐）
6. ⬜ **提 mcmod.cn 收录**（`publish/mcmod-smartmaid.md`）
7. ⬜ **CurseForge**（`publish/curseforge-smartmaid.md`）
8. ⬜ 发完回知乎/B 站补「已上架 Modrinth」

**deskpet-mod（暂缓，截图是唯一硬缺口）：**

- ⬜ 游戏内截图 2~3 张（建筑识别效果）——补齐后其物料可随时启用
- ⬜ Modrinth / mcmod / 苦力怕 / CurseForge（`--project deskpet-mod`）
