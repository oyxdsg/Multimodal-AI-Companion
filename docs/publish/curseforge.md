# CurseForge 发布包

> 定位：**分发渠道**（有长期下载量），不是宣传阵地。新建项目要过作者后台，文件可以走 API 传。
> 注意 CurseForge 与 Modrinth **不通用**——同一份英文正文两边都能用，但表单字段要按下面重新填。

## 一、前置（你操作）

1. CurseForge 账号（CurseForge / Overwolf 账号体系），开 <https://www.curseforge.com> 登录
2. 进作者后台 <https://authors.curseforge.com>，接受作者协议
3. **Start a Project** → 游戏 `Minecraft` → 类型 `Mods`
4. API Token：<https://www.curseforge.com/account/api-tokens> 生成一个（只显示一次，同样别贴进文件）

## 二、项目表单字段

| 字段 | 值 |
|---|---|
| Project name | `DeskPet Mod` |
| Project URL (slug) | `deskpet-mod`（如被占则 `deskpet-companion`） |
| Summary | `Companion mod for the DeskPet desktop AI pet — it lets your desktop pet see what happens in your world.` |
| Categories | `Utility` / `Mobs`（主分类 Utility） |
| License | MIT |
| Source | <https://github.com/oyxdsg/Multimodal-AI-Companion> |
| Issues | <https://github.com/oyxdsg/Multimodal-AI-Companion/issues> |
| Logo | `docs/images/deskpet-mod-icon.png`（512×512） |

> ⚠️ CurseForge 有一类常见拒因是「描述与功能不符」。和 Modrinth 一样，**正文第一句就写清需要配套桌宠程序**，别让人装完发现"没效果"。

## 三、Description 正文

CurseForge 的描述编辑器是**富文本（所见即所得）**，不渲染 Markdown 源码。
两个办法任选：

1. **省事**：把 [`modrinth.md`](modrinth.md) §三 的正文先粘到 <https://markdowntohtml.com> 之类转换器，再粘进 CF 编辑器（表格和标题都会保留）；
2. 或在 CF 编辑器里手动排版，内容用同一份。

内容结构（与 Modrinth 版一致，别删这几块）：首屏「Read before downloading」警示框 → What it does（事件采集 + 建筑识别）→ Ecosystem 表（含 SmartMaid 链接）→ Requirements → Where the data goes → Scope（单人/局域网）→ Privacy → **Credits（CC BY-NC-SA 署名，硬要求）**。

## 四、文件上传

### 方式 A：网页上传（最简单）

项目页 → Files → Upload file：

| 字段 | 值 |
|---|---|
| File | `deskpet-mod-2.0.0.jar` |
| Display name | `DeskPet Mod 2.0.0` |
| Release type | `release`（首次发布也可能被要求 `beta`） |
| Game versions | `26.2` + `Fabric` + `Java 25` |
| 关系依赖 | `fabric-api` = **Required dependency** |

### 方式 B：API 上传（项目建好后我来跑）

```powershell
$env:CURSEFORGE_TOKEN="xxxxx"      # curseforge.com/account/api-tokens
$env:CURSEFORGE_PROJECT_ID="123456" # 项目页右侧栏的数字 Project ID
python docs\publish\publish_curseforge.py upload
```

（脚本待你把项目建好后我补——CF 的 API 需要 `X-Api-Token` 头 + `metadata`/`file` 两段 multipart，
游戏版本要用 `/api/game/versions` 返回的**数字 ID**，拿到 token 后一次就能对齐。）

## 五、审核说明

- Minecraft（PC-only）项目：先自动安全扫描，**过审即上线**，之后人工复审；有问题会被下架整改
- 新作者账号首次发布可能有人工审核延迟（一般 1~3 个工作日）
- 被拒不会封号，按提示改完重新提交即可；只有严重违规才会拒绝后再申请新项目受限
