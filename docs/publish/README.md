# MC 渠道发布包（deskpet-mod）

> 四个渠道的**可直接投递**物料。每个文件里的文案都能整段复制粘贴。
> 总纲与素材署名见 [`../MC渠道发布物料.md`](../MC渠道发布物料.md)（该文件中「SmartMaid 未公开」的表述已过时——它已于 2026-09-24 开源）。

| 文件 | 渠道 | 形式 |
|---|---|---|
| [`modrinth.md`](modrinth.md) | Modrinth | 表单字段 + 英文正文 + API 上传 |
| [`curseforge.md`](curseforge.md) | CurseForge | 步骤 + 英文正文 + API 上传 |
| [`mcmod.md`](mcmod.md) | MC 百科 mcmod.cn | 收录表单字段 + 中文简介 |
| [`klpbbs.md`](klpbbs.md) | 苦力怕论坛 · deskpet-mod | 标题 + 正文 + 附件命名 |
| [`klpbbs-smartmaid.md`](klpbbs-smartmaid.md) | 苦力怕论坛 · **SmartMaid（主宣传位）** | 标题 + 正文 + 致谢声明 |

辅助脚本：[`publish_modrinth.py`](publish_modrinth.py)（建项目 + 传版本 + 提审，一条命令）。

---

## 截图状态

- ✅ **SmartMaid 3 张已就绪**（2026-09-24，用户截）：`SmartMaid/docs/images/` ——
  `smartmaid-inventory-gui.png`（背包+实时模型）、`smartmaid-settings-panel.png`（管理面板）、
  `smartmaid-chat-bubble.png`（对话气泡）。已嵌入 SmartMaid README「游戏内截图」节。
- ✅ 桌宠侧：`maid-ingame.png`（SmartMaid 游戏内）、`deskpet-mod-icon.png`（512×512）、
  `deskpet-mod-cover.png` + `deskpet-mod-cover-240x150.png`
- ❌ **deskpet-mod 自身的游戏内截图（建筑识别效果）仍缺**——klpbbs 的 deskpet-mod 帖（[`klpbbs.md`](klpbbs.md)）
  发帖前必须补：正在建房子 + 桌宠气泡点评 1 张、建筑完成被识别 1 张（游戏内 F2）。

### SmartMaid 发帖前置（仅女仆帖）

P4 发布前收尾（`/summonmaid` 权限限制 + debug 日志关闭）**没做之前别发**——
版规对资源质量有要求，debug 刷屏会挨差评。见 [`klpbbs-smartmaid.md`](klpbbs-smartmaid.md) §前置。

---

## 推荐执行顺序

| 步 | 渠道 | 前置 | 我能自动做的部分 |
|---|---|---|---|
| 1 | **Modrinth** | 你的 Personal Access Token（`modrinth.com/settings/pats`，勾 `PROJECT_CREATE`+`VERSION_CREATE`） | 全部：建项目+传版本+提审（脚本） |
| 2 | **mcmod.cn** | 账号（QQ 登录即可）；普通用户提交要填 2 位验证码 | 表单字段全部备好，你粘贴 |
| 3 | **苦力怕论坛** | 账号 + 版块发帖权限（新号先看版块《发帖须知》） | 标题/正文/附件命名全部备好 |
| 4 | **CurseForge** | 作者后台建项目（网页操作，需审核）；API token（`curseforge.com/account/api-tokens`） | 建好后我用 API 传文件 |

发完三个渠道后，回知乎文章 / B 站补一条「已上架 Modrinth」。

---

## 四渠道共用的事实基线（写文案时别写错）

- 版本：**Minecraft 26.2（Fabric）**，Java ≥ 25，前置 **Fabric API**
- 文件：`deskpet-mod-2.0.0.jar`（~0.12 MB，46 class，纯代码、0 图像 → MIT 干净）
- 它**只写文件**（`.minecraft/deskpet/`），不发网络请求、无遥测；真正的体验在桌宠程序
- 建筑识别是**服务端采集**：单机/局域网可用，**远程多人服务器不采集**（进度/聊天/合成/拾取仍可采集）
- 许可：代码 MIT；**商店图标/头图里的角色形象是 CC BY-NC-SA 4.0**（溟月 © 上善无形 ｜ 女仆版二创 © ZipZipPipe）——每个用图的页面都要带这行署名
- 三仓库：主程序 <https://github.com/oyxdsg/Multimodal-AI-Companion> · 动画素材 <https://github.com/oyxdsg/Multimodal-AI-Companion-Assets> · 女仆模组 <https://github.com/oyxdsg/SmartMaid>
