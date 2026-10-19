"""应用上下文 — 装配 Blender Hub 全部核心服务 + 模块管理器。

保留 v1 的全部服务（含 auth 登录注册、signature 模块签名校验、ModuleManager 动态模块），
同时为 v2 新窗口提供 bus / info / warn / error 适配。
"""

from __future__ import annotations

from .module_manager import ModuleManager
from .services.auth_service import AuthService
from .services.blender_service import BlenderService
from .services.budeco_auth import BudecoAuthService
from .services.config_service import ConfigService
from .services.download_service import DownloadService
from .services.event_bus import EventBus
from .services.log_service import LogService
from .services.signature_service import SignatureService


class AppContext:
    def __init__(self) -> None:
        self.log = LogService.instance()
        self.config = ConfigService()
        self.bus = EventBus(log_service=self.log)
        self.downloads = DownloadService(log_service=self.log, max_concurrent=3)
        self.blender = BlenderService(log_service=self.log)
        self.auth = AuthService(log_service=self.log)
        self.signature = SignatureService(log_service=self.log)
        self.budeco_auth = BudecoAuthService(log_service=self.log)

        self.module_manager = ModuleManager(
            event_bus=self.bus,
            config_service=self.config,
            log_service=self.log,
            download_service=self.downloads,
            blender_service=self.blender,
            auth_service=self.auth,
            budeco_auth=self.budeco_auth,
            signature_service=self.signature,  # load_module 时做 manifest 完整性校验
        )
        self.log.info("应用上下文已初始化", module="app")

        # 主窗口引用：模块需要它来驱动底部下载条等全局 UI 反馈。
        # 旧实现里 show_download/hide_download 零调用点就是因为模块拿不到 MainWindow。
        self.main_window = None

    def set_main_window(self, window) -> None:
        self.main_window = window
        # 同步给模块上下文：version_library 等模块通过 ctx.main_window 调用
        # show_download/hide_download 显示下载进度。不同步的话模块侧拿不到，
        # 下载回调第一行就 AttributeError（表现为"下载完但不安装"）。
        try:
            self.module_manager.set_main_window(window)
        except AttributeError:
            pass

    # ── 旧模块 ModuleContext 命名兼容 ──
    @property
    def event_bus(self):
        return self.bus

    @property
    def config_service(self):
        return self.config

    @property
    def log_service(self):
        return self.log

    @property
    def download_service(self):
        return self.downloads

    @property
    def blender_service(self):
        return self.blender

    # ── 新窗口便捷转发 ──
    def info(self, msg: str, module: str = "app") -> None:
        self.log.info(msg, module=module)

    def warn(self, msg: str, module: str = "app") -> None:
        self.log.warning(msg, module=module)

    def error(self, msg: str, module: str = "app") -> None:
        self.log.error(msg, module=module)
