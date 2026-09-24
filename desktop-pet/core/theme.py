"""全局界面主题：Ant Design 风格「后台管理系统」QSS。

依据《后台管理系统 · 组件库规范 v1.0》（Ant Design 风格）落地：
- 设计令牌（Design Tokens）：色彩 / 字号 / 间距 / 圆角 / 阴影，组件统一引用，
  不硬编码，保证主题一致与长期可维护。
- 品牌色阶 Brand Blue：#E6F4FF → #BAE0FF → #91CAFF → #69B1FF → #4096FF
  → #1677FF → #0958D9 → #003EB3 → #002C8C → #001D66
- 功能色：成功 #52C41A / 警告 #FAAD14 / 错误 #FF4D4F / 信息 #1677FF
- 中性灰：#FAFAFA / #F5F5F5 / #F0F0F0 / #D9D9D9 / #BFBFBF / #8C8C8C / #595959 / #262626
- 语义色：textPrimary rgba(0,0,0,.88) / textSecondary .65 / textTertiary .45
  colorBorder #D9D9D9 / colorBorderSecondary #F0F0F0 / colorBgLayout #F5F5F5
- 字体阶梯：H1 38 / H2 30 / H3 24 / H4 20 / Body 14 / Caption 12
- 间距 8pt 栅格：8 / 12 / 16 / 24 / 32 / 48
- 圆角：2 / 4 / 6（按钮、输入框）/ 8（卡片、弹窗）/ 999（胶囊）
- 控件高度：大 40 / 中 32（默认）/ 小 24
"""

# ---------------- 设计令牌（Design Tokens） ----------------
BLUE = "#1677FF"          # 品牌主色 Brand Blue 6
BLUE_HOVER = "#4096FF"    # Brand Blue 5
BLUE_ACTIVE = "#0958D9"   # Brand Blue 7
BLUE_BG = "#E6F4FF"       # Brand Blue 1（浅色底 / 选中底）
BLUE_BORDER = "#91CAFF"   # Brand Blue 3

GREEN = "#52C41A"         # 成功 Success
GOLD = "#FAAD14"          # 警告 Warning
RED = "#FF4D4F"           # 错误 Error
RED_HOVER = "#FF7875"
RED_ACTIVE = "#D9363E"
RED_BG = "#FFF2F0"
RED_BORDER = "#FFCCC7"

TEXT = "rgba(0, 0, 0, 0.88)"            # textPrimary
TEXT_SECONDARY = "rgba(0, 0, 0, 0.65)"  # textSecondary
TEXT_TERTIARY = "rgba(0, 0, 0, 0.45)"   # textTertiary
TEXT_DISABLED = "rgba(0, 0, 0, 0.25)"
BORDER = "#D9D9D9"                      # colorBorder
BORDER_SECONDARY = "#F0F0F0"            # colorBorderSecondary
BG_LAYOUT = "#F5F5F5"                   # colorBgLayout
BG_CONTAINER = "#FFFFFF"
FILL = "#FAFAFA"

