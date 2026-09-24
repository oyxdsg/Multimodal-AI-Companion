# Modrinth 发布包

> 建议**第一个发**：自助、免审核排队最短、长期自然流量。slug `deskpet-mod` 已确认可用（2026-09-24 查询 404），
> 游戏版本 `26.2` 与加载器 `fabric` 都在 Modrinth 标签表里。

## 一、Token 准备（你操作，1 分钟）

1. 登录 <https://modrinth.com> → 右上角头像 → **Settings → Personal Access Tokens**（或直接开 <https://modrinth.com/settings/pats>）
2. New token，名字随意（如 `deskpet-publish`），权限勾 **`PROJECT_CREATE`** 和 **`VERSION_CREATE`**
3. 复制 token（只显示一次），**不要贴到任何文件/聊天记录里**，直接在终端里设：
   `$env:MODRINTH_TOKEN="mrp_xxx"`（PowerShell）或 `export MODRINTH_TOKEN=mrp_xxx`

## 二、表单字段

| 字段 | 值 |
|---|---|
| Project type | `mod` |
| Title | `DeskPet Mod` |
| Slug | `deskpet-mod` |
| Summary | `Companion mod for the DeskPet desktop AI pet — it lets your desktop pet see what happens in your world.` |
| Categories（featured） | `utility` · `decoration` · `game-mechanics` |
| Client side | `required` |
| Server side | `optional` |
| License | `MIT` |
| Source URL | `https://github.com/oyxdsg/Multimodal-AI-Companion` |
| Issues URL | `https://github.com/oyxdsg/Multimodal-AI-Companion/issues` |
| Icon | `docs/images/deskpet-mod-icon.png`（512×512） |
| Gallery | 截图就绪后传（见 [`README.md`](README.md#⚠️-发布前唯一的硬缺口游戏内截图)） |

### Version 字段

| 字段 | 值 |
|---|---|
| Name | `DeskPet Mod 2.0.0` |
| Version number | `2.0.0` |
| Channel | `release` |
| Game versions | `26.2` |
| Loaders | `fabric` |
| Dependencies | Fabric API（`required`，脚本会自动解析其 project id） |
| File | `deskpet-mod/build/libs/deskpet-mod-2.0.0.jar` |
| Changelog | `First release: game event collection + local building recognition for Minecraft 26.2 (Fabric).` |

## 三、Description 正文（整段复制到 Body）

> Modrinth 规则 §2.2 要求描述**有英文版**、§2.1 要求写清「这是什么 / 为什么要装 / 装之前必须知道什么」——
> 下面这篇就是按这个写的，**第一屏就把「单独装没效果」讲明**，避免差评。

正文是单一事实来源：[`modrinth-description.md`](modrinth-description.md)（上传脚本也读这个文件，
要改正文只改它）。整段复制到 Modrinth 的 Body 输入框即可。

## 四、一条命令上传（脚本）

```powershell
$env:MODRINTH_TOKEN="mrp_xxx"
python docs\publish\publish_modrinth.py create     # 建项目(draft) + 传 2.0.0 版本
python docs\publish\publish_modrinth.py submit     # 提交审核 / 转公开
# 以后发新版：
python docs\publish\publish_modrinth.py version --jar build\libs\deskpet-mod-2.0.1.jar --number 2.0.1
```

脚本行为：`create` 先建 **draft** 项目（Modrinth 官方建议 `is_draft` 恒为 true），传版本，再把链接打给你看；
确认没问题后 `submit` 提审。全程走你终端里的代理环境变量（没设代理会自动直连）。
结果写到 `publish_modrinth_result.json`，出错时把里面的 `description` 发我。

## 五、审核要点（如果被打回）

- 常见退回原因 = 元数据与描述不一致：确保 Game versions 只有 `26.2`、Loaders 只有 `fabric`、
  依赖只有 Fabric API（required），与正文 Requirements 一致
- 「依赖外部程序」必须保持首屏可见（正文第一段就是）——这是规则 §2.1 的硬要求，删了反而容易被拒
- 图标/头图含 CC BY-NC-SA 形象，Credits 段必须保留
