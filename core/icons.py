"""导航图标 — 用 QPainter 矢量绘制，不依赖 emoji 字体。

为什么不直接用 emoji 字符：
1. `QPushButton` **不渲染 HTML 富文本**（只有 QLabel 会），所以
   `f"<span style='font-size:17px'>{icon}</span>{label}"` 会被原样当纯文本显示 —— 这是
   2026-10-16 修图标时踩的坑（界面直接把 `<span style=...>` 显示出来了）。
2. emoji/几何符号的实际大小受字体影响，无法精确控制；QPushButton 的文本字号一改，
   图标跟着变，做不到"图标大、文字正常"。
3. 不同系统的符号字形差异大（有的还是彩色 emoji），视觉不可控。

因此改为：QPainter 画到 QPixmap，再用 QIcon + setIconSize 精确控制像素尺寸。
按 2 倍分辨率绘制，保证 HiDPI 下清晰。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# 逻辑尺寸（实际按 DPR 放大绘制）
NAV_ICON_SIZE = 18


def _new_canvas(size: int, dpr: float = 2.0) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pm, p


def _pen(p: QPainter, color: str, width: float = 1.5) -> None:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


# ── 各图标绘制 ────────────────────────────────────────────────────────────

def _draw_addon_store(p: QPainter, s: float, color: str) -> None:
    """插件市场：2x2 圆角方块网格（象征一组插件）"""
    _pen(p, color, 1.5)
    gap = s * 0.12
    cell = (s - gap) / 2
    r = cell * 0.26
    for row in range(2):
        for col in range(2):
            x = col * (cell + gap)
            y = row * (cell + gap)
            rect = QRectF(x, y, cell, cell)
            path = QPainterPath()
            path.addRoundedRect(rect, r, r)
            p.drawPath(path)
    # 右上角小圆点作为点缀
    p.setBrush(QColor(color))
    dot = s * 0.09
    p.drawEllipse(QPointF(s - dot, dot), dot, dot)


def _draw_config_center(p: QPainter, s: float, color: str) -> None:
    """配置中心：三条滑块（左右不同位置的圆点）"""
    _pen(p, color, 1.4)
    ys = [s * 0.25, s * 0.5, s * 0.75]
    knob_x = [s * 0.66, s * 0.34, s * 0.58]
    for y, kx in zip(ys, knob_x):
        p.drawLine(QPointF(s * 0.16, y), QPointF(s * 0.84, y))
    p.setBrush(QColor(color))
    r = s * 0.105
    for y, kx in zip(ys, knob_x):
        p.drawEllipse(QPointF(kx, y), r, r)


def _draw_project_center(p: QPainter, s: float, color: str) -> None:
    """项目中心：文件夹"""
    _pen(p, color, 1.5)
    body = QRectF(s * 0.14, s * 0.28, s * 0.72, s * 0.46)
    path = QPainterPath()
    path.addRoundedRect(body, s * 0.09, s * 0.09)
    p.drawPath(path)
    # 上方的标签页
    tab = QPainterPath()
    tab.moveTo(s * 0.14, s * 0.30)
    tab.lineTo(s * 0.14, s * 0.22)
    tab.lineTo(s * 0.44, s * 0.22)
    tab.lineTo(s * 0.50, s * 0.28)
    p.drawPath(tab)


def _draw_version_library(p: QPainter, s: float, color: str) -> None:
    """版本更新：环形刷新箭头（沿用 Material refresh 的缺口 + 箭头造型）"""
    import math

    _pen(p, color, 1.5)
    cx = cy = s / 2
    r = s * 0.30
    rect = QRectF(cx - r, cy - r, r * 2, r * 2)
    path = QPainterPath()
    path.arcMoveTo(rect, 40)          # 右上留出缺口，避免与箭头重叠
    path.arcTo(rect, 40, 280)
    p.drawPath(path)

    # 箭头在缺口起点、指向顺时针方向
    ang = math.radians(40)
    tip = QPointF(cx + math.cos(ang) * r, cy - math.sin(ang) * r)
    tangent = QPointF(math.sin(ang), math.cos(ang))
    normal = QPointF(-math.cos(ang), math.sin(ang))
    length = s * 0.105
    p.drawLine(tip, QPointF(
        tip.x() - tangent.x() * length + normal.x() * length,
        tip.y() - tangent.y() * length + normal.y() * length,
    ))
    p.drawLine(tip, QPointF(
        tip.x() - tangent.x() * length - normal.x() * length,
        tip.y() - tangent.y() * length - normal.y() * length,
    ))


def _draw_settings(p: QPainter, s: float, color: str) -> None:
    """设置：齿轮（中心圆 + 6 个齿）"""
    import math

    _pen(p, color, 1.5)
    cx = cy = s / 2
    r_out = s * 0.30
    r_in = s * 0.16
    path = QPainterPath()
    teeth = 6
    steps = teeth * 2
    for i in range(steps + 1):
        ang = (math.pi * 2 / steps) * i - math.pi / 2
        rr = r_out if i % 2 == 0 else r_out * 0.76
        x = cx + math.cos(ang) * rr
        y = cy + math.sin(ang) * rr
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(QPointF(cx, cy), r_in, r_in)


def _draw_menu(p: QPainter, s: float, color: str) -> None:
    """菜单：三条等长横线（Clash 侧栏底部的展开/合并按钮）"""
    _pen(p, color, 1.5)
    ys = [s * 0.26, s * 0.50, s * 0.74]
    for y in ys:
        p.drawLine(QPointF(s * 0.18, y), QPointF(s * 0.82, y))


# ── Windows 11 标准标题栏按钮图标（caption glyphs）────────────────────────
# 依据 Microsoft Learn《标题栏设计》：caption 按钮 46x32，关闭 hover #C42B1C；
# 图标为 1.5px 线宽的几何符号（原生 Segoe Fluent Icons 的等宽矢量等价物）。
CAPTION_ICON_SIZE = 12
CAPTION_LINE_W = 1.0   # caption glyph 线宽：1.5 在 12px 尺寸下偏粗（用户反馈
                        # "最大化线粗了"），Win11 原生 glyph 约 1px 量级


def _draw_caption_min(p: QPainter, s: float, color: str) -> None:
    """最小化：一条水平短线（y 居中，宽 60%）"""
    _pen(p, color, CAPTION_LINE_W)
    p.drawLine(QPointF(s * 0.20, s * 0.5), QPointF(s * 0.80, s * 0.5))


def _draw_caption_max(p: QPainter, s: float, color: str) -> None:
    """最大化：空心方框（居中，占 60%）"""
    _pen(p, color, CAPTION_LINE_W)
    path = QPainterPath()
    path.addRoundedRect(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 0.5, 0.5)
    p.drawPath(path)


def _draw_caption_restore(p: QPainter, s: float, color: str) -> None:
    """还原：两个叠置方框（后大前小，Win11 restore 图标造型）"""
    _pen(p, color, CAPTION_LINE_W)
    # 后方大方框（只画上边和右边，形成叠置感）
    back = QPainterPath()
    back.moveTo(s * 0.24, s * 0.12)
    back.lineTo(s * 0.88, s * 0.12)
    back.lineTo(s * 0.88, s * 0.76)
    p.drawPath(back)
    # 前方实心小方框
    front = QPainterPath()
    front.addRoundedRect(QRectF(s * 0.12, s * 0.24, s * 0.52, s * 0.52), 0.5, 0.5)
    p.drawPath(front)


def _draw_caption_close(p: QPainter, s: float, color: str) -> None:
    """关闭：X 交叉线（Win11 为 10px 正方形内两条对角线）"""
    _pen(p, color, CAPTION_LINE_W)
    p.drawLine(QPointF(s * 0.26, s * 0.26), QPointF(s * 0.74, s * 0.74))
    p.drawLine(QPointF(s * 0.74, s * 0.26), QPointF(s * 0.26, s * 0.74))


_CAPTION_DRAWERS = {
    "min": _draw_caption_min,
    "max": _draw_caption_max,
    "restore": _draw_caption_restore,
    "close": _draw_caption_close,
}


def caption_icon(kind: str, color: str, size: int = CAPTION_ICON_SIZE) -> QIcon:
    """构造 Win11 标准标题栏按钮图标（min/max/restore/close）。"""
    icon = QIcon()
    pm, p = _new_canvas(size)
    try:
        drawer = _CAPTION_DRAWERS.get(kind)
        if drawer is not None:
            drawer(p, float(size), color)
        else:
            _draw_caption_close(p, float(size), color)
    finally:
        p.end()
    icon.addPixmap(pm)
    return icon


def caption_icon_size(size: int = CAPTION_ICON_SIZE) -> QSize:
    return QSize(size, size)


_DRAWERS = {
    "addon_store": _draw_addon_store,
    "config_center": _draw_config_center,
    "project_center": _draw_project_center,
    "version_library": _draw_version_library,
    "settings": _draw_settings,
    "menu": _draw_menu,
}


def nav_pixmap(kind: str, color: str, size: int = NAV_ICON_SIZE) -> QPixmap:
    """绘制单个导航图标。未知 kind 回退为四角小方块。"""
    pm, p = _new_canvas(size)
    try:
        drawer = _DRAWERS.get(kind)
        if drawer is None:
            _draw_addon_store(p, float(size), color)
        else:
            drawer(p, float(size), color)
    finally:
        p.end()
    return pm


def nav_icon(kind: str, color: str, size: int = NAV_ICON_SIZE) -> QIcon:
    """构造导航图标（单色；选中态由调用方换色重设）"""
    icon = QIcon()
    icon.addPixmap(nav_pixmap(kind, color, size))
    return icon


def nav_icon_size(size: int = NAV_ICON_SIZE) -> QSize:
    return QSize(size, size)


# ── 品牌图标（标题栏）────────────────────────────────────────────────────

def official_icon_path() -> Path | None:
    """定位应用图标文件（界面品牌 logo 与应用图标同源）。

    frozen 运行时随 --add-data 放在 _MEIPASS 根目录；
    开发环境在 packaging/ 下（历史上放在 build/，保留兼容）。

    注意：这里必须覆盖 packaging/ —— 图标已从 build/ 迁到 packaging/，
    只查 build/ 会让源码运行时找不到文件、静默回退成代码画的 "B" 方块，
    表现为"界面 logo 还是旧的"。与 main.py:_app_icon_path 保持同一套查找顺序。
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "icon.ico")  # type: ignore[attr-defined]
    root = Path(__file__).resolve().parent.parent
    candidates.append(root / "packaging" / "icon.ico")
    candidates.append(root / "build" / "icon.ico")   # 旧布局兼容
    for p in candidates:
        if p.exists():
            return p
    return None


