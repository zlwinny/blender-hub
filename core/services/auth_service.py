"""
认证服务 — 登录/Pro 权限（v1.0 占位，v1.5 正式实现）

v1.0: 本地优先，无需登录，is_pro 永远 False
"""

from typing import Optional
from dataclasses import dataclass


@dataclass
class UserInfo:
    user_id: str = ""
    username: str = ""
    is_pro: bool = False
    is_logged_in: bool = False


class AuthService:
    """用户认证服务"""

    def __init__(self, log_service=None):
        self._log = log_service
        self._user: Optional[UserInfo] = None
        self._listeners: list = []

    @property
    def current_user(self) -> UserInfo:
        if self._user is None:
            self._user = UserInfo()
        return self._user

    @property
    def is_logged_in(self) -> bool:
        return self.current_user.is_logged_in

    @property
    def is_pro(self) -> bool:
        return self.current_user.is_pro

    def login(self, username: str, password: str = "") -> bool:
        """登录（v1.5 实现）"""
        # TODO: v1.5 实现 OAuth/邮箱登录
        if self._log:
            self._log.info(f"Login attempt: {username} (not implemented in v1.0)", module="auth_service")
        return False

    def logout(self):
        self._user = UserInfo()
        for listener in self._listeners:
            try:
                listener()
            except Exception:
                pass

    def add_listener(self, callback):
        """登录状态变化监听"""
        self._listeners.append(callback)

    def remove_listener(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)
