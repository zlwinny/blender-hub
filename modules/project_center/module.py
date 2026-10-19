"""
项目中心模块 — 扫描最近 .blend 项目，绑定版本打开

UI: 响应式网格 + .blend 内嵌预览缩略图 + 版本匹配提示
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QGridLayout, QMessageBox, QFileDialog,
    QListWidget, QListWidgetItem, QMenu,
)
from PyQt6.QtCore import (
    QObject, QPointF, QRunnable, QRectF, Qt, QThread, QThreadPool, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from pathlib import Path
from typing import Optional, List
from datetime import datetime
import os
import json
import struct
import threading

from core import theme  # pyright: ignore[reportImplicitRelativeImport]
from core.api.module_api import BaseModule
from core.services.blender_service import BlenderInstallation


# 常用项目目录（自动扫描）
COMMON_PROJECT_DIRS = [
    "~/Documents/Blender",
    "~/Desktop",
    "~/Downloads",
    "D:/BlenderProjects",
    "D:/Projects",
]

# ── 卡片尺寸（网格按可用宽度自动决定列数）────────────────────────────────
CARD_W = 232
CARD_H = 244
THUMB_H = 108
GRID_GAP = 10


# ── 缩略图：优先 .blend 内嵌预览，其次同名图片，最后矢量占位 ──────────────
#
# Blender 保存时会把视口预览写进文件里的 TEST 块，格式为
#   BlendThumbnail { int32 width; int32 height; uchar rgba[]; }
# 绝大多数文件的长相是 "BLENDER" + 指针宽度('_'=4/'-'=8) + 端序('v'/''V') + 版本3字节。
# 这里按 BHead 链表顺序查找，命中即返回；压缩文件（gzip）直接跳过，避免整包解压卡界面。
_THUMB_CACHE: dict[str, tuple[float, QPixmap]] = {}
# 后台线程专用缓存（存 QImage，主线程再转 QPixmap）
_THUMB_IMAGE_CACHE: dict[str, tuple[float, QImage]] = {}
_MAX_BLOCK = 64 * 1024 * 1024
_SIBLING_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _read_blend_preview(path: Path) -> Optional[QImage]:
    """从 .blend 的 TEST 块读出内嵌预览图；失败返回 None（绝不抛异常）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:7] != b"BLENDER":
                return None            # 压缩包 / 非 blend
            ptr = 8 if head[7:8] == b"-" else 4
            endian = "<" if head[8:9] == b"v" else ">"
            bhead = 4 + 4 + ptr + 4 + 4

            for _ in range(20000):
                bh = f.read(bhead)
                if len(bh) < bhead:
                    return None
                code = bh[0:4]
                length = struct.unpack(endian + "i", bh[4:8])[0]
                if code == b"ENDB":
                    return None
                if length < 0 or length > _MAX_BLOCK:
                    return None
                if code != b"TEST":
                    f.seek(length, 1)
                    continue

                data = f.read(length)
                if length == 256 * 256 * 4:
                    w = h = 256
                    raw = data
                else:
                    if len(data) < 8:
                        return None
                    w, h = struct.unpack(endian + "ii", data[:8])
                    raw = data[8:]
                if not (0 < w <= 2048 and 0 < h <= 2048) or len(raw) < w * h * 4:
                    return None
                img = QImage(bytes(raw), w, h, w * 4, QImage.Format.Format_RGBA8888)
                if img.isNull():
                    return None
                # Blender 的 TEST 块按 OpenGL 习惯自下而上存储，直接交给 QImage 会上下颠倒。
                # 这里垂直翻转回来；flipped() 返回独立缓冲区，也顺带避免了
                # QImage 引用 Python bytes 生命周期的问题。
                return img.flipped(Qt.Orientation.Vertical)
    except (OSError, struct.error, ValueError):
        return None