_ANT_QSS = """
* { outline: none; }
QWidget {
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    color: rgba(0, 0, 0, 0.88);
}
QDialog { background: #FFFFFF; }
QWidget#panel { background: #FFFFFF; }

/* ---------- 标签页 Tabs：选中主色 + 底部主色下划线 ---------- */
QTabWidget::pane {
    border: none;
    border-top: 1px solid #F0F0F0;
    background: transparent;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: rgba(0, 0, 0, 0.65);
    padding: 8px 16px;
    margin: 0;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #1677FF;
    border-bottom: 2px solid #1677FF;
}
QTabBar::tab:hover:!selected { color: #4096FF; }

/* ---------- 按钮 Button ---------- */
/* 主按钮 primary：高 32px、圆角 6px、左右内边距 16px、字号 14px */
QPushButton {
    background: #1677FF;
    color: #FFFFFF;
    border: 1px solid #1677FF;
    border-radius: 6px;
    padding: 0 16px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton:hover { background: #4096FF; border-color: #4096FF; }
QPushButton:pressed { background: #0958D9; border-color: #0958D9; }
QPushButton:focus { border-color: #4096FF; }
QPushButton:disabled {
    background: rgba(0, 0, 0, 0.04);
    color: rgba(0, 0, 0, 0.25);
    border-color: #D9D9D9;
}
QPushButton:default { border-color: #1677FF; }

/* 次要按钮 default/outline：白底、灰边、黑字 */
QPushButton#btnOutline {
    background: #FFFFFF;
    color: rgba(0, 0, 0, 0.88);
    border: 1px solid #D9D9D9;
    border-radius: 6px;
    padding: 0 16px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton#btnOutline:hover { color: #4096FF; border-color: #4096FF; }
QPushButton#btnOutline:pressed { color: #0958D9; border-color: #0958D9; }
QPushButton#btnOutline:disabled {
    background: rgba(0, 0, 0, 0.04);
    color: rgba(0, 0, 0, 0.25);
    border-color: #D9D9D9;
}

/* 文本按钮 text：无边框无底色 */
QPushButton#btnText {
    background: transparent;
    color: rgba(0, 0, 0, 0.88);
    border: none;
    border-radius: 6px;
    padding: 0 8px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton#btnText:hover { background: rgba(0, 0, 0, 0.06); }
QPushButton#btnText:pressed { background: rgba(0, 0, 0, 0.1); }

/* 链接按钮 link */
QPushButton#btnLink {
    background: transparent;
    color: #1677FF;
    border: none;
    padding: 0 4px;
    min-height: 24px;
    font-size: 14px;
}
QPushButton#btnLink:hover { color: #4096FF; }
QPushButton#btnLink:pressed { color: #0958D9; }

/* 危险按钮 danger（主要） */
QPushButton#btnDanger {
    background: #FF4D4F;
    color: #FFFFFF;
    border: 1px solid #FF4D4F;
    border-radius: 6px;
    padding: 0 16px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton#btnDanger:hover { background: #FF7875; border-color: #FF7875; }
QPushButton#btnDanger:pressed { background: #D9363E; border-color: #D9363E; }

/* 危险次要按钮 danger + outline */
QPushButton#btnDangerOutline {
    background: #FFFFFF;
    color: #FF4D4F;
    border: 1px solid #FF4D4F;
    border-radius: 6px;
    padding: 0 16px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton#btnDangerOutline:hover { color: #FF7875; border-color: #FF7875; }
QPushButton#btnDangerOutline:pressed { color: #D9363E; border-color: #D9363E; }

/* 图标按钮 icon：透明底、hover 浅灰 */
QPushButton#iconBtn {
    background: transparent;
    color: rgba(0, 0, 0, 0.65);
    border: none;
    border-radius: 6px;
    padding: 4px;
    min-height: 0;
}
QPushButton#iconBtn:hover { background: rgba(0, 0, 0, 0.06); }
QPushButton#iconBtn:pressed { background: rgba(0, 0, 0, 0.1); }
QPushButton#iconBtn:focus { border: 1px solid #4096FF; }
QPushButton#iconBtn:disabled { background: transparent; }

/* ---------- 下拉选择 Select ---------- */
QComboBox {
    background: #FFFFFF;
    border: 1px solid #D9D9D9;
    border-radius: 6px;
    padding: 0 12px;
    min-height: 30px;
    color: rgba(0, 0, 0, 0.88);
    font-size: 14px;
}
QComboBox:hover { border-color: #4096FF; }
QComboBox:focus { border-color: #1677FF; }
QComboBox:disabled { background: rgba(0, 0, 0, 0.04); color: rgba(0, 0, 0, 0.25); }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid rgba(0, 0, 0, 0.45);
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    border: 1px solid #F0F0F0;
    border-radius: 8px;
    selection-background-color: #E6F4FF;
    selection-color: rgba(0, 0, 0, 0.88);
    outline: none;
    padding: 4px;
}
QComboBox QAbstractItemView::item { min-height: 30px; padding: 0 8px; border-radius: 4px; }
QComboBox QAbstractItemView::item:hover { background: #F5F5F5; }
QComboBox QAbstractItemView::item:selected { background: #E6F4FF; color: rgba(0, 0, 0, 0.88); }

/* ---------- 复选框 Checkbox：选中蓝底白勾 ---------- */
QCheckBox { color: rgba(0, 0, 0, 0.88); spacing: 8px; font-size: 14px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #D9D9D9;
    border-radius: 4px;
    background: #FFFFFF;
}
QCheckBox::indicator:hover { border: 1px solid #4096FF; }
QCheckBox::indicator:checked {
    background: #1677FF;
    border: 1px solid #1677FF;
    image: url("__CHECK_PNG__");
}
QCheckBox::indicator:disabled { background: rgba(0, 0, 0, 0.04); border-color: #D9D9D9; }

/* ---------- 单选 Radio：选中蓝环 + 蓝点 ---------- */
QRadioButton { color: rgba(0, 0, 0, 0.88); spacing: 8px; font-size: 14px; }
QRadioButton::indicator {
    width: 16px; height: 16px;
    border: 1px solid #D9D9D9;
    border-radius: 9px;
    background: #FFFFFF;
}
QRadioButton::indicator:hover { border: 1px solid #4096FF; }
QRadioButton::indicator:checked {
    border: 5px solid #1677FF;
    background: #FFFFFF;
    border-radius: 9px;
}

/* ---------- 滑块 Slider：主色轨道 ---------- */
QSlider::groove:horizontal {
    height: 4px;
    background: #F0F0F0;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px;
    margin: -5px 0;
    background: #FFFFFF;
    border: 2px solid #1677FF;
    border-radius: 9px;
}
QSlider::handle:horizontal:hover { border-color: #4096FF; }
QSlider::sub-page:horizontal {
    background: #1677FF;
    border-radius: 2px;
}

/* ---------- 输入框 Input ---------- */
QLineEdit, QTextEdit {
    background: #FFFFFF;
    border: 1px solid #D9D9D9;
    border-radius: 6px;
    padding: 4px 11px;
    color: rgba(0, 0, 0, 0.88);
    selection-background-color: #BAE0FF;
    font-size: 14px;
}
QLineEdit { min-height: 24px; }
QLineEdit:hover, QTextEdit:hover { border-color: #4096FF; }
QLineEdit:focus, QTextEdit:focus { border-color: #1677FF; }
QLineEdit:disabled, QTextEdit:disabled { background: rgba(0, 0, 0, 0.04); color: rgba(0, 0, 0, 0.25); }

/* ---------- 列表 List：选中浅蓝底 ---------- */
QListWidget {
    background: #FFFFFF;
    border: 1px solid #F0F0F0;
    border-radius: 8px;
    padding: 4px;
    color: rgba(0, 0, 0, 0.88);
}
QListWidget::item { padding: 5px 10px; border-radius: 6px; }
QListWidget::item:hover { background: #F5F5F5; }
QListWidget::item:selected {
    background: #E6F4FF;
    color: rgba(0, 0, 0, 0.88);
}
QListWidget::item:focus { border: none; }

/* ---------- 文本标签 ---------- */
QLabel { color: rgba(0, 0, 0, 0.88); background: transparent; }

/* ---------- 消息框 ---------- */
QMessageBox { background: #FFFFFF; }
QMessageBox QPushButton { min-width: 72px; }

/* ---------- 右键菜单 Menu ---------- */
QMenu {
    background: #FFFFFF;
    border: 1px solid #F0F0F0;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 5px 24px 5px 12px;
    border-radius: 4px;
    color: rgba(0, 0, 0, 0.88);
    font-size: 14px;
}
QMenu::item:selected { background: #F5F5F5; color: rgba(0, 0, 0, 0.88); }
QMenu::item:disabled { color: rgba(0, 0, 0, 0.25); }
QMenu::separator { height: 1px; background: #F0F0F0; margin: 4px 8px; }

/* ---------- 滚动条 ScrollBar ---------- */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.15);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: rgba(0, 0, 0, 0.25); }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: rgba(0, 0, 0, 0.15);
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: rgba(0, 0, 0, 0.25); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""


def apply_theme(app):
    """给 QApplication 应用全局 Ant Design 主题（唯一默认主题）。"""
    try:
        from core.icons import check_png_path
        # 复选框选中态为「蓝底白勾」→ 白色对勾 PNG
        check_png = check_png_path(color="#FFFFFF", px=16)
    except Exception:
        check_png = ""
    app.setStyleSheet(_ANT_QSS.replace("__CHECK_PNG__", check_png))


# ===========================================================================
# WorkBuddy 视觉语言（WBS = WorkBuddy Style）
# ---------------------------------------------------------------------------
# 取值来源：本机 WorkBuddy 5.5.6「设置」面板实截取色（DPR 125%，下列数值
# 均已按 1.25 归一为 CSS px）。不是照抄它的 --wb-* 变量名，而是把实测到的
# 观感搬过来，因此这里只保留真正影响观感的量。
#
# 三条核心机制（改样式时别破坏）：
#   1) 分层靠亮度差、不靠描边——弹窗底 #F6F8F5 / 卡片 #FFFFFF，全窗 0 描边；
#   2) 交互反馈是「淡色遮罩」——hover #EDF0EC、选中 #C3DCC6，都不加边框；
#   3) 动效短（150~200ms）且位移用减速曲线。
#
# 与既有 Ant 蓝主题的关系：两者并存。WBS_* 目前只作用于设置对话框
# （settings_dialog 用 wbs_dialog_qss() 覆盖到自己的 shell 上），
# 其余界面仍是上方 _ANT_QSS。要全局切换时，把 WBS_* 接到 _ANT_QSS 的同名
# 令牌即可（唯一需要人工确认的是桌宠身份色海蓝 #1E88E5 是否让位给品牌绿）。
# ===========================================================================

WBS_BG = "#F6F8F5"          # 弹窗底 / 导航列底 / 内容区底（**不是纯白**）
WBS_CARD = "#FFFFFF"        # 卡片 / 输入控件底
WBS_HOVER = "#EDF0EC"       # hover：4% 黑遮罩叠在 #F6F8F5 上的等效实色
WBS_HOVER_STRONG = "#E4E9E2"  # pressed / 8% 遮罩
WBS_TRACK = "#EDF2ED"       # 进度条槽 / 卡片内浅分隔
WBS_BORDER = "#E2E8E1"      # 极浅描边（输入框、次要按钮）
WBS_BORDER_STRONG = "#D3DAD2"

WBS_TEXT = "#293B30"        # 主文字（带绿调的墨色，不是纯黑）
WBS_TEXT_2 = "#59675E"      # 次要文字
WBS_TEXT_3 = "#6C7870"      # 三级文字 / 说明
WBS_TEXT_GROUP = "#5F6D63"  # 导航分组小标题

WBS_ACCENT = "#3C8C4E"      # 强调绿（进度条填充 / 勾选 / 聚焦边）
WBS_ACCENT_SOFT = "#C3DCC6"  # 导航选中填充
WBS_SEL_TEXT = "#23382A"    # 导航选中文字 / 图标
WBS_ICON = "#5B6A60"        # 导航未选中图标
WBS_BTN = "#519560"         # 主按钮底
WBS_BTN_HOVER = "#46854F"
WBS_BTN_PRESSED = "#3A7444"
WBS_SUCCESS = "#3C8C4E"
WBS_ERROR = "#B4453C"

WBS_RADIUS_DLG = 24         # 弹窗圆角
WBS_RADIUS_CARD = 16        # 卡片圆角
WBS_RADIUS_ITEM = 8         # 导航项圆角
WBS_RADIUS_CTRL = 8         # 控件圆角
WBS_NAV_W = 200             # 导航列宽
WBS_NAV_PAD = 12            # 导航列内边距
WBS_ITEM_H = 34             # 导航项高（padding 6 + 行高 22）
WBS_GROUP_TITLE_H = 34      # 分组小标题行高（含上间距）
WBS_PAD_RIGHT = 20          # 内容区右/下内边距
WBS_PAD_GAP = 12            # 导航与卡片之间的间隙

# 设置页常用内联样式速查：同一条样式在多个页面重复出现，集中到令牌层，
# 避免以后调色时漏改（QSS 无法覆盖 widget 自带 stylesheet，这些必须内联）。
WBS_HINT_QSS = f"color: {WBS_TEXT_3}; font-size: 12px;"          # 灰色说明文字
WBS_SECTION_QSS = f"color: {WBS_TEXT}; font-size: 14px; font-weight: 500;"  # 页内小节标题
WBS_SECONDARY_QSS = f"color: {WBS_TEXT_2};"                      # 次要正文
WBS_ERR_QSS = f"color: {WBS_ERROR}; font-size: 12px;"            # 校验失败
WBS_OK_QSS = f"color: {WBS_SUCCESS}; font-size: 12px;"           # 校验通过

# QSS 里的 @TOKEN@ 占位表（保持单一数据源，别在 QSS 里写死色值）
_WBS_TOKENS = {
    "BG": WBS_BG, "CARD": WBS_CARD, "HOVER": WBS_HOVER,
    "HOVER_STRONG": WBS_HOVER_STRONG, "TRACK": WBS_TRACK,
    "BORDER": WBS_BORDER, "BORDER_STRONG": WBS_BORDER_STRONG,
    "TEXT": WBS_TEXT, "TEXT_2": WBS_TEXT_2, "TEXT_3": WBS_TEXT_3,
    "TEXT_GROUP": WBS_TEXT_GROUP,
    "ACCENT": WBS_ACCENT, "ACCENT_SOFT": WBS_ACCENT_SOFT,
    "SEL_TEXT": WBS_SEL_TEXT,
    "BTN": WBS_BTN, "BTN_HOVER": WBS_BTN_HOVER, "BTN_PRESSED": WBS_BTN_PRESSED,
    "R_DLG": str(WBS_RADIUS_DLG), "R_CARD": str(WBS_RADIUS_CARD),
    "R_ITEM": str(WBS_RADIUS_ITEM), "R_CTRL": str(WBS_RADIUS_CTRL),
    "NAV_PAD": str(WBS_NAV_PAD),
}

# 设置对话框专用 QSS。挂在 shell 上会向所有子控件级联，
# 但并不影响对话框之外的任何界面。
_WBS_DIALOG_QSS = """
/* ---- 弹窗外壳：灰绿底 + 24px 圆角，无描边（分层全靠底色差） ---- */
QFrame#settingsShell {
    background: @BG@;
    border: none;
    border-radius: @R_DLG@px;
}
QWidget#settingsRight { background: transparent; }
QStackedWidget#settingsStack { background: transparent; border: none; }