def brand_pixmap(color: str, size: int = 20) -> QPixmap:
    """标题栏/侧栏品牌图标。

    优先使用 **应用图标**（packaging/icon.ico，打包后取 _MEIPASS/icon.ico）；
    找不到时才退回代码绘制的圆角方块 + "B"。
    注意：只替换 exe 的资源图标不会改变界面内的 logo —— 这里才是界面 logo 的来源。
    """
    icon_file = official_icon_path()
    if icon_file is not None:
        icon = QIcon(str(icon_file))
        if not icon.isNull():
            pm = icon.pixmap(QSize(size, size))
            if not pm.isNull():
                return pm
    return _draw_brand_fallback(color, size)


def _draw_brand_fallback(color: str, size: int) -> QPixmap:
    """回退方案：圆角方块 + 点阵 "B"（仅在官方图标缺失时使用）"""
    pm, p = _new_canvas(size)
    try:
        s = float(size)
        # 圆角方块底（描边风格，跟随主题强调色）
        _pen(p, color, 1.6)
        path = QPainterPath()
        path.addRoundedRect(QRectF(s * 0.10, s * 0.10, s * 0.80, s * 0.80), s * 0.22, s * 0.22)
        p.drawPath(path)

        # 点阵 "B"（3x5，用细横线绘制，避免小尺寸下糊成一团）
        p.setBrush(QColor(color))
        x0 = s * 0.34
        stem_w = s * 0.09
        top = s * 0.28
        bottom = s * 0.72
        rows = 5
        step = (bottom - top) / (rows - 1)
        # 竖干
        p.drawRect(QRectF(x0, top, stem_w, bottom - top))
        # 三条横（顶/中/底）
        bar_w = s * 0.24 if True else 0.0
        for idx_row in (0, 2, 4):
            y = top + step * idx_row
            p.drawRect(QRectF(x0, y - s * 0.045, bar_w, s * 0.09))
        # 上下两个碗的右侧竖
        p.drawRect(QRectF(x0 + bar_w - stem_w, top, stem_w, step * 2))
        p.drawRect(QRectF(x0 + bar_w - stem_w, top + step * 2, stem_w, step * 2))
    finally:
        p.end()
    return pm
