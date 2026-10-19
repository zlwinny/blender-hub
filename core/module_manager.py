"""
模块管理器 — 扫描/注册/生命周期管理

扫描 modules/ 目录，读取 manifest.json，加载模块。
新增模块 = 新建文件夹 + 写 manifest.json，无需改核心代码。
"""

from __future__ import annotations

import json
import importlib
import sys
from pathlib import Path
from typing import Any

from core.api.module_api import BaseModule, ModuleContext  # pyright: ignore[reportImplicitRelativeImport]
from core.services.event_bus import EventBus  # pyright: ignore[reportImplicitRelativeImport]
from core.services.config_service import ConfigService  # pyright: ignore[reportImplicitRelativeImport]
from core.services.log_service import LogService  # pyright: ignore[reportImplicitRelativeImport]
from core.services.download_service import DownloadService  # pyright: ignore[reportImplicitRelativeImport]
from core.services.blender_service import BlenderService  # pyright: ignore[reportImplicitRelativeImport]
from core.services.auth_service import AuthService  # pyright: ignore[reportImplicitRelativeImport]
from core.services.signature_service import SignatureService  # pyright: ignore[reportImplicitRelativeImport]


class ModuleInfo:
    """模块元信息"""
    def __init__(self, manifest: dict[str, Any], module_path: Path) -> None:  # pyright: ignore[reportExplicitAny]
        self.id: str = manifest.get("id", "")
        self.name: str = manifest.get("name", "")
        self.version: str = manifest.get("version", "0.0.0")
        self.author: str = manifest.get("author", "")
        self.description: str = manifest.get("description", "")
        self.min_hub_version: str = manifest.get("min_hub_version", "1.0.0")
        self.dependencies: list[str] = manifest.get("dependencies", [])
        self.entry_point: str = manifest.get("entry_point", "")
        self.permissions: list[str] = manifest.get("permissions", [])
        self.features: list[str] = manifest.get("features", [])
        # 侧边栏顺序：manifest 可选 "order" 字段，未声明时排在有声明之后（同序保持目录名顺序）
        try:
            self.order: int = int(manifest.get("order", 100))
        except (TypeError, ValueError):
            self.order = 100
        self.module_path: Path = module_path
        self.instance: BaseModule | None = None