/* ---- 卡片：白底 + 16px 圆角 + 无描边 ---- */
QFrame#settingsCard {
    background: @CARD@;
    border: none;
    border-radius: @R_CARD@px;
}

/* ---- 左导航 ---- */
QListWidget#settingsNav {
    background: transparent;
    border: none;
    padding: @NAV_PAD@px;
    outline: none;
}
QListWidget#settingsNav::item {
    padding: 0 12px;
    margin: 1px 0;
    color: @TEXT@;
    font-size: 14px;
    border: none;
    border-radius: @R_ITEM@px;
}
QListWidget#settingsNav::item:hover { background: @HOVER@; }
QListWidget#settingsNav::item:selected {
    background: @ACCENT_SOFT@;
    color: @SEL_TEXT@;
}
QListWidget#settingsNav::item:hover:selected { background: @ACCENT_SOFT@; }
QListWidget#settingsNav::item:focus { border: none; }
/* 分组小标题项被设为 NoItemFlags → 命中 :disabled，因此无 hover / 不可选 */
QListWidget#settingsNav::item:disabled {
    background: transparent;
    color: @TEXT_GROUP@;
    font-size: 12px;
}

/* ---- 按钮 ---- */
QPushButton {
    background: @BTN@;
    color: #FFFFFF;
    border: 1px solid @BTN@;
    border-radius: @R_CTRL@px;
    padding: 0 16px;
    min-height: 30px;
    font-size: 14px;
}
QPushButton:hover { background: @BTN_HOVER@; border-color: @BTN_HOVER@; }
QPushButton:pressed { background: @BTN_PRESSED@; border-color: @BTN_PRESSED@; }
/* ⚠️ 必须显式压掉这两条：全局 Ant 主题里有 `QPushButton:focus{border-color:#4096FF}`
   和 `QPushButton:default{border-color:#1677FF}`，伪状态选择器比普通规则更具体，
   不覆盖就会在聚焦/默认按钮上露出一圈蓝边。 */
