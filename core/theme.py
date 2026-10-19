"""设计系统 —— 视觉令牌与 QSS 主题生成。

审美方向：深色沉浸 + Blender 橙强调 + 大圆角卡片 + 清晰字阶 + 轻量微交互。
QSS 不支持 transform/box-shadow，质感由边框+背景+代码侧阴影实现。
"""

from __future__ import annotations


class Tokens:
    # ── 背景层次（由深到浅）──
    # 2026-10 改版：色温由冷蓝灰改为暖褐灰，取值为实测 FlClash 深色主题
    # 的逐像素统计结果（占比见注释）。这是"看起来像 FlClash"最关键的一环 ——
    # 布局相同但底色偏红/偏蓝，观感差异就非常大。
    BG_ROOT = "#151313"         # FlClash 最底背景 35.3%
    BG_PANEL = "#1D1B1B"        # 次级 19.7%
    BG_CARD = "#221F1F"         # 卡片 12.5%
    BG_CARD_HOVER = "#272424"   # 悬停 7.5%
    BG_ELEVATED = "#373434"     # 顶栏/浮层 5.9%
    BG_INPUT = "#1A1717"        # 输入框：介于根背景与卡片之间

    # ── 边框 ──
    BORDER = "#2E2A2A"
    BORDER_STRONG = "#3D3838"

    # ── 文本字阶（暖白，与暖底协调）──
    TEXT = "#E8E4E2"
    TEXT_SUB = "#C7BFBE"
    TEXT_MUTED = "#8A8180"

    # ── 强调色 ──
    # 保留 Blender 橙：这是本应用的品牌关联，替换成 FlClash 的 #D8C0C3 会失去
    # 与 Blender 的视觉联系。橙色与暖褐底同属暖色系，搭配反而比冷底更协调。
    ACCENT = "#E87D0D"          # Blender 橙
    ACCENT_HOVER = "#F59024"
    ACCENT_PRESS = "#CE6F0A"
    ACCENT_SOFT = "rgba(232,125,13,0.16)"
    INFO = "#3D8BFD"
    SUCCESS = "#3FB950"
    WARN = "#D29922"
    DANGER = "#F85149"

    # ── 几何 ──
    R_CARD = "14px"
    R_CTRL = "9px"
    R_PILL = "18px"
    R_WINDOW = "12px"      # 无边框窗口的圆角（配合 WA_TranslucentBackground 才会真正裁切）
    NAV_W = 76             # 侧栏宽度：对齐 FlClash 的窄条侧栏（~70px），
                           # 恰好容纳 4 字中文（11px × 4 = 44px + 内边距）
    NAV_W_COLLAPSED = 76   # 合并态宽度：用户要求侧栏宽度固定为展开宽度，
                           # 折叠只切换文字显隐（纯图标模式），不缩宽度
    TOPBAR_H = 32          # 标题栏高度：Windows 11 官方规范（Microsoft Learn
                           # 《标题栏设计》：标准标题栏 32px）

    FONT = '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif'

    # ── 语义别名（旧代码里散落的硬编码色值，2026-10 收编进令牌系统）──
    # 中性色一律引用上面的基础令牌（改基础色即全局生效）；
    # 彩色功能色保留独立值，避免在暖底上损失可读性。
    ON_CARD = TEXT            # 卡片内正文（近 TEXT，稍暖）
    ON_CARD_SUB = TEXT_SUB        # 卡片内次要文字
    ON_CARD_MUTED = TEXT_MUTED      # 卡片内弱化文字/空态提示
    ON_CARD_DISABLED = TEXT_MUTED   # 禁用态文字
    ROW_BG = BG_CARD             # 列表行背景（近 BG_CARD）
    ROW_BG_HOVER = BG_CARD_HOVER       # 列表行 hover（近 BG_CARD_HOVER）
    ROW_BORDER = BORDER# 列表行边框
    CHIP_BG = BG_CARD_HOVER# 标签/芯片底色（近 BG_CARD_HOVER）
    CHIP_BORDER = BORDER_STRONG
    CHIP_TEXT = TEXT_SUB
    PAGER_TEXT = TEXT_MUTED
    ACCENT_SOFT_BG = "#3a2e1e"     # 橙色系浅底（近 ACCENT_SOFT）
    ACCENT_SOFT_BORDER = "#4a3e2e"
    ACCENT_ALT = "#e0a030"         # 次级橙（hover 态）
    ACCENT_LEGACY = "#ffa500"      # 旧版强调橙
    INFO_ALT = "#4a9cff"           # 次级蓝（hover 态）
    HIGHLIGHT_BLUE = "#60cdff"     # 冷调高亮蓝（选中/链接）
    HIGHLIGHT_BLUE_DIM = "#1e2e3e"# 高亮蓝的底色
    SUCCESS_BRIGHT = "#7cda7c"     # 亮绿（正文态）
    SUCCESS_SOFT = "#50c050"       # 中绿（按钮/图标）
    SUCCESS_GLOW = "#6cff6c"       # 高亮绿（强调数字）
    SUCCESS_BG = "#1e3a1e"         # 绿系底色
    SUCCESS_BORDER = "#2a4a1a"
    SUCCESS_BG_ALT = "#3a5a2a"
    DANGER_SOFT = "#e05050"        # 次级红（hover 态）
    DANGER_BG = "#3a1f1d"
    DANGER_BG_ALT = "#4A2A28"
    TRACK = BG_ELEVATED              # 进度槽
    TRACK_ALT = BG_CARD
    SELECTION = BG_CARD_HOVER          # 选中行
    DIVIDER = BORDER_STRONG
    DIVIDER_ALT = BORDER_STRONG
    OVERLAY_BG = BG_ROOT
    OVERLAY_BG_ALT = BG_PANEL
    ROW_ALT = BG_CARD_HOVER
    ROW_ALT2 = BG_PANEL
    ROW_ALT3 = BORDER_STRONG
    ROW_ALT4 = BG_CARD
    ROW_ALT5 = BG_ROOT
    PANEL_BLUE = "#1a3a5a"
    GREY_1 = TEXT_SUB
    GREY_2 = TEXT_SUB
    GREY_3 = TEXT_MUTED
    GREY_4 = TEXT_MUTED
    ROW_ACCENT = "#3A1F1D"


