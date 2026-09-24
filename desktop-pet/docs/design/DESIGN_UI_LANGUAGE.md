# DESIGN_UI_LANGUAGE.md — WorkBuddy 视觉语言取证与移植

> 取证对象：本机 `E:\新建文件夹\WorkBuddy` 5.5.6。不是复述截图，是从它的打包产物里读出来的真实值。
> 状态：**已落地**（2.23.0，只作用于设置对话框；见 §5）。§1–§4 是取证，§6 是遗留项。
> 落地代码：`core/theme.py` 的 `WBS_*` 区 + `wbs_dialog_qss()`、`pet/settings_dialog.py`、
> `core/icons.py::chevron_png_path()`、`core/widgets.py::NoWheelComboBox`。

## 一、取证方式（可复现）

| 项 | 值 |
|---|---|
| 打包归档 | `resources/app.asar`（297 MB，20474 个文件） |
| 设置面板实现 | `renderer/assets/ui-docs-viewer-Rvg3S459.js`（17.2 MB）+ `ui-docs-viewer-CXp0RDLt.css`（2.07 MB） |
| 入口桩 | `renderer/assets/SettingsModal-*.js` 只有 142 字节，只做 re-export —— 真身在上面那个 17 MB chunk |
| 图标 | 手绘 SVG 组件，内联在 `ui-docs-viewer-*.js`；另有 `icons-*.js`（3.76 MB）是 Iconify 的 `vscode-icons`，只用于**文件类型**彩色图标，与 UI 图标不是一套 |

asar 头格式：`[u32=4][u32 headerLen]` → `headerBlob`，`headerBlob[0:4]`=pickle 载荷长、`headerBlob[4:8]`=字符串长、字符串自 `headerBlob[8]` 起；文件数据起点 = `8 + headerLen`。

**底座结论**：React + **Radix Themes / Radix Colors**（gray / mauve / slate / sage / olive / sand / amber / blue / … 各 12 阶 + `a1`–`a12` alpha 阶 + `-contrast`，并带 `color(display-p3 …)` 高清变体），其上覆盖一层自研 **`--wb-*`** 体系（**2049 个自定义属性**）。

## 二、它"耐看"的三条机制

### 1. 背景永远不是纯白 —— 靠亮度差分层，不靠描边

灰度阶梯（这就是"分层次"的全部秘密）：

| 层级 | 值 | 用途 |
|---|---|---|
| `--wb-palette-gray-1` | `#FAFAFA` | 最浅底（`--wb-bg-hover-subtle`） |
| `--wb-palette-gray-2` | `#F7F7F7` | **弹窗底 / 导航底 / 内容底**（`--wb-bg-secondary`） |
| `--wb-palette-gray-3` | `#F2F2F2` | 浅悬停（`--wb-bg-hover-light`） |
| `--wb-palette-gray-4` | `#EBEBEB` | 悬停（`--wb-todo-menu-bg-hover`） |
| `--wb-palette-gray-5` | `#E6E6E6` | 按下 / 旧版选中（`--wb-todo-menu-bg-active`） |
| 卡片 | `#FFFFFF` | `--wb-bg-primary` / `--wb-bg-card` |

关键点：**相邻层亮度差只有 3% 左右**（#F7F7F7 → #FFFFFF = 8/255），肉眼几乎看不出边界，但层次感成立。整个设置面板 **0 条描边** —— `--wb-border-card` 只用在别处，弹窗自己 `border: none`。

强化层用 `color-mix`：`--cb-settings-card-background: color-mix(in srgb, 编辑器底色 96%, #64748b 4%)`，再强是 8%。即"卡片白"其实是**白里掺 4% 冷灰**，不是死白。

### 2. 交互反馈是"淡色遮罩"，不是变色/加边

| 状态 | 浅色 | 深色 |
|---|---|---|
| hover | `rgba(0,0,0,0.04)`（=`--wb-bg-surface-overlay-soft`） | `rgba(255,255,255,0.05)` |
| active / 按下 | `rgba(0,0,0,0.08)`（=`--wb-bg-surface-overlay-strong`） | `rgba(255,255,255,0.10)` |
| 菜单项 hover | `rgba(0,0,0,0.04)` | `rgba(255,255,255,0.06)` |
| 菜单项 active | `rgba(0,0,0,0.08)` | `rgba(255,255,255,0.12)` |