def _sibling_image(path: Path) -> Optional[Path]:
    """同名图片兜底：很多用户会把渲染图放在 .blend 旁边。"""
    for ext in _SIBLING_EXTS:
        cand = path.with_suffix(ext)
        if cand.is_file():
            return cand
    return None


def load_thumbnail_image(path: Path) -> Optional[QImage]:
    """线程安全版：只产出 QImage。

    ⚠ QPixmap 属于 GUI 资源，Qt 要求只能在主线程创建/使用；在 worker 线程里
    `QPixmap(...)` / `QPixmap.fromImage(...)` 会在 Windows 上直接崩溃。
    后台解析缩略图必须走这个函数，回主线程再转 QPixmap。
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    key = str(path)
    cached = _THUMB_IMAGE_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    img = _read_blend_preview(path)
    if img is None or img.isNull():
        sib = _sibling_image(path)
        if sib is not None:
            loaded = QImage(str(sib))     # QImage 可直接从文件加载，线程安全
            if not loaded.isNull():
                img = loaded
    if img is None or img.isNull():
        return None
    _THUMB_IMAGE_CACHE[key] = (mtime, img)
    return img


def load_thumbnail(path: Path) -> Optional[QPixmap]:
    """主线程取缩略图（QPixmap，带 mtime 失效缓存），内部复用 QImage 缓存。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    key = str(path)
    cached = _THUMB_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    img = load_thumbnail_image(path)
    if img is None:
        return None
    pm = QPixmap.fromImage(img)
    if pm.isNull():
        return None
    _THUMB_CACHE[key] = (mtime, pm)
    return pm


