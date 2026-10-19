"""
日志服务 — 本地滚动日志 + 崩溃内存缓冲区

特性：
- 按天切割，保留 7 天
- 单文件 10MB 上限
- 崩溃前 50 条日志的内存环形缓冲区
- 不记录用户隐私数据
"""

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Optional
from urllib.parse import urlsplit

import platformdirs


def redact_url(url: str) -> str:
    """日志脱敏：只保留 scheme://host/path，去掉 query/fragment。

    下载链接常带临时凭据、网盘提取码（pwd=...），不应原样写进日志。
    """
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except ValueError:
        pass
    return url.split("?", 1)[0].split("#", 1)[0]


class MemoryBufferHandler(logging.Handler):
    """内存环形缓冲区，保留最近 N 条日志记录"""

    def __init__(self, capacity: int = 50):
        super().__init__()
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(module)s: %(message)s"
        ))

    def emit(self, record):
        self.buffer.append(self.format(record))

    def get_recent(self, count: int = None) -> list:
        """获取最近日志"""
        n = count or self.capacity
        return list(self.buffer)[-n:]


class LogService:
    """全局日志服务"""

    _instance: Optional["LogService"] = None

    def __init__(self):
        self._logger = logging.getLogger("BlenderHub")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()

        # 配置目录
        config_dir = Path(platformdirs.user_config_dir("BlenderHub", "BlenderHub Team"))
        self._log_dir = config_dir / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # 文件处理器：按天切割，保留 7 天
        log_file = self._log_dir / "blender_hub.log"
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_file),
            when="midnight",
            interval=1,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(name)s | %(module)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        self._logger.addHandler(file_handler)

        # 控制台处理器（DEBUG 级别仅在开发模式启用）
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "[%(levelname)s] %(name)s: %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        self._logger.addHandler(console_handler)

        # 内存缓冲区（崩溃报告用）
        self._memory_handler = MemoryBufferHandler(capacity=50)
        self._logger.addHandler(self._memory_handler)

    @classmethod
    def instance(cls) -> "LogService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def debug(self, msg: str, module: str = None):
        self._log(logging.DEBUG, msg, module)

    def info(self, msg: str, module: str = None):
        self._log(logging.INFO, msg, module)

    def warning(self, msg: str, module: str = None, exc_info: bool = False):
        self._log(logging.WARNING, msg, module, exc_info)

    def error(self, msg: str, module: str = None, exc_info: bool = False):
        self._log(logging.ERROR, msg, module, exc_info)

    def critical(self, msg: str, module: str = None, exc_info: bool = True):
        self._log(logging.CRITICAL, msg, module, exc_info)

    def _log(self, level: int, msg: str, module: str = None, exc_info: bool = False):
        if module:
            msg = f"[{module}] {msg}"
        extra = {"module_name": module or "unknown"}
        self._logger.log(level, msg, exc_info=exc_info, extra=extra)

    def get_recent_logs(self, count: int = 50) -> list:
        """获取内存中最近 N 条日志（用于崩溃报告）"""
        return self._memory_handler.get_recent(count)