设置导航落到具体灰阶：hover `gray-4 #EBEBEB`、active 旧实现 `gray-5 #E6E6E6`。
**选中态另有品牌色方案**：`.bind-channel-nav-item.is-active { background: var(--wb-status-success-soft-bg); color: var(--wb-brand-primary); }`，即**淡绿软底 `rgba(16,185,129,0.10)` + 品牌绿文字 `#00C29A`** —— 截图里那颗被选中的绿色就是这样来的。

`--wb-brand-primary` = `--wb-palette-brand-8` = **`#00C29A`**（青绿），`brand-7` = `#40D1B3`，`brand-primary-subtle` = `rgba(0,194,154,0.12)`，`brand-primary-deep` = `#087866`。开关的选中轨道 `--wb-switch-track-bg-checked: var(--wb-brand-primary)` 就是它。

### 3. 动效：短、且缓动偏"减速"，没有一处超过 400ms

| token | 值 |
|---|---|
| `--wb-motion-duration-instant` | 100ms |
| `--wb-motion-duration-fast` / `-sm` | 150ms |
| `--wb-motion-duration-base` / `-medium` | 200ms |
| `--wb-motion-duration-gentle` | 300ms |
| `--wb-motion-duration-slow` | 400ms |
| `--wb-motion-easing-standard` | `cubic-bezier(0.4, 0, 0.2, 1)` |
| `--wb-motion-easing-emphasized` | `cubic-bezier(0.2, 0, 0, 1)` |
| `--wb-motion-easing-decelerate` | `cubic-bezier(0, 0, 0.2, 1)` |
| `--wb-motion-easing-accelerate` | `cubic-bezier(0.4, 0, 1, 1)` |

弹窗入场 = **`slideUp 200ms emphasized`**（位移，不是淡入）+ 遮罩 `fadeIn 150ms standard`。
也就是：**位移动画用 emphasized、透明度用 standard**，这是它"有动态感但不飘"的原因。

## 三、可直接搬的 token 表

**间距**（2 的倍数为主，5px 是特例）
`1=2  2=4  2-5=5  3=8  4=12  5=16  6=20  7=24  8=32  9=40  10=48  12=64`

**圆角**
`xs=2  sm=4  md=6  lg=8  xl=12  2xl=16  3xl=20  4xl=24  full=9999`
（弹窗 24，卡片 16，导航项 8，开关 full）

**字号 / 行高**（组合成 14 档语义，不要乱挑）
`1=8  2=10  3=12  4=13  5=14  6=16  7=18  8=20  9=24  10=28  11=32`
`lh: 4=16  4-2=18  5=20  5-2=22  6=24  7=28  8=32  10=40`
字重只有 `400 / 500 / 600 / 700`。
- `--wb-font-body-size` = 14px / lh 22px / 400 ← 正文、导航项
- `--wb-font-caption-size` = 12px / lh 18px / 400 ← 分组小标题、辅助说明
- `--wb-font-spec-body-md` = 14px / lh 22px / 500 ← 区块标题

**图标**
`sm=12  ms=14  md=16  lg=20  xl=24`

**尺寸/控件高度**：`3=12 3-2=14 4=16 5=20 6=24 7=28 8=32(size-control-md) 9=36 10=40 11=44 12=48`

**阴影**（全部是"低不透明度 + 大位移"，所以不脏）
| 用途 | 值 |
|---|---|
| 卡片 | `0 1px 2px rgba(0,0,0,0.04)` |
| 卡片柔和 | `0 16px 32px -8px rgba(0,0,0,0.03)` |
| 下拉 / 菜单 | `0 6px 24px rgba(0,0,0,0.04)` |
| 弹窗（设置） | `0 16px 40px rgba(0,0,0,0.24), 0 8px 32px rgba(0,0,0,0.08)` ← **双层** |
| 对话框 | `0 8px 24px rgba(0,0,0,0.08)` |
| 焦点环 | `0 0 0 2px rgba(26,121,255,0.10)` |

