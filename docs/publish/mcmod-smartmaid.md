# MC 百科（mcmod.cn）收录 · SmartMaid（智能女仆）

> 提交入口：<https://www.mcmod.cn/class/add>（QQ 登录）。普通用户提交需填 2 位图片验证码，
> 提交后进审核（几天），状态在 <https://www.mcmod.cn/verify.html>。规则细节见 [`mcmod.md`](mcmod.md)。

## 收录定位

**「模组」类**（不是"资料片"也不是"整合包"）。这是女仆模组的百科词条，也是玩家搜"女仆 mod"时的第一入口——
价值不低于论坛发帖本身。

## 表单字段

| 字段 | 值 |
|---|---|
| 中文名 | **智能女仆**（搜索确认无重名；有重名就用 `智能女仆 (Smart Maid)`） |
| 英文名 | Smart Maid |
| 来源 | 原创模组（非搬运、非汉化） |
| 前置 | Fabric API |
| 支持版本 | Java 版 · 26.2 · Fabric |
| 封面图（240×150） | `SmartMaid/docs/images/smartmaid-cover-240x150.png`（已按百科规格裁好） |
| 资料来源链接 | `https://github.com/oyxdsg/SmartMaid` |

## 简介（短简介栏，直接粘贴）

```
智能女仆（Smart Maid）是一个 Minecraft 26.2（Fabric）的开源 AI 女仆模组：/summonmaid 召唤一只
只属于你的女仆，每名玩家仅限一只、无需驯服，退出游戏自动消失并本地保存装备（每 5 秒持久化）。

行为由本地规则引擎驱动（毫秒级响应）：盾牌格挡（去掉原版 0.25 秒架盾前摇，贴脸箭也挡得下）、
近战走位（死区 2.7~3.2 格，后退/侧移绕行/背对跳）、远程弓箭边退边射、半血自动进食回血；
跳跃寻路可跨 1~3 格沟、上 1 格高台，寻路失败时自动降级为直线移动并挖方块/搭方块通过。

/maidtasks 指挥她干活：挖矿（自动探测周围矿物）、耕作、建造、收集、合成、烧炼、存取箱子；
背包布局与玩家完全一致（41 格），界面左上角实时渲染女仆模型。Shift+右键打开木质管理面板，
交互距离、护卫模式、生命与回血速度、死亡掉落等按玩家绑定保存。

聊天栏对话与语音朗读为可选功能，需配合同一作者的开源桌宠程序 DeskPet（不装也完全可玩）。

开源仓库：https://github.com/oyxdsg/SmartMaid（代码 MIT；表情数据来自 Emotecraft，GPL-3.0；
女仆皮肤为「大肥鱼」角色衍生作品，CC BY-NC-SA 4.0，禁止商用）。
```

## 图片素材（上传到百科图床）

| 用途 | 文件 |
|---|---|
| 封面 240×150 | `smartmaid-cover-240x150.png` ✅ |
| 背包界面 | `SmartMaid/docs/images/smartmaid-inventory-gui.png` ✅ |
| 管理面板 | `SmartMaid/docs/images/smartmaid-settings-panel.png` ✅ |
| 对话气泡 | `SmartMaid/docs/images/smartmaid-chat-bubble.png` ✅ |

## 关联

- 审核通过后，把 mcmod 词条链接补进：SmartMaid README「相关仓库」表 + klpbbs 帖 + Modrinth 描述。
- 同一作者词条关联：若之后收录 DeskPet 桌宠（软件类）或 deskpet-mod，在"相关资料"里互相挂链接。
