# MC 渠道发布包 · SmartMaid（智能女仆）

> **当前策略（2026-09-24 用户拍板）：只发女仆模组，deskpet-mod 暂缓。**
> deskpet-mod 的物料保留在本目录备查（[`modrinth.md`](modrinth.md) / [`curseforge.md`](curseforge.md) /
> [`mcmod.md`](mcmod.md) / [`klpbbs.md`](klpbbs.md)），暂不投递。

| 文件 | 渠道 | 状态 |
|---|---|---|
| [`modrinth-smartmaid.md`](modrinth-smartmaid.md) | Modrinth | ⏳ token 已就绪，**卡在账号邮箱验证**（见下） |
| [`modrinth-description-smartmaid.md`](modrinth-description-smartmaid.md) | Modrinth 英文正文 | ✅（脚本直接读取） |
| [`mcmod-smartmaid.md`](mcmod-smartmaid.md) | MC 百科 mcmod.cn | ✅ 可投递 |
| [`klpbbs-smartmaid.md`](klpbbs-smartmaid.md) | 苦力怕论坛 | ✅ 可投递（截图已齐） |
| [`curseforge-smartmaid.md`](curseforge-smartmaid.md) | CurseForge | ✅ 可投递 |

辅助脚本：[`publish_modrinth.py`](publish_modrinth.py)（`--project smartmaid` 已是默认；
deskpet-mod 配置保留，随时可发）。

### Modrinth API 两个实测坑（2026-09-24）

1. **`POST /project` 必须带 `"initial_versions": []`** —— 该字段官方文档标注 Deprecated
   （「请改为先建项目再上传版本」），但 schema **仍要求它存在**，漏了会直接 400：
   `missing field 'initial_versions'`。脚本已传空数组，版本照旧走 `POST /version`。
2. **账号必须先验证邮箱**，否则 `POST /project` 返回
   `401 unauthorized / "Please verify your email before publishing!"`
   —— 注意这条**不是 token 无效**（token 无效会报 `Invalid Authentication Credentials`）。
   处理：<https://modrinth.com/settings/account> 点重新发送验证邮件，或查收 Modrinth 注册邮件；
   验证后原命令直接重跑即可。
   （附带事实：`GET /user` 用只勾了 PROJECT_CREATE/VERSION_CREATE 的 token 会 401，
   因为读用户信息需要 `USER_READ` 权限位 —— 别把它误判成 token 坏了。）

---

## 已就绪的发布资产

- **P4 发布前收尾已完成（2026-09-24）**：
  - 五个命令（`/summonmaid` `/maidtasks` `/maidai` `/maidanim` `/maidperception`）加
    `Commands.hasPermission(Commands.LEVEL_GAMEMASTERS)` 权限检查（26.2 新权限 API，等同旧版等级 2）
  - `MaidDebug.ENABLED=false`（事件级日志整体关闭；排查问题时改回 `true` 重新构建）
  - jar 内图标（`assets/smartmaid/icon.png`，Mod Menu 可显示）+ `fabric.mod.json` 声明
  - JDK25 重新构建 BUILD SUCCESSFUL，开包复验通过（0 残留、META-INF 许可齐全、11 表情）
- **图标**：`SmartMaid/docs/images/smartmaid-icon.png`（512×512，女仆特写构图，
  与 deskpet-mod 的全身像区分）；mcmod 封面 `smartmaid-cover-240x150.png`
- **截图**：`SmartMaid/docs/images/` 三张（背包 GUI / 管理面板 / 对话气泡），已嵌入 GitHub README
- **jar**：`smartmaid-0.1.0.jar`（~0.9 MB）

## 你的动作清单

| 渠道 | 你要做的事 |
|---|---|
| Modrinth | ① **先验证邮箱**（<https://modrinth.com/settings/account>，否则 API 拒绝发布）② 确认 token 仍在（<https://modrinth.com/settings/pats>，勾 `PROJECT_CREATE`+`VERSION_CREATE`）→ 告诉我，剩下的我来跑脚本 |
| mcmod.cn | 登录 → <https://www.mcmod.cn/class/add> → 照 [`mcmod-smartmaid.md`](mcmod-smartmaid.md) 粘贴（要填 2 位验证码） |
| 苦力怕论坛 | 登录 → 按版块模板发帖，照 [`klpbbs-smartmaid.md`](klpbbs-smartmaid.md) 粘贴 + 传 3 张截图 + jar 附件 |
| CurseForge | 作者后台建项目 → 照 [`curseforge-smartmaid.md`](curseforge-smartmaid.md)；建好给我 token 我用 API 传文件 |

## 事实基线（各渠道文案别写错）

- **Minecraft 26.2（Fabric）**，前置 **Fabric API**，**Java ≥ 25**
- jar：`smartmaid-0.1.0.jar`（~0.9 MB，186 entries；含 GPL-3.0 表情数据 + CC BY-NC-SA 皮肤，
  `META-INF/` 里带 LICENSE + NOTICE）
- **命令需要 OP（`LEVEL_GAMEMASTERS`，等同旧版等级 2）**——单机开作弊可用；服务器上只有管理员能召唤。
  这是为了防他人服务器乱刷（P4 收尾决策，README/帖子里已写明）
- 聊天/语音对话是**可选**功能，需配套开源桌宠程序 DeskPet；其余功能完全独立可用——
  这条在各渠道描述里都要披露（Modrinth 规则 §2 也要求）
- 许可：代码 MIT；`MaidMoveControl` 改编自车万女仆（MIT）；表情数据 Emotecraft（GPL-3.0）；
  皮肤「大肥鱼」衍生（CC BY-NC-SA 4.0，禁止商用）——每处发布页都要带归属
- 三仓库：女仆模组 <https://github.com/oyxdsg/SmartMaid> · 桌宠
  <https://github.com/oyxdsg/Multimodal-AI-Companion> · 动画素材
  <https://github.com/oyxdsg/Multimodal-AI-Companion-Assets>
