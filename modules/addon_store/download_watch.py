"""下载目录守望者 —— 等用户在浏览器里下完插件，自动拾取 zip。

为什么需要它：
budeco.top 的下载按钮最终多数指向百度网盘分享页。百度对大文件/陌生设备会
强制跳客户端，网页端也可能要验证码——任何"应用内直接下载"的方案都不可靠
（内嵌浏览器在这台机器上更是直接 native crash，见 qq_login_dialog 顶部说明）。
但有一个稳定的事实：**用户自己的浏览器是登录着百度网盘的**，点一下就能下下来。

于是把动作拆成两半：
  应用内：解析出网盘链接 + 提取码 → 复制提取码 → 打开系统浏览器 → 进入等待；
  应用内：后台盯着下载目录，一旦出现"新的、已写完的" zip 就自动校验并安装。
用户全程不需要切回应用点按钮，体验上接近"全在 HUB 内完成"。

判定「新 zip」的四条规则（缺一不可，避免把旧文件/半成品误当插件）：
  ① 后缀 .zip；② mtime 不早于开始等待的时刻；
  ③ 大小 ≥ min_size_kb（过滤零字节、图标之类的小文件）；
  ④ 连续 stable_ms 内大小不变（下载已写完，没在下到一半时抢文件）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


def default_watch_dirs() -> List[Path]:
    """常见下载目录（按优先级）：用户 Downloads → 桌面。"""
    home = Path.home()
    candidates = [home / "Downloads", home / "Desktop"]
    # 非中文 Windows 下 OneDrive 接管「桌面/文档」的常见形态
    candidates.append(home / "OneDrive" / "Downloads")
    candidates.append(home / "OneDrive" / "Desktop")
    seen, out = set(), []
    for p in candidates:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append(p)
    return out


class DownloadWatcher(QObject):
    """轮询下载目录，命中「新写完的 zip」时发出 found 信号。"""

    found = pyqtSignal(str)      # zip 绝对路径
    timed_out = pyqtSignal()    # 超过 timeout 仍没等到
    waiting = pyqtSignal(int)   # 已等待秒数（用于 UI 倒计时）

    def __init__(
        self,
        dirs: Optional[List[Path]] = None,
        parent=None,
        min_size_kb: int = 50,
        stable_ms: int = 2000,
        timeout_s: int = 900,
        interval_ms: int = 800,
    ) -> None:
        super().__init__(parent)
        self._dirs: List[Path] = list(dirs) if dirs else default_watch_dirs()
        self._min_size = min_size_kb * 1024
        self._stable_s = stable_ms / 1000.0
        self._timeout_s = timeout_s
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self._running = False
        self._since = 0.0
        self._started_at = 0.0
        self._last_tick = 0.0
        # path -> (size, 该大小首次见到的时间)
        self._sizes: Dict[str, Tuple[int, float]] = {}

    # ── 控制 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        if not self._dirs:
            self.timed_out.emit()
            return
        self._running = True
        # 留 5 秒容差：用户可能在弹窗打开前就已经点了下载
        self._since = time.time() - 5.0
        self._started_at = time.time()
        self._last_tick = self._started_at
        self._sizes.clear()
        self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def watch_dirs(self) -> List[Path]:
        return list(self._dirs)

    def set_dirs(self, dirs: List[Path]) -> None:
        """运行中切换监控目录（用户改下载位置时用）。"""
        self._dirs = list(dirs)
        self._sizes.clear()

    # ── 轮询 ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        if not self._running:
            return
        now = time.time()

        for d in self._dirs:
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for entry in entries:
                if not entry.name.lower().endswith(".zip"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                if st.st_mtime < self._since:
                    continue
                if st.st_size < self._min_size:
                    continue
                key = str(Path(d) / entry.name)
                prev = self._sizes.get(key)
                if prev is not None and prev[0] == st.st_size:
                    if now - prev[1] >= self._stable_s:
                        self.found.emit(key)
                        self.stop()
                        return
                else:
                    # 大小变了（或首次见到）：重置稳定计时
                    self._sizes[key] = (st.st_size, now)

        # 清理已消失的文件记录，避免字典无限增长
        for key in [k for k in self._sizes if not os.path.exists(k)]:
            self._sizes.pop(key, None)

        if now - self._started_at > self._timeout_s:
            self.timed_out.emit()
            self.stop()
            return

        if now - self._last_tick >= 1.0:
            self._last_tick = now
            self.waiting.emit(int(now - self._started_at))