QPushButton:focus { border-color: @BTN@; }
QPushButton:default { border-color: @BTN@; }
QPushButton:disabled {
    background: @HOVER@;
    color: @TEXT_3@;
    border-color: @BORDER@;
}
QPushButton#btnOutline {
    background: @CARD@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
}
QPushButton#btnOutline:hover { color: @ACCENT@; border-color: @ACCENT@; }
QPushButton#btnOutline:pressed { color: @BTN_PRESSED@; border-color: @BTN_PRESSED@; }
QPushButton#btnOutline:focus { color: @TEXT@; border-color: @ACCENT@; }
QPushButton#btnOutline:default { border-color: @BORDER@; }
QPushButton#btnOutline:disabled {
    background: @HOVER@;
    color: @TEXT_3@;
    border-color: @BORDER@;
}
QPushButton#iconBtn {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px;
    min-height: 0;
}
QPushButton#iconBtn:hover { background: @HOVER@; }
QPushButton#iconBtn:pressed { background: @HOVER_STRONG@; }
QPushButton#iconBtn:focus { border: none; }

/* ---- 输入 / 下拉 ---- */
QLineEdit, QTextEdit {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: @R_CTRL@px;
    padding: 4px 11px;
    color: @TEXT@;
    font-size: 14px;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @SEL_TEXT@;
}
QLineEdit { min-height: 24px; }
QLineEdit:hover, QTextEdit:hover { border-color: @ACCENT@; }
QLineEdit:focus, QTextEdit:focus { border-color: @ACCENT@; }
QLineEdit:disabled, QTextEdit:disabled { background: @BG@; color: @TEXT_3@; }