def stylesheet() -> str:
    t = Tokens
    return f"""
/* ══ 全局 ══ */
QWidget {{
    background-color: {t.BG_ROOT};
    color: {t.TEXT};
    font-family: {t.FONT};
    font-size: 13px;
}}
QWidget#windowRoot {{
    background-color: {t.BG_ROOT};
    border: 1px solid {t.BORDER};
    /* 无边框窗口的圆角：必须在代码里开 WA_TranslucentBackground，
       否则圆角外的直角会露出黑底 */
    border-radius: {t.R_WINDOW};
}}
/* 最大化/全屏：去圆角去边框，内容铺满整个工作区
   （属性由 MainWindow.resizeEvent 同步维护） */
QWidget#windowRoot[maximized="true"] {{
    border: none;
    border-radius: 0px;
}}

/* ══ 自定义标题栏 ══
   标题栏左侧品牌区（logo + BlenderHub + v2.0）已按用户要求移除（2026-10-16），
   品牌标识只保留在侧栏顶部；brandLogo/brandMark/brandSub 规则随之废弃。 */
QWidget#topBar {{
    background-color: {t.BG_PANEL};
    border-bottom: 1px solid {t.BORDER};
}}
/* Windows 11 标准 caption 按钮（Microsoft Learn《标题栏设计》+ Fluent 规范）：
   46x32 热区、无边框、原生无圆角、失焦半透明、关闭 hover 官方红 #C42B1C。
   图标为 QPainter 矢量绘制（core/icons.py caption_icon），不依赖字体符号。 */
QPushButton#winBtn, QPushButton#winBtnClose {{
    background-color: transparent;
    border: none;
    padding: 0;
    color: {t.TEXT_SUB};
    min-width: 46px;
    max-width: 46px;
    min-height: 32px;
    max-height: 32px;
}}
QPushButton#winBtn:hover {{ background-color: {t.BG_ELEVATED}; color: {t.TEXT}; }}
QPushButton#winBtnClose:hover {{ background-color: #C42B1C; color: #ffffff; }}

/* ══ 左侧导航 ══ */
QWidget#navBar {{ background-color: {t.BG_PANEL}; border-right: 1px solid {t.BORDER}; }}
/* 侧栏导航项：QToolButton + ToolButtonTextUnderIcon（图标在上、文字在下）。
   注意选择器必须是 QToolButton —— 写 QPushButton 不会命中。 */
QToolButton#navItem {{
    background-color: transparent;
    border: none;
    border-radius: 10px;
    color: {t.TEXT_SUB};
    padding: 8px 4px 6px 4px;
    font-size: 11px;
}}
QToolButton#navItem:hover {{
    background-color: {t.BG_CARD};
    color: {t.TEXT};
}}
QToolButton#navItem[selected="true"] {{
    background-color: {t.BG_CARD};
    color: {t.TEXT};
}}
/* 侧栏顶部品牌 logo 容器 */
QLabel#navLogo {{
    background-color: transparent;
    border: none;
}}
/* 侧栏底部分隔线（导航组与三横按钮之间） */
QFrame#navDivider {{
    background-color: transparent;
    color: {t.BORDER};
    border: none;
    border-top: 1px solid {t.BORDER};
    margin: 6px 8px 6px 8px;
    max-height: 1px;
}}
/* 侧栏底部三横按钮：固定尺寸小热区（44x32），由布局 AlignHCenter 居中，
   不像导航项那样占满栏宽 —— FlClash 的合并按钮就是居中的小图标按钮 */
QToolButton#navToggle {{
    background-color: transparent;
    border: none;
    border-radius: 10px;
    color: {t.TEXT_SUB};
    padding: 0;
    min-width: 44px;
    max-width: 44px;
    min-height: 32px;
    max-height: 32px;
}}
QToolButton#navToggle:hover {{
    background-color: {t.BG_CARD};
    color: {t.TEXT};
}}
QLabel#navSection {{ color: {t.TEXT_MUTED}; font-size: 10px; padding: 14px 18px 6px 18px; letter-spacing: 1px; }}
QWidget#navFooter {{ background-color: transparent; }}

/* ══ 页面 ══ */
QLabel#pageTitle {{ color: {t.TEXT}; font-size: 20px; font-weight: 650; }}
QLabel#pageHint {{ color: {t.TEXT_MUTED}; font-size: 12px; }}
QLabel#sectionTitle {{ color: {t.TEXT_SUB}; font-size: 12px; font-weight: 600; letter-spacing: .6px; }}

/* ══ 卡片 ══
   注意：卡片背景/边框/圆角已改为 **AddonCard.paintEvent 自绘**
   （core/icons 无关，见 modules/addon_store/module.py）。
   原因：①QSS border-radius 在未开 WA_TranslucentBackground 的 widget 上
   画直角背景；②开了 WA_TranslucentBackground 后 QSS 背景整体丢失
   （实测卡片区域露出窗口根底色，缩略图块边界消失——用户 2026-10-16
   反馈"图块边框没有了"）。自绘是唯一可控路径，QSS 规则随之退役。 */
QFrame#card {{ }}
QFrame#panel {{
    background-color: {t.BG_PANEL};
    border: 1px solid {t.BORDER};
    border-radius: {t.R_CARD};
}}

/* ══ 按钮 ══ */
QPushButton {{
    background-color: {t.BG_ELEVATED};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: {t.R_CTRL};
    color: {t.TEXT};
    padding: 7px 14px;
    font-size: 12.5px;
}}
QPushButton:hover {{ background-color: #2A303C; border: 1px solid #3D4553; }}
QPushButton:pressed {{ background-color: #20252F; }}
QPushButton:disabled {{ background-color: {t.BG_CARD}; color: {t.TEXT_MUTED}; border: 1px solid {t.BORDER}; }}
QPushButton#primary {{
    background-color: {t.ACCENT};
    border: 1px solid {t.ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background-color: {t.ACCENT_HOVER}; border: 1px solid {t.ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background-color: {t.ACCENT_PRESS}; }}
/* 卡片内小号主按钮（安装按钮）：objectName=primaryButton。
   注意与 #primary 区分——旧实现只给大按钮定义了样式，卡片安装按钮
   objectName 不匹配导致掉回 Qt 默认灰色样式（无悬停/点击反馈）。 */
QPushButton#primaryButton {{
    background-color: {t.ACCENT};
    border: 1px solid {t.ACCENT};
    border-radius: 5px;
    color: #ffffff;
    padding: 2px 12px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{
    background-color: {t.ACCENT_HOVER};
    border: 1px solid {t.ACCENT_HOVER};
}}
QPushButton#primaryButton:pressed {{
    background-color: {t.ACCENT_PRESS};
    border: 1px solid {t.ACCENT_PRESS};
    /* padding 位移模拟按下：文字下沉 1px */
    padding: 3px 11px 1px 13px;
}}
QPushButton#ghost {{ background-color: transparent; border: 1px solid {t.BORDER_STRONG}; }}
QPushButton#ghost:hover {{ background-color: {t.BG_ELEVATED}; }}
QPushButton#danger {{ color: {t.DANGER}; border: 1px solid #4A2A28; background-color: transparent; }}
QPushButton#danger:hover {{ background-color: #3A1F1D; }}

/* ══ 输入 ══ */
QLineEdit, QComboBox {{
    background-color: {t.BG_INPUT};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: {t.R_CTRL};
    padding: 7px 11px;
    color: {t.TEXT};
    selection-background-color: {t.ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {t.ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {t.BG_ELEVATED};
    border: 1px solid {t.BORDER_STRONG};
    selection-background-color: {t.ACCENT_SOFT};
    color: {t.TEXT};
    outline: none;
}}

/* ══ 滚动 ══ */
QScrollArea {{ background-color: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #333A47; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #414A5A; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #333A47; border-radius: 5px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}

/* ══ 进度 ══ */
QProgressBar {{
    background-color: {t.BG_INPUT};
    border: 1px solid {t.BORDER};
    border-radius: 6px;
    height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {t.ACCENT}; border-radius: 5px; }}

/* ══ 列表/表格 ══ */
QListWidget, QTreeWidget {{
    background-color: {t.BG_PANEL};
    border: 1px solid {t.BORDER};
    border-radius: {t.R_CARD};
    outline: none;
    padding: 4px;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 2px; }}
QListWidget::item:hover {{ background-color: {t.BG_CARD_HOVER}; }}
QListWidget::item:selected {{ background-color: {t.ACCENT_SOFT}; color: {t.TEXT}; }}

/* ══ 徽标 ══ */
QLabel#badge {{
    background-color: {t.ACCENT_SOFT};
    color: {t.ACCENT};
    border-radius: 7px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#badgeMuted {{ color: {t.TEXT_MUTED}; font-size: 11px; }}
QLabel#badgeOk {{ color: {t.SUCCESS}; font-size: 11px; }}
QLabel#badgeWarn {{ color: {t.WARN}; font-size: 11px; }}

/* ══ 标签页 ══ */
QTabWidget::pane {{
    border: 1px solid {t.BORDER};
    border-radius: {t.R_CARD};
    background-color: transparent;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.TEXT_SUB};
    padding: 8px 18px;
    margin-right: 4px;
    border: 1px solid transparent;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    font-size: 12.5px;
}}
QTabBar::tab:hover {{ color: {t.TEXT}; background-color: {t.BG_CARD}; }}
QTabBar::tab:selected {{
    color: {t.ACCENT};
    background-color: {t.BG_PANEL};
    border: 1px solid {t.BORDER};
    border-bottom-color: {t.BG_PANEL};
    font-weight: 600;
}}

/* ══ 对话框 / 关于链接行 ══ */
QDialog {{ background-color: {t.BG_PANEL}; }}
QLabel#dialogTitle {{ color: {t.TEXT}; font-size: 20px; font-weight: 650; }}
QLabel#dialogBody {{ color: {t.TEXT_SUB}; font-size: 12.5px; }}
QFrame#linkRow {{ background-color: transparent; border-radius: 8px; }}
QFrame#linkRow:hover {{ background-color: {t.BG_CARD_HOVER}; }}
QLabel#linkRowText {{ color: {t.TEXT}; font-size: 13px; background: transparent; }}
QLabel#linkRowArrow {{ color: {t.TEXT_MUTED}; font-size: 13px; background: transparent; }}

/* ══ 语义文本 / 标签 / 按钮 ══
   过去各模块到处写 `font-size: 11px`、`color: #888` 之类的内联样式，
   换主题时必然漏改。统一收成下面这些语义类，模块只设 objectName。 */
QLabel#title {{ color: {t.TEXT}; font-size: 13px; font-weight: 600; }}
QLabel#body {{ color: {t.TEXT_SUB}; font-size: 12.5px; }}
QLabel#muted {{ color: {t.TEXT_MUTED}; font-size: 11px; }}
QLabel#hint {{ color: {t.TEXT_MUTED}; font-size: 13px; }}
QLabel#tagInstalled {{
    color: {t.SUCCESS_BRIGHT}; background-color: {t.SUCCESS_BG};
    border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: bold;
}}
QLabel#tagAccent {{
    color: {t.ACCENT_LEGACY}; background-color: {t.ACCENT_SOFT_BG};
    border-radius: 6px; padding: 8px 20px; font-size: 13px; font-weight: bold;
}}
QLabel#tagLink {{
    color: {t.HIGHLIGHT_BLUE}; background-color: {t.HIGHLIGHT_BLUE_DIM};
    border-radius: 4px; padding: 2px 10px; font-size: 11px;
}}
QLabel#tagMuted {{
    color: #888; background-color: {t.CHIP_BG};
    border-radius: 4px; padding: 2px 10px; font-size: 11px;
}}
QFrame#thumbBox {{ background-color: {t.OVERLAY_BG}; border-radius: 8px; }}
QPushButton#price {{
    background-color: {t.ACCENT_SOFT_BG}; color: {t.ACCENT_LEGACY};
    border: none; border-radius: 6px; padding: 8px 20px;
    font-size: 13px; font-weight: bold;
}}
QPushButton#price:hover {{ background-color: {t.ACCENT_SOFT_BORDER}; }}
/* 键盘可达性：Tab 聚焦时给出可见反馈 */
QPushButton:focus {{ border: 1px solid {t.ACCENT}; }}
QToolButton:focus {{ border: 1px solid {t.ACCENT}; }}

/* ══ 菜单/提示 ══ */
QMenu {{ background-color: {t.BG_ELEVATED}; border: 1px solid {t.BORDER_STRONG}; border-radius: 10px; padding: 5px; color: {t.TEXT}; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {t.ACCENT_SOFT}; color: {t.TEXT}; }}
QToolTip {{ background-color: {t.BG_ELEVATED}; color: {t.TEXT}; border: 1px solid {t.BORDER_STRONG}; padding: 5px 8px; }}
QMessageBox {{ background-color: {t.BG_PANEL}; }}
"""
