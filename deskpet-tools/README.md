# 桌宠角色工具（DeskPet Role Tool）

桌宠角色包的制作工具，独立于桌宠程序运行。包含两个功能：

1. **① 视频 → 动作**：把一段**绿幕角色视频**自动转化为标准化的桌宠动作（透明 PNG 帧图，必要时含位移轨迹）。
2. **② 动作 → 角色包**：把一组标准化动作打包成**角色包**，放到桌宠的 `desktop-pet/skins/` 目录，桌宠即可识别并在「设置 → 皮肤」中切换。

## 安装

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

依赖：`opencv-python`（视频解码 + 抠图）、`Pillow`（PNG 保存，兼容中文路径）、`numpy`。

## 运行

```bash
python role_tool.py
```

会打开 GUI，两个标签页对应两个功能。

---

## ① 视频 → 动作

- **绿幕视频**：背景为纯绿（约 `(19,207,27)`），角色以站立姿态开头结尾。
- **动作类型**：
  - `站姿动作`（思考/开心/哭泣/害羞/惊讶/站着睡着…）：无位移轨迹。
  - `位移 · 垂直`（跳跃类）：输出仅 `dy` 的 `traj.json`。
  - `位移 · 双向`（打滚类）：输出 `dx + dy` 的 `traj.json`。
- **位移锚点**（位移动作时可选，站姿固定脚底）：
  - `脚底贴地窄带`（推荐跳跃）：贴地不动点，垂直轨迹 = 脚离地高度。
  - `角色中心`（推荐打滚）：内容包围盒中心不动点，水平+垂直轨迹 = 中心偏移。
- **动作名称**：建议用中文（如「思考」），用于默认输出目录名。
- **输出**：`<输出目录>/frame_0000.png …`（位移动作另含 `traj.json`）。

处理流程（对应《视频处理成图片成为宠物动作.md》）：色度键抠图（软 alpha + 连通域主体保护 + 去绿溢色 + 水印/绿影清理 + 连通域面积过滤 + 边缘羽化）→ 首帧站立高度统一缩放（480px 基准）→ 锚点对齐（脚底窄带对齐桌宠「正面」站位 `FOOT_TARGET=209.6`，或内容中心居中，画布 512×512）→ 位移动作按锚点提取轨迹。

## ② 动作 → 角色包

- 填写角色信息：**角色 id**（英文/数字，唯一，也是文件夹名）、角色名称、默认宠物名、作者、版本。
- 逐个「添加动作」：选动作 key（桌宠内部标识，如 `think`）+ 显示名（右键菜单/AI 标签显示的名字）+ 帧图目录。
- 可选填**角色专属 prompt**（`.txt`）：提供则切换皮肤时用它覆盖当前人格提示词全文；不提供则沿用当前人格提示词、只替换宠物名字。
- 输出目录默认 `desktop-pet/skins/`。

### 角色包结构

```
desktop-pet/skins/<角色id>/
├── role.json            # 角色元数据（必需）
├── prompt.txt           # 可选：角色专属人设
├── idle/                # 每动作一个目录（目录名 = 动作 key）
│   ├── frame_0000.png
│   └── traj.json        # 位移动作可选
└── think/
    ├── frame_0000.png
    └── ...
```

### role.json 字段

```json
{
  "id": "dafeiyu",
  "name": "鲸鱼女仆",
  "author": "",
  "version": "1.0.0",
  "default_pet_name": "大肥鱼",
  "prompt_file": "prompt.txt",
  "actions": {
    "idle":  {"label": "坐地上生气", "dir": "idle"},
    "think": {"label": "思考",       "dir": "think"}
  },
  "action_tags": {"待机": "idle", "思考": "think"},
  "chatlines":   {"idle": ["喵~"], "click": ["干嘛戳我！"]}
}
```

- `actions`：`动作key → {label 显示名, dir 帧图目录名}`，缺 `dir` 时默认等于 key。
- `action_tags`：可选，覆盖 AI 输出规范里的 `{动作标签词}`（如 `{思考} {惊讶}`）；不提供则沿用桌宠内置词表。
- `chatlines`：可选，覆盖随机台词库（待机/点击/拖动等卖萌台词）。

## 桌宠端使用

1. 用本工具把角色包打包到 `desktop-pet/skins/`（一个角色一个文件夹）。
2. 启动/重启桌宠，右键「设置」→ 新增「皮肤」页 → 下拉选择角色 → 确定。
3. 桌宠随即：切换全部动作素材、右键「🎬 动作」菜单按角色的动作显示名生成、宠物名改为角色的默认宠物名、AI 的 `{动作标签词}` 与台词跟随皮肤。
4. 切回「内置（鲸鱼女仆）」即恢复默认外观与人格。

## 命令行用法（进阶）

```bash
# 视频 → 动作（motion: static|vertical|dual，anchor: foot|center）
python video_to_action.py 视频.mp4 输出目录 vertical foot

# 动作目录 → 角色包
python package_role.py 角色id 角色名 输出目录 动作key:帧图目录 ...
```

## 桌宠诊断脚本（`诊断工具/`）

与 Minecraft 模组（`deskpet-mod`）联动的诊断/监测脚本，独立于桌宠程序运行，仅依赖 Python 标准库：

| 脚本 | 用途 |
|------|------|
| `check_building.py` | 检查建筑识别感知包（`deskpet/buildings/latest.json`） |
| `build_diag.py` | 建筑识别感知包 + 方块数据 体检/监测（`--watch` 实时） |
| `rescue_build_log.py` | 从 `latest.log` 抢救旧版模组的 `[building] place` 方块记录为 jsonl |
| `check_chat.py` | 检查模组是否采集到聊天（对比 `latest.log` 的 `[CHAT]`） |
| `analyze_chat.py` | 分析 `latest.log` 中 `[CHAT]` 消息数量与类型分布 |

运行：`python 诊断工具/check_building.py [--watch] [.minecraft目录]`（其余脚本用法见各自 `--help` 或文件头部注释）。

**`.minecraft` 目录怎么定位**（`诊断工具/_mc.py`，按优先级）：

1. 环境变量 `DESKPET_MC`
2. 同目录 `mc_path.txt`（**不入库**，用来放你本机的实例路径，首行写路径即可）
3. `%APPDATA%\.minecraft`
4. 当前工作目录下的 `.minecraft`

所有脚本也都支持**命令行传参**直接覆盖。所以别人 clone 下来无需改代码 ——
实例不在标准位置时，写一行 `mc_path.txt` 或设个环境变量即可。