class ModuleManager:
    """模块生命周期管理"""

    def __init__(
        self,
        modules_dir: Path | None = None,
        event_bus: EventBus | None = None,
        config_service: ConfigService | None = None,
        log_service: LogService | None = None,
        download_service: DownloadService | None = None,
        blender_service: BlenderService | None = None,
        auth_service: AuthService | None = None,
        budeco_auth: "BudecoAuthService | None" = None,  # pyright: ignore[reportUndefinedVariable]
        signature_service: "SignatureService | None" = None,
        main_window=None,
    ) -> None:
        if modules_dir:
            self._modules_dir: Path = modules_dir
        elif getattr(sys, 'frozen', False):
            self._modules_dir = Path(sys._MEIPASS) / "modules"  # type: ignore[attr-defined]  # pyright: ignore[reportAny, reportUnknownMemberType]
        else:
            self._modules_dir = Path(__file__).resolve().parent.parent / "modules"
        self._modules: dict[str, ModuleInfo] = {}
        self._loaded_order: list[str] = []
        self._context: ModuleContext = ModuleContext(
            event_bus=event_bus,
            config_service=config_service,
            log_service=log_service,
            download_service=download_service,
            blender_service=blender_service,
            auth_service=auth_service,
            budeco_auth=budeco_auth,
            main_window=main_window,
        )
        self._log: LogService | None = log_service
        # 完整性校验服务：load_module 时做manifest 校验，失败拒绝加载
        self._signature_service: SignatureService | None = signature_service
        self._main_window = main_window

    def set_main_window(self, window) -> None:
        """主窗口就绪后回填（MainWindow 在 AppContext 之后才创建）。"""
        self._main_window = window
        self._context.main_window = window

    def discover(self) -> list[ModuleInfo]:
        """扫描 modules/ 目录，发现所有有效模块"""
        self._modules.clear()
        self._loaded_order.clear()

        if not self._modules_dir.exists():
            if self._log:
                self._log.warning(f"Modules directory not found: {self._modules_dir}", module="module_manager")
            return []

        for item in sorted(self._modules_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue

            manifest_path = item / "manifest.json"
            if not manifest_path.exists():
                if self._log:
                    self._log.warning(f"Module '{item.name}' missing manifest.json", module="module_manager")
                continue

            try:
                manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))  # pyright: ignore[reportExplicitAny, reportAny]
                info = ModuleInfo(manifest, item)

                if not info.id or not info.entry_point:
                    if self._log:
                        self._log.warning(f"Module '{item.name}' has invalid manifest", module="module_manager")
                    continue

                self._modules[info.id] = info
                self._loaded_order.append(info.id)

                if self._log:
                    self._log.info(f"Discovered module: {info.name} ({info.id}) v{info.version}", module="module_manager")

            except (json.JSONDecodeError, IOError) as e:
                if self._log:
                    self._log.error(f"Failed to load manifest for '{item.name}': {e}", module="module_manager")

        # 按 manifest 的 order 排序（稳定排序：未声明 order 的保持目录名顺序）
        self._loaded_order.sort(key=lambda mid: self._modules[mid].order)

        if self._log:
            self._log.info(f"Discovered {len(self._modules)} module(s)", module="module_manager")

        return self.get_all_modules()

    def load_module(self, module_id: str) -> BaseModule | None:
        """加载并初始化指定模块"""
        info = self._modules.get(module_id)
        if info is None:
            if self._log:
                self._log.error(f"Module not found: {module_id}", module="module_manager")
            return None

        if info.instance is not None:
            return info.instance  # 已加载

        # 完整性校验：应用主打"模块签名安全"，但 verify_module 此前零调用点，
        # 整个安全模型是死代码。模块目录来自打包产物，若被替换/篡改即可注入任意代码。
        # 校验失败则拒绝加载。
        if self._signature_service is not None:
            try:
                if not self._signature_service.verify_module(info.module_path):
                    if self._log:
                        self._log.error(
                            f"Module integrity check failed, refusing to load: {info.id}",
                            module="module_manager",
                        )
                    return None
            except Exception as e:  # noqa: BLE001
                # 校验器自身异常时按"不信任"处理，避免校验被绕过
                if self._log:
                    self._log.error(
                        f"Integrity check raised for '{info.id}', refusing to load: {e}",
                        module="module_manager",
                    )
                return None

        try:
            # 解析 entry_point: "module:ClassName"
            if ":" not in info.entry_point:
                raise ValueError(f"Invalid entry_point: {info.entry_point}")

            module_name, class_name = info.entry_point.split(":", 1)

            # 将 modules 父目录加入 sys.path，并支持 "addon_store.module" 这类
            # 相对于 modules/ 的入口名（旧实现插入父目录却期望 "modules.addon_store.module"，
            # 两者不自洽；同时 4 个模块都叫 module.py，同名子模块在 sys.path 上会互相覆盖）。
            modules_dir = str(info.module_path.parent)
            if modules_dir not in sys.path:
                sys.path.insert(0, modules_dir)
            package_root = str(info.module_path.parent.parent)
            if package_root not in sys.path:
                sys.path.insert(0, package_root)

            # 动态导入。
            #
            # 冻结环境（PyInstaller）下的实际布局：
            #   _internal/modules/addon_store/  只有 __init__.py + manifest.json（作为 data 分发）
            #   module.py 本体被编译进 base_library.zip / PYZ，即"modules.addon_store.module"
            # 因此必须用**完整包路径** modules.<pkg>.module 导入。
            #
            # 开发环境则相反：module.py 是真实文件，modules/ 目录里有 __init__.py，
            # 两种路径都能导入。优先用完整包路径，失败再回退到模块自身推导的路径。
            module_dir_name = info.module_path.parent.name
            candidates = [
                module_name,  # manifest 里声明的 modules.addon_store.module（冻结环境唯一可用）
                f"modules.{module_dir_name}.module",
                f"{module_dir_name}.module",
            ]
            mod = None
            last_error: Exception | None = None
            for candidate in candidates:
                try:
                    mod = importlib.import_module(candidate)
                    break
                except ImportError as e:
                    last_error = e
            if mod is None:
                raise ImportError(
                    f"Cannot import module for '{module_dir_name}'; tried {candidates}"
                ) from last_error

            cls: type[BaseModule] = getattr(mod, class_name)  # pyright: ignore[reportAny]

            # 实例化
            instance: BaseModule = cls()
            instance.id = info.id
            instance.name = info.name
            instance.initialize(self._context)

            info.instance = instance

            if self._log:
                self._log.info(f"Loaded module: {info.name} ({info.id})", module="module_manager")

            return instance

        except Exception as e:
            if self._log:
                self._log.error(f"Failed to load module '{module_id}': {e}", module="module_manager", exc_info=True)
            return None

    def load_all(self) -> list[BaseModule]:
        """加载所有已发现的模块"""
        instances: list[BaseModule] = []
        for module_id in self._loaded_order:
            instance = self.load_module(module_id)
            if instance:
                instances.append(instance)
        return instances

    def get_module(self, module_id: str) -> ModuleInfo | None:
        return self._modules.get(module_id)

    def get_all_modules(self) -> list[ModuleInfo]:
        return [self._modules[mid] for mid in self._loaded_order if mid in self._modules]

    def get_loaded_modules(self) -> list[BaseModule]:
        return [
            info.instance for info in self._modules.values()
            if info.instance is not None
        ]

    def get_module_widget(self, module_id: str) -> Any | None:  # pyright: ignore[reportExplicitAny]
        """获取模块的 QWidget（返回 QWidget，但避免 PyQt6 循环导入）"""
        instance = self.load_module(module_id)
        if instance is None:
            return None
        return instance.get_widget()

    def shutdown_all(self) -> None:
        """关闭所有已加载的模块"""
        for info in self._modules.values():
            if info.instance is not None:
                try:
                    info.instance.on_shutdown()
                except Exception as e:
                    if self._log:
                        self._log.error(f"Error shutting down module '{info.id}': {e}", module="module_manager")

    def collect_state_snapshots(self) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
        """收集所有模块的状态快照（供热更新使用）"""
        snapshots: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]
        for info in self._modules.values():
            if info.instance is not None:
                try:
                    state = info.instance.get_state_snapshot()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
                    if state:
                        snapshots[info.id] = state
                except (TypeError, AttributeError):
                    pass
        return snapshots

    def restore_state_snapshots(self, snapshots: dict[str, Any]) -> None:  # pyright: ignore[reportExplicitAny]
        """恢复所有模块的状态快照"""
        for module_id, state in snapshots.items():  # pyright: ignore[reportAny]
            info = self._modules.get(module_id)
            if info and info.instance and state:
                try:
                    info.instance.restore_state(state)  # pyright: ignore[reportUnknownMemberType, reportAny]
                except (TypeError, AttributeError):
                    pass