**文字色**
`primary`: `rgba(0,0,0,0.9)` · `secondary`: `rgba(0,0,0,0.7)` · `tertiary`: `rgba(0,0,0,0.5)` · `hint/placeholder`: `rgba(0,0,0,0.3)`
深色对应：`rgba(255,255,255,0.85) / 0.65 / 0.5 / 0.4`

## 四、组件配方（照抄真实 CSS）

### 设置弹窗
```css
width: 880px; max-width: 92vw; height: 720px; max-height: 88vh;
background: #F7F7F7;            /* --wb-bg-secondary，不是白 */
border: none;                    /* 0 描边 */
border-radius: 24px;             /* --wb-radius-4xl */
box-shadow: 0 16px 40px rgba(0,0,0,.24), 0 8px 32px rgba(0,0,0,.08);
animation: slideUp 200ms cubic-bezier(.2,0,0,1);
```

### 左导航
```css
width: 200px; padding: 12px; gap: 2px; background: #F7F7F7;
border-right: none;              /* 靠背景差分层，不画分界线 */
user-select: none;
```

**导航项**
```css
padding: 6px 12px; border-radius: 8px; gap: 8px;
font-size: 14px; line-height: 22px;
transition: background 150ms cubic-bezier(.4,0,.2,1), color 150ms …;
/* hover  → #EBEBEB
   active → 淡绿软底 rgba(16,185,129,.10) + 文字/图标 #00C29A    */
```
图标色：未选中 `rgba(0,0,0,0.7)`，选中跟随文字色。

**分组小标题**
```css
padding: 2px 12px; font-size: 12px; line-height: 18px;
color: rgba(0,0,0,0.7);          /* --wb-color-text-secondary */
user-select: none;
/* 组间距 margin-top: 12px；也有实现用 176px 宽、1px 的分隔线 */
```

### 右内容区（**注意 padding 四边不对称**）
```css
padding: 0 20px 20px 12px;   /* 上 0 / 右 20 / 下 20 / 左 12 */
overflow-y: auto; scrollbar-gutter: stable;
background: #F7F7F7;
/* 滚动条默认完全透明，hover 面板才出现，宽 4px，thumb rgba(0,0,0,.12) */
```

### 区块标题（title + 说明两句式，是"不啰嗦但说清"的关键）
```css
.section          { display:flex; flex-direction:column; gap:8px; }
.section-header   { padding-left: 8px; margin-top: 8px; }
.section-title    { font-size:14px; line-height:22px; font-weight:500; color:rgba(0,0,0,.9); }
.section-desc     { margin-top:2px; font-size:14px; line-height:22px; color:rgba(0,0,0,.5); }
```

### 卡片与卡内行（分层的落点）
```css
.card        { background:#FFF; border:none; border-radius:16px; }
.card-row    { display:flex; justify-content:space-between; align-items:center;
               padding:12px 16px; border-radius:16px; }
.card-label  { font-size:14px; line-height:22px; font-weight:500; margin-bottom:4px; }
.card-desc   { font-size:14px; line-height:22px; color:rgba(0,0,0,.5); }
.card-list   { display:flex; flex-direction:column; gap:8px; }
```

### 开关
```css
track: 26×16, radius 9999, padding 2px, 未选中 #E6E6E6 / 选中 #00C29A
thumb: 12px, 圆, 白, 位移 8px, 阴影 0 1px 2px rgba(0,0,0,.18)
transition: 150ms cubic-bezier(.4,0,.2,1)
```

