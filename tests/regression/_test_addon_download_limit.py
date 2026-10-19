"""插件下载大小上限 / 流式读取 / 响应关闭的回归测试。

背景：旧实现用 `requests.get(...)` 后直接取 `resp.content`，
服务端返回超大内容或无限流会一次性占满内存，且响应对象从不关闭。

现在下载跑在 `_AddonDownloadWorker` 后台线程里，但大小上限逻辑收在
`_download_addon_bytes` 这个同步方法里，可直接覆盖：
1. Content-Length 超限 → 直接抛错
2. 无 Content-Length 但实体超限 → 边读边检查并抛错
3. 正常大小 → 分块读取 + 进度回调 + 响应关闭
4. 非 200 → 如实报错（不再回退占位安装）
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import modules.addon_store.module as mod  # noqa: E402
from modules.addon_store.module import AddonStoreModule, MAX_ADDON_BYTES  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


class _FakeResp:
    def __init__(self, status=200, body=b"", declared=None, ctype="application/zip"):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        if declared is not None:
            self.headers["Content-Length"] = str(declared)
        self._body = body
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True
        return False


def make_module():
    m = AddonStoreModule.__new__(AddonStoreModule)
    m.ctx = SimpleNamespace(log_service=SimpleNamespace(
        error=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
    ))
    return m


def try_fetch(resp):
    """用假 requests 跑一次同步下载，返回 (ok, content_or_error)。"""
    mod.requests = SimpleNamespace(get=lambda url, **kw: resp)
    m = make_module()
    try:
        return True, m._download_addon_bytes("https://example.com/addon.zip")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ── 0. 默认上限 ──
check("默认上限 200MB", MAX_ADDON_BYTES == 200 * 1024 * 1024, str(MAX_ADDON_BYTES))

# 测试用小上限，避免真的造 200MB 数据
mod.MAX_ADDON_BYTES = 1024

# ── 1. Content-Length 超限 ──
resp = _FakeResp(body=b"", declared=4096)
ok, res = try_fetch(resp)
check("Content-Length 超限抛错", not ok and "过大" in res, str(res))
check("Content-Length 超限也关闭响应", resp.closed)

# ── 2. 无 Content-Length，实体超限 ──
resp = _FakeResp(body=b"x" * 4096, declared=None)
ok, res = try_fetch(resp)
check("实体超限抛错", not ok and "超过上限" in res, str(res))
check("实体超限也关闭响应", resp.closed)

# ── 3. 正常大小 ──
body = b"PK\x03\x04" + b"y" * 500
progress = []
resp = _FakeResp(body=body, declared=len(body))
mod.requests = SimpleNamespace(get=lambda url, **kw: resp)
m = make_module()
content = m._download_addon_bytes(
    "https://example.com/addon.zip",
    lambda done, total, pct: progress.append((done, total, pct)),
)
check("正常下载返回内容", content == body, str(len(content)))
check("进度回调被调用", len(progress) >= 1, str(progress[-1:] if progress else []))
check("进度百分比到 100", progress and progress[-1][2] == 100, str(progress[-1:]))
check("正常下载关闭响应", resp.closed)

# ── 4. 非 200 如实报错 ──
resp = _FakeResp(status=404, body=b"<html>", declared=5)
ok, res = try_fetch(resp)
check("HTTP 404 如实报错", not ok and "HTTP 404" in res, str(res))

# 还原（同一进程内其它用例不受影响）
mod.MAX_ADDON_BYTES = MAX_ADDON_BYTES

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
