# CurseForge 发布 · SmartMaid（智能女仆）

> CurseForge 无法纯 API 建项目（必须网页走一次作者流程），文件上传可走 API。
> 总规则见 [`curseforge.md`](curseforge.md)（deskpet-mod 版，流程相同，内容不同）。

## 前置

| 项 | 状态 |
|---|---|
| P4 发布收尾 | ✅ 已完成（权限 `LEVEL_GAMEMASTERS` + debug 关闭 + 图标） |
| 项目图标 | ✅ `SmartMaid/docs/images/smartmaid-icon.png`（512×512，特写构图） |
| 英文正文 | ✅ 复用 [`modrinth-description-smartmaid.md`](modrinth-description-smartmaid.md) 正文代码块（CurseForge 编辑器语法兼容 markdown 子集，粘贴后预览检查） |
| 作者账号 + API token | ⬜ 你：<https://curseforge.com/> 注册作者 → <https://curseforge.com/account/api-tokens> 取 token |

## 网页建项目（一次性，照抄）

入口：<https://www.curseforge.com/minecraft/search?class=mods> → 右上 Start a Project → Minecraft → Mod。

| 字段 | 值 |
|---|---|
| Project name | `Smart Maid` |
| Project slug / URL | `smart-maid`（`smartmaid` 大概率也行，被占就加 `-mod`） |
| Project summary | `A rule-driven AI maid: follows, fights, jumps gaps and really does your chores. Optional AI chat & voice via the free DeskPet companion.` |
| Categories | `Adventure and RPG`（主）+ `Utility & QoL` |
| License | `MIT` |
| Description | 粘贴英文正文；**首屏保留** Requirements 块（26.2 + Fabric API + Java 25 + 命令需 OP + 聊天需外部程序） |

建好后记下项目 **slug/ID**（API 传文件要用）。

## 上传版本文件（API）

```powershell
$env:CURSEFORGE_TOKEN="你的token"
python docs/publish/publish_curseforge.py --project smart-maid --jar "C:\Users\86187\Desktop\女仆项目开发\SmartMaid\build\libs\smartmaid-0.1.0.jar"
```

（脚本待写——先用网页也行：项目页 → File → Upload File，选 jar，游戏版本勾 `26.2`，加载器勾 `Fabric`，
依赖声明里搜 `Fabric API` 选 **Required**，changelog 见下。）

| 字段 | 值 |
|---|---|
| Display name | `Smart Maid 0.1.0` |
| File name | `smartmaid-0.1.0.jar`（~0.9 MB） |
| Game versions | `26.2` + `Fabric` + `Java 25`（有这项就勾） |
| Release type | `release` |
| Required dependencies | `fabric-api` |

Changelog：

```
First release: rule-driven AI maid for Minecraft 26.2 (Fabric) —
combat (shield / melee / bow), jump pathfinding, real chores
(mining / farming / building / crafting), 41-slot inventory,
wooden settings menu. MIT, open source.
```

## 注意

- PC-only 模组（client required / server optional）通常**自动审核、几分钟上线**。
- CurseForge 对皮肤/素材的版权表单：素材按 CC BY-NC-SA 分发（非商业、署名、相同方式共享），在版权下拉里选 `Custom` 并粘贴一句：
  `Code MIT. Skin CC BY-NC-SA 4.0 (see NOTICE). Emote data GPL-3.0 (Emotecraft).`
- 描述里必须保留对 DeskPet 外部程序的披露——CurseForge 审核对"需要配套程序"的项目会要求写明。