### 图标（**这是"好看"的真正来源**）
不是图标库，是**逐个手绘的 React 组件**：
```jsx
<svg width="16" height="16" viewBox="0 0 16 17" fill="none" aria-hidden="true">
  <path d="…" stroke="currentColor" strokeWidth="1.3"
        strokeLinecap="round" strokeLinejoin="round"/>
</svg>
```
- **16×16 画布 + 1.3 描边 + 圆头圆角 + `currentColor`**
- 对比：Tabler/Lucide 是 24 画布 / 2 描边，等比缩到 16 后视觉更重。1.3 让它"细而清楚"。
- 颜色一律交给 CSS（`currentColor`），所以同一个图标能在 hover / 选中 / 禁用间自动换色，不需要维护多套。

## 五、移植到桌宠（Qt / QSS）· **已落地**

### 5.1 落地前后对比

| 项 | 落地前（Ant 版） | 落地后（WBS） |
|---|---|---|
| 弹窗底 | `#FFFFFF` + 1px 描边 | `#F6F8F5`，**0 描边** |
| 圆角 | 8px | 24px（卡片 16px） |
| 导航宽 / 项高 | 170px / 44px | 200px / 34px |
| 导航 hover | `#F5F5F5` | `#EDF0EC`（≈4% 黑遮罩的等效实色） |
| 导航选中 | `#E6F4FF` + `#1677FF` + 600 字重 | `#C3DCC6` 填充 + `#23382A` 文字，**不加粗** |
| 分组标题 | 无 | 12px `#5F6D63`，共三组 |
| 卡片 | 无（控件直接铺在面板上） | 每页一张白卡片 + 卡内滚动 |
| 阴影 | 无 | `QGraphicsDropShadowEffect`（模糊 40 / y 偏移 16） |
| 下拉箭头 | Ant 三角 hack（实际渲染成小方块） | 描边 chevron PNG，**展开翻转朝上** |
| 滑块滑钮 | 空心方块 | 14px 圆钮 + 绿环 |
| 强调色 | Ant 蓝 `#1677FF` | 绿 `#3C8C4E` / 主按钮 `#519560` |

### 5.2 Qt / QSS 的硬限制与绕法

1. **没有 `color-mix()`** → 预乘成实色。灰底上的 4% 黑遮罩直接写 `#EDF0EC`。
2. **没有 `transition` / `animation`** → 本版**只做状态色、不做过渡**。要补过渡得用
   `QPropertyAnimation` / `QVariantAnimation` 手动插值背景色，代价不小、收益有限，暂不做。
3. **没有真 `box-shadow`** → `QGraphicsDropShadowEffect`，**只能叠一层**，取更外扩的那层
   （`blur 40 / offset(0,16) / rgba(0,0,0,58)`）。两个附加条件：父布局要留白否则投影被裁；
   还要把对话框自身底色置透明（`QDialog { background: transparent }`），否则投影后面会露出白矩形。
4. **字体**：WorkBuddy 指定 `"PingFang SC"`；Windows 上对应 **微软雅黑 / Microsoft YaHei UI**，别照抄 PingFang。
5. **图标不用换描边**：`core/icons.py` 本来就是 24 viewBox + `stroke_width` 参数，
   **stroke 2 在 16px 显示时恰好等于 WorkBuddy 的 1.3**（2 × 16/24 = 1.33），
   所以导航图标只需换颜色，视觉分量已经一致。

### 5.3 ⚠️ 四个 Qt 陷阱（落地时实测踩到）

1. **`QScrollArea.setWidget()` 内部会强制 `widget->setAutoFillBackground(true)`** ——
   卡片内的页面于是按调色板自绘 `#EFEFEF`，把卡片的白色**整块盖掉**。必须在 `setWidget()`
   **之后**再 `page.setAutoFillBackground(False)`；写在前面会**静默失效**。
   定位手法：把 card / viewport / page 分别染红 / 蓝 / 洋红，逐个采样，谁显色就是谁在画。
2. **QSS 与全局 `_ANT_QSS` 是按属性合并、不是简单覆盖** —— 覆盖某属性时，必须把 Ant
   也声明过的同组属性**一起显式声明**。已踩两处：
   - `QComboBox::down-arrow`：Ant 的 `border-left/right/top` 三角 hack 会**糊在 chevron 上**
     （实测一根 5px 灰条）→ 必须显式 `border: none; margin: 0 9px 0 0;`。
   - `QPushButton:focus` / `:default`：Ant 分别写 `border-color: #4096FF` / `#1677FF`，
     **伪状态选择器比普通规则更具体**，普通 `QPushButton` 规则压不住 → 聚焦/默认按钮露蓝边。必须显式覆盖。