QComboBox {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: @R_CTRL@px;
    padding: 0 12px;
    min-height: 30px;
    color: @TEXT@;
    font-size: 14px;
}
QComboBox:hover { border-color: @ACCENT@; }
QComboBox:focus { border-color: @ACCENT@; }
QComboBox:disabled { background: @BG@; color: @TEXT_3@; }
QComboBox::drop-down { border: none; width: 34px; }
/* ⚠️ 必须显式清掉 border/margin：全局 Ant 主题里也有一条 QComboBox::down-arrow
   （border-top 三角 hack），Qt 会把两份规则的**属性合并**，不清掉就会在我这条
   chevron 上面糊一根 5px 灰条（实测如此）。 */
QComboBox::down-arrow {
    image: url("@CHEV_DOWN@");
    width: 16px;
    height: 16px;
    border: none;
    margin: 0 9px 0 0;
}
/* 展开时箭头翻转朝上。用自维护的动态属性而不是 :on —— QComboBox 的 :on 在实测中
   没有触发（弹出不重绘本体），见 core.widgets.NoWheelComboBox。 */
QComboBox[popupOpen="true"]::down-arrow { image: url("@CHEV_UP@"); }
QComboBox QAbstractItemView {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: @ACCENT_SOFT@;
    selection-color: @SEL_TEXT@;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 0 8px;
    border-radius: 6px;
}
QComboBox QAbstractItemView::item:hover { background: @HOVER@; }
QComboBox QAbstractItemView::item:selected {
    background: @ACCENT_SOFT@;
    color: @SEL_TEXT@;
}

