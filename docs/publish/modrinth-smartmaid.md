# Modrinth 发布表单 · SmartMaid（智能女仆）

> 用 [`publish_modrinth.py`](publish_modrinth.py) 一条命令上传（`--project smartmaid` 已是默认），
> 或按本文件在网页 <https://modrinth.com/dashboard> 手动创建。
> slug `smartmaid` **可用**（2026-09-24 查询 404），Modrinth 上无同名/同类项目（搜索 total_hits=0）。

## 前置条件

| 项 | 状态 |
|---|---|
| 英文正文（规则 §2 硬要求） | ✅ [`modrinth-description-smartmaid.md`](modrinth-description-smartmaid.md) |
| 图标 512×512 | ✅ `SmartMaid/docs/images/smartmaid-icon.png`（特写构图，与 deskpet-mod 的全身像区分） |
| 游戏/加载器标签 | ✅ `26.2` + `fabric` 均在 Modrinth 标签表内（已核实） |
| token | ✅ 已生成（`PROJECT_CREATE`+`VERSION_CREATE`）并已通过邮箱验证；**收尾需另建带写权限的**（见下） |
| P4 发布收尾（权限+日志） | ✅ 已完成（2026-09-24，`LEVEL_GAMEMASTERS` 权限 + debug 关闭 + 重建） |

## 权限位（2026-09-24 实测）

PAT 的权限位按动作分开，**只勾 CREATE 两个只能建、不能改**：

| 动作 | 权限位 |
|---|---|
| 建项目 `POST /project` | `PROJECT_CREATE` |
| 传版本 `POST /version` | `VERSION_CREATE` |
| **提审** / 改项目元数据 / 传 gallery `PATCH /project/<id>` | `PROJECT_WRITE` |
| 改版本元数据（如 `environment`）`PATCH /version/<id>` | `VERSION_WRITE` |
| 读自己的草稿 `GET /project/<slug\|id>` | `PROJECT_READ` |

症状：只勾 CREATE 时 `PATCH /project` → `401 Invalid Authentication Credentials`；
`GET` 草稿 → **404**（草稿对无权限者就是 404）。
**建议一次勾齐 6 个**：`PROJECT_CREATE` · `PROJECT_READ` · `PROJECT_WRITE` ·
`VERSION_CREATE` · `VERSION_READ` · `VERSION_WRITE`。
另外 **草稿项目用 slug 查不到**（即使 owner），`create` 会把 id 记进
`publish_modrinth_state.json`（已 gitignore），后续命令靠它定位，也可 `--project-id` 指定。

## 表单字段（网页创建时照抄）

| 字段 | 值 |
|---|---|
| Project name | `Smart Maid` |
| Project slug | `smartmaid` |
| Summary（一句话，≤256 字符） | `A rule-driven AI maid: she follows, fights, jumps gaps and really does your chores. Optional AI chat & voice via the free DeskPet companion.` |
| Body | 粘贴 [`modrinth-description-smartmaid.md`](modrinth-description-smartmaid.md) 代码块内的内容 |
| Categories | `adventure` · `mobs` · `game-mechanics`（`mobs` 分类**确实存在**，实测 API 接受；此前以为没有是记错了） |
| Environment | `client_and_server`（`fabric.mod.json` 的 `environment` 是 `*`，服务端与客户端入口点都有）。⚠️ 该字段要显式传，**漏传页面就显示 unknown** —— 已补进脚本，首个版本需 `VERSION_WRITE` 权限 PATCH 修正 |
| License | `MIT`，additional license URL 填 <https://github.com/oyxdsg/SmartMaid/blob/main/NOTICE.md>（jar 内含 GPL-3.0 表情与 CC BY-NC-SA 皮肤，声明必须可达——正文里也写全了） |
| Source URL | `https://github.com/oyxdsg/SmartMaid` |
| Issues URL | `https://github.com/oyxdsg/SmartMaid/issues` |
| Discord / Wiki | 留空 |
| Project icon | 上传 `smartmaid-icon.png` |

## 版本字段

| 字段 | 值 |
|---|---|
| Version number | `0.1.0` |
| Version title | `Smart Maid 0.1.0` |
| Channel | `release` |
| Loaders | `Fabric` |
| Game versions | `26.2` |
| Dependencies | **Required** → `fabric-api`（脚本自动按 slug 关联；网页手动创建时在 Dependencies 搜 fabric-api 选 Required） |
| Files | `smartmaid-0.1.0.jar`（~0.9 MB，**不要传** `-sources` jar） |
| Changelog | `First release: rule-driven AI maid for Minecraft 26.2 (Fabric) — combat, jump pathfinding, chores, 41-slot inventory, wooden settings menu.` |

## 当前状态（2026-09-24 14:55）

| 项 | 状态 |
|---|---|
| 项目 | ✅ 已建为**草稿**，id `WZi1HspD`，slug `smartmaid`，MIT 识别正常，图标已上传 |
| 版本 `0.1.0` | ✅ 已上传（id `P86FhwTB`，904,065 B，sha1 `755b0c1f…`），已自动关联 `fabric-api` 为 **required** |
| `26.2` / `fabric` 标签 | ✅ 已带上 |
| gallery 三张截图 | ⬜ 待传（需 `PROJECT_WRITE`） |
| `environment` | ⬜ 仍是 `unknown`（需 `VERSION_WRITE`；或用网页端版本编辑页勾 Client/Server） |
| 提审 | ⬜ 待做（需 `PROJECT_WRITE`；或网页端点 Submit for review） |

## 上传后自检（提交审核前）

1. 首屏能看到三件事：**需要 26.2 + Fabric API + Java 25**、**命令需要 OP**、**聊天功能需要外部程序 DeskPet**（规则 §2 的披露要求，也是防差评的第一道闸）。
2. 三个 gallery 截图：背包 GUI / 管理面板 / 对话气泡（`SmartMaid/docs/images/`；脚本 `gallery` 子命令已按此配置）。
3. License 显示为 MIT，且正文「License & credits」节可见 GPL-3.0 与 CC BY-NC-SA 归属。
4. 确认无误再点 Submit for review（脚本对应 `submit` 子命令）。
