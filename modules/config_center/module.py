"""
配置中心模块 — Blender 配置快照（记录配置清单）

UI: 三栏式：快照列表 / 详情 / 操作面板
说明：恢复与跨版本迁移尚未实现，入口已置灰，避免误报"已完成"。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QFrame, QMessageBox,
    QGroupBox, QTextEdit, QSplitter, QComboBox, QInputDialog, QScrollArea,
    QTabWidget,
)
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from pathlib import Path
from typing import Optional, List
import json
import os
from datetime import datetime
import uuid

from core import i18n, theme  # pyright: ignore[reportImplicitRelativeImport]
from core.api.module_api import BaseModule


class ConfigSnapshot:
    """配置快照"""
    def __init__(
        self,
        snapshot_id: str,
        name: str,
        blender_version: str,
        created_at: str,
        description: str = "",
        config_data: dict = None,
    ):
        self.id = snapshot_id
        self.name = name
        self.blender_version = blender_version
        self.created_at = created_at
        self.description = description
        self.config_data = config_data or {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "blender_version": self.blender_version,
            "created_at": self.created_at,
            "description": self.description,
            "config_data": self.config_data,
        }

    @staticmethod
    def from_dict(d: dict) -> "ConfigSnapshot":
        return ConfigSnapshot(
            snapshot_id=d["id"],
            name=d["name"],
            blender_version=d.get("blender_version", "unknown"),
            created_at=d.get("created_at", ""),
            description=d.get("description", ""),
            config_data=d.get("config_data", {}),
        )


def _blender_config_dir(version: str) -> Path:
    """Windows 下 Blender 的用户配置目录：
    %APPDATA%/Blender Foundation/Blender/<version>/config
    """
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Blender Foundation" / "Blender" / version / "config"


class ConfigCenterModule(BaseModule):
    """配置中心模块"""

    def __init__(self):
        super().__init__()
        self.id = "config_center"
        self.name = "配置"
        self.icon = "⚙️"
        self._widget: Optional[QWidget] = None
        self._snapshot_list: QListWidget = None
        self._detail_area: QTextEdit = None
        self._snapshots: List[ConfigSnapshot] = []

    def get_widget(self) -> QWidget:
        if self._widget:
            return self._widget

        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # 标题
        header = QHBoxLayout()
        self._title_label = QLabel(i18n.tr("module.config.title"))
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD};")
        header.addWidget(self._title_label)
        header.addStretch()
        layout.addLayout(header)

        # 两个标签页：Blender 配置（快照清单）与应用设置（原独立设置页）
        self._tabs = QTabWidget()
        tabs = self._tabs
        tabs.setObjectName("configTabs")

        # ── Tab 1：Blender 配置（三栏式 QSplitter）──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：快照列表
        left_panel = QFrame()
        left_panel.setObjectName("card")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)

        left_header = QLabel("📋 配置快照")
        left_header.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme.Tokens.ON_CARD};")
        left_layout.addWidget(left_header)

        self._snapshot_list = QListWidget()
        self._snapshot_list.currentItemChanged.connect(self._on_snapshot_selected)
        left_layout.addWidget(self._snapshot_list, 1)

        splitter.addWidget(left_panel)

        # 中栏：快照详情
        mid_panel = QFrame()
        mid_panel.setObjectName("card")
        mid_layout = QVBoxLayout(mid_panel)
        mid_layout.setContentsMargins(12, 12, 12, 12)

        mid_header = QLabel("📄 快照详情")
        mid_header.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme.Tokens.ON_CARD};")
        mid_layout.addWidget(mid_header)

        self._detail_area = QTextEdit()
        self._detail_area.setReadOnly(True)
        self._detail_area.setStyleSheet(
            f"QTextEdit {{ background-color: {theme.Tokens.OVERLAY_BG}; border: 1px solid {theme.Tokens.ROW_BORDER}; "
            "border-radius: 8px; padding: 12px; color: {theme.Tokens.ON_CARD_SUB}; font-family: 'Consolas', monospace; }"
        )
        mid_layout.addWidget(self._detail_area, 1)

        splitter.addWidget(mid_panel)

        # 右栏：操作面板
        right_panel = QFrame()
        right_panel.setObjectName("card")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)

        right_header = QLabel("🔧 操作")
        right_header.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {theme.Tokens.ON_CARD};")
        right_layout.addWidget(right_header)

        # 新建快照
        create_group = QGroupBox("创建快照")
        create_layout = QVBoxLayout(create_group)
        create_layout.setSpacing(8)

        self._blender_version_combo = QComboBox()
        self._blender_version_combo.addItem("自动检测...")
        create_layout.addWidget(QLabel("Blender 版本:"))
        create_layout.addWidget(self._blender_version_combo)

        create_btn = QPushButton("💾 新建配置快照")
        create_btn.setObjectName("primaryButton")
        create_btn.clicked.connect(self._create_snapshot)
        create_layout.addWidget(create_btn)
        right_layout.addWidget(create_group)

        # 恢复
        restore_group = QGroupBox("快照操作")
        restore_layout = QVBoxLayout(restore_group)
        restore_layout.setSpacing(8)

        self._restore_btn = QPushButton(i18n.tr("config.restore"))
        restore_btn = self._restore_btn
        restore_btn.setObjectName("secondaryButton")
        restore_btn.setEnabled(False)
        restore_btn.setToolTip("恢复配置尚未实现；当前版本只记录配置清单，不会写回 Blender")
        restore_btn.clicked.connect(self._restore_snapshot)
        restore_layout.addWidget(restore_btn)

        self._migrate_btn = QPushButton(i18n.tr("config.migrate"))
        migrate_btn = self._migrate_btn
        migrate_btn.setObjectName("secondaryButton")
        migrate_btn.setEnabled(False)
        migrate_btn.setToolTip("跨版本迁移尚未实现；当前版本只记录配置清单")
        migrate_btn.clicked.connect(self._migrate_config)
        restore_layout.addWidget(migrate_btn)

        self._hint_label = QLabel(i18n.tr("config.hint"))
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(f"color: {theme.Tokens.TEXT_MUTED}; font-size: 11px;")
        restore_layout.addWidget(self._hint_label)

        delete_btn = QPushButton("🗑 删除快照")
        delete_btn.setObjectName("dangerButton")
        delete_btn.clicked.connect(self._delete_snapshot)
        restore_layout.addWidget(delete_btn)

        right_layout.addWidget(restore_group)
        right_layout.addStretch()

        splitter.addWidget(right_panel)

        # 设置初始比例
        splitter.setSizes([280, 400, 240])
        self._splitter = splitter

        blender_page = QWidget()
        blender_layout = QVBoxLayout(blender_page)
        blender_layout.setContentsMargins(0, 8, 0, 0)
        # 三栏套滚动区：窄屏降级为上下堆叠后，内容总高超过视口，必须可滚动，
        # 否则底部的「创建快照 / 快照操作」会被窗口直接裁掉（用户实测截图）。
        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_scroll.setFrameShape(QFrame.Shape.NoFrame)
        config_scroll.setWidget(splitter)
        blender_layout.addWidget(config_scroll, 1)
        self._install_responsive(blender_page)
        tabs.addTab(blender_page, i18n.tr("config.tab.blender"))

        # ── Tab 2：应用设置（设置页整体嵌入，不再占用侧边栏）──
        if self.ctx is not None:
            from modules.settings import SettingsModule

            tabs.addTab(SettingsModule(self.ctx, show_header=False), i18n.tr("config.tab.app"))

        layout.addWidget(tabs, 1)

        return self._widget

    def _install_responsive(self, page: QWidget) -> None:
        """窄屏降级：宽度不足时把三栏改为上下堆叠，避免内容被挤成一条。"""

        class _Watch(QObject):
            def __init__(self, cb) -> None:
                super().__init__()
                self._cb = cb

            def eventFilter(self, obj, event) -> bool:  # noqa: N802
                if event is not None and event.type() == QEvent.Type.Resize:
                    self._cb(obj.width())
                return False

        self._resize_watch = _Watch(self._on_page_resize)
        page.installEventFilter(self._resize_watch)

    def _on_page_resize(self, width: int) -> None:
        splitter = getattr(self, "_splitter", None)
        if splitter is None or width <= 0:
            return
        narrow = width < 980
        if narrow and splitter.orientation() != Qt.Orientation.Vertical:
            splitter.setOrientation(Qt.Orientation.Vertical)
            # 竖排时按各面板自身的建议高度分配（外层滚动区兜底）
            splitter.setSizes([
                max(120, splitter.widget(i).sizeHint().height())
                for i in range(splitter.count())
            ])
        elif not narrow and splitter.orientation() != Qt.Orientation.Horizontal:
            splitter.setOrientation(Qt.Orientation.Horizontal)
            splitter.setSizes([280, 400, 240])

    def retranslate(self) -> None:
        """语言切换后刷新本页文案（主窗口统一调用，无需重启）。"""
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(i18n.tr("module.config.title"))
        tabs = getattr(self, "_tabs", None)
        if tabs is not None:
            tabs.setTabText(0, i18n.tr("config.tab.blender"))
            if tabs.count() > 1:
                tabs.setTabText(1, i18n.tr("config.tab.app"))
                settings_page = tabs.widget(1)
                fn = getattr(settings_page, "retranslate", None)
                if callable(fn):
                    fn()
        if getattr(self, "_restore_btn", None) is not None:
            self._restore_btn.setText(i18n.tr("config.restore"))
        if getattr(self, "_migrate_btn", None) is not None:
            self._migrate_btn.setText(i18n.tr("config.migrate"))
        if getattr(self, "_hint_label", None) is not None:
            self._hint_label.setText(i18n.tr("config.hint"))

    def on_navigate_to(self):
        self._load_snapshots()
        self._refresh_blender_versions()

    def _load_snapshots(self):
        """从本地加载所有配置快照"""
        self._snapshots.clear()
        self._snapshot_list.clear()

        if self.ctx is None:
            return

        snapshots_dir = Path(self.ctx.config_service.base_dir) / "config_snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        for json_file in sorted(snapshots_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                snapshot = ConfigSnapshot.from_dict(data)
                self._snapshots.append(snapshot)

                item = QListWidgetItem()
                item.setText(f"{snapshot.name}")
                item.setData(Qt.ItemDataRole.UserRole, snapshot.id)

                # 副标题
                sub = f"Blender {snapshot.blender_version}  |  {snapshot.created_at}"
                item.setToolTip(sub)

                self._snapshot_list.addItem(item)
            except (json.JSONDecodeError, OSError):
                pass

    def _refresh_blender_versions(self):
        """刷新版本选择器"""
        if self.ctx is None or self._blender_version_combo is None:
            return

        self._blender_version_combo.clear()
        self._blender_version_combo.addItem("自动检测...")

        installations = self.ctx.blender_service.detect_installed()
        for inst in installations:
            self._blender_version_combo.addItem(f"Blender {inst.version}")

    def _on_snapshot_selected(self, current, previous):
        if current is None:
            self._detail_area.clear()
            return

        snapshot_id = current.data(Qt.ItemDataRole.UserRole)
        snapshot = next((s for s in self._snapshots if s.id == snapshot_id), None)
        if snapshot is None:
            return

        # 显示详情
        detail = (
            f"快照名称: {snapshot.name}\n"
            f"Blender 版本: {snapshot.blender_version}\n"
            f"创建时间: {snapshot.created_at}\n"
            f"描述: {snapshot.description}\n"
            f"──────────────────────\n"
        )

        data = snapshot.config_data or {}
        if data.get("kind") == "inventory":
            detail += f"配置目录: {data.get('config_dir', '')}\n"
            detail += f"目录存在: {'是' if data.get('exists') else '否'}\n"
            files = data.get("files", []) or []
            addons = data.get("addons", []) or []
            detail += f"配置文件: {len(files)} 个\n"
            for item in files[:12]:
                detail += f"  • {item.get('path')}  ({item.get('size', 0)} B)\n"
            if len(files) > 12:
                detail += f"  … 其余 {len(files) - 12} 个\n"
            detail += f"插件目录: {len(addons)} 个\n"
            for name in addons[:12]:
                detail += f"  • {name}\n"
            if len(addons) > 12:
                detail += f"  … 其余 {len(addons) - 12} 个\n"
        elif data:
            detail += "包含配置项:\n"
            for key in data.keys():
                detail += f"  • {key}\n"
        else:
            detail += "(空快照)\n"

        detail += "\n💡 快照仅记录配置清单，恢复 / 迁移为规划功能。"
        self._detail_area.setText(detail)

    def _collect_config_inventory(self, version: str) -> dict:
        """读取 Blender 用户配置目录的真实清单（只读，不改动任何文件）。"""
        cfg_dir = _blender_config_dir(version)
        inventory: dict = {
            "kind": "inventory",
            "config_dir": str(cfg_dir),
            "exists": cfg_dir.is_dir(),
            "files": [],
            "addons": [],
        }
        if not cfg_dir.is_dir():
            return inventory
        try:
            for f in sorted(cfg_dir.rglob("*")):
                if f.is_file():
                    inventory["files"].append({
                        "path": str(f.relative_to(cfg_dir)).replace("\\", "/"),
                        "size": f.stat().st_size,
                    })
        except OSError:
            pass
        addons_dir = cfg_dir.parent / "scripts" / "addons"
        if addons_dir.is_dir():
            try:
                inventory["addons"] = sorted(
                    p.name for p in addons_dir.iterdir()
                    if p.is_dir() and not p.name.startswith("__")
                )
            except OSError:
                pass
        return inventory

    def _create_snapshot(self):
        """创建新的配置快照"""
        if self.ctx is None:
            return

        # 获取快照名称
        name, ok = QInputDialog.getText(
            self._widget, "新建快照", "请输入快照名称:",
            text=f"配置快照 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )
        if not ok or not name:
            return

        # 获取描述
        desc, ok = QInputDialog.getText(
            self._widget, "快照描述", "请输入描述（可选）:",
            text="",
        )
        if not ok:
            return

        # 获取 Blender 版本
        version_text = self._blender_version_combo.currentText()
        if version_text == "自动检测...":
            installations = self.ctx.blender_service.detect_installed()
            blender_version = installations[0].version if installations else "unknown"
        else:
            blender_version = version_text.replace("Blender ", "")

        # 真实清单（只读）：配置目录文件 + 插件目录名
        config_data = self._collect_config_inventory(blender_version)

        snapshot = ConfigSnapshot(
            snapshot_id=str(uuid.uuid4()),
            name=name,
            blender_version=blender_version,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            description=desc,
            config_data=config_data,
        )

        # 保存到文件
        snapshots_dir = Path(self.ctx.config_service.base_dir) / "config_snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        file_path = snapshots_dir / f"{snapshot.id}.json"
        file_path.write_text(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        self.ctx.log_service.info(f"Created config snapshot: {name}", module="config_center")
        self.ctx.event_bus.emit("config.snapshot_created", {
            "snapshot_id": snapshot.id,
            "name": name,
            "blender_version": blender_version,
        })

        self._load_snapshots()

    def _restore_snapshot(self):
        """恢复配置：尚未实现，明确告知而不是假装成功。"""
        QMessageBox.information(
            self._widget, "规划功能",
            "恢复配置尚未实现。\n\n当前版本只记录配置清单，不会写回 Blender 配置目录。",
        )

    def _migrate_config(self):
        """跨版本迁移：尚未实现。"""
        QMessageBox.information(
            self._widget, "规划功能",
            "跨版本迁移尚未实现。\n\n当前版本只记录配置清单，不会改动 Blender 配置。",
        )

    def _delete_snapshot(self):
        """删除选中的配置快照"""
        current_item = self._snapshot_list.currentItem()
        if current_item is None:
            QMessageBox.information(self._widget, "提示", "请先选择一个配置快照")
            return

        snapshot_id = current_item.data(Qt.ItemDataRole.UserRole)
        snapshot = next((s for s in self._snapshots if s.id == snapshot_id), None)
        if snapshot is None:
            return

        reply = QMessageBox.question(
            self._widget, "确认删除",
            f"确定要删除配置快照「{snapshot.name}」吗？\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.ctx:
                snapshots_dir = Path(self.ctx.config_service.base_dir) / "config_snapshots"
                file_path = snapshots_dir / f"{snapshot.id}.json"
                file_path.unlink(missing_ok=True)

                self.ctx.log_service.info(f"Deleted config snapshot: {snapshot.name}", module="config_center")

            self._load_snapshots()
            self._detail_area.clear()

    @staticmethod
    def _version_lt(v1: str, v2: str) -> bool:
        try:
            p1 = [int(x) for x in v1.replace("-beta", "").split(".")]
            p2 = [int(x) for x in v2.replace("-beta", "").split(".")]
            return p1 < p2
        except (ValueError, IndexError):
            return False
