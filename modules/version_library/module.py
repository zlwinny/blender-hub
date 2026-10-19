"""
版本库模块 — Blender 版本下载/安装/删除/启动

UI: 网格卡片展示已安装版本 + "添加版本"按钮 + 本地扫描
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication,
    QScrollArea, QFrame, QGridLayout, QMessageBox, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem, QTabWidget,
    QCheckBox, QProgressBar, QSizePolicy, QMenu, QFileDialog,
    QProgressDialog, QLineEdit,
)
from PyQt6.QtCore import Qt, QThread, QMetaObject, pyqtSignal, QObject, QTimer, QSize
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont, QIcon, QPixmap, QImage
import zipfile
import shutil
import tempfile
import time
import json
from pathlib import Path
from typing import Optional
import uuid

from core import i18n, theme  # pyright: ignore[reportImplicitRelativeImport]
from core.api.module_api import BaseModule, ModuleContext
from core.services.blender_service import BlenderInstallation, BlenderService


# 小于这个体积的"已下载包"不可信（可能是半截文件），宁可重下也不误装
_REUSE_MIN_BYTES = 10 * 1024 * 1024

# ── 安装位置持久化（模块级：对话框 / 版本库 / 设置页三处共用同一份设置） ──
_KEY_INSTALL_DIR = "blender/install_dir"
_KEY_MANAGED_ROOTS = "blender/managed_roots"


def _settings() -> QSettings:
    return QSettings("BlenderHub Team", "BlenderHub")


def get_install_root(fallback: str = "") -> str:
    """上次选择的 Blender 安装根目录（没有/已失效则回退到默认目录）。

    "已失效"指保存的目录已经不存在（临时目录被清、盘符拔出、用户手工删了）。
    这种情况下继续显示旧路径只会让人困惑——回退默认，用户还能重新选。
    """
    saved = _settings().value(_KEY_INSTALL_DIR, "", type=str)
    if not saved:
        return fallback
    try:
        if not Path(str(saved)).is_dir():
            return fallback
    except OSError:
        return fallback
    return str(saved)


def get_managed_roots(fallback: str = "") -> list:
    """本应用管理的所有安装根目录：默认目录 + 用户自选过的目录。

    不存在的目录会被跳过（扫描时无意义，还会让删除保护把垃圾路径当合法根）。
    """
    roots: list = []
    if fallback:
        roots.append(fallback)
    extra = _settings().value(_KEY_MANAGED_ROOTS, [])
    if isinstance(extra, str):
        extra = [extra]
    for r in extra or []:
        r = str(r)
        if not r or r in roots:
            continue
        try:
            if not Path(r).is_dir():
                continue
        except OSError:
            continue
        roots.append(r)
    return roots


def save_install_root(root: str, fallback: str = "") -> None:
    """保存安装根目录（立即落盘，不等到点"下载"才写）。

    用户要求"设置过就要 save"：在对话框里选了目录、或在设置页点了保存，
    都应当立刻写进 QSettings，下次打开直接带出。
    """
    if not root:
        return
    try:
        # 保存即创建：这样"保存过的路径一定存在"，读取时的存在性校验才不会
        # 把用户刚填的目录当成失效而回退
        Path(root).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    s = _settings()
    cur = s.value(_KEY_MANAGED_ROOTS, [])
    if isinstance(cur, str):
        cur = [cur]
    cur = [str(x) for x in (cur or [])]
    key = str(Path(root))
    if key not in cur:
        cur.append(key)
        s.setValue(_KEY_MANAGED_ROOTS, cur)
    s.setValue(_KEY_INSTALL_DIR, key)
    s.sync()


class _DownloadBridge(QObject):
    """下载线程 → 主线程 的信号桥。

    为什么必须有它：DownloadService 用 ``threading.Thread``（**没有 Qt 事件循环**）
    跑下载。从这种线程里更新 GUI，两条常见路都走不通——
      • ``QMetaObject.invokeMethod(obj, lambda, ...)``：PyQt6 已移除对 Python
        callable 的支持，直接 TypeError；
      • ``QTimer.singleShot(0, fn)``：无 receiver 重载（PyQt6 未暴露），定时器
        归属调用者线程，而该线程没有事件循环 → 回调**永不触发**。
    pyqtSignal 的发射是线程安全的，Qt 会自动把槽函数派发到 receiver 所属线程的
    事件循环，这是 PyQt6 下跨线程更新 UI 唯一可靠的方式。
    """

    # 结构化进度：百分比 / 已下载MB / 总MB / 速度MB·s⁻¹
    # （旧实现传"拼好的文本"，模块侧无法据此画真正的进度条，用户三次反馈
    #   "看不到进度条"——UI 需要的是数值，不是字符串）
    progress = pyqtSignal(int, float, float, float)
    done = pyqtSignal()             # 下载结束（隐藏进度条）
    install = pyqtSignal(object, str, str)  # (task, version, install_root) 下载完成，开始解压
    error = pyqtSignal(str)         # 下载失败信息


class DownloadWorker(QThread):
    """后台下载 Blender 安装包（流式 + 进度 + 可取消）。"""

    progress = pyqtSignal(int, int, int)   # 已下载字节, 总字节(0=未知), 百分比
    finished = pyqtSignal(bool, str)       # success, 文件路径或错误信息

    def __init__(self, url: str, dest_path: str):
        super().__init__()
        self.url = url
        self.dest_path = dest_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self):
        import requests

        dest = Path(self.dest_path)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with requests.get(self.url, stream=True, timeout=(10, 60)) as resp:
                if resp.status_code != 200:
                    self.finished.emit(False, f"下载失败：HTTP {resp.status_code}\n{self.url}")
                    return
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if self._cancelled:
                            self.finished.emit(False, "下载已取消")
                            return
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        pct = int(done * 100 / total) if total else 0
                        self.progress.emit(done, total, pct)
            if dest.stat().st_size <= 0:
                dest.unlink(missing_ok=True)
                self.finished.emit(False, "下载到的文件为空")
                return
            self.finished.emit(True, str(dest))
        except Exception as e:  # noqa: BLE001
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            self.finished.emit(False, f"下载失败：{e}")


# 解压安全上限：防止 zip 炸弹 / 异常包
_MAX_ZIP_ENTRIES = 200_000
_MAX_ZIP_BYTES = 8 * 1024 * 1024 * 1024   # 8 GB


class InstallWorker(QThread):
    """后台解压安装线程"""
    progress = pyqtSignal(int)     # 0-100
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, zip_path: str, install_dir: str):
        super().__init__()
        self.zip_path = zip_path
        self.install_dir = install_dir

    def run(self):
        try:
            install_dir = Path(self.install_dir)
            install_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(self.zip_path, "r") as zf:
                infos = zf.infolist()
                if len(infos) > _MAX_ZIP_ENTRIES:
                    raise ValueError(
                        f"压缩包条目数 {len(infos)} 超过上限 {_MAX_ZIP_ENTRIES}"
                    )
                # 进度按「已解压字节 / 总字节」算。旧实现按文件个数算，
                # 而 Blender 包里大量小文件集中在前段 → 进度条几秒冲到 90%
                # 再长时间不动，看起来像卡死。
                total_bytes = sum(max(0, i.file_size) for i in infos)
                if total_bytes > _MAX_ZIP_BYTES:
                    raise ValueError(
                        f"解压后总大小 {total_bytes} 字节超过上限 {_MAX_ZIP_BYTES} 字节"
                    )
                total_bytes = total_bytes or 1
                done_bytes = 0
                last_pct = -1
                for info in infos:
                    # 协作式中断：on_shutdown 会 requestInterruption，
                    # 350MB 解压期间必须可中止，否则退出要等解压完
                    if self.isInterruptionRequested():
                        self.finished.emit(False, "安装已取消")
                        return
                    # 路径穿越/绝对路径防御：zipfile.extract 会剥离，显式校验更稳
                    member = Path(info.filename)
                    if member.is_absolute() or ".." in member.parts:
                        raise ValueError(f"压缩包内存在非法路径: {info.filename}")
                    zf.extract(info, install_dir)
                    done_bytes += max(0, info.file_size)
                    pct = min(100, int(done_bytes / total_bytes * 100))
                    if pct != last_pct:
                        last_pct = pct
                        self.progress.emit(pct)

            self.finished.emit(True, f"安装完成: {install_dir}")
        except Exception as e:
            self.finished.emit(False, f"安装失败: {str(e)}")


class VersionRow(QFrame):
    """已安装版本的行组件（列表布局，一行一个版本）"""
    launch_clicked = pyqtSignal(str)   # version
    delete_clicked = pyqtSignal(str)   # version
    details_clicked = pyqtSignal(str)  # version
    update_clicked = pyqtSignal(str)   # version

    def __init__(self, installation: BlenderInstallation, is_latest: bool = False,
                 latest_version: str = "", parent=None):
        super().__init__(parent)
        self.installation = installation
        self._is_latest = is_latest
        self._latest_version = latest_version
        self.setObjectName("versionRow")
        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame {{ background-color: {theme.Tokens.ROW_BG}; border: 1px solid {theme.Tokens.ROW_BORDER}; "
            "border-radius: 10px; margin: 2px 0; }"
            "QFrame:hover { background-color: {theme.Tokens.ROW_BG_HOVER}; border-color: {theme.Tokens.HIGHLIGHT_BLUE}; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        # 左侧：图标 + 版本信息
        icon_label = QLabel()
        icon_result = self._find_icon(installation)
        if icon_result:
            if isinstance(icon_result, QPixmap):
                # 从 exe 提取的 QPixmap，直接缩放
                icon_label.setPixmap(icon_result.scaled(36, 36,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                # .ico 文件路径
                icon = QIcon(icon_result)
                icon_label.setPixmap(icon.pixmap(36, 36))
        else:
            icon_label.setText("🟠")
            icon_label.setStyleSheet("font-size: 28px; border: none; background: transparent;")
        icon_label.setFixedWidth(40)
        layout.addWidget(icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        # 版本号 + LTS 标签 + 最新标识
        ver_row = QHBoxLayout()
        ver_row.setSpacing(8)
        version_label = QLabel(f"Blender {installation.version}")
        version_font = QFont()
        version_font.setPointSize(13)
        version_font.setBold(True)
        version_label.setFont(version_font)
        version_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD}; border: none; background: transparent;")
        ver_row.addWidget(version_label)

        # 渠道徽章：LTS / 稳定版 / 测试版 三类要能一眼分开
        # （旧实现只有 LTS 有标记，稳定版和测试版长得一模一样）
        tag_text, tag_bg, tag_fg = self._channel_tag(installation)
        tag_label = QLabel(tag_text)
        tag_label.setStyleSheet(
            f"background-color: {tag_bg}; color: {tag_fg}; padding: 2px 8px; "
            "border-radius: 4px; font-size: 10px; font-weight: bold;"
        )
        tag_label.setFixedWidth(54)
        tag_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag_label.setToolTip(self._channel_tooltip(installation))
        ver_row.addWidget(tag_label)

        if is_latest:
            latest_tag = QLabel("最新")
            # 蓝色已让给"稳定版"徽章，这里改中性灰，避免两个蓝块分不清
            latest_tag.setStyleSheet(
                f"background-color: {theme.Tokens.ROW_ALT3}; color: {theme.Tokens.GREY_2}; padding: 2px 8px; "
                "border-radius: 4px; font-size: 10px; font-weight: bold;"
            )
            ver_row.addWidget(latest_tag)
        elif latest_version:
            update_hint = QLabel(f"可更新至 {latest_version}")
            update_hint.setStyleSheet(
                f"color: {theme.Tokens.ACCENT_ALT}; font-size: 11px; border: none; background: transparent;"
            )
            ver_row.addWidget(update_hint)

        ver_row.addStretch()
        info_layout.addLayout(ver_row)

        # 路径
        path_label = QLabel(str(installation.path))
        path_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_DISABLED}; font-size: 11px; border: none; background: transparent;")
        info_layout.addWidget(path_label)

        layout.addLayout(info_layout, 1)

        # 右侧：操作按钮
        if not is_latest and latest_version:
            update_btn = QPushButton(f"⬆ 更新到 {latest_version}")
            update_btn.setObjectName("primaryButton")
            update_btn.setFixedHeight(32)
            update_btn.setStyleSheet(
                f"QPushButton {{ background-color: {theme.Tokens.SUCCESS_BORDER}; color: {theme.Tokens.SUCCESS_GLOW}; border: none; "
                "border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: bold; }"
                "QPushButton:hover { background-color: {theme.Tokens.SUCCESS_BG_ALT}; }"
            )
            update_btn.clicked.connect(lambda: self.update_clicked.emit(installation.version))
            layout.addWidget(update_btn)

        launch_btn = QPushButton("🚀 启动")
        launch_btn.setObjectName("primaryButton")
        launch_btn.setFixedHeight(32)
        launch_btn.clicked.connect(lambda: self.launch_clicked.emit(installation.version))
        layout.addWidget(launch_btn)

        more_btn = QPushButton("⋯")
        more_btn.setFixedSize(32, 32)
        more_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; border: 1px solid {theme.Tokens.ROW_ALT3}; "
            "border-radius: 4px; color: {theme.Tokens.GREY_2}; }"
            f"QPushButton:hover {{ background-color: {theme.Tokens.ROW_ALT}; }}"
        )
        more_btn.clicked.connect(self._show_menu)
        layout.addWidget(more_btn)

    @staticmethod
    def _channel_tag(installation: BlenderInstallation) -> tuple:
        """渠道徽章 (文字, 底色, 字色)：LTS=绿 / 稳定版=蓝灰 / 测试版=橙。"""
        v = (installation.version or "").lower()
        if "alpha" in v or "beta" in v or "-rc" in v:
            return ("🧪 测试", theme.Tokens.ROW_ACCENT, theme.Tokens.ACCENT)
        if installation.is_lts:
            return ("🏷 LTS", theme.Tokens.SUCCESS_BG, theme.Tokens.SUCCESS_BRIGHT)
        return ("✅ 稳定", theme.Tokens.PANEL_BLUE, theme.Tokens.INFO_ALT)

    @staticmethod
    def _channel_tooltip(installation: BlenderInstallation) -> str:
        if (installation.version or "").lower().find("beta") >= 0 or "alpha" in (installation.version or "").lower():
            return "测试版：含最新改动，可能有未修复问题"
        if installation.is_lts:
            return "LTS：长期支持版，官方维护 2 年以上，适合生产环境"
        return "稳定版：常规发布版，功能较新但支持周期短于 LTS"

    @staticmethod
    def _find_icon(installation: BlenderInstallation):
        """查找 Blender 图标：优先 blender.ico，否则从 blender.exe 提取内嵌图标"""
        import ctypes
        from ctypes import wintypes

        # 先找 .ico
        for p in [installation.path / "blender.ico",
                   installation.executable.parent / "blender.ico"]:
            if p.is_file():
                return str(p)

        # 从 blender.exe 提取（用 Windows Shell API + QImage.fromHICON）
        exe = installation.executable
        if exe.is_file():
            shell32 = ctypes.windll.shell32
            user32 = ctypes.windll.user32
            large_icons = (ctypes.c_void_p * 1)()
            count = shell32.ExtractIconExW(str(exe), 0, large_icons, None, 1)
            if count > 0 and large_icons[0]:
                hicon = large_icons[0]
                image = QImage.fromHICON(hicon)
                user32.DestroyIcon(hicon)
                if not image.isNull():
                    return QPixmap.fromImage(image)
        return None

    def _show_menu(self):
        menu = QMenu(self)
        delete_action = menu.addAction("🗑 删除")
        details_action = menu.addAction("📋 详情")
        action = menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
        if action == delete_action:
            self.delete_clicked.emit(self.installation.version)
        elif action == details_action:
            self.details_clicked.emit(self.installation.version)


class ScanWorker(QThread):
    """后台扫描本地 Blender 安装的线程 — 实时发送每个找到的安装"""
    found = pyqtSignal(object)       # BlenderInstallation
    finished = pyqtSignal(int)       # total found count

    def __init__(self, blender_service, deep_scan: bool = True, extra_dirs=None):
        super().__init__()
        self.blender_service = blender_service
        self.deep_scan = deep_scan
        self.extra_dirs = extra_dirs or []

    def run(self):
        # 先做快速扫描（固定路径），每找到一个就发射
        results_fast = self.blender_service.detect_installed(
            deep_scan=False,
            extra_dirs=self.extra_dirs,
        )
        for inst in results_fast:
            if self.isInterruptionRequested():
                return
            self.found.emit(inst)

        # 再做深度扫描
        if self.deep_scan and not self.isInterruptionRequested():
            results_deep = self.blender_service.detect_installed(deep_scan=True)
            # 只发送快速扫描没找到的新版本
            fast_versions = {inst.version for inst in results_fast}
            for inst in results_deep:
                if self.isInterruptionRequested():
                    return
                if inst.version not in fast_versions:
                    self.found.emit(inst)
            total = len(set(fast_versions) | {i.version for i in results_deep})
        else:
            total = len(results_fast)

        self.finished.emit(total)


class VersionSelectorDialog(QDialog):
    """选择要安装的 Blender 版本对话框 — 含在线下载 + 本地扫描"""

    def __init__(self, parent=None, blender_service=None, default_install_dir: str = "",
                 installed_versions=None):
        super().__init__(parent)
        self.blender_service = blender_service
        self.install_dir = default_install_dir  # 在线下载的安装根目录
        # 本地已安装的版本号 → 在线列表里不再重复列出（避免"装一个已有的"）
        self.installed_versions = set(installed_versions or ())
        self._hidden_versions: set = set()  # 用集合去重：同一版本会同时出现在稳定版/LTS 两个列表
        self.setWindowTitle("添加 Blender 版本")
        self.setMinimumSize(650, 520)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        self.selected_version: Optional[str] = None
        self._version_list: dict = {}
        self._scanned_installations: list = []
        self._local_list: Optional[QListWidget] = None
        self._local_status_label: Optional[QLabel] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 标签页：在线下载 / 本地扫描
        self.tabs = QTabWidget()

        # ── 在线下载标签页 ──
        online_widget = QWidget()
        online_layout = QVBoxLayout(online_widget)
        online_layout.setSpacing(8)

        online_tabs = QTabWidget()
        available = BlenderService.get_available_versions()
        for key, label in [("stable", "稳定版"), ("lts", "LTS"), ("beta", "测试版")]:
            tab = self._build_version_tab(key, available)
            online_tabs.addTab(tab, label)
        online_layout.addWidget(online_tabs)

        # 镜像源选择
        mirror_layout = QHBoxLayout()
        mirror_layout.addWidget(QLabel("下载源:"))
        from PyQt6.QtWidgets import QComboBox
        self.mirror_combo = QComboBox()
        self.mirror_combo.addItems(
            ["南京大学镜像（推荐）", "清华镜像 (TUNA)", "中科大镜像 (USTC)", "官方源（国内常被拦截）"]
        )
        self.mirror_combo.setCurrentIndex(0)
        mirror_layout.addWidget(self.mirror_combo)
        mirror_layout.addStretch()
        self.refresh_btn = QPushButton("🔄 刷新列表")
        self.refresh_btn.setObjectName("secondaryButton")
        self.refresh_btn.setToolTip(
            "重新联网获取可下载版本（默认 6 小时本地缓存，刚发布的新版本要刷新才看得到）"
        )
        self.refresh_btn.clicked.connect(self._refresh_version_lists)
        mirror_layout.addWidget(self.refresh_btn)
        online_layout.addLayout(mirror_layout)

        # ── 安装位置（用户要求可自选；旧实现硬编码 base_dir/blender_versions）──
        install_layout = QHBoxLayout()
        install_layout.addWidget(QLabel("安装到:"))
        self.install_dir_edit = QLineEdit(self.install_dir)
        self.install_dir_edit.setPlaceholderText("选择 Blender 版本的安装根目录")
        self.install_dir_edit.setToolTip(
            "新版本会安装到「该目录/<版本号>」，例如 D:/Blender/5.2.2"
        )
        install_layout.addWidget(self.install_dir_edit, 1)
        self.install_browse_btn = QPushButton("浏览...")
        self.install_browse_btn.setObjectName("secondaryButton")
        self.install_browse_btn.clicked.connect(self._browse_install_dir)
        install_layout.addWidget(self.install_browse_btn)
        online_layout.addLayout(install_layout)

        # 本地已装版本被折叠掉时，给一行说明，免得用户以为列表缺版本
        self._online_hint = QLabel("")
        self._online_hint.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px; padding: 2px 0;"
        )
        self._online_hint.setVisible(False)
        online_layout.addWidget(self._online_hint)
        if self._hidden_versions:
            self._online_hint.setText(
                f"已隐藏 {len(self._hidden_versions)} 个本地已安装的版本（更新列表中已存在）"
            )
            self._online_hint.setVisible(True)

        self.tabs.addTab(online_widget, "🌐 在线下载")

        # ── 本地扫描标签页 ──
        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.setSpacing(8)

        local_header = QHBoxLayout()
        local_title = QLabel("🔍 扫描本地已安装的 Blender")
        local_title.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.Tokens.GREY_1};")
        local_header.addWidget(local_title)
        local_header.addStretch()

        scan_btn = QPushButton("🔄 开始扫描")
        scan_btn.setObjectName("primaryButton")
        scan_btn.clicked.connect(self._start_local_scan)
        local_header.addWidget(scan_btn)

        browse_btn = QPushButton("📁 指定目录")
        browse_btn.setObjectName("secondaryButton")
        browse_btn.clicked.connect(self._browse_directory)
        local_header.addWidget(browse_btn)
        local_layout.addLayout(local_header)

        self._local_status_label = QLabel("点击「开始扫描」自动搜索系统中的 Blender 安装")
        self._local_status_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px; padding: 4px 0;")
        local_layout.addWidget(self._local_status_label)

        self._local_list = QListWidget()
        self._local_list.setStyleSheet("QListWidget::item { padding: 12px; }")
        self._local_list.itemDoubleClicked.connect(self._on_local_accept)
        self._local_list.itemSelectionChanged.connect(self._on_local_selection_changed)
        local_layout.addWidget(self._local_list, 1)

        self.tabs.addTab(local_widget, "💻 本地已安装")

        layout.addWidget(self.tabs)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.add_local_btn = QPushButton("⬇ 下载并安装")
        self.add_local_btn.setObjectName("primaryButton")
        self.add_local_btn.setEnabled(False)
        # 旧实现把按钮**固定**连到 _on_local_accept：在线下载标签页下点它毫无
        # 反应（读的是本地列表的选中项），用户只能靠双击列表项才能触发下载。
        # 改为按当前标签页分发。
        self.add_local_btn.clicked.connect(self._on_primary_clicked)
        btn_layout.addWidget(self.add_local_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

        # ⚠ 不能在这里再写 self._version_list = {}：_build_version_tab 早已把
        # 三个在线列表填进这个 dict，重新赋值会把它们清空，导致在线下载路径
        # 永远取不到列表（选中/双击/按钮全失效）。dict 的初始化在 __init__ 开头。
        self._extra_dirs = []
        self._selected_local_installation = None

        # 标签页切换 / 选中变化 → 主按钮文案与可用状态跟着变
        self.tabs.currentChanged.connect(self._update_primary_button)
        for list_widget in self._version_list.values():
            list_widget.itemSelectionChanged.connect(self._update_primary_button)
        self._local_list.itemSelectionChanged.connect(self._update_primary_button)
        self._update_primary_button()

    # ── 主按钮（随标签页分发） ────────────────────────────────────────────

    def _online_current_list(self) -> Optional[QListWidget]:
        """当前在线子标签（稳定版/LTS/测试版）对应的列表。"""
        online_tabs = self.tabs.widget(0).findChild(QTabWidget)
        idx = online_tabs.currentIndex() if online_tabs else 0
        cats = ["stable", "lts", "beta"]
        return self._version_list.get(cats[idx] if idx < len(cats) else "stable")

    def _update_primary_button(self) -> None:
        is_online = self.tabs.currentIndex() == 0
        if is_online:
            self.add_local_btn.setText("⬇ 下载并安装")
            lw = self._online_current_list()
            self.add_local_btn.setEnabled(bool(lw and lw.currentItem()))
        else:
            self.add_local_btn.setText("✓ 添加选中的本地版本")
            self.add_local_btn.setEnabled(self._local_list.currentItem() is not None)

    def _on_primary_clicked(self) -> None:
        if self.tabs.currentIndex() == 0:
            self._on_accept()        # 在线：设 selected_version → accept → 触发下载安装
        else:
            self._on_local_accept()  # 本地：加入已安装列表

    def reject(self):
        """对话框关闭时停止扫描"""
        if hasattr(self, '_scan_worker') and self._scan_worker is not None:
            self._scan_worker.requestInterruption()
            self._scan_worker.quit()
            self._scan_worker.wait(2000)
            self._scan_worker = None
        if self.blender_service:
            self.blender_service.cancel_scan()
        super().reject()

    def closeEvent(self, event):
        """窗口关闭时停止扫描"""
        self.reject()
        super().closeEvent(event)

    def _build_version_tab(self, category: str, available: dict) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        list_widget = QListWidget()
        list_widget.setStyleSheet("QListWidget::item { padding: 12px; }")

        for ver in available.get(category, []):
            # 本地已经装了这个版本 → 不再列出来（用户要求"本地检测到的版本
            # 在在线安装中不再显示"），避免装一个已有的重复版本
            if ver.get("version") in self.installed_versions:
                self._hidden_versions.add(ver["version"])
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, ver["version"])
            tag = "🏷 LTS  " if ver.get("is_lts") else "🔰 稳定  "
            item.setText(f"{tag}{ver['label']}  —  {ver['size']}  —  {ver['date']}")
            list_widget.addItem(item)

        list_widget.itemDoubleClicked.connect(self._on_accept)
        self._version_list[category] = list_widget
        layout.addWidget(list_widget)

        return widget

    def _refresh_version_lists(self):
        """强制联网刷新可下载版本列表（跳过 6 小时缓存）。"""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("🔄 获取中…")
        self._online_hint.setText("正在联网获取最新版本…")
        self._online_hint.setVisible(True)
        QApplication.processEvents()

        try:
            data = BlenderService.get_available_versions(force=True) or {}
        except Exception as e:  # noqa: BLE001
            data = {}
            self._online_hint.setText(f"刷新失败：{e}")
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("🔄 刷新列表")
            return

        # 重建三个子标签页的列表（保留已装版本过滤）
        available = data
        cats = [("stable", "稳定版"), ("lts", "LTS"), ("beta", "测试版")]
        tabs = self.tabs.widget(0).findChild(QTabWidget)
        if tabs is not None:
            for idx, (key, _label) in enumerate(cats):
                lw = self._version_list.get(key)
                if lw is None:
                    continue
                lw.clear()
                for ver in available.get(key, []):
                    if ver.get("version") in self.installed_versions:
                        continue
                    item = QListWidgetItem()
                    item.setData(Qt.ItemDataRole.UserRole, ver["version"])
                    tag = "🏷 LTS  " if ver.get("is_lts") else (
                        "🧪 测试  " if key == "beta" else "🔰 稳定  "
                    )
                    item.setText(f"{tag}{ver['label']}  —  {ver.get('size','')}  —  {ver.get('date','')}")
                    lw.addItem(item)
                w = tabs.widget(idx)
                if w is not None:
                    tabs.setTabText(idx, f"{_label} ({lw.count()})")

        newest = (available.get("stable") or [{}])[0].get("version", "?")
        beta_newest = (available.get("beta") or [{}])[0].get("version", "无")
        self._online_hint.setText(
            f"已更新：最新稳定版 {newest}　最新测试版 {beta_newest}"
        )
        self._online_hint.setVisible(True)
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("🔄 刷新列表")
        self._update_primary_button()

    def _browse_install_dir(self):
        """选择 Blender 安装根目录"""
        start = self.install_dir_edit.text().strip() or str(Path.home())
        picked = QFileDialog.getExistingDirectory(
            self, "选择 Blender 安装根目录", start,
        )
        if picked:
            self.install_dir_edit.setText(picked)
            self.install_dir = picked
            # 选完立刻落盘（用户要求"设置过就要 save"，不等到点下载）
            save_install_root(picked)

    def _on_accept(self):
        """在线下载 - 确定"""
        # 用户在输入框里可能直接手改路径，以输入框为准，并立即保存
        typed = self.install_dir_edit.text().strip()
        if typed:
            self.install_dir = typed
            save_install_root(typed)
        online_tabs = self.tabs.widget(0).findChild(QTabWidget)
        current_tab = online_tabs.currentIndex() if online_tabs else 0
        categories = ["stable", "lts", "beta"]
        category = categories[current_tab] if current_tab < len(categories) else "stable"
        list_widget = self._version_list.get(category)
        if list_widget and list_widget.currentItem():
            self.selected_version = list_widget.currentItem().data(Qt.ItemDataRole.UserRole)
        self.accept()

    # ─── 本地扫描 ───

    def _start_local_scan(self):
        """启动本地扫描"""
        self._local_list.clear()
        self._scanned_installations = []
        self._local_status_label.setText("⏳ 正在扫描系统中已安装的 Blender...")
        self._local_status_label.setStyleSheet(f"color: {theme.Tokens.ACCENT_ALT}; font-size: 11px; padding: 4px 0;")

        if self.blender_service is None:
            self._local_status_label.setText("⚠ 扫描服务不可用")
            self._local_status_label.setStyleSheet(f"color: {theme.Tokens.DANGER_SOFT}; font-size: 11px;")
            return

        # 先重置取消标志
        self.blender_service.reset_cancel()

        self._scan_worker = ScanWorker(
            self.blender_service, deep_scan=True, extra_dirs=self._extra_dirs,
        )
        self._scan_worker.found.connect(self._on_found_local)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_found_local(self, installation: BlenderInstallation):
        """发现一个本地安装"""
        self._scanned_installations.append(installation)

        source_icons = {
            "auto": "📁", "registry": "📋", "steam": "🎮",
            "custom": "📂", "portable": "💿",
        }
        icon = source_icons.get(installation.source, "📁")

        item = QListWidgetItem()
        lts_tag = " [LTS]" if installation.is_lts else ""
        item.setText(f"{icon}  {installation.label}{lts_tag}  —  {installation.path}")
        item.setData(Qt.ItemDataRole.UserRole, installation)
        item.setToolTip(f"版本: {installation.version}\n路径: {installation.path}\n来源: {installation.source}")
        self._local_list.addItem(item)

    def _on_scan_finished(self, count: int):
        """扫描完成"""
        if count == 0:
            self._local_status_label.setText(
                "❌ 未找到本地 Blender 安装。\n"
                "  提示：请确保 Blender 已安装，或点击「📁 指定目录」手动选择。"
            )
            self._local_status_label.setStyleSheet(f"color: {theme.Tokens.DANGER_SOFT}; font-size: 11px; padding: 4px 0;")
        else:
            self._local_status_label.setText(f"✅ 扫描完成，找到 {count} 个 Blender 安装")
            self._local_status_label.setStyleSheet(f"color: {theme.Tokens.SUCCESS_SOFT}; font-size: 11px; padding: 4px 0;")

    def _browse_directory(self):
        """手动浏览选择 Blender 目录或可执行文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Blender 可执行文件",
            "",
            "Blender (blender.exe);;所有文件 (*.*)",
        )
        if not path:
            return

        exe_path = Path(path)
        if not exe_path.exists():
            return

        # 读取版本（走 BlenderService：目录名 → PE 版本资源 → 隐藏窗口的
        # --version，不再直接 subprocess，避免弹黑窗）
        from core.services.blender_service import BlenderService
        svc = BlenderService()
        version = svc._read_version(exe_path)

        if version:
            is_lts = svc._is_lts(version)
            inst = BlenderInstallation(
                version=version, path=exe_path.parent, executable=exe_path,
                is_lts=is_lts, source="custom",
            )
            self._on_found_local(inst)
            self._local_status_label.setText(f"✅ 已添加: Blender {version} ({exe_path.parent})")
            self._local_status_label.setStyleSheet(f"color: {theme.Tokens.SUCCESS_SOFT}; font-size: 11px; padding: 4px 0;")
        else:
            QMessageBox.warning(self, "无法识别", f"无法读取 Blender 版本号:\n{path}")

    def _on_local_selection_changed(self):
        """本地列表选中状态变化（按钮状态由 _update_primary_button 统一管）"""
        self._update_primary_button()

    def _on_local_accept(self):
        """本地扫描 - 添加选中版本"""
        current = self._local_list.currentItem()
        if current:
            inst = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(inst, BlenderInstallation):
                self.selected_version = inst.version
                self._selected_local_installation = inst
                self.accept()