def peek_thumbnail(path: Path) -> Optional[QPixmap]:
    """只取**缓存命中**的缩略图，绝不读盘。

    卡片构造时用它：命中就直接显示，未命中先占位，由后台线程解析后回填，
    避免主线程同步读取 .blend（单块最大 64MB × 50 张 → 打开页面瞬间卡住）。
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _THUMB_CACHE.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    return None


class _ThumbSignals(QObject):
    # 用 QImage 而非 QPixmap 跨线程传递（QPixmap 不能离开 GUI 线程）
    ready = pyqtSignal(str, QImage)   # (项目路径, 缩略图)


class _ThumbTask(QRunnable):
    """在线程池里解析 .blend 内嵌预览（只产出 QImage，不碰 UI）。"""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.signals = _ThumbSignals()

    def run(self) -> None:
        img = load_thumbnail_image(self.path)
        if img is not None and not img.isNull():
            self.signals.ready.emit(str(self.path), img)


def _cover(pm: QPixmap, w: int, h: int) -> QPixmap:
    """等比缩放并居中裁切到 w×h（避免拉伸变形）。"""
    if pm.isNull() or w <= 0 or h <= 0:
        return pm
    scaled = pm.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - w) // 2)
    y = max(0, (scaled.height() - h) // 2)
    return scaled.copy(x, y, w, h)


def _placeholder_pixmap(w: int, h: int) -> QPixmap:
    """没有预览图时的矢量占位（不用 emoji：部分环境缺字体会显示成方框）。"""
    pm = QPixmap(max(1, w), max(1, h))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#232a42"))
        p.drawRoundedRect(0, 0, w, h, 8, 8)

        # 山峦轮廓，表达「没有预览图」而不是「加载失败」
        p.setBrush(QColor("#39456a"))
        path = QPainterPath()
        path.moveTo(w * 0.16, h * 0.76)
        path.lineTo(w * 0.40, h * 0.42)
        path.lineTo(w * 0.56, h * 0.64)
        path.lineTo(w * 0.68, h * 0.52)
        path.lineTo(w * 0.84, h * 0.76)
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(QColor("#4b5883"))
        r = max(2.0, w * 0.055)
        p.drawEllipse(QPointF(w * 0.74, h * 0.30), r, r)
    finally:
        p.end()
    return pm


class ProjectInfo:
    """项目信息"""
    def __init__(
        self,
        path: Path,
        name: str = "",
        size: int = 0,
        modified: str = "",
        bound_version: str = "",
        thumbnail: str = "",
    ):
        self.path = path
        self.name = name or path.stem
        self.size = size
        self.modified = modified
        self.bound_version = bound_version
        self.thumbnail = thumbnail

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "size": self.size,
            "modified": self.modified,
            "bound_version": self.bound_version,
        }

    @staticmethod
    def from_dict(d: dict) -> "ProjectInfo":
        return ProjectInfo(
            path=Path(d["path"]),
            name=d.get("name", ""),
            size=d.get("size", 0),
            modified=d.get("modified", ""),
            bound_version=d.get("bound_version", ""),
        )


class ProjectCard(QFrame):
    """项目卡片"""
    open_clicked = pyqtSignal(str)     # path
    bind_clicked = pyqtSignal(str)     # path

    def __init__(self, project: ProjectInfo, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("card")
        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # 缩略图：缓存命中直接用；未命中先显示矢量占位，
        # 由后台线程解析 .blend 后回填（避免主线程同步读大文件卡顿）
        thumb_w = CARD_W - 28
        self._thumb = QLabel()
        self._thumb.setObjectName("thumbBox")
        self._thumb.setFixedSize(thumb_w, THUMB_H)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb.setToolTip(str(project.path))
        cached = peek_thumbnail(project.path)
        self._thumb.setPixmap(
            _cover(cached, thumb_w, THUMB_H)
            if cached is not None and not cached.isNull()
            else _placeholder_pixmap(thumb_w, THUMB_H)
        )
        layout.addWidget(self._thumb)

        # 项目名
        name_label = QLabel(project.name)
        name_label.setObjectName("title")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # 修改时间
        time_label = QLabel(project.modified)
        time_label.setObjectName("muted")
        layout.addWidget(time_label)

        # 绑定版本
        if project.bound_version:
            ver_label = QLabel(f"🔗 Blender {project.bound_version}")
            ver_label.setObjectName("badge")
            layout.addWidget(ver_label)
        else:
            no_ver = QLabel("未绑定版本")
            no_ver.setObjectName("badgeWarn")
            layout.addWidget(no_ver)

        layout.addStretch()

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        open_btn = QPushButton("🚀 打开")
        open_btn.setObjectName("primaryButton")
        open_btn.setFixedHeight(28)
        open_btn.clicked.connect(lambda: self.open_clicked.emit(str(self.project.path)))
        btn_row.addWidget(open_btn, 1)

        more_btn = QPushButton("⋯")
        more_btn.setObjectName("ghost")
        more_btn.setFixedSize(28, 28)
        more_btn.clicked.connect(self._show_menu)
        btn_row.addWidget(more_btn)

        layout.addLayout(btn_row)

        # 双击打开
        self.mouseDoubleClickEvent = lambda e: self.open_clicked.emit(str(self.project.path))

    def set_thumbnail(self, pm: QPixmap) -> None:
        """后台解析完成后的回填（主线程调用）。"""
        if pm is None or pm.isNull() or self._thumb is None:
            return
        self._thumb.setPixmap(_cover(pm, self._thumb.width(), self._thumb.height()))

    # ── 卡片底自绘 ────────────────────────────────────────────────────
    # theme 里 QFrame#card 是空规则（注释说明卡片背景改由各卡片自己 paintEvent 画），
    # 但 ProjectCard 之前没实现 paintEvent → 卡片完全没有背景/边框/圆角，
    # 视觉上就是一堆内容直接浮在页面底色上（用户反馈"布局都没有"）。
    # 这里按 AddonCard 的方案补齐：圆角填充 + 1px 描边 + hover 变色。
    RADIUS = 12
    _hover = False

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        bg = theme.Tokens.BG_CARD_HOVER if self._hover else theme.Tokens.BG_CARD
        p.fillPath(path, QColor(bg))
        border = theme.Tokens.BORDER_STRONG if self._hover else theme.Tokens.BORDER
        p.setPen(QPen(QColor(border), 1.0))
        p.drawPath(path)

    def _show_menu(self):
        menu = QMenu(self)
        bind_action = menu.addAction("🔗 绑定版本")
        browse_action = menu.addAction("📂 在文件管理器中打开")
        action = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        if action == bind_action:
            self.bind_clicked.emit(str(self.project.path))
        elif action == browse_action:
            os.startfile(str(self.project.path.parent))


class ProjectScanWorker(QThread):
    """后台扫描 .blend 文件。

    旧实现直接在 GUI 线程对 Desktop/Downloads/D:/Projects 做无深度上限的
    rglob("*.blend")，大目录下界面冻结数分钟且无法取消。
    这里改为后台线程 + 深度上限 + 文件数上限 + 可取消 + 进度信号。
    """

    progress = pyqtSignal(int, str)      # (已扫描数, 当前目录)
    finished_with = pyqtSignal(list)     # List[tuple[str, int, float]] -> (path, size, mtime)
    failed = pyqtSignal(str)

    MAX_FILES = 300
    MAX_DEPTH = 4
    SKIP_DIRS = {
        "$RECYCLE.BIN", "System Volume Information", "node_modules",
        ".git", ".svn", "__pycache__", "AppData", "$RECYCLE.BIN",
        "SystemVolumeInformation", ".cache", "venv", ".venv",
    }

    def __init__(self, dirs: list[str], bindings: dict):
        super().__init__()
        self._dirs = dirs
        self._bindings = bindings
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def _is_skipped(self, name: str) -> bool:
        return name in self.SKIP_DIRS or name.startswith(".")

    def run(self):
        results: list[tuple[str, int, float]] = []
        seen: set[str] = set()
        try:
            for dir_pattern in self._dirs:
                if self._cancel.is_set():
                    break
                root = Path(dir_pattern).expanduser()
                if not root.exists():
                    continue

                # 用 os.walk + 深度剪枝，比 rglob 快且可控
                root_depth = len(root.parts)
                for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                    if self._cancel.is_set():
                        break
                    current = Path(dirpath)
                    if len(current.parts) - root_depth >= self.MAX_DEPTH:
                        dirnames[:] = []  # 超深度，不再深入
                        continue

                    # 就地剪枝：修改 dirnames 阻止继续下降
                    dirnames[:] = [
                        d for d in dirnames
                        if not self._is_skipped(d)
                    ]

                    for fn in filenames:
                        if not fn.lower().endswith(".blend"):
                            continue
                        fp = current / fn
                        key = str(fp)
                        if key in seen:
                            continue
                        seen.add(key)
                        try:
                            st = fp.stat()
                        except OSError:
                            continue
                        results.append((key, st.st_size, st.st_mtime))

                        if len(results) % 20 == 0 or len(results) == 1:
                            self.progress.emit(len(results), str(current))
                        if len(results) >= self.MAX_FILES:
                            break

                    if len(results) >= self.MAX_FILES:
                        break

                if len(results) >= self.MAX_FILES:
                    break

            self.finished_with.emit(results)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class _PageWidget(QWidget):
    """页面容器：窗口尺寸变化时通知外面重排卡片网格。"""

    def __init__(self, on_resize=None, parent=None):
        super().__init__(parent)
        self._on_resize = on_resize

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._on_resize is not None:
            self._on_resize()


class ProjectCenterModule(BaseModule):
    """项目中心模块"""

    def __init__(self):
        super().__init__()
        self.id = "project_center"
        self.name = "项目"
        self.icon = "📁"
        self._widget: Optional[QWidget] = None
        self._grid_layout: Optional[QGridLayout] = None
        self._scroll: Optional[QScrollArea] = None
        self._cards_container: Optional[QWidget] = None
        self._cards: List[ProjectCard] = []
        self._empty_widget: Optional[QWidget] = None
        self._cols: int = 0
        self._reflow_timer: Optional[QTimer] = None
        self._projects: List[ProjectInfo] = []
        self._project_bindings: dict = {}
        self._scan_worker: Optional[ProjectScanWorker] = None
        self._status_label: Optional[QLabel] = None
        self._refresh_btn: Optional[QPushButton] = None
        self._cancel_btn: Optional[QPushButton] = None
        # 后台缩略图任务（保持引用，避免 QRunnable 被 Python GC 提前回收）
        self._thumb_tasks: list[_ThumbTask] = []

    def get_widget(self) -> QWidget:
        if self._widget:
            return self._widget

        self._widget = _PageWidget(self._schedule_reflow)
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # 标题
        header = QHBoxLayout()
        title = QLabel("📁 项目")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: #e8e8e8;")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(self._scan_projects)
        header.addWidget(refresh_btn)
        self._refresh_btn = refresh_btn

        cancel_btn = QPushButton("⏹ 取消")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self._cancel_scan)
        cancel_btn.setVisible(False)
        header.addWidget(cancel_btn)
        self._cancel_btn = cancel_btn

        open_file_btn = QPushButton("📂 打开文件...")
        open_file_btn.setObjectName("primaryButton")
        open_file_btn.clicked.connect(self._open_file_dialog)
        header.addWidget(open_file_btn)

        layout.addLayout(header)

        # 扫描进度状态（扫描时可见）
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: #707070; font-size: 12px;")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # 滚动区域 + 卡片网格
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        cards_container = QWidget()
        self._grid_layout = QGridLayout(cards_container)
        self._grid_layout.setSpacing(GRID_GAP)
        self._grid_layout.setContentsMargins(0, 6, 0, 6)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(cards_container)
        layout.addWidget(scroll, 1)

        self._scroll = scroll
        self._cards_container = cards_container
        return self._widget

    # ── 网格重排：按可用宽度决定列数，并把卡片整体顶到上方 ──────────────
    def _schedule_reflow(self) -> None:
        if self._reflow_timer is None:
            timer = QTimer(self._widget)
            timer.setSingleShot(True)
            timer.setInterval(80)
            timer.timeout.connect(self._relayout_cards)
            self._reflow_timer = timer
        self._reflow_timer.start()

    def _relayout_cards(self) -> None:
        grid = self._grid_layout
        if grid is None:
            return

        # 先把已有部件摘下来（不销毁），再按新列数摆放
        while grid.count():
            grid.takeAt(0)
        for r in range(0, grid.rowCount() + 2):
            grid.setRowStretch(r, 0)

        avail = 0
        if self._scroll is not None:
            avail = self._scroll.viewport().width()
        if avail <= 0 and self._cards_container is not None:
            avail = self._cards_container.width()

        cols = max(1, (max(avail, CARD_W) + GRID_GAP) // (CARD_W + GRID_GAP))
        self._cols = cols

        for i, card in enumerate(self._cards):
            row, col = divmod(i, cols)
            grid.addWidget(card, row, col)

        if self._cards:
            # 末尾补一个弹性行，避免卡片被垂直居中（上方一大片空白）
            rows = (len(self._cards) + cols - 1) // cols
            grid.setRowStretch(rows, 1)
        elif self._empty_widget is not None:
            grid.addWidget(self._empty_widget, 0, 0, 1, max(cols, 1))
            grid.setRowStretch(1, 1)

        self._schedule_thumbnails()

    def _schedule_thumbnails(self) -> None:
        """把未命中缓存的缩略图交给线程池解析，完成后回填卡片。"""
        pool = QThreadPool.globalInstance()
        for card in self._cards:
            path = card.project.path
            if peek_thumbnail(path) is not None:
                continue
            task = _ThumbTask(path)
            self._thumb_tasks.append(task)
            task.signals.ready.connect(  # pyright: ignore[reportUnusedCallResult]
                lambda p, pm, t=task: self._on_thumb_ready(p, pm, t)
            )
            pool.start(task)

    def _on_thumb_ready(self, path_str: str, img: QImage, task: _ThumbTask) -> None:
        if task in self._thumb_tasks:
            self._thumb_tasks.remove(task)
        # 回主线程后再转 QPixmap（QPixmap 只能在 GUI 线程使用）
        pm = load_thumbnail(Path(path_str))
        if pm is None:
            return
        for card in self._cards:
            if str(card.project.path) == path_str:
                card.set_thumbnail(pm)
                break

    def on_navigate_to(self):
        self._load_bindings()
        self._scan_projects()

    def _load_bindings(self):
        """从配置文件加载项目-版本绑定"""
        if self.ctx is None:
            return

        bindings_json = self.ctx.config_service.get("project_center", "bindings", "{}")
        try:
            self._project_bindings = json.loads(bindings_json) if isinstance(bindings_json, str) else bindings_json
        except (json.JSONDecodeError, TypeError):
            self._project_bindings = {}

    def _save_bindings(self):
        if self.ctx:
            self.ctx.config_service.set("project_center", "bindings", json.dumps(self._project_bindings))

    def _scan_projects(self):
        """启动后台扫描 .blend 文件（不在 GUI 线程做磁盘遍历）"""
        if self._grid_layout is None:
            return

        # 已有扫描在跑：先取消旧的，避免并发重复扫描
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            self._scan_worker.wait(2000)

        self._clear_cards()
        self._projects.clear()

        if self._status_label is not None:
            self._status_label.setText("⏳ 正在扫描项目文件…")
            self._status_label.setVisible(True)
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(False)
        if self._cancel_btn is not None:
            self._cancel_btn.setVisible(True)

        import os as _os
        if _os.environ.get("BH_SYNC_SCAN") == "1":
            #诊断模式：同步扫描（不走 QThread）
            _w = ProjectScanWorker(list(COMMON_PROJECT_DIRS), dict(self._project_bindings))
            _w.run()   # 同步执行 run()，信号直连
            return
        worker = ProjectScanWorker(list(COMMON_PROJECT_DIRS), dict(self._project_bindings))
        self._scan_worker = worker
        worker.progress.connect(self._on_scan_progress)
        worker.finished_with.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.start()

    def _cancel_scan(self):
        """取消正在进行的扫描"""
        worker = self._scan_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            if self._status_label is not None:
                self._status_label.setText("已取消扫描")
        self._set_scan_controls(False)

    def _set_scan_controls(self, scanning: bool) -> None:
        if self._status_label is not None and not scanning:
            self._status_label.setVisible(False)
        if self._refresh_btn is not None:
            self._refresh_btn.setEnabled(not scanning)
        if self._cancel_btn is not None:
            self._cancel_btn.setVisible(scanning)

    def _on_scan_progress(self, count: int, current_dir: str) -> None:
        if self._status_label is not None:
            short = current_dir[-50:] if len(current_dir) > 50 else current_dir
            self._status_label.setText(f"⏳ 已找到 {count} 个项目…  ({short})")

    def _on_scan_failed(self, error: str) -> None:
        self._set_scan_controls(False)
        if self.ctx:
            self.ctx.log_service.error(f"Project scan failed: {error}", module="project_center")

    def _on_scan_finished(self, results: list) -> None:
        """扫描完成（主线程）：构建 ProjectInfo 并渲染卡片"""
        self._set_scan_controls(False)

        truncated = len(results) >= ProjectScanWorker.MAX_FILES
        for key, size, mtime in results:
            self._projects.append(ProjectInfo(
                path=Path(key),
                size=size,
                modified=datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                bound_version=self._project_bindings.get(key, ""),
            ))

        # 按修改时间排序
        self._projects.sort(key=lambda p: p.modified, reverse=True)
        self._projects = self._projects[:50]

        # 渲染卡片（列数由可用宽度决定，交给 _relayout_cards）
        if self._grid_layout is not None and self._projects:
            self._cards = []
            for proj in self._projects:
                card = ProjectCard(proj)
                card.open_clicked.connect(self._open_project)
                card.bind_clicked.connect(self._bind_version)
                self._cards.append(card)
            self._cols = 0
            self._relayout_cards()
        elif self._grid_layout is not None:
            empty = QLabel(
                "📂 未找到 .blend 项目\n\n"
                "点击「📂 打开文件...」手动选择项目\n"
                "或在常用目录中放置 .blend 文件后点击刷新"
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("hint")
            empty.setStyleSheet("padding: 40px;")
            self._empty_widget = empty
            self._relayout_cards()

        if self.ctx:
            self.ctx.log_service.info(
                f"Scanned {len(self._projects)} .blend projects"
                + (" (truncated at limit)" if truncated else ""),
                module="project_center",
            )

    def on_shutdown(self):
        """退出时停止扫描线程"""
        worker = self._scan_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.requestInterruption()
            worker.wait(2000)
        self._scan_worker = None
        super().on_shutdown()

    def _clear_cards(self):
        if self._grid_layout is None:
            return
        # 先断引用再删：否则 _relayout_cards 可能操作已 deleteLater 的卡片
        self._cards = []
        self._empty_widget = None
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _open_project(self, path: str):
        """打开项目（用绑定版本或选择版本）"""
        if self.ctx is None:
            return

        bound_version = self._project_bindings.get(path, "")
        installations = self.ctx.blender_service.detect_installed()

        if not installations:
            QMessageBox.warning(self._widget, "提示", "请先在「更新」中安装 Blender")
            return

        target = None

        # 如果有绑定版本，优先使用
        if bound_version:
            target = next((inst for inst in installations if inst.version == bound_version), None)
            if target is None:
                # 绑定版本不可用
                reply = QMessageBox.question(
                    self._widget,
                    "版本不匹配",
                    f"该项目绑定的 Blender {bound_version} 未安装。\n是否使用其他版本打开？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        # 选择版本
        if target is None:
            from PyQt6.QtWidgets import QInputDialog
            version_names = [inst.label for inst in installations]
            item, ok = QInputDialog.getItem(
                self._widget, "选择 Blender 版本",
                "请选择用于打开此项目的 Blender 版本:",
                version_names, 0, False,
            )
            if not ok:
                return
            target = installations[version_names.index(item)]

        # 启动
        self.ctx.blender_service.launch(target, path)

        if self.ctx:
            self.ctx.log_service.info(f"Opening project: {Path(path).name} with Blender {target.version}", module="project_center")

    def _bind_version(self, path: str):
        """为项目绑定 Blender 版本"""
        if self.ctx is None:
            return

        installations = self.ctx.blender_service.detect_installed()
        if not installations:
            QMessageBox.warning(self._widget, "提示", "请先在「更新」中安装 Blender")
            return

        from PyQt6.QtWidgets import QInputDialog
        version_names = ["(不绑定)"] + [inst.label for inst in installations]
        item, ok = QInputDialog.getItem(
            self._widget, "绑定版本",
            f"为「{Path(path).name}」绑定 Blender 版本:",
            version_names, 0, False,
        )
        if not ok:
            return

        if item == "(不绑定)":
            self._project_bindings.pop(path, None)
        else:
            idx = version_names.index(item) - 1
            self._project_bindings[path] = installations[idx].version

        self._save_bindings()
        self._scan_projects()

        if self.ctx:
            self.ctx.log_service.info(f"Bound {Path(path).name} to {self._project_bindings.get(path, 'none')}", module="project_center")

    def _open_file_dialog(self):
        """手动选择 .blend 文件打开"""
        file_path, _ = QFileDialog.getOpenFileName(
            self._widget,
            "选择 Blender 项目",
            str(Path.home()),
            "Blender 文件 (*.blend);;所有文件 (*.*)",
        )
        if file_path:
            self._open_project(file_path)

    def get_state_snapshot(self) -> dict:
        return {}

    def restore_state(self, state: dict):
        pass