/* ---- 勾选 / 滑块 ---- */
QCheckBox { color: @TEXT@; spacing: 8px; font-size: 14px; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 4px;
    background: @CARD@;
}
QCheckBox::indicator:hover { border: 1px solid @ACCENT@; }
QCheckBox::indicator:checked {
    background: @ACCENT@;
    border-color: @ACCENT@;
    image: url("@CHECK@");
}
QCheckBox::indicator:disabled { background: @BG@; border-color: @BORDER@; }

QSlider { min-height: 20px; }
QSlider::groove:horizontal {
    height: 4px;
    background: @BORDER@;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    height: 4px;
    background: @ACCENT@;
    border-radius: 2px;
}
/* ⚠️ handle 的 border-radius 只有**显式给出 height** 时才生效，否则退化成矩形
   （实测表现为一个空心方块）。radius 必须 = height/2 = 7px。 */
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: @CARD@;
    border: 2px solid @ACCENT@;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { border-color: @BTN_HOVER@; }
QSlider::handle:horizontal:pressed {
    background: @HOVER@;
    border-color: @BTN_PRESSED@;
}

/* ---- 标签 / 列表 / 滚动条 ---- */
QLabel { color: @TEXT@; background: transparent; }
QListWidget {
    background: @CARD@;
    border: 1px solid @BORDER@;
    border-radius: @R_CTRL@px;
    padding: 4px;
    color: @TEXT@;
}
QListWidget::item { padding: 5px 10px; border-radius: 6px; }
QListWidget::item:hover { background: @HOVER@; }
QListWidget::item:selected { background: @ACCENT_SOFT@; color: @SEL_TEXT@; }
QListWidget::item:focus { border: none; }

QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.12);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: rgba(0, 0, 0, 0.22); }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: rgba(0, 0, 0, 0.12);
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: rgba(0, 0, 0, 0.22); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
"""


def wbs_dialog_qss():
    """返回设置对话框专用的 QSS（已把 @TOKEN@ 占位与图标路径换成实值）。

    图标（勾选对勾 / 下拉 chevron）走 `core.icons` 落成 PNG 再被 `image: url()` 引用；
    QSS 不支持 data URI，也不保证能直接吃 SVG，所以统一用 PNG（与既有做法一致）。
    """
    try:
        from core.icons import check_png_path, chevron_png_path
        check_png = check_png_path(color="#FFFFFF", px=16)
        chev_down = chevron_png_path(color=WBS_TEXT_2, up=False, size=16)
        chev_up = chevron_png_path(color=WBS_TEXT_2, up=True, size=16)
    except Exception:
        check_png = chev_down = chev_up = ""
    text = (_WBS_DIALOG_QSS
            .replace("@CHECK@", check_png)
            .replace("@CHEV_DOWN@", chev_down)
            .replace("@CHEV_UP@", chev_up))
    for key, val in _WBS_TOKENS.items():
        text = text.replace("@" + key + "@", val)
    return text
