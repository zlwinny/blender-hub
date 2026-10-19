"""
下载服务 — 全局唯一下载队列 + 断点续传

特性：
- 下载队列管理（并发数可控）
- 断点续传（HTTP Range）
- 进度回调
- 支持国内镜像源
"""

import time
import threading
from pathlib import Path
from typing import Optional, Callable, Dict
from dataclasses import dataclass, field
from enum import Enum

import requests


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _Cancelled(Exception):
    """下载被暂停/取消时在 worker 内部抛出的哨兵，区别于真实网络错误。"""


@dataclass
class DownloadTask:
    task_id: str
    url: str
    save_path: Path
    display_name: str = ""
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0          # 0.0 - 1.0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bytes: float = 0.0       # 字节/秒
    error_message: str = ""
    # 期望的文件大小（字节）。None 表示未知，不做大小下限校验。
    # 旧实现用硬编码 20MB 下限，导致所有小于 20MB 的合法插件 zip 被误判为"验证页"删除。
    expected_size: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    on_progress: Optional[Callable] = None
    on_complete: Optional[Callable] = None
    on_error: Optional[Callable] = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)


class DownloadService:
    """全局下载队列管理"""

    # 国内镜像源
    # 2026-10 实测：官方 download.blender.org 已启用 Cloudflare 人机验证（返回 HTML 质询页），
    # 清华 / 中科大未托管 Blender 发行包（403）。南京大学镜像实测 200 可用，故列为默认。
    MIRRORS = {
        "nju": "https://mirror.nju.edu.cn/blender/release/",
        "tsinghua": "https://mirrors.tuna.tsinghua.edu.cn/blender/release/",
        "ustc": "https://mirrors.ustc.edu.cn/blender/release/",
        "official": "https://download.blender.org/release/",
    }
    DEFAULT_MIRROR = "nju"

    def __init__(self, log_service=None, max_concurrent: int = 3):
        self._log = log_service
        self._max_concurrent = max_concurrent
        self._tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self._active_count = 0
        self._active_lock = threading.Lock()
        # 有工作线程结束时置位，供 shutdown 用事件等待而非忙轮询
        self._active_event = threading.Event()

    def add_task(
        self,
        task_id: str,
        url: str,
        save_path: Path,
        display_name: str = "",
        on_progress: Callable = None,
        on_complete: Callable = None,
        on_error: Callable = None,
        expected_size: Optional[int] = None,
    ) -> DownloadTask:
        """添加下载任务到队列

        expected_size: 期望文件大小（字节）。传入后下载会做严格大小比对；
        留空则跳过大小下限校验，仅做 HTML 页 / ZIP magic 校验。
        """
        save_path.parent.mkdir(parents=True, exist_ok=True)

        task = DownloadTask(
            task_id=task_id,
            url=url,
            save_path=save_path,
            display_name=display_name or save_path.name,
            on_progress=on_progress,
            on_complete=on_complete,
            on_error=on_error,
            expected_size=expected_size,
        )

        with self._lock:
            self._tasks[task_id] = task

        if self._log:
            self._log.info(f"Download task added: {task.display_name}", module="download_service")

        # 尝试立即启动
        self._start_next()

        return task

    def _start_next(self):
        """启动队列中待下载任务，直到并发数打满或队列排空"""
        while True:
            with self._active_lock:
                if self._active_count >= self._max_concurrent:
                    return

            with self._lock:
                task = None
                for candidate in self._tasks.values():
                    if candidate.status == DownloadStatus.PENDING:
                        candidate.status = DownloadStatus.DOWNLOADING
                        task = candidate
                        break
                if task is None:
                    return

            with self._active_lock:
                self._active_count += 1

            thread = threading.Thread(target=self._download_worker, args=(task,), daemon=True)
            thread.start()

    # 网络类异常重试策略：连接错误 / 超时 / 5xx / 429
    _RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
    _MAX_RETRIES = 3
    _CHUNK_SIZE = 8192

    def _download_worker(self, task: DownloadTask):
        """下载工作线程"""
        # 先写入 .part 临时文件，完成并通过校验后再 rename 到目标路径，
        # 避免崩溃/取消后残留的损坏文件被下次续传当成已有进度。
        part_path = task.save_path.with_suffix(task.save_path.suffix + ".part")
        try:
            resume_pos = part_path.stat().st_size if part_path.exists() else 0

            attempt = 0
            while True:
                try:
                    self._download_once(task, part_path, resume_pos)
                    break
                except _Cancelled:
                    # 暂停/取消：状态由 pause()/cancel() 预先置好，这里只负责退出线程，
                    # 不落盘最终文件（.part 保留，供 resume 续传）
                    return
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    retryable = (
                        isinstance(exc, (requests.ConnectionError, requests.Timeout))
                        or status in self._RETRY_STATUS
                    )
                    attempt += 1
                    # 已下载的字节数（下次续传起点）
                    resume_pos = part_path.stat().st_size if part_path.exists() else 0
                    if not retryable or attempt >= self._MAX_RETRIES or task._cancel_event.is_set():
                        raise
                    backoff = 2 ** (attempt - 1)
                    if self._log:
                        self._log.info(
                            f"Download retry {attempt}/{self._MAX_RETRIES} in {backoff}s: "
                            f"{task.display_name} - {exc}",
                            module="download_service",
                        )
                    if task._cancel_event.wait(backoff):
                        # 状态已由 pause/cancel 设置，这里不再覆盖
                        return

            # 校验通过后再落到最终路径
            part_path.replace(task.save_path)

            task.status = DownloadStatus.COMPLETED
            task.progress = 1.0

            if self._log:
                self._log.info(f"Download completed: {task.display_name}", module="download_service")

            if task.on_complete:
                try:
                    task.on_complete(task)
                except Exception:
                    if self._log:
                        self._log.error(
                            f"on_complete callback failed: {task.display_name}", module="download_service"
                        )

        except Exception as e:
            task.status = DownloadStatus.FAILED
            task.error_message = str(e)

            if self._log:
                self._log.error(f"Download failed: {task.display_name} - {e}", module="download_service")

            if task.on_error:
                try:
                    task.on_error(task, str(e))
                except Exception:
                    if self._log:
                        self._log.error(
                            f"on_error callback failed: {task.display_name}", module="download_service"
                        )

        finally:
            with self._active_lock:
                self._active_count -= 1
            self._active_event.set()
            self._start_next()

    def _download_once(self, task: DownloadTask, part_path: Path, resume_pos: int) -> None:
        """执行一次完整下载（内部调用，失败向上抛异常由 _download_worker 重试）"""
        headers = {}
        if resume_pos > 0:
            headers["Range"] = f"bytes={resume_pos}-"

        # timeout 用 (connect, read) 元组，避免单值 30s 把"建连慢"和"传输卡"混为一谈
        with requests.get(
            task.url, headers=headers, stream=True, timeout=(10, 30)
        ) as response:
            # 旧实现缺 raise_for_status，404/403 的 HTML 错误页会被当正文写进 .zip。
            # 但续传场景服务器可能返回 200（忽略 Range），此时 response.status_code != 206，
            # raise_for_status 对 200 无副作用，两种情况都正确。
            response.raise_for_status()

            if resume_pos > 0 and response.status_code == 206:
                mode = "ab"
                task.downloaded_bytes = resume_pos
            else:
                # 服务器不支持续传或重新开始：丢弃已有部分
                resume_pos = 0
                mode = "wb"
                task.downloaded_bytes = 0

            content_length = response.headers.get("content-length")
            if content_length:
                base = task.downloaded_bytes if mode == "ab" else 0
                task.total_bytes = int(content_length) + base
            else:
                # 无长度信息时置 0，进度回调会显示为不确定进度
                task.total_bytes = 0

            window_bytes = 0
            window_start = time.time()

            with open(part_path, mode) as f:
                for chunk in response.iter_content(chunk_size=self._CHUNK_SIZE):
                    if task._cancel_event.is_set():
                        # 抛出哨兵，由 worker 统一退出，避免在此覆盖 PAUSED/CANCELLED
                        raise _Cancelled()

                    f.write(chunk)
                    task.downloaded_bytes += len(chunk)
                    if task.total_bytes > 0:
                        task.progress = task.downloaded_bytes / task.total_bytes

                    # 真实速度：滑动窗口测量，而不是"每块 / 0.1"的编造常数
                    window_bytes += len(chunk)
                    now = time.time()
                    elapsed = now - window_start
                    if elapsed >= 0.5:
                        task.speed_bytes = window_bytes / elapsed
                        window_bytes = 0
                        window_start = now

                    if task.on_progress:
                        try:
                            task.on_progress(
                                task.progress, task.downloaded_bytes, task.total_bytes, task.speed_bytes
                            )
                        except Exception:
                            if self._log:
                                self._log.error(
                                    f"on_progress callback failed: {task.display_name}",
                                    module="download_service",
                                )

            _ctype = response.headers.get("Content-Type", "")
            magic = b""
            try:
                with open(part_path, "rb") as probe:
                    magic = probe.read(2)
            except OSError:
                pass

        # ── 完整性校验：防止镜像源返回 HTML 验证页被当成安装包 ──
        size = part_path.stat().st_size if part_path.exists() else 0
        if _ctype and "text/html" in _ctype:
            self._discard_part(part_path)
            raise ValueError(
                f"下载内容异常：镜像源返回的是 HTML 页面而非安装包"
                f"（Content-Type={_ctype}）。请在「更新」中换用其它镜像源后重试。"
            )

        if task.expected_size is not None and size != task.expected_size:
            self._discard_part(part_path)
            raise ValueError(
                f"下载文件大小不符：期望 {task.expected_size / 1024 / 1024:.1f}MB，"
                f"实际 {size / 1024 / 1024:.1f}MB。请换用其它镜像源后重试。"
            )

        # ZIP 安装包 magic 校验（仅在目标后缀为 .zip 时）
        if task.save_path.suffix.lower() == ".zip" and magic != b"PK":
            self._discard_part(part_path)
            raise ValueError(
                f"下载内容不是有效的 ZIP 包（文件头 {magic!r}）。请换用其它镜像源后重试。"
            )

    @staticmethod
    def _discard_part(part_path: Path) -> None:
        """删除 .part 临时文件（只删临时文件，不动用户已有的最终文件）"""
        try:
            part_path.unlink()
        except OSError:
            pass

    def pause(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == DownloadStatus.DOWNLOADING:
                task._cancel_event.set()
                task.status = DownloadStatus.PAUSED

    def resume(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == DownloadStatus.PAUSED:
                task.status = DownloadStatus.PENDING
                task._cancel_event.clear()
        self._start_next()

    def cancel(self, task_id: str):
        task = None
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task._cancel_event.set()
                task.status = DownloadStatus.CANCELLED
        if task:
            # 清理 .part，避免下次误当续传进度；删除失败忽略（可能正被工作线程占用）
            part_path = task.save_path.with_suffix(task.save_path.suffix + ".part")
            try:
                part_path.unlink()
            except OSError:
                pass

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def shutdown(self, timeout: float = 3.0) -> None:
        """退出前停止所有下载线程。

        旧实现线程为 daemon=True 且从不 join，应用退出时仍在传输的线程被强杀，
        会留下半截 .part 文件并可能让 Qt 报线程销毁告警。此处协作式取消并等待收尾。
        """
        with self._lock:
            tasks = list(self._tasks.values())

        for task in tasks:
            if task.status in (
                DownloadStatus.DOWNLOADING,
                DownloadStatus.PENDING,
                DownloadStatus.PAUSED,
            ):
                task._cancel_event.set()
                if task.status != DownloadStatus.CANCELLED:
                    task.status = DownloadStatus.CANCELLED

        # 等待工作线程自行结束（工作线程内部通过 part_path.replace 落盘，取消时不会动最终文件）。
        # 用事件等待替代 sleep 忙轮询：线程结束时 set，shutdown 立即醒来。
        deadline = time.time() + timeout
        while True:
            with self._active_lock:
                if self._active_count <= 0:
                    break
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            self._active_event.wait(remaining)
            self._active_event.clear()

        if self._log:
            self._log.info("DownloadService shutdown complete", module="download_service")

    def get_all_tasks(self) -> list:
        with self._lock:
            return list(self._tasks.values())

    def clear_completed(self):
        with self._lock:
            self._tasks = {
                k: v for k, v in self._tasks.items()
                if v.status not in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED)
            }

    @staticmethod
    def resolve_mirror_url(official_url: str, mirror: str = "official") -> str:
        """将官方 URL 映射到镜像源"""
        base = DownloadService.MIRRORS.get(mirror)
        if not base or mirror == "official":
            return official_url
        # https://download.blender.org/release/Blender4.2/blender-4.2.0-windows-x64.zip
        # → https://mirrors.tuna.tsinghua.edu.cn/blender/release/Blender4.2/blender-4.2.0-windows-x64.zip
        return official_url.replace("https://download.blender.org/release/", base)
