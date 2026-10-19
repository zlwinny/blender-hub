"""
配置服务 — 模块隔离的配置读写

特性：
- 每个模块有独立的 JSON 配置文件
- 支持默认值
- 自动创建配置目录
"""

import json
import os
from pathlib import Path
from typing import Any, Optional
from threading import RLock

import platformdirs


class ConfigService:
    """模块隔离的 JSON 配置管理"""

    def __init__(self, app_name: str = "BlenderHub", app_author: str = "BlenderHub Team"):
        self._base_dir = Path(platformdirs.user_config_dir(app_name, app_author))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._cache: dict = {}

    def _config_path(self, module_id: str) -> Path:
        return self._base_dir / f"{module_id}_config.json"

    def get(self, module_id: str, key: str, default: Any = None) -> Any:
        """读取模块配置项"""
        with self._lock:
            data = self._load_module_config(module_id)
            return data.get(key, default)

    def set(self, module_id: str, key: str, value: Any):
        """设置模块配置项"""
        with self._lock:
            data = self._load_module_config(module_id)
            data[key] = value
            self._save_module_config(module_id, data)

    def get_all(self, module_id: str) -> dict:
        """获取模块全部配置"""
        with self._lock:
            return dict(self._load_module_config(module_id))

    def set_all(self, module_id: str, data: dict):
        """批量设置模块配置"""
        with self._lock:
            self._save_module_config(module_id, dict(data))

    def delete(self, module_id: str, key: str):
        """删除配置项"""
        with self._lock:
            data = self._load_module_config(module_id)
            data.pop(key, None)
            self._save_module_config(module_id, data)

    def clear_module(self, module_id: str):
        """清空模块全部配置"""
        path = self._config_path(module_id)
        with self._lock:
            path.unlink(missing_ok=True)
            self._cache.pop(module_id, None)

    def _load_module_config(self, module_id: str) -> dict:
        if module_id in self._cache:
            return self._cache[module_id]
        path = self._config_path(module_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                # 保留损坏文件，避免下一次写入把原始数据彻底覆盖
                try:
                    path.replace(path.with_name(path.name + ".corrupt"))
                except OSError:
                    pass
                data = {}
        else:
            data = {}
        self._cache[module_id] = data
        return data

    def _save_module_config(self, module_id: str, data: dict):
        path = self._config_path(module_id)
        # 原子写：先写临时文件再替换，避免崩溃/断电产生截断的 JSON
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp_path, path)
        self._cache[module_id] = data

    @property
    def base_dir(self) -> Path:
        return self._base_dir
