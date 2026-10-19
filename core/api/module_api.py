"""
模块基类 + 模块上下文接口

每个功能模块继承 BaseModule，通过 ModuleContext 获取核心服务。
"""

from abc import ABC, abstractmethod
from typing import Optional
from PyQt6.QtWidgets import QWidget


class ModuleContext:
    """核心层注入给模块的上下文对象"""

    def __init__(
        self,
        event_bus=None,
        config_service=None,
        log_service=None,
        download_service=None,
        blender_service=None,
        auth_service=None,
        budeco_auth=None,
        main_window=None,
    ):
        self.event_bus = event_bus
        self.config_service = config_service
        self.log_service = log_service
        self.download_service = download_service
        self.blender_service = blender_service
        self.auth_service = auth_service
        self.budeco_auth = budeco_auth
        # 主窗口引用：模块要把下载进度显示到全局状态条时用（show_download/hide_download）。
        # 旧实现没有这个字段，version_library 的下载回调第一行就 AttributeError，
        # 表现为"下载完成了但安装从不开始"。
        self.main_window = main_window

    # ── 旧组件命名兼容：设置页等既有 QWidget 同时接受 AppContext / ModuleContext ──
    @property
    def config(self):
        return self.config_service

    def info(self, msg: str, module: str = "app") -> None:
        if self.log_service:
            self.log_service.info(msg, module=module)

    def warn(self, msg: str, module: str = "app") -> None:
        if self.log_service:
            self.log_service.warning(msg, module=module)


class BaseModule(ABC):
    """
    模块基类 — 所有功能模块必须继承

    生命周期：
    1. __init__()         → 构造
    2. initialize(ctx)    → 注入核心上下文
    3. get_widget()       → 返回 UI（首次导航时调用）
    4. on_navigate_to()   → 用户进入模块
    5. on_navigate_away() → 用户离开模块
    6. on_shutdown()      → Hub 关闭
    """

    def __init__(self):
        self.id: str = ""
        self.name: str = ""
        self.icon: str = "📦"  # 默认图标
        self.ctx: Optional[ModuleContext] = None

    @abstractmethod
    def get_widget(self) -> QWidget:
        """返回模块的主界面 QWidget"""
        ...

    def initialize(self, context: ModuleContext):
        """接收核心层注入的上下文"""
        self.ctx = context
        if context.log_service:
            context.log_service.info(f"Module initialized: {self.id}", module="module_manager")

    def on_navigate_to(self):
        """用户导航到此模块时触发"""
        pass

    def on_navigate_away(self):
        """用户离开此模块时触发"""
        pass

    def on_shutdown(self):
        """Hub 关闭时触发"""
        pass

    # ─── 热更新状态快照 ──────────────────────────

    def get_state_snapshot(self) -> dict:
        """
        返回当前模块的可序列化 UI 状态（供热更新使用）
        约束：返回值必须是纯 JSON 可序列化的 dict，大小 < 1MB
        """
        return {}

    def restore_state(self, state: dict):
        """
        根据快照恢复模块 UI 状态
        恢复失败不应崩溃，降级为默认状态
        """
        pass