class VersionLibraryModule(BaseModule):
    """版本库模块"""

    def __init__(self):
        super().__init__()
        self.id = "version_library"
        self.name = "更新"
        self.icon = "📦"
        self._widget: Optional[QWidget] = None
        self._rows_layout: Optional[QVBoxLayout] = None
        self._installations: list = []
        self._quiet_scan: bool = False
        self._rendered_from_cache: bool = False
        self._scanning = False
        self._scan_completed = False  # 是否已完成过扫描（用于缓存）
        self._deep_scan_btn: Optional[QPushButton] = None
        self._status_label: Optional[QLabel] = None
        # 显式声明后台线程引用：未初始化时 _install_worker 会抛 AttributeError，
        # 且旧实现复用同一属性覆盖正在运行的 QThread，导致线程被 GC 崩溃
        self._scan_worker: Optional[QThread] = None
        self._install_worker: Optional[InstallWorker] = None
        # 本页面内的下载/安装进度面板（不依赖 main_window，一定有地方显示）
        self._dl_panel: Optional[QWidget] = None
        self._dl_title: Optional[QLabel] = None
        self._dl_detail: Optional[QLabel] = None
        self._dl_bar: Optional[QProgressBar] = None
        self._dl_hide_timer: Optional[QTimer] = None
        self._dl_version: str = ""
        self._dl_bridge: Optional["_DownloadBridge"] = None
        self._dl_logged_bucket: int = -1  # 进度日志按 10% 打点，便于真机复盘
        self._dl_is_lts: bool = False     # 本次安装的 LTS 标记（在线数据为准）
        # 用户自选的安装根目录（本次安装用），留空走默认目录
        self._install_root: str = ""
        self._status_timer: Optional[QTimer] = None
        self._bottom_timer: Optional[QTimer] = None
        self._auto_deep_scanned: bool = False  # 本次会话是否已自动后台深扫过

    def _build_dl_panel(self) -> QWidget:
        """页面内的下载/安装进度卡片（默认隐藏）。"""
        panel = QWidget()
        panel.setObjectName("dlPanel")
        panel.setStyleSheet(
            f"QWidget#dlPanel {{ background-color: {theme.Tokens.BG_PANEL};"
            f" border: 1px solid {theme.Tokens.BORDER}; border-radius: 8px; }}"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(12)
        self._dl_title = QLabel("")
        self._dl_title.setStyleSheet(
            f"color: {theme.Tokens.ACCENT}; font-size: 13px; font-weight: 600;"
            + " background: transparent; border: none;"
        )
        self._dl_detail = QLabel("")
        self._dl_detail.setStyleSheet(
            f"color: {theme.Tokens.TEXT_SUB}; font-size: 12px;"
            + " background: transparent; border: none;"
        )
        row.addWidget(self._dl_title)
        row.addStretch()
        row.addWidget(self._dl_detail)
        outer.addLayout(row)

        self._dl_bar = QProgressBar()
        self._dl_bar.setRange(0, 100)
        self._dl_bar.setValue(0)
        self._dl_bar.setTextVisible(False)
        self._dl_bar.setFixedHeight(8)
        self._dl_bar.setStyleSheet(
            "QProgressBar { background-color: #22262E; border: none; border-radius: 4px; }"
            f"QProgressBar::chunk {{ background-color: {theme.Tokens.ACCENT}; border-radius: 4px; }}"
        )
        outer.addWidget(self._dl_bar)

        self._dl_panel = panel
        panel.setVisible(False)
        return panel

    # ── 进度面板控制 ─────────────────────────────────────────────────────

    def _dl_show(self, title: str, detail: str = "", pct: int = 0) -> None:
        if self._dl_panel is None:
            return
        if self._dl_hide_timer is not None:
            self._dl_hide_timer.stop()
        if self._dl_title is not None:
            self._dl_title.setText(title)
        if self._dl_detail is not None:
            self._dl_detail.setText(detail)
        if self._dl_bar is not None:
            self._dl_bar.setValue(max(0, min(100, int(pct))))
        self._dl_panel.setVisible(True)

    def _dl_update(self, title: str = None, detail: str = None, pct: int = None) -> None:
        if self._dl_title is not None and title is not None:
            self._dl_title.setText(title)
        if self._dl_detail is not None and detail is not None:
            self._dl_detail.setText(detail)
        if self._dl_bar is not None and pct is not None:
            self._dl_bar.setValue(max(0, min(100, int(pct))))

    def _dl_hide_later(self, delay_ms: int = 0) -> None:
        if self._dl_panel is None:
            return
        if self._dl_hide_timer is None:
            # QTimer 必须有 Qt 父对象：本模块不是 QObject（QTimer(self) 会 TypeError）
            self._dl_hide_timer = QTimer(self._widget)
            self._dl_hide_timer.setSingleShot(True)
            self._dl_hide_timer.timeout.connect(self._dl_panel_hide)
        self._dl_hide_timer.start(max(0, int(delay_ms)))

    def _dl_panel_hide(self) -> None:
        if self._dl_panel is not None:
            self._dl_panel.setVisible(False)
        if self._dl_bar is not None:
            self._dl_bar.setValue(0)

    def get_widget(self) -> QWidget:
        if self._widget:
            return self._widget

        self._widget = QWidget()

        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        # 标题行
        header = QHBoxLayout()
        self._title_label = QLabel(i18n.tr("module.version.title"))
        title = self._title_label
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #ffffff;")
        header.addWidget(title)
        header.addStretch()

        self._deep_scan_btn = QPushButton("🔄 深度扫描")
        self._deep_scan_btn.setObjectName("secondaryButton")
        self._deep_scan_btn.setToolTip("扫描系统中所有已安装的 Blender 版本（含注册表、Steam、便携版）")
        self._deep_scan_btn.clicked.connect(self._deep_refresh)
        header.addWidget(self._deep_scan_btn)

        add_btn = QPushButton("+ 添加版本")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._add_version)
        header.addWidget(add_btn)

        layout.addLayout(header)

        # 扫描状态标签
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px; padding: 0 0 4px 0;")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # ── 下载 / 安装进度面板 ──────────────────────────────────────────
        # 用户三次反馈"界面看不到下载进度"：主窗口底部那条 42px 灰字在视觉上
        # 太弱。进度必须出现在用户**正在操作的这个页面**上，且必须是真进度条。
        layout.addWidget(self._build_dl_panel())

        # 滚动区域 + 版本列表
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        rows_container = QWidget()
        self._rows_layout = QVBoxLayout(rows_container)
        self._rows_layout.setSpacing(6)
        self._rows_layout.setContentsMargins(0, 8, 0, 8)
        self._rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(rows_container)
        layout.addWidget(scroll, 1)

        return self._widget

    # ── 安装位置（用户可自选，并跨会话记住） ──────────────────────────────

    @staticmethod
    def _settings() -> QSettings:
        return _settings()

    def _default_root(self) -> str:
        """默认安装根目录（base_dir/blender_versions）。"""
        if self.ctx is None:
            return ""
        return str(Path(self.ctx.config_service.base_dir) / "blender_versions")

    def _default_install_root(self) -> str:
        """上次选择的安装根目录；没有就用默认目录。"""
        return get_install_root(self._default_root())

    def _managed_roots(self) -> list:
        """本应用管理的所有安装根目录（默认目录 + 用户自选过的目录）。

        用途：① 扫描时作为 extra_dirs，自定义目录里的版本才扫得到；
             ② 删除保护——只有这些目录下的版本允许删除。
        """
        return get_managed_roots(self._default_root())

    def _add_managed_root(self, root: str) -> None:
        """登记一个安装根目录（扫描 + 删除保护都靠它）"""
        save_install_root(root, self._default_root())

    # ── 已安装版本缓存（重启不再重扫） ────────────────────────────────────

    def _cache_path(self):
        if self.ctx is None:
            return None
        base = Path(self.ctx.config_service.base_dir) / "blender_versions"  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return base / "installed_cache.json"

    def _save_installed_cache(self) -> None:
        """把扫描结果落盘，下次启动直接渲染，不必再等一轮扫描。"""
        path = self._cache_path()
        if path is None:
            return
        data = [
            {
                "version": i.version,
                "path": str(i.path),
                "executable": str(i.executable),
                "is_lts": bool(i.is_lts),
                "source": getattr(i, "source", "cache"),
            }
            for i in self._installations
        ]
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _load_installed_cache(self) -> list:
        """读上次扫描结果（仅保留仍然存在的安装）。"""
        path = self._cache_path()
        if path is None:
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for item in raw:
            exe = Path(item.get("executable", ""))
            if not exe.is_file():
                continue  # 那个安装已经没了，别拿陈旧记录骗人
            version = item.get("version", "")
            is_lts = bool(item.get("is_lts", False))
            # LTS 标记**重算**，不信任缓存值：缓存可能是旧规则写下的
            # （LTS 清单曾与在线版本列表不同步，装了 5.2 LTS 却记成非 LTS）。
            try:
                if self.ctx is not None and self.ctx.blender_service is not None:
                    is_lts = bool(self.ctx.blender_service._is_lts(version))
            except Exception:  # noqa: BLE001
                pass
            out.append(BlenderInstallation(
                version=version,
                path=Path(item.get("path", "")),
                executable=exe,
                is_lts=is_lts,
                source=item.get("source", "cache"),
            ))
        return out

    def _render_from_cache_then_verify(self) -> None:
        """启动路径：先用缓存把列表画出来，再后台静默校验。"""
        cached = self._load_installed_cache()
        if cached:
            self._installations = cached
            self._scan_completed = True
            self._rendered_from_cache = True
            self._clear_rows()
            self._render_rows()
            self._set_scanning(True, "⏳ 正在后台校验已安装版本...")
            # 后台快速扫一遍：有新装/卸载的再更新界面（用户无感知）。
            # ⚠ 早先这里误调了一个本模块不存在的方法名，导航时直接
            # AttributeError，状态栏永远停在"正在后台校验"、扫描也从未启动。
            # 本模块唯一的扫描入口就是 _refresh()。
            self._refresh(deep_scan=False, quiet=True)
        else:
            self._rendered_from_cache = False
            self._refresh()

    def _deep_scan_stale(self, days: int = 7) -> bool:
        """距离上次深度扫描是否已经超过 N 天（从没扫过也算过期）。"""
        last = self._settings().value("blender/last_deep_scan", 0, type=int)
        if not last:
            return True
        try:
            return (time.time() - int(last)) > days * 86400
        except (TypeError, ValueError):
            return True

    def retranslate(self) -> None:
        """语言切换后刷新本页标题（主窗口统一调用）。"""
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(i18n.tr("module.version.title"))

    def on_navigate_to(self):
        if not self._scan_completed:
            self._render_from_cache_then_verify()
        else:
            # 缓存有效，仅重新渲染列表
            self._clear_rows()
            self._render_rows()

        # 长期没做过全盘扫描 → 延迟 3 秒后台静默补一次（不影响操作，
        # 扫到的新版本自动并入列表）。每个会话最多一次。
        if not self._auto_deep_scanned and self._deep_scan_stale():
            self._auto_deep_scanned = True
            self._defer_timer = QTimer(self._widget)
            self._defer_timer.setSingleShot(True)
            self._defer_timer.timeout.connect(lambda: self._deep_refresh(silent=True))
            self._defer_timer.start(3000)

    def on_shutdown(self):
        """Hub 关闭时停止扫描线程并等待解压线程收尾"""
        self._stop_scan()

        # 解压线程同样需要收尾：MainWindow.closeEvent 现在会真正调用 shutdown_all，
        # 这里用协作式中断 + wait，避免退出时 QThread 被强杀。
        worker = self._install_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(3000):
                self.ctx.log_service.warning(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                    "InstallWorker still running at shutdown", module="version_library"
                )
        self._install_worker = None

        super().on_shutdown()

    def on_navigate_away(self):
        """离开模块时停止扫描"""
        self._stop_scan()
        super().on_navigate_away()

    def _refresh(self, deep_scan: bool = False, quiet: bool = False, keep_rows: bool = False):
        """刷新已安装版本列表

        deep_scan: 是否递归全盘（慢）
        quiet:     静默——结果不用 toast 打扰用户
        keep_rows: 不清空现有列表（避免闪一下空白），扫描完成后整体替换
        """
        if self.ctx is None:
            return

        # 取消之前可能正在运行的扫描
        self._stop_scan()

        if not keep_rows:
            # 清空现有列表
            self._clear_rows()
            self._installations = []

        # 重置取消标志
        self.ctx.blender_service.reset_cancel()

        self._quiet_scan = quiet
        self._set_scanning(True, "⏳ 正在扫描已安装版本...", silent=quiet)

        # 自定义安装目录必须作为 extra_dirs 传进去，否则装到 D:/Blender 之类
        # 位置的版本永远扫不出来（默认只扫固定路径 + 注册表 + Steam）。
        self._scan_worker = ScanWorker(
            self.ctx.blender_service, deep_scan=deep_scan,
            extra_dirs=self._managed_roots(),
        )
        self._scan_worker.found.connect(self._on_scan_found_card)
        self._scan_worker.finished.connect(self._on_scan_finished_card)
        self._scan_worker.start()

    def _deep_refresh(self, silent: bool = True):
        """深度扫描系统中所有 Blender 安装（默认**后台静默**）。

        静默的含义：不弹 toast、不清空列表造成闪烁、按钮文案不变，
        只在页面里留一行小字；扫到的新版本直接并入列表。
        """
        if self.ctx is None:
            return

        # 取消之前的扫描（先 cancel 再等旧线程结束，然后才 reset）
        self._stop_scan()

        if not silent:
            self._clear_rows()
            self._installations = []

        # 确保旧线程完全退出后再重置取消标志
        self.ctx.blender_service.reset_cancel()

        self._quiet_scan = silent
        self._set_scanning(True, "🔄 后台深度扫描中…（不影响操作）", silent=silent)
        self._auto_deep_scanned = True

        # 启动实时扫描（先快速扫、再深度扫）
        self._scan_worker = ScanWorker(
            self.ctx.blender_service, deep_scan=True,
            extra_dirs=self._managed_roots(),
        )
        self._scan_worker.found.connect(self._on_scan_found_card)
        self._scan_worker.finished.connect(self._on_scan_finished_card)
        self._scan_worker.start()

    def _on_scan_found_card(self, installation: BlenderInstallation):
        """实时收到一个 Blender 安装，立即添加到版本列表"""
        # 去重
        if any(existing.version == installation.version for existing in self._installations):
            return
        self._installations.append(installation)
        self._installations.sort(
            key=lambda x: BlenderService._version_tuple(x.version), reverse=True,
        )
        # 扫描过程中不清除重绘，等扫描完成后统一渲染
        # 因为列表布局需要知道最新版本才能判断每行的状态

    def _on_scan_finished_card(self, total: int):
        """扫描完成"""
        # 深度扫描是重活，做完记时间戳，7 天内不再自动补扫
        if bool(getattr(self._scan_worker, "deep_scan", False)):
            try:
                self._settings().setValue("blender/last_deep_scan", int(time.time()))
            except Exception:  # noqa: BLE001
                pass
        self._scan_completed = True
        self._set_scanning(False, "")
        # 扫描完成后统一渲染列表
        self._clear_rows()
        self._render_rows()
        self._save_installed_cache()
        quiet = getattr(self, "_quiet_scan", False)
        self._quiet_scan = False
        # 通过 toast 消息栏显示结果（5秒后消失）
        if quiet:
            # 后台静默：不弹 toast（用户要求"深度扫描后台静默"），
            # 只在页面里留一行会自动消失的小字，列表已经同步更新。
            self._flash_status(f"✅ 已更新，共 {total} 个版本", 4000)
        elif total > 0:
            message = f"✅ 扫描完成，找到 {total} 个 Blender 版本"
            if self.ctx and self.ctx.event_bus:
                self.ctx.event_bus.emit("ui.toast", {"message": message, "duration": 5000})
        else:
            if self.ctx and self.ctx.event_bus:
                self.ctx.event_bus.emit("ui.toast", {"message": "❌ 未找到 Blender 安装", "duration": 5000})
        if self.ctx and self.ctx.log_service:
            self.ctx.log_service.info(
                f"Scan found {total} Blender installation(s)",
                module="version_library",
            )
        self._scan_worker = None

    def _set_scanning(self, scanning: bool, message: str = "", silent: bool = False):
        """设置扫描状态（禁用/启用按钮，显示状态信息）

        silent=True：静默模式，按钮文案保持不变（界面不跳字），只留一行小字。
        """
        self._scanning = scanning
        if self._deep_scan_btn:
            self._deep_scan_btn.setEnabled(not scanning)
            if scanning and silent:
                pass  # 静默：不改按钮文案，避免"扫描中/深度扫描"来回跳
            else:
                self._deep_scan_btn.setText("⏳ 扫描中..." if scanning else "🔄 深度扫描")
        if self._status_label:
            self._status_label.setText(message)
            self._status_label.setVisible(bool(message))

    def _hide_bottom_later(self, win, ms: int) -> None:
        """几秒后收掉主窗口底部条。

        定时器挂在页面 widget 上（有 receiver，且属于主线程），不用
        ``QTimer.singleShot(0, fn)`` 那种无 receiver 的形态——那种在无事件
        循环的线程里根本不会触发（本项目踩过的坑）。
        """
        if self._bottom_timer is None:
            self._bottom_timer = QTimer(self._widget)
            self._bottom_timer.setSingleShot(True)
        try:
            self._bottom_timer.timeout.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._bottom_timer.timeout.connect(win.hide_download)
        self._bottom_timer.start(max(0, int(ms)))

    def _flash_status(self, text: str, ms: int = 5000) -> None:
        """临时显示一行状态文字，几秒后自动消失（替代 toast，不打断操作）。"""
        if self._status_label is None:
            return
        self._status_label.setText(text)
        self._status_label.setVisible(True)
        if self._status_timer is None:
            # QTimer 必须有 Qt 父对象：本模块不是 QObject
            self._status_timer = QTimer(self._widget)
            self._status_timer.setSingleShot(True)
            self._status_timer.timeout.connect(self._status_label.hide)
        self._status_timer.start(max(0, int(ms)))

    def _stop_scan(self):
        """停止正在运行的扫描线程"""
        if hasattr(self, '_scan_worker') and self._scan_worker is not None:
            worker = self._scan_worker
            self._scan_worker = None
            # 断开旧 worker 的信号，防止旧线程完成后干扰新扫描
            try:
                worker.found.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass
            # 先通知 blender_service 取消（会设置 _cancel_flag，让 detect_installed 内部停止）
            if self.ctx and self.ctx.blender_service:
                self.ctx.blender_service.cancel_scan()
            # 再请求 QThread 中断
            worker.requestInterruption()
            worker.quit()
            worker.wait(3000)
            # wait 超时后强制 terminate（如果线程还没退出）
            if worker.isRunning():
                worker.terminate()

    def _get_latest_available_version(self, major_minor: str = "") -> str:
        """获取最新可下载的稳定版版本号
        Args:
            major_minor: 如 "4.2"，则只返回该小版本系列的最新 patch，如 "4.2.10"
                         为空则返回全局最新版本
        """
        try:
            available = BlenderService.get_available_versions()
            stable_versions = available.get("stable", [])
            if stable_versions:
                if major_minor:
                    # 只取同小版本系列的
                    filtered = [v for v in stable_versions
                                if v["version"].startswith(major_minor + ".")]
                    if filtered:
                        sorted_vers = sorted(filtered,
                                             key=lambda v: BlenderService._version_tuple(v["version"]),
                                             reverse=True)
                        return sorted_vers[0]["version"]
                else:
                    sorted_vers = sorted(stable_versions,
                                         key=lambda v: BlenderService._version_tuple(v["version"]),
                                         reverse=True)
                    return sorted_vers[0]["version"]
        except Exception:
            pass
        return ""

    def _clear_rows(self):
        """清空列表布局"""
        if self._rows_layout is not None:
            while self._rows_layout.count():
                item = self._rows_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

    def _render_rows(self):
        """渲染已安装版本的行列表"""
        if self._rows_layout is None:
            return

        for inst in self._installations:
            inst_tuple = BlenderService._version_tuple(inst.version)
            # 获取同小版本系列 (major.minor) 的最新 patch 版本
            major_minor = ".".join(inst.version.split(".")[:2])
            latest_in_series = self._get_latest_available_version(major_minor)
            latest_in_series_tuple = BlenderService._version_tuple(latest_in_series) if latest_in_series else (0,)

            is_latest = inst_tuple >= latest_in_series_tuple if latest_in_series_tuple != (0,) else True
            has_update = latest_in_series and inst_tuple < latest_in_series_tuple

            row = VersionRow(inst, is_latest=is_latest,
                             latest_version=latest_in_series if has_update else "")
            row.launch_clicked.connect(self._launch_blender)
            row.delete_clicked.connect(self._delete_version)
            row.details_clicked.connect(self._show_details)
            row.update_clicked.connect(self._update_version)
            self._rows_layout.addWidget(row)

        # 底部占位 stretch
        self._rows_layout.addStretch()

        # 空状态
        if not self._installations:
            empty_label = QLabel("尚未安装任何 Blender 版本\n\n点击「+ 添加版本」在线下载或扫描本地\n点击「🔄 深度扫描」自动搜索已安装版本")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_DISABLED}; font-size: 16px; padding: 60px;")
            self._rows_layout.addWidget(empty_label)

    def _add_version(self):
        """弹出添加版本对话框（在线下载 + 本地扫描）"""
        blender_service = self.ctx.blender_service if self.ctx else None
        # 已装版本集合：内存里还没扫完时退回磁盘缓存，否则刚开应用就点
        # "添加版本"会漏掉过滤（列表里仍出现已经装过的版本）
        installed = {i.version for i in self._installations}
        if not installed:
            try:
                installed = {i.version for i in self._load_installed_cache()}
            except Exception:  # noqa: BLE001
                installed = set()

        dialog = VersionSelectorDialog(
            self._widget, blender_service=blender_service,
            default_install_dir=self._default_install_root(),
            installed_versions=installed,
        )

        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_version:
            return

        # 判断是本地添加还是在线下载
        if hasattr(dialog, '_selected_local_installation') and dialog._selected_local_installation:
            # 本地版本：直接添加到已安装列表
            inst = dialog._selected_local_installation
            self._add_local_installation(inst)
        else:
            # 在线下载（安装位置由用户在对话框里选，留空则用默认目录）
            self._download_version(
                dialog.selected_version,
                dialog.mirror_combo.currentText(),
                getattr(dialog, "install_dir", "") or "",
            )

    def _add_local_installation(self, inst: BlenderInstallation):
        """添加本地已安装的 Blender 版本到列表"""
        # 检查是否已存在同版本
        for existing in self._installations:
            if existing.version == inst.version:
                QMessageBox.information(
                    self._widget, "已存在",
                    f"Blender {inst.version} 已在列表中。\n路径: {existing.path}",
                )
                return

        self._installations.append(inst)
        self._installations.sort(key=lambda x: BlenderService._version_tuple(x.version), reverse=True)
        self._clear_rows()
        self._render_rows()

        if self.ctx:
            self.ctx.log_service.info(
                f"Added local Blender {inst.version} from {inst.path} (source: {inst.source})",
                module="version_library",
            )
            self.ctx.event_bus.emit("blender.local_added", {
                "version": inst.version,
                "path": str(inst.path),
                "source": inst.source,
            })

    def _download_version(self, version: str, mirror_text: str, install_root: str = ""):
        """下载指定版本

        install_root: 安装根目录，实际装到 <install_root>/<version>。
        留空则用默认目录（base_dir/blender_versions）。
        """
        if self.ctx is None:
            return

        # 安装根目录：用户自选 → 计入托管目录（后续扫描能找到、且允许删除）
        root = Path(install_root) if install_root else (
            Path(self.ctx.config_service.base_dir) / "blender_versions"
        )

        # 先验证目录可写：选到 Program Files / 只读盘的话，别等下完 400MB 才报错
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".bh_write_test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except Exception as e:  # noqa: BLE001
            _ = QMessageBox.warning(
                self._widget, "安装位置不可用",
                f"无法写入安装目录：\n{root}\n\n{e}\n\n请在「添加版本」里另选一个目录。",
            )
            return

        self._install_root = str(root)
        self._add_managed_root(str(root))

        # LTS 标记在**开始安装时就定下来**（此时在线版本数据一定可用），
        # 装完回填列表/广播事件都用这个值，避免再判一次不一致
        try:
            self._dl_is_lts = bool(self.ctx.blender_service._is_lts(version))
        except Exception:  # noqa: BLE001
            self._dl_is_lts = False

        # 映射镜像源
        mirror_map = {
            "南京大学镜像（推荐）": "nju",
            "清华镜像 (TUNA)": "tsinghua",
            "中科大镜像 (USTC)": "ustc",
            "官方源（国内常被拦截）": "official",
        }
        mirror = mirror_map.get(mirror_text, "nju")

        # 下载目录（先算，本地已有完整包时不需要联网拿 URL）
        downloads_dir = Path(self.ctx.config_service.base_dir) / "downloads" / "blender"
        save_path = downloads_dir / f"blender-{version}-windows-x64.zip"

        task_id = f"blender_{version}_{uuid.uuid4().hex[:8]}"

        # ── 已有完整安装包就直接进安装，不重复下 400MB ──
        # 上一轮"下载成功但安装没执行"会在 downloads 里残留完整 zip；用户再点
        # 下载又要等 35s。用 zipfile 读中央目录判断完整性（截断包会抛 BadZipFile，
        # 且只读末尾几百字节，不校验 CRC，几乎无耗时）。
        if save_path.exists() and save_path.stat().st_size > _REUSE_MIN_BYTES:
            try:
                with zipfile.ZipFile(save_path) as _zf:
                    _reuse = bool(_zf.namelist())
            except Exception:
                _reuse = False
            if _reuse:
                if self.ctx.log_service:
                    self.ctx.log_service.info(
                        "复用已下载的安装包 "
                        f"({save_path.stat().st_size / 1024 / 1024:.0f}MB): {save_path.name}",
                        module="version_library",
                    )
                self._dl_version = version
                self._dl_show(f"📦 正在安装 Blender {version}", "使用已下载的安装包…", 0)
                from types import SimpleNamespace

                cached_task = SimpleNamespace(save_path=save_path)
                # 直接调用：此处本来就在主线程（按钮点击回调），
                # 再套一层延后派发反而会在无事件循环的调用场景下永不触发。
                self._on_dl_install(cached_task, version, self._install_root)
                self.ctx.event_bus.emit("blender.download_started", {"version": version})
                return

        # 获取下载 URL
        url = self.ctx.blender_service.get_download_url(version)
        if url is None:
            QMessageBox.warning(self._widget, "错误", f"无法获取 Blender {version} 的下载地址")
            return

        # 使用镜像源
        from core.services.download_service import DownloadService
        url = DownloadService.resolve_mirror_url(url, mirror)

        # 下载线程（threading.Thread，无 Qt 事件循环）→ 主线程 的信号桥。
        # 两种"切主线程"的旧写法都不可靠（详见 _DownloadBridge 的说明），
        # pyqtSignal 是唯一可靠路径。
        bridge = _DownloadBridge(self._widget)
        self._dl_bridge = bridge  # 持引用防 GC
        self._dl_version = version
        self._dl_logged_bucket = -1
        bridge.progress.connect(self._on_dl_progress)
        bridge.done.connect(self._on_dl_done)
        bridge.install.connect(self._on_dl_install)
        bridge.error.connect(self._on_dl_error)

        # 立刻把面板亮出来（不等第一个 chunk）——用户点了按钮就必须有反馈
        self._dl_show(f"⬇ 下载中 Blender {version}", "正在连接镜像源…", 0)

        # 进度节流：404MB 的包按 8KB 一块回调 ≈ 5 万次/次下载。
        # 逐块 emit 会把主线程事件队列灌满（界面卡顿且进度文本闪烁），
        # 这里只在"百分比变化 ≥1 或距上次 ≥200ms"时发信号。
        _last = {"pct": -1, "t": 0.0}
        import time as _time

        def on_progress(progress: float, downloaded: int, total: int, speed: float) -> None:
            # 下载阶段进度。只做数值换算 + emit（线程安全），GUI 更新交给主线程槽。
            pct = int(downloaded * 100 / total) if total > 0 else int(progress * 100)
            now = _time.monotonic()
            if pct == _last["pct"] and (now - _last["t"]) < 0.2:
                return
            _last["pct"] = pct
            _last["t"] = now
            bridge.progress.emit(
                max(0, min(100, pct)),
                downloaded / 1024 / 1024,
                total / 1024 / 1024,
                speed / 1024 / 1024,
            )

        def on_complete(task):
            bridge.done.emit()
            bridge.install.emit(task, version, self._install_root)

        def on_error(task, error):
            bridge.done.emit()
            bridge.error.emit(f"Blender {version} 下载失败:\n{error}")

        self.ctx.download_service.add_task(
            task_id=task_id,
            url=url,
            save_path=save_path,
            display_name=f"Blender {version}",
            on_progress=on_progress,
            on_complete=on_complete,
            on_error=on_error,
        )

        # 广播事件
        self.ctx.event_bus.emit("blender.download_started", {"version": version})

    # ── 下载进度桥接槽（主线程执行） ─────────────────────────────────────

    def _on_dl_progress(self, pct: int, mb: float, total_mb: float, speed_mbps: float) -> None:
        """主线程槽：把进度同时画在「页面内面板」和「窗口底部条」上。"""
        version = self._dl_version
        size_txt = f"{mb:.0f}/{total_mb:.0f}MB" if total_mb > 0 else f"{mb:.0f}MB"
        # 每 10% 打一条 INFO：真机若"又看不到进度"，日志能直接判定是
        # 「信号没到主线程」还是「到了但没画出来」
        bucket = pct // 10
        if bucket != self._dl_logged_bucket:
            self._dl_logged_bucket = bucket
            if self.ctx and self.ctx.log_service:
                self.ctx.log_service.info(
                    f"download progress {pct}% ({size_txt}, {speed_mbps:.1f}MB/s) "
                    # isVisible() 在父窗口未显示时恒为 False（无头/未 show 场景），
                    # isHidden() 才反映"自己有没有被显式隐藏"
                    f"panel_visible={not self._dl_panel.isHidden() if self._dl_panel else None}",
                    module="version_library",
                )
        # 页面内面板（用户正在看的地方，一定有）——幂等显示，防止任何
        # 早期分支漏掉 _dl_show 时进度条"有值但看不见"
        if self._dl_panel is not None and self._dl_panel.isHidden():
            self._dl_panel.setVisible(True)
        self._dl_update(
            title=f"⬇ 下载中 Blender {version}  {pct}%",
            detail=f"{size_txt} · {speed_mbps:.1f} MB/s",
            pct=pct,
        )
        # 全局底部条（切到别的页面也还能看到）
        win = getattr(self.ctx, "main_window", None) if self.ctx else None
        if win is not None and hasattr(win, "show_download"):
            win.show_download(
                f"下载中 Blender {version}  {pct}%  ({size_txt}, {speed_mbps:.1f}MB/s)",
                pct,
            )

    def _on_dl_done(self) -> None:
        win = getattr(self.ctx, "main_window", None) if self.ctx else None
        if win is not None and hasattr(win, "hide_download"):
            win.hide_download()

    def _on_dl_error(self, message: str) -> None:
        self._dl_update(title="❌ 下载失败", detail="", pct=0)
        self._dl_hide_later(8000)
        QMessageBox.warning(self._widget, "下载失败", message)

    def _on_dl_install(self, task, version: str, install_root: str = "") -> None:  # noqa: ANN001
        """下载完成 → 开始解压安装（主线程）。"""
        root = Path(install_root) if install_root else (
            Path(self.ctx.config_service.base_dir) / "blender_versions"
        )
        install_dir = root / version
        old_worker = self._install_worker
        if old_worker is not None and old_worker.isRunning():
            old_worker.wait(3000)

        # 页面内面板切到"安装"阶段（即使对话框被挡住也能看到进度）
        self._dl_show(f"📦 正在安装 Blender {version}", "解压中…", 0)
        # 底部条也跟着切（done 信号刚把它隐藏过），保持视觉连续
        win = getattr(self.ctx, "main_window", None) if self.ctx else None
        if win is not None and hasattr(win, "show_download"):
            win.show_download(f"📦 正在安装 Blender {version}", 0)

        dlg = QProgressDialog("正在解压 Blender…", "取消", 0, 100, self._widget)
        dlg.setWindowTitle(f"安装 Blender {version}")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        self._install_progress = dlg

        def on_prog(pct: int) -> None:
            dlg.setValue(pct)
            self._dl_update(
                title=f"📦 正在安装 Blender {version}  {pct}%",
                detail="解压中…",
                pct=pct,
            )
            if win is not None and hasattr(win, "show_download"):
                win.show_download(f"安装中 Blender {version}  {pct}%", pct)

        def on_finished(success: bool, message: str) -> None:
            dlg.close()
            self._install_progress = None
            self._on_install_finished(success, message)

        worker = InstallWorker(str(task.save_path), str(install_dir))
        worker.setParent(self._widget)
        self._install_worker = worker
        worker.progress.connect(on_prog)
        worker.finished.connect(on_finished)
        dlg.canceled.connect(worker.requestInterruption)
        worker.start()

    @staticmethod
    def _find_blender_exe(root: Path) -> Optional[Path]:
        """在解压出的安装目录里定位 blender.exe。

        官方 zip 解压后是 <安装目录>/blender-x.y.z-windows-x64/blender.exe，
        所以 exe 不在根目录而在下一层。
        """
        if not root.exists():
            return None
        candidates = [root / "blender.exe"]
        try:
            for child in sorted(root.iterdir()):
                if child.is_dir():
                    candidates.append(child / "blender.exe")
        except OSError:
            return None
        for c in candidates:
            if c.is_file():
                return c
        try:  # 兜底：两层内递归找一次
            for c in sorted(root.rglob("blender.exe")):
                return c
        except OSError:
            pass
        return None

    def _insert_installed(self, install_dir: str) -> bool:
        """把刚装好的版本**立刻**插进列表（不等扫描）。

        自定义安装目录不在默认扫描路径里，只靠 _refresh() 的话列表要么很久
        才更新，要么永远扫不到——安装完就得直接落一条。
        """
        if not install_dir or self.ctx is None:
            return False
        exe = self._find_blender_exe(Path(install_dir))
        if exe is None:
            return False
        try:
            version = self.ctx.blender_service._read_version(exe) or ""
        except Exception:  # noqa: BLE001
            version = ""
        if not version:
            import re

            m = re.search(r"(\d+\.\d+(?:\.\d+)?)", Path(install_dir).name)
            version = m.group(1) if m else ""
        if not version:
            return False
        # 优先用安装开始时算好的标记（那时在线版本数据一定可用）
        is_lts = bool(getattr(self, "_dl_is_lts", False))
        if not is_lts:
            try:
                is_lts = bool(self.ctx.blender_service._is_lts(version))
            except Exception:  # noqa: BLE001
                is_lts = False
        inst = BlenderInstallation(
            version=version, path=exe.parent, executable=exe,
            is_lts=is_lts, source="managed",
        )
        self._installations = [i for i in self._installations if i.version != version]
        self._installations.append(inst)
        self._installations.sort(
            key=lambda x: BlenderService._version_tuple(x.version), reverse=True,
        )
        self._clear_rows()
        self._render_rows()
        self._save_installed_cache()
        return True

    def _on_install_finished(self, success: bool, message: str):
        # 底部条收尾：给一个明确的完成/失败提示，**几秒后自动收掉**。
        # 旧实现在这里直接 hide —— 用户要么完全看不到"已完成"，
        # 要么（解压耗时长时）看到"安装中 100%"长时间挂着不动。
        _win = getattr(self.ctx, "main_window", None) if self.ctx else None
        if success:
            # 面板先收尾成"已完成"，再弹模态框——弹窗期间背后也是完成态
            self._dl_update(
                title=f"✅ Blender {self._dl_version} 已安装",
                detail="可在下方版本列表中启动",
                pct=100,
            )
            self._dl_hide_later(6000)
            if _win is not None and hasattr(_win, "show_download"):
                _win.show_download(f"✅ Blender {self._dl_version} 已安装", 100)
                self._hide_bottom_later(_win, 4000)
            QMessageBox.information(self._widget, "安装完成", message)
            # 广播安装完成事件。
            # ⚠ install_dir 在 InstallWorker 里是 **str**（构造参数直接存），
            # 旧实现写 .install_dir.name 会 AttributeError——而 PyQt6 的 slot
            # 未捕获异常会直接 abort 整个进程（无 traceback、退出码 127），
            # 表现为"安装完成了，应用一闪就没了"。
            if self.ctx:
                install_dir = self._install_worker.install_dir if self._install_worker else ""
                self.ctx.event_bus.emit("blender.installed", {
                    "version": Path(install_dir).name if install_dir else "",
                    "path": install_dir,
                    "is_lts": bool(getattr(self, "_dl_is_lts", False)),
                })
            # 先立刻落一条到列表，再后台静默校一遍（带自定义安装根目录）。
            # keep_rows=True：后台校验不能把刚插进去的那条清空（否则列表闪空）
            self._insert_installed(install_dir)
            self._refresh(deep_scan=False, quiet=True, keep_rows=True)
        else:
            self._dl_update(title="❌ 安装失败", detail=message[:80], pct=0)
            self._dl_hide_later(8000)
            if _win is not None and hasattr(_win, "show_download"):
                _win.show_download(f"❌ Blender {self._dl_version} 安装失败", 0)
                self._hide_bottom_later(_win, 5000)
            QMessageBox.warning(self._widget, "安装失败", message)

    def _launch_blender(self, version: str):
        """启动 Blender"""
        for inst in self._installations:
            if inst.version == version:
                if self.ctx:
                    self.ctx.blender_service.launch(inst)
                return

    def _delete_version(self, version: str):
        """删除 Blender 版本。

        安全约束：只有由本应用下载安装的版本（位于**任一托管安装根目录**下，
        含用户自选的自定义目录）才允许递归删除。旧实现对扫描到的任意安装
        直接 shutil.rmtree，会把 Program Files / Steam 里的 Blender 整个删掉。
        """
        target = next((i for i in self._installations if i.version == version), None)
        if target is None:
            return

        is_managed = False
        try:
            resolved_target = Path(target.path).resolve()
            for root in self._managed_roots():
                try:
                    resolved_root = Path(root).resolve()
                except (OSError, ValueError):
                    continue
                if not resolved_target.is_relative_to(resolved_root):
                    continue
                if resolved_target == resolved_root:
                    continue
                # 只允许删「<安装根>/<版本号>/…」这种**由本应用创建**的目录。
                # zip 解压通常多一层（<root>/5.2.2/blender-5.2.2-windows-x64），
                # 所以按"是否位于 <root>/<version> 之下"判断。
                # ⚠ 用户可能把安装根设成 "D:\Program Files\Blender Foundation"，
                # 那里还躺着官方安装器建的 "Blender 4.5" —— 它不等于
                # "<root>/4.5.14"，必须判为外部安装，绝不代删。
                version_dir = resolved_root / str(version)
                if resolved_target != version_dir and not resolved_target.is_relative_to(version_dir):
                    continue
                is_managed = True
                break
        except (OSError, ValueError):
            is_managed = False

        if not is_managed:
            # 外部安装（Program Files / Steam / 便携版）——只提示，不代劳
            where = target.path
            _ = QMessageBox.warning(
                self._widget, "无法删除外部安装",
                f"Blender {version} 位于应用管理目录之外：\n\n{where}\n\n"
                "该版本由系统安装包、Steam 或手动安装，Blender Hub 不会删除它。\n"
                "如需移除，请自行前往上述目录手动操作。",
            )
            return

        reply = QMessageBox.question(
            self._widget,
            "确认删除",
            f"确定要删除 Blender {version} 吗？\n"
            f"路径: {target.path}\n\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            shutil.rmtree(target.path)
            if self.ctx:
                self.ctx.log_service.info(
                    f"Deleted managed Blender {version} at {target.path}", module="version_library"
                )
                self.ctx.event_bus.emit("blender.uninstalled", {"version": version})
                # 安装目录已删除，必须让检测缓存失效，否则 60s 内仍会显示旧版本
                self.ctx.blender_service.clear_cache()
        except Exception as e:
            _ = QMessageBox.warning(self._widget, "删除失败", str(e))

        self._refresh()

    def _update_version(self, version: str):
        """更新到同小版本系列的最新 patch 版本（下载 + 安装）"""
        major_minor = ".".join(version.split(".")[:2])
        latest = self._get_latest_available_version(major_minor)
        if not latest:
            QMessageBox.warning(self._widget, "提示", "无法获取最新版本信息")
            return

        if BlenderService._version_tuple(version) >= BlenderService._version_tuple(latest):
            QMessageBox.information(self._widget, "已是最新", f"Blender {version} 已是该系列最新版本。")
            return

        reply = QMessageBox.question(
            self._widget,
            "确认更新",
            f"将下载并安装 Blender {latest}（从 {version} 更新）\n\n"
            f"仅更新小版本（{major_minor}.x 系列），不会跨大版本。\n"
            f"注意：当前版本 {version} 不会被删除。\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            # 装到当前版本**旁边**（用户若把 Blender 装在自选目录，更新也应落在那儿）
            root = ""
            target = next((i for i in self._installations if i.version == version), None)
            if target is not None:
                p = Path(target.path)
                root = str(p.parent.parent) if p.parent.name == version else str(p.parent)
            self._download_version(latest, "南京大学镜像（推荐）", root)

    def _show_details(self, version: str):
        for inst in self._installations:
            if inst.version == version:
                major_minor = ".".join(inst.version.split(".")[:2])
                latest_in_series = self._get_latest_available_version(major_minor)
                inst_tuple = BlenderService._version_tuple(inst.version)
                latest_tuple = BlenderService._version_tuple(latest_in_series) if latest_in_series else (0,)
                update_info = ""
                if latest_in_series and inst_tuple < latest_tuple:
                    update_info = f"\n{major_minor}.x 系列可更新至: {latest_in_series}"
                elif latest_in_series and inst_tuple >= latest_tuple:
                    update_info = f"\n状态: 已是 {major_minor}.x 系列最新版本"

                msg = (
                    f"版本: {inst.version}\n"
                    f"LTS: {'是' if inst.is_lts else '否'}\n"
                    f"路径: {inst.path}\n"
                    f"可执行文件: {inst.executable}"
                    f"{update_info}"
                )
                QMessageBox.information(self._widget, f"Blender {version} 详情", msg)
                return
