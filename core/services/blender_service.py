"""
Blender 服务 — 本地 Blender 检测/启动/版本解析

特性：
- 自动扫描常见安装目录 + Windows 注册表 + Steam + 自定义路径
- 深度递归扫描（支持便携版、多版本共存）
- 跨平台路径抽象
- 读取 Blender 版本号
"""

import subprocess
import sys
import re
import os
import json
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict


_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _version_from_name(name: str) -> Optional[str]:
    """从目录/文件名里解析版本号。

    Blender 的目录名几乎总是带版本号：
      `blender-4.2.23-windows-x64`（官方压缩包）、`Blender 4.2`（安装器）、
      `blender-3.6.5-linux-x64`、`4.2`（版本库自己的安装目录）。
    零成本、零进程，作为版本检测的第一选择。
    """
    if not name:
        return None
    m = _VERSION_RE.search(name)
    return m.group(1) if m else None


def _pe_file_version(path: str) -> Optional[str]:
    """读 Windows PE 文件的版本资源（FileVersion），不启动进程、不弹窗。

    失败（非 Windows / 无版本资源 / 读不了）一律返回 None，调用方继续兜底。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version.dll")
        ver.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [
            ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        ver.VerQueryValueW.restype = wintypes.BOOL

        dummy = wintypes.DWORD(0)
        size = ver.GetFileVersionInfoSizeW(str(path), ctypes.byref(dummy))
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
            ]

        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\\\", ctypes.byref(ptr), ctypes.byref(length)):
            return None
        info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        major = (info.dwFileVersionMS >> 16) & 0xFFFF
        minor = info.dwFileVersionMS & 0xFFFF
        patch = (info.dwFileVersionLS >> 16) & 0xFFFF
        if (major, minor) == (0, 0):
            return None
        return f"{major}.{minor}.{patch}"
    except Exception:  # noqa: BLE001
        return None


class BlenderInstallation:
    """已安装的 Blender 版本信息"""
    def __init__(self, version: str, path: Path, executable: Path, is_lts: bool = False,
                 source: str = "auto"):
        self.version = version          # "4.2.0"
        self.path = path                # 安装目录
        self.executable = executable    # blender.exe 路径
        self.is_lts = is_lts
        self.source = source            # 发现来源: "auto" | "registry" | "steam" | "custom" | "portable"
        self.label = f"Blender {version}{' LTS' if is_lts else ''}"

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "path": str(self.path),
            "executable": str(self.executable),
            "is_lts": self.is_lts,
            "label": self.label,
            "source": self.source,
        }

    @staticmethod
    def from_dict(d: dict) -> "BlenderInstallation":
        return BlenderInstallation(
            version=d["version"],
            path=Path(d["path"]),
            executable=Path(d["executable"]),
            is_lts=d.get("is_lts", False),
            source=d.get("source", "auto"),
        )


class BlenderService:
    """Blender 版本检测与启动"""

    # 常见 Blender 安装路径搜索模式
    _SEARCH_DIRS_WINDOWS = [
        # Program Files 各盘符
        "C:/Program Files/Blender Foundation/Blender*",
        "C:/Program Files/Blender*",
        "C:/Program Files (x86)/Blender Foundation/Blender*",
        "D:/Program Files/Blender Foundation/Blender*",
        "D:/Program Files/Blender*",
        "D:/Program Files (x86)/Blender Foundation/Blender*",
        "E:/Program Files/Blender Foundation/Blender*",
        "E:/Program Files/Blender*",
        # 根目录便携版
        "C:/Blender*",
        "D:/Blender*",
        "E:/Blender*",
        "F:/Blender*",
        "G:/Blender*",
        # 用户目录便携版
        "~/Blender*",
        "~/Desktop/Blender*",
        "~/Downloads/Blender*",
        "~/Documents/Blender*",
        # 工具盘
        "D:/Tools/Blender*",
        "E:/Tools/Blender*",
    ]

    _SEARCH_DIRS_MACOS = [
        "/Applications/Blender*.app",
        "~/Applications/Blender*.app",
        "~/Downloads/Blender*.app",
        "~/Desktop/Blender*.app",
    ]

    _SEARCH_DIRS_LINUX = [
        "/usr/share/blender*",
        "/usr/local/blender*",
        "/opt/blender*",
        "~/blender*",
        "~/Downloads/blender*",
        "~/Desktop/blender*",
    ]

    def __init__(self, log_service=None):
        self._log = log_service
        self._cancel_flag = threading.Event()
        # 扫描结果缓存：detect_installed 会对每个候选 exe 跑 `blender.exe --version`
        # （subprocess + 10s 超时），全盘深扫可达数分钟。三个模块都在 GUI 线程同步调用它，
        # 导致切换页面时界面冻结。这里加 TTL 缓存，让重复调用走内存而非重扫。
        self._cache: dict = {}          # (deep_scan, tuple(extra_dirs)) -> (timestamp, List[BlenderInstallation])
        self._cache_lock = threading.Lock()
        self._cache_ttl = 60.0          # 秒；深度扫描结果缓存更久（全盘 IO 代价高）
        self._cache_ttl_deep = 600.0
        # 由本服务启动的 Blender 子进程句柄（launch 持有，退出时 terminate_all 回收）
        self._launched: list[tuple[subprocess.Popen, str]] = []
        self._proc_lock = threading.Lock()

    def clear_cache(self) -> None:
        """清空扫描缓存（安装/卸载 Blender 后应调用，否则 60s 内看不到变化）"""
        with self._cache_lock:
            self._cache.clear()
        if self._log:
            self._log.debug("Blender detection cache cleared", module="blender_service")

    def cancel_scan(self):
        """取消正在进行的扫描"""
        self._cancel_flag.set()

    def reset_cancel(self):
        """重置取消标志（新扫描前调用）"""
        self._cancel_flag.clear()

    def _is_cancelled(self) -> bool:
        return self._cancel_flag.is_set()

    # ─── 完整扫描（自动 + 注册表 + Steam + 深度递归） ───

    def detect_installed(self, deep_scan: bool = False, extra_dirs: List[str] = None,
                         use_cache: bool = True) -> List[BlenderInstallation]:
        """
        全面检测已安装的 Blender 版本

        Args:
            deep_scan: 是否启用深度递归扫描（搜索整个磁盘常见目录）
            extra_dirs: 额外搜索的自定义目录列表
            use_cache: 是否使用 TTL 缓存（GUI 线程调用应保持 True；
                       安装/卸载后需clear_cache() 或传 use_cache=False 强制重扫）
        """
        cache_key = (deep_scan, tuple(sorted(extra_dirs)) if extra_dirs else ())

        if use_cache:
            with self._cache_lock:
                hit = self._cache.get(cache_key)
                if hit is not None:
                    ts, cached = hit
                    ttl = self._cache_ttl_deep if deep_scan else self._cache_ttl
                    if time.time() - ts < ttl:
                        if self._log:
                            self._log.debug(
                                f"detect_installed cache hit (deep={deep_scan}, "
                                f"age={time.time() - ts:.1f}s)", module="blender_service"
                            )
                        return list(cached)

        result = self._detect_installed_uncached(deep_scan, extra_dirs)

        if use_cache:
            with self._cache_lock:
                self._cache[cache_key] = (time.time(), list(result))
        return result

    def _detect_installed_uncached(self, deep_scan: bool, extra_dirs: Optional[List[str]]) -> List[BlenderInstallation]:
        """实际执行扫描（无缓存）。耗时长，应在后台线程调用。"""
        installations = []
        executable_name = "blender.exe" if sys.platform == "win32" else "blender"

        # ── 1. 固定路径 glob 扫描 ──
        if self._is_cancelled():
            return []
        search_dirs = []
        if sys.platform == "win32":
            search_dirs = self._SEARCH_DIRS_WINDOWS
        elif sys.platform == "darwin":
            search_dirs = self._SEARCH_DIRS_MACOS
        else:
            search_dirs = self._SEARCH_DIRS_LINUX

        import glob
        for pattern in search_dirs:
            if self._is_cancelled():
                break
            expanded = str(Path(pattern).expanduser().resolve())
            try:
                for d in glob.glob(expanded):
                    if self._is_cancelled():
                        break
                    d = Path(d)
                    if d.is_dir():
                        self._scan_directory(d, executable_name, installations, source="auto")
            except Exception:
                pass

        # ── 2. Windows 注册表扫描 ──
        if sys.platform == "win32" and not self._is_cancelled():
            installations.extend(self._scan_registry())

        # ── 3. Steam 库扫描 ──
        if not self._is_cancelled():
            installations.extend(self._scan_steam_library(executable_name))

        # ── 4. PATH 环境变量扫描 ──
        if not self._is_cancelled():
            installations.extend(self._scan_path_env(executable_name))

        # ── 5. 深度递归扫描（可选） ──
        if deep_scan and not self._is_cancelled():
            installations.extend(self._deep_scan_common_drives(executable_name))

        # ── 6. 自定义目录扫描 ──
        if extra_dirs and not self._is_cancelled():
            for custom_dir in extra_dirs:
                if self._is_cancelled():
                    break
                d = Path(custom_dir).expanduser().resolve()
                if d.exists():
                    if d.is_file() and d.name.lower() == executable_name.lower():
                        # 直接指定了 exe
                        parent = d.parent
                        version = self._read_version(d)
                        if version:
                            installations.append(BlenderInstallation(
                                version=version, path=parent, executable=d,
                                is_lts=self._is_lts(version), source="custom",
                            ))
                    elif d.is_dir():
                        self._scan_directory(d, executable_name, installations, source="custom",
                                             recursive=True)

        # 去重（优先保留版本号最完整的，相同版本保留 source 优先级高的）
        source_priority = {"auto": 0, "registry": 1, "steam": 2, "custom": 3, "portable": 4}
        by_version: Dict[str, List[BlenderInstallation]] = {}
        for inst in installations:
            by_version.setdefault(inst.version, []).append(inst)

        unique = []
        for version, insts in by_version.items():
            # 按 source 优先级排序，取最高优先级的
            insts.sort(key=lambda x: source_priority.get(x.source, 0), reverse=True)
            unique.append(insts[0])

        if self._log and not self._is_cancelled():
            self._log.info(f"Detected {len(unique)} Blender installation(s) (deep_scan={deep_scan})",
                           module="blender_service")

        return sorted(unique, key=lambda x: self._version_tuple(x.version), reverse=True)

    def _scan_directory(self, d: Path, exe_name: str, results: list,
                        source: str = "auto", recursive: bool = False, depth: int = 0):
        """扫描目录下第一层或递归寻找 blender 可执行文件"""
        if depth > 3 or self._is_cancelled():
            return

        # 直接检查
        exe = d / exe_name
        if sys.platform == "darwin":
            exe = d / "Contents" / "MacOS" / "blender"
        if exe.exists():
            version = self._read_version(exe)
            if version:
                results.append(BlenderInstallation(
                    version=version, path=d, executable=exe,
                    is_lts=self._is_lts(version), source=source,
                ))
                return  # 找到就不继续递归

        if recursive:
            try:
                for child in d.iterdir():
                    if self._is_cancelled():
                        return
                    if child.is_dir():
                        self._scan_directory(child, exe_name, results,
                                             source=source, recursive=True, depth=depth + 1)
            except PermissionError:
                pass

    def _scan_registry(self) -> List[BlenderInstallation]:
        """Windows 注册表扫描 Blender 安装"""
        results = []
        try:
            import winreg
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlenderFoundation\Blender"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\BlenderFoundation\Blender"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\BlenderFoundation\Blender"),
            ]
            for hkey, subkey in reg_paths:
                try:
                    with winreg.OpenKey(hkey, subkey) as key:
                        i = 0
                        while True:
                            try:
                                ver_name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, ver_name) as ver_key:
                                    try:
                                        install_dir, _ = winreg.QueryValueEx(ver_key, "")
                                        d = Path(install_dir)
                                        exe = d / "blender.exe"
                                        if exe.exists():
                                            version = self._read_version(exe) or ver_name
                                            results.append(BlenderInstallation(
                                                version=version, path=d, executable=exe,
                                                is_lts=self._is_lts(version), source="registry",
                                            ))
                                    except OSError:
                                        pass
                                i += 1
                            except OSError:
                                break
                except OSError:
                    continue
        except ImportError:
            pass
        return results

    def _scan_steam_library(self, exe_name: str) -> List[BlenderInstallation]:
        """扫描 Steam 库中的 Blender"""
        results = []
        steam_dirs = []
        if sys.platform == "win32":
            steam_dirs = [
                Path("C:/Program Files (x86)/Steam/steamapps/common"),
                Path("D:/SteamLibrary/steamapps/common"),
                Path("E:/SteamLibrary/steamapps/common"),
            ]
        elif sys.platform == "darwin":
            steam_dirs = [
                Path.home() / "Library/Application Support/Steam/steamapps/common",
            ]
        else:
            steam_dirs = [
                Path.home() / ".steam/steam/steamapps/common",
                Path.home() / ".local/share/Steam/steamapps/common",
            ]

        for steam_dir in steam_dirs:
            blender_dir = steam_dir / "Blender"
            if blender_dir.is_dir():
                self._scan_directory(blender_dir, exe_name, results, source="steam", recursive=True)
        return results

    def _scan_path_env(self, exe_name: str) -> List[BlenderInstallation]:
        """扫描 PATH 环境变量中的 blender"""
        results = []
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            exe = Path(path_dir) / exe_name
            if exe.exists():
                version = self._read_version(exe)
                if version:
                    results.append(BlenderInstallation(
                        version=version, path=exe.parent, executable=exe,
                        is_lts=self._is_lts(version), source="auto",
                    ))
        return results

    def _deep_scan_common_drives(self, exe_name: str) -> List[BlenderInstallation]:
        """深度扫描：在常见盘符下搜索 Blender 目录"""
        results = []
        if sys.platform != "win32":
            return results

        # 扫描 C/D/E/F/G 盘根目录下名称包含 Blender 的文件夹
        for drive_letter in ["C", "D", "E", "F", "G"]:
            if self._is_cancelled():
                return results
            root = Path(f"{drive_letter}:/")
            if not root.exists():
                continue
            try:
                for entry in root.iterdir():
                    if self._is_cancelled():
                        return results
                    if entry.is_dir() and "blender" in entry.name.lower():
                        self._scan_directory(entry, exe_name, results,
                                             source="portable", recursive=True, depth=2)
            except PermissionError:
                continue
        return results

    # ─── 获取可下载版本列表 ───

    @staticmethod
    def get_available_versions(force: bool = False) -> Dict[str, list]:
        """获取可下载的 Blender 版本列表。

        force=True 时跳过 6 小时本地缓存重新联网（供「刷新」按钮使用）。
        优先返回在线拉取的结果（见 :meth:`fetch_available_versions`，带本地缓存），
        网络不可用时退回硬编码静态列表——旧实现只有这份静态数据，最新只到
        2025-05，用户在「添加版本」里永远看不到 2026 年的新版本。
        """
        if not force:
            cached = BlenderService._load_versions_cache()
            if cached:
                # 老缓存可能是"测试版为空"时代写下的，补一次每日构建
                if not cached.get("beta"):
                    daily = BlenderService.fetch_daily_builds(timeout=8)
                    if daily:
                        cached["beta"] = daily
                        BlenderService._save_versions_cache(cached)
                return cached
        try:
            online = BlenderService.fetch_available_versions(timeout=8)
            if online:
                return online
        except Exception:  # noqa: BLE001
            pass
        return {
            "stable": [
                {"version": "4.4.1", "label": "Blender 4.4.1", "is_lts": False, "size": "350 MB", "date": "2025-05-20"},
                {"version": "4.2.5", "label": "Blender 4.2 LTS", "is_lts": True, "size": "340 MB", "date": "2025-04-10"},
                {"version": "4.1.1", "label": "Blender 4.1.1", "is_lts": False, "size": "320 MB", "date": "2024-05-10"},
                {"version": "4.0.2", "label": "Blender 4.0.2", "is_lts": False, "size": "310 MB", "date": "2023-12-05"},
                {"version": "3.6.11", "label": "Blender 3.6 LTS", "is_lts": True, "size": "300 MB", "date": "2024-06-01"},
                {"version": "3.3.21", "label": "Blender 3.3 LTS", "is_lts": True, "size": "290 MB", "date": "2024-06-01"},
                {"version": "2.93.18", "label": "Blender 2.93 LTS", "is_lts": True, "size": "250 MB", "date": "2023-12-01"},
            ],
            "beta": [
                {"version": "4.5.0-beta", "label": "Blender 4.5 Beta", "is_lts": False, "size": "360 MB", "date": "2025-05-01"},
                {"version": "4.4.0-alpha", "label": "Blender 4.4 Alpha", "is_lts": False, "size": "355 MB", "date": "2025-03-01"},
            ],
            "lts": [
                {"version": "4.2.5", "label": "Blender 4.2 LTS", "is_lts": True, "size": "340 MB", "date": "2025-04-10"},
                {"version": "3.6.11", "label": "Blender 3.6 LTS", "is_lts": True, "size": "300 MB", "date": "2024-06-01"},
                {"version": "3.3.21", "label": "Blender 3.3 LTS", "is_lts": True, "size": "290 MB", "date": "2024-06-01"},
                {"version": "2.93.18", "label": "Blender 2.93 LTS", "is_lts": True, "size": "250 MB", "date": "2023-12-01"},
            ],
        }

    # ── 在线版本列表（镜像目录解析，替代硬编码） ────────────────────────

    # 镜像优先级：国内 nju 直连最快，官方站兜底
    _RELEASE_INDEX_URLS = (
        "https://mirrors.nju.edu.cn/blender/release/",
        "https://download.blender.org/release/",
    )
    _LTS_MINORS = {"2.83", "2.93", "3.3", "3.6", "3.10", "4.2", "4.5", "5.2"}
    _VERSIONS_CACHE_NAME = "available_versions.json"
    _VERSIONS_CACHE_TTL = 6 * 3600  # 6 小时

    @staticmethod
    def _versions_cache_path():
        import os
        from pathlib import Path

        base = Path(os.environ.get("LOCALAPPDATA", ".")) / "BlenderHub Team" / "BlenderHub"
        return base / BlenderService._VERSIONS_CACHE_NAME

    @classmethod
    def _load_versions_cache(cls) -> Optional[Dict[str, list]]:
        """读本地版本缓存（6 小时内有效）。"""
        import time

        path = cls._versions_cache_path()
        try:
            if not path.is_file():
                return None
            if time.time() - path.stat().st_mtime > cls._VERSIONS_CACHE_TTL:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("stable"):
                return data
        except (OSError, ValueError):
            return None
        return None

    @classmethod
    def _save_versions_cache(cls, data: Dict[str, list]) -> None:
        try:
            path = cls._versions_cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def fetch_available_versions(cls, timeout: int = 10) -> Optional[Dict[str, list]]:
        """从镜像目录实时拉取 Blender 版本列表。

        数据来源是 Blender 官方 release 目录（`BlenderX.Y/` 下的
        `blender-X.Y.Z-windows-x64.zip` 文件名），每个 minor 系列取最高补丁版。
        拉取失败返回 None，由调用方回落到静态列表。
        """
        import re as _re

        import requests as _requests

        session = _requests.Session()
        session.headers["User-Agent"] = "BlenderHub/1.0"

        minors: list[str] = []
        for base in cls._RELEASE_INDEX_URLS:
            try:
                resp = session.get(base, timeout=timeout)
                if resp.status_code != 200:
                    continue
                minors = sorted(set(_re.findall(r"Blender(\d+\.\d+)/", resp.text)))
                if minors:
                    break
            except Exception:  # noqa: BLE001
                continue
        if not minors:
            return None

        # 只细看最近若干个 minor，避免请求过多（每个系列一次目录请求）
        minors = minors[-8:]
        stable: list[dict] = []
        for minor in minors:
            try:
                resp = session.get(f"{cls._RELEASE_INDEX_URLS[0]}Blender{minor}/",
                                   timeout=timeout)
                if resp.status_code != 200:
                    continue
                patches = sorted(set(_re.findall(
                    rf"blender-({_re.escape(minor)}\.\d+)-windows-x64\.(?:zip|msi)",
                    resp.text,
                )))
                # 必须按**数值**排序：字符串比较会让 4.5.14 输给 4.5.9
                # （"1" < "9"），导致取不到真正的最新补丁版。
                patches.sort(key=lambda s: [int(p) for p in s.split(".")])
            except Exception:  # noqa: BLE001
                continue
            if not patches:
                continue
            latest = patches[-1]
            is_lts = minor in cls._LTS_MINORS
            stable.append({
                "version": latest,
                "label": f"Blender {latest}" + (" LTS" if is_lts else ""),
                "is_lts": is_lts,
                "size": "约 350 MB",
                "date": "",
            })

        if not stable:
            return None
        stable.sort(key=lambda x: [int(p) for p in x["version"].split(".")], reverse=True)
        # 测试版（alpha/beta）不在 release 目录里，得从官方每日构建抓。
        # 旧实现这里恒为空 → 「添加版本」的测试版标签页永远是空白。
        beta = cls.fetch_daily_builds(timeout=max(4, timeout // 2))
        data = {"stable": stable, "beta": beta, "lts": [s for s in stable if s["is_lts"]]}
        cls._save_versions_cache(data)
        return data

    # ─── 核心工具方法 ───

    def _read_version(self, exe: Path) -> Optional[str]:
        """读取 Blender 版本号。

        ⚠ 旧实现**一上来就 subprocess 跑 `blender.exe --version`**：
          1. Windows 上启动控制台子系统程序会**弹出黑窗**（深度扫描每命中一个
             候选就弹一次，用户看到"黑窗不断弹出"）；
          2. 每个候选最多等 10s，全盘深扫被拖到数分钟。
        现在的顺序是「零进程 → 再进程」：目录名 → PE 版本资源 → 最后才 subprocess
        （且带 CREATE_NO_WINDOW，即便真是控制台程序也不弹窗）。
        """
        exe = Path(exe)
        # 1) PE 版本资源：常带完整三段号（4.5.14），且不启动进程
        pe = _pe_file_version(str(exe))
        # 2) 目录 / 文件名里的版本号（blender-4.2.23-windows-x64、"Blender 4.2"）
        name_ver = None
        for n in (exe.parent.name, exe.parent.parent.name, exe.name):
            name_ver = _version_from_name(n)
            if name_ver:
                break
        v = self._pick_version(pe, name_ver)
        if v:
            return v
        # 3) 兜底：真跑一次 --version（隐藏窗口）
        return self._read_version_by_process(exe)

    @staticmethod
    def _pick_version(pe_ver: Optional[str], name_ver: Optional[str]) -> Optional[str]:
        """在 PE 版本与目录名版本之间挑一个更可信的。

        目录名已有三段号时用目录名（安装器/压缩包命名最贴近实际版本）；
        目录名只有 "Blender 4.5" 两段、而 PE 能给出 4.5.14 且前两段一致时，
        用 PE 补上 patch 号。
        """
        if not pe_ver:
            return name_ver
        if not name_ver:
            return pe_ver
        pt = BlenderService._version_tuple(pe_ver)
        nt = BlenderService._version_tuple(name_ver)
        if len(nt) >= 3:
            return name_ver
        if pt[:2] == nt[:2]:
            return pe_ver
        return name_ver

    def _read_version_by_process(self, exe: Path) -> Optional[str]:
        """最后手段：运行 --version（Windows 下强制隐藏控制台窗口）"""
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            result = subprocess.run(
                [str(exe), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                **kwargs,
            )
            output = result.stdout or result.stderr
            # "Blender 4.2.0" or "Blender 4.2.0 LTS"
            match = re.search(r'Blender\s+(\d+\.\d+(?:\.\d+)?)', output)
            if match:
                return match.group(1)
            if self._log:
                # 旧实现 except: pass 让超时/崩溃/权限问题全部静默返回 None，
                # 版本检测失败无任何痕迹，无法定位是哪个 exe 读不出来。
                self._log.debug(
                    f"No version parsed from '{exe.name}' output: {output[:120]!r}",
                    module="blender_service",
                )
        except subprocess.TimeoutExpired:
            if self._log:
                self._log.debug(f"'--version' timed out (10s): {exe}", module="blender_service")
        except (OSError, PermissionError) as e:
            if self._log:
                # 路径不存在 / 无执行权限 —— 最常见的原因，值得记录
                self._log.debug(f"Cannot execute '{exe}': {e}", module="blender_service")
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.error(f"Unexpected error reading version of '{exe}': {e}", module="blender_service")
        return None

    @staticmethod
    def _version_tuple(version: str) -> tuple:
        """版本号转为可比较的元组"""
        try:
            parts = version.replace("-beta", "").replace("-alpha", "").split(".")
            return tuple(int(p) for p in parts if p.isdigit())
        except Exception:
            return (0,)

    def _is_lts(self, version: str) -> bool:
        """判断是否为 LTS 版本。

        ⚠ 旧实现维护了一份**和在线版本列表不同步**的硬编码集合
        {2.83, 2.93, 3.3, 3.6, 4.2, 4.5}，而在线列表用的是 `_LTS_MINORS`
        （含 5.2）→ 用户装了 5.2.2 LTS，版本库里却显示成"稳定版"。
        现在两处统一到 `_LTS_MINORS`，并以在线/缓存版本数据里的官方标注为准。
        """
        try:
            parts = str(version).split(".")
            if len(parts) < 2:
                return False
            minor = f"{parts[0]}.{parts[1]}"
        except Exception:  # noqa: BLE001
            return False

        # 1) 以在线（或 6h 缓存）版本数据里的官方 LTS 标注为准
        try:
            data = BlenderService._load_versions_cache() or {}
            for item in (data.get("stable") or []) + (data.get("lts") or []):
                v = str(item.get("version", "")).split(".")
                if len(v) >= 2 and f"{v[0]}.{v[1]}" == minor:
                    return bool(item.get("is_lts", False))
        except Exception:  # noqa: BLE001
            pass

        # 2) 离线兜底：内置 LTS 小版本清单（唯一数据源，与在线列表共用）
        return minor in BlenderService._LTS_MINORS

    def launch(self, installation: BlenderInstallation, project_path: str = None):
        """启动 Blender。

        旧实现丢弃 Popen 返回值，句柄从不回收：既无法追踪/终止已启动实例，
        也会持续泄漏进程资源。这里持有句柄并提供 active_instances()/terminate_all()。
        start_new_session 在 Windows 上是空操作（仅 POSIX 有效），保留无害。
        """
        args = [str(installation.executable)]
        if project_path:
            args.append(project_path)
        if self._log:
            self._log.info(f"Launching Blender {installation.version}: {' '.join(args)}", module="blender_service")
        try:
            proc = subprocess.Popen(args, start_new_session=True)
        except (OSError, PermissionError) as e:
            # exe 不存在 / 无执行权限 —— 旧实现静默失败，用户看不到任何提示
            if self._log:
                self._log.error(f"Failed to launch Blender '{installation.executable}': {e}", module="blender_service")
            raise

        with self._proc_lock:
            self._launched.append((proc, installation.version))
            # 清理已退出的句柄，避免列表无限增长
            self._launched = [(p, v) for p, v in self._launched if p.poll() is None]
        return proc

    def active_instances(self) -> list[str]:
        """返回仍在运行的 Blender 实例版本列表"""
        with self._proc_lock:
            self._launched = [(p, v) for p, v in self._launched if p.poll() is None]
            return [v for _, v in self._launched]

    def terminate_all(self, timeout: float = 3.0) -> None:
        """退出应用时终止所有由本服务启动的 Blender 实例。

        只处理本服务自己 launch 的进程，不会误杀用户手动打开的 Blender。
        """
        with self._proc_lock:
            procs = list(self._launched)
            self._launched = []

        for proc, version in procs:
            if proc.poll() is not None:
                continue
            try:
                proc.terminate()
            except Exception as e:  # noqa: BLE001
                if self._log:
                    self._log.debug(f"terminate Blender {version} failed: {e}", module="blender_service")

        deadline = time.time() + timeout
        for proc, _ in procs:
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except Exception:
                pass
        if self._log and procs:
            self._log.info(f"Terminated {len(procs)} launched Blender instance(s)", module="blender_service")

    # ── 每日构建（alpha / beta）：官方 release 目录只有正式版，
    # 测试版通道要用 builder.blender.org，否则「测试版」列表永远空着。
    _DAILY_URL = "https://builder.blender.org/download/daily/"
    _DAILY_ZIP_RE = re.compile(
        r"https://cdn\.builder\.blender\.org/download/daily/"
        r"blender-(\d+\.\d+\.\d+)-(alpha|beta)\+([A-Za-z0-9._\-]+)-windows\.amd64-release\.zip"
    )

    @classmethod
    def fetch_daily_builds(cls, timeout: int = 10) -> list:
        """抓取官方每日构建（alpha/beta），用于填充「测试版」列表。

        页面按构建时间倒序，所以**第一条就是最新**的；同 minor 只保留最新一条。
        抓不到就返回空列表（不影响正式版列表）。
        """
        try:
            import requests
        except Exception:  # noqa: BLE001
            return []
        try:
            resp = requests.get(
                cls._DAILY_URL, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001
            return []

        out: Dict[str, dict] = {}
        for m in cls._DAILY_ZIP_RE.finditer(resp.text):
            ver, kind, build = m.group(1), m.group(2), m.group(3)
            key = ".".join(ver.split(".")[:2])
            if key in out:
                continue  # 已有更新的一条
            out[key] = {
                "version": f"{ver}-{kind}",
                "label": f"Blender {ver} {kind.capitalize()}",
                "is_lts": False,
                "size": "",
                "date": build[:12],
                "url": m.group(0),
            }
        items = sorted(
            out.values(), key=lambda v: cls._version_tuple(v["version"]), reverse=True,
        )
        return items[:4]

    def get_download_url(self, version: str, platform: str = None) -> Optional[str]:
        """获取 Blender 下载 URL（走国内镜像，避免官方源 Cloudflare 人机验证）"""
        if platform is None:
            platform = sys.platform

        platform_map = {
            "win32": "windows-x64",
            "darwin": "macos-x64",
            "linux": "linux-x64",
        }
        plat = platform_map.get(platform, "windows-x64")

        major_minor = ".".join(version.split(".")[:2])

        # 测试版（每日构建）不走 release 目录：文件名带 "+main.<hash>"，
        # 只能用它自己的 CDN 直链，否则拼出来的地址 404。
        try:
            for item in (BlenderService.get_available_versions().get("beta") or []):
                if str(item.get("version")) == str(version) and item.get("url"):
                    return str(item["url"])
        except Exception:  # noqa: BLE001
            pass

        official = f"https://download.blender.org/release/Blender{major_minor}/blender-{version}-{plat}.zip"
        try:
            from core.services.config_service import ConfigService
            from core.services.download_service import DownloadService
            mirror = ConfigService().get("settings", "mirror", DownloadService.DEFAULT_MIRROR)
            return DownloadService.resolve_mirror_url(official, mirror)
        except Exception:
            return official
