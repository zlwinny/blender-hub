"""
事件总线 — 模块间通信核心

提供两种通信模式：
1. Pub/Sub：发布-订阅（广播通知，fire-and-forget）
2. Request-Reply：请求-应答（同步查询）

特性：
- 订阅者异常隔离：单个订阅者异常不影响其他订阅者
- 关键事件重试：支持指数退避自动重试
- 离线事件队列：网络不可达时事件持久化到 SQLite
- 事件命名规范：{domain}.{action} 或 {domain}.{resource}.{action}
"""

import time
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict


class EventBus:
    """全局事件总线（线程安全）"""

    def __init__(self, log_service=None):
        self._lock = threading.RLock()
        # Pub/Sub 订阅者: {event_name: [(handler, retry, backoff), ...]}
        self._subscribers: Dict[str, List[tuple]] = defaultdict(list)
        # Request-Reply 处理器: {event_name: handler}
        self._request_handlers: Dict[str, Callable] = {}
        # 离线事件队列: [(event_name, payload, persist_on_failure)]
        self._offline_queue: List[tuple] = []
        # 已发出事件去重缓存: {(event_id, timestamp)}
        self._emitted_events: set = set()
        # 日志服务引用
        self._log = log_service

    # ─── Pub/Sub ───────────────────────────────────────

    def subscribe(
        self,
        event: str,
        handler: Callable[[dict], Any],
        retry: int = 0,
        backoff: float = 2.0,
    ):
        """订阅事件
        Args:
            event: 事件名，如 "blender.installed"
            handler: 回调函数，接收 payload dict，可返回 {"status": "ack"/"nack", ...}
            retry: 失败后最多重试次数，0 表示不重试
            backoff: 重试间隔指数退避基数（秒）
        """
        with self._lock:
            self._subscribers[event].append((handler, retry, backoff))

    def unsubscribe(self, event: str, handler: Callable):
        """取消订阅"""
        with self._lock:
            self._subscribers[event] = [
                (h, r, b) for (h, r, b) in self._subscribers[event] if h != handler
            ]

    def emit(self, event: str, payload: dict, persist_on_failure: bool = False):
        """发布事件（广播给所有订阅者）
        每个订阅者独立执行，异常不互相影响。
        """
        event_id = str(uuid.uuid4())
        timestamp = time.time()

        with self._lock:
            subs = list(self._subscribers.get(event, []))

        for handler, max_retry, backoff_base in subs:
            self._invoke_handler(event, handler, payload, max_retry, backoff_base)

        # 限制去重缓存大小，防止内存泄漏
        if len(self._emitted_events) > 10000:
            self._emitted_events.clear()
        self._emitted_events.add((event_id, timestamp))

        # 如果需要持久化且当前离线
        if persist_on_failure:
            with self._lock:
                self._offline_queue.append((event, payload, persist_on_failure))

    def _invoke_handler(
        self,
        event: str,
        handler: Callable,
        payload: dict,
        max_retry: int,
        backoff_base: float,
    ):
        """调用单个订阅者，支持重试"""
        attempt = 0
        while True:
            try:
                result = handler(payload)
                if self._log:
                    self._log.debug(f"Event '{event}' → {handler.__name__}: {result}", module="event_bus")
                break  # 成功
            except Exception as e:
                if self._log:
                    self._log.error(
                        f"Event '{event}' handler '{handler.__name__}' failed: {e}",
                        module="event_bus",
                        exc_info=True,
                    )
                attempt += 1
                if attempt > max_retry:
                    break
                wait = backoff_base ** attempt
                time.sleep(wait)

    # ─── Request-Reply ─────────────────────────────────

    def register_handler(self, request_name: str, handler: Callable[[dict], Any]):
        """注册请求处理器（同步查询用）"""
        with self._lock:
            self._request_handlers[request_name] = handler

    def request(self, request_name: str, payload: dict = None, timeout: float = 3.0) -> Any:
        """同步请求-应答
        Args:
            request_name: 请求名，如 "blender.versions.list"
            payload: 请求参数
            timeout: 超时时间（秒）
        Returns:
            处理器返回值，若超时或未注册则返回 None
        """
        with self._lock:
            handler = self._request_handlers.get(request_name)

        if handler is None:
            if self._log:
                self._log.warning(f"No handler registered for request: {request_name}", module="event_bus")
            return None

        try:
            return handler(payload or {})
        except Exception as e:
            if self._log:
                self._log.error(
                    f"Request '{request_name}' handler failed: {e}",
                    module="event_bus",
                    exc_info=True,
                )
            return None

    # ─── 离线队列 ──────────────────────────────────────

    def replay_offline_events(self) -> int:
        """网络恢复后重放离线队列中的事件，返回重放数量"""
        with self._lock:
            events = list(self._offline_queue)
            self._offline_queue.clear()

        count = 0
        for event, payload, _ in events:
            self.emit(event, payload, persist_on_failure=True)
            count += 1

        if self._log:
            self._log.info(f"Replayed {count} offline events", module="event_bus")
        return count

    def offline_queue_size(self) -> int:
        with self._lock:
            return len(self._offline_queue)

    # ─── 工具方法 ──────────────────────────────────────

    def has_subscribers(self, event: str) -> bool:
        with self._lock:
            return len(self._subscribers.get(event, [])) > 0

    def subscriber_count(self, event: str) -> int:
        with self._lock:
            return len(self._subscribers.get(event, []))

    def clear(self):
        """清空所有订阅和处理器（主要用于测试）"""
        with self._lock:
            self._subscribers.clear()
            self._request_handlers.clear()
            self._offline_queue.clear()