3. **`QSlider::handle` 的 `border-radius` 必须配合显式 `height`**，否则不生效、退化成矩形
   （渲染出来是个空心方块）。radius 必须 = height/2。
4. **`QComboBox::down-arrow:on` 实测无效**：QComboBox 弹出时确实会置 `State_On`，
   但**不会重绘本体**，那张图永远画不出来。改用自维护动态属性（见 5.4）。

### 5.4 下拉箭头翻动（实现方式）

```python
# core/widgets.py · NoWheelComboBox
def _mark_popup(self, opened):
    self.setProperty("popupOpen", "true" if opened else "false")
    style = self.style()
    style.unpolish(self)
    style.polish(self)
    self.update()

def showPopup(self):
    super().showPopup()
    self._mark_popup(True)
    ...

def hidePopup(self):
    super().hidePopup()
    self._mark_popup(False)
```
```css
QComboBox::down-arrow { image: url("<chevron_down.png>"); }
QComboBox[popupOpen="true"]::down-arrow { image: url("<chevron_up.png>"); }
```
箭头 PNG 由 `core/icons.py::chevron_png_path()` 用现成的 `icon()` 渲染（2x、stroke 2、圆头圆角），
QSS 用 `image: url()` 引用 —— **不做 data URI**（QSS 不支持），也**不直接喂 SVG**
（不保证有 svg imageformats 插件），所以统一落 PNG，与既有的 `check_png_path()` 一致。

### 5.5 配套工具

| 工具 | 用途 |
|---|---|
| `tools/preview_settings_dialog.py` | **不启动桌宠**把设置对话框渲染成 PNG（真实平台 + `Qt.WA_DontShowOnScreen`，窗口不上屏、不碰键鼠），改 UI 后立刻目视核对 |
| `tools/restore_wbs_ui.py` | 一键回滚本次改版；带「选错旧时间戳」防呆（要 `--force` 才放行） |

**核对细节的正确手法**：`QWidget.grab()` **单抓某个控件**再放大，别按坐标裁整个窗口 ——
`mapTo()` 在 `WA_DontShowOnScreen` 下算出的位置与实际渲染**差几十像素**（第一轮就栽在这）。
判「翻动」这类状态变化：抓两态各一张，**逐像素算差异比例**。

## 六、遗留 / 待确认

- **其余界面仍是 Ant 蓝**（聊天窗 / 气泡 / 右键菜单）。要全局统一，把 `WBS_*` 接到 `_ANT_QSS`
  的同名令牌即可；但需先决定**桌宠身份色海蓝 `#1E88E5` 是否让位给品牌绿**。
- **过渡动效未做**：QSS 无法 transition，本版只做状态色（见 5.2）。WorkBuddy 那种 150ms 淡入
  要另用 `QPropertyAnimation` 补。
- **选中态那圈橙色已定性**：实截取色 `#E59700`，且外层垫了 1px 白 —— 是**系统焦点环**
  （outline + offset 的典型结构），不是选中态样式，**已刻意不照搬**。选中态只保留
  「`#C3DCC6` 填充 + `#23382A` 文字」。（原 §6 里"两套实现并存"的疑点至此关闭。）
- `wb-button` / `wb-switch` / `wb-input` 等完整组件库分散在 `safe-delete-events-*.css`（444 KB）
  等文件里，本轮只精读了设置相关部分，未全量梳理。
- 深色模式 token 已抽到（`#1E1E1E` 底 / `#252525` 卡片 / hover `rgba(255,255,255,0.06)`），
  桌宠目前只有浅色主题，暂不移植。
- 取证原始素材（89 个 CSS 与 token 清单）留在 `%TEMP%\wb_css` 与 `%TEMP%\wb_tokens*.txt`，
  属临时目录，需要时按 §1 的步骤重新抽取即可。

