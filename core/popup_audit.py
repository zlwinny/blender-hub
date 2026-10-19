"""弹窗审计 — 把每一次对话框显示记进日志（标题 + 调用位置）。

排查"某个操作总会弹窗但不知道是谁弹的"这类问题时，光看代码很费劲：
弹窗调用点散落在多个模块，而且很多只在特定运行状态下触发。
这里统一给 QMessageBox / QInputDialog / QDialog.exec 包一层，
不改任何行为，只多打一条日志。
"""
from __future__ import annotations

import traceback
from typing import Callable, Optional


def install_orphan_guard(app, sink: Callable[[str], None], interval_ms: int = 3000) -> bool:
    """兜底守卫：周期性关掉"没有标题的顶层空窗口"，并留痕。

    用户报的那个"空白 Blender Hub 窗口"就是这类东西的产物——无标题，所以
    标题栏退化成 exe 产品名。所有正常对话框都会设标题，因此**标题为空的
    可见顶层窗口一律视为孤儿**；关掉它只是结束一个用户根本用不到的空窗。
    """
    try:
        from PyQt6.QtCore import QTimer  # noqa: PLC0415
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False

    try:
        app = app or QApplication.instance()
        if app is None:
            return False

        def _sweep() -> None:
            try:
                for w in list(app.topLevelWidgets()):
                    if not w.isVisible() or not w.isWindow():
                        continue
                    cls = w.metaObject().className()
                    if cls in ("MainWindow", "QMainWindow"):
                        continue  # 主窗口标题也是空的，必须排除
                    if str(w.windowTitle()).strip():
                        continue  # 有标题 = 正常窗口
                    sink(f"orphan closed: {cls} {w.width()}x{w.height()} obj={w.objectName()}")
                    w.close()
            except Exception:  # noqa: BLE001
                pass

        timer = QTimer(app)
        timer.setInterval(max(1000, int(interval_ms)))
        timer.timeout.connect(_sweep)
        timer.start()
        app._popup_audit_guard = timer  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


def install_show_watcher(app, sink: Callable[[str, str, str], None]) -> bool:
    """全局监听：任何**顶层窗口**被 show 都记一条。

    比包 exec()/show() 更彻底——不用猜代码在哪 show 窗口，Qt 的事件先到
    这里。用来抓"空白窗口是谁开的"这类问题：日志会给出类名 + 标题 + 尺寸。
    """
    try:
        from PyQt6.QtCore import QEvent, QObject  # noqa: PLC0415
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False

    try:
        app = app or QApplication.instance()
        if app is None:
            return False

        class Watcher(QObject):
            def eventFilter(self, obj, event):  # noqa: ANN001, ANN201
                try:
                    if event.type() == QEvent.Type.Show and obj.isWidgetType():
                        w = obj
                        # 只关心**顶层窗口**（子控件 show 太多，会淹掉日志）
                        if w.isWindow():
                            cls = w.metaObject().className()
                            if cls not in ("MainWindow", "QMainWindow"):
                                sink(
                                    str(w.windowTitle()),
                                    cls,
                                    f"{w.width()}x{w.height()} obj={w.objectName()}",
                                )
                except Exception:  # noqa: BLE001
                    pass
                return False

        watcher = Watcher()
        app.installEventFilter(watcher)
        # 必须把引用挂到 app 上，否则 Python 侧被 GC → 过滤器失效
        app._popup_audit_watcher = watcher  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


def _caller() -> str:
    """最近的**业务**调用帧（跳过本模块自身）"""
    try:
        for fr in reversed(traceback.extract_stack()[:-1]):
            # 只跳过本模块自身（精确匹配文件名；子串匹配会连
            # "_test_popup_audit.py" 之类的调用者也一起跳过）
            if fr.filename.replace("\\", "/").split("/")[-1] == "popup_audit.py":
                continue
            short = fr.filename.replace("\\", "/").split("/")[-1]
            return f"{short}:{fr.lineno} {fr.name}"
    except Exception:  # noqa: BLE001
        pass
    return "?"


def install(sink: Callable[[str, str, str], None]) -> bool:
    """包装弹窗入口。sink(title, kind, where) 由调用方写日志。"""
    try:
        from PyQt6.QtWidgets import (  # noqa: PLC0415
            QDialog, QInputDialog, QMessageBox, QProgressDialog,
        )
    except Exception:  # noqa: BLE001
        return False

    def wrap(cls, name: str, title_index: Optional[int] = None) -> None:
        orig = getattr(cls, name, None)
        if orig is None or getattr(orig, "_audited", False):
            return

        def patched(*a, **k):
            try:
                title = ""
                if title_index is not None and len(a) > title_index:
                    title = str(a[title_index])
                elif a and hasattr(a[0], "windowTitle"):
                    title = str(a[0].windowTitle())
                sink(title, f"{cls.__name__}.{name}", _caller())
            except Exception:  # noqa: BLE001
                pass
            return orig(*a, **k)

        patched._audited = True  # type: ignore[attr-defined]
        if isinstance(orig, staticmethod):
            setattr(cls, name, staticmethod(patched))
        else:
            setattr(cls, name, patched)

    for m in ("information", "warning", "critical", "question"):
        wrap(QMessageBox, m, title_index=1)
    for m in ("getItem", "getText", "getInt", "getDouble"):
        wrap(QInputDialog, m, title_index=1)
    wrap(QDialog, "exec")
    wrap(QProgressDialog, "exec")
    # 不用 exec() 的窗口（show() 显示的进度框 / 自定义对话框）同样要留痕：
    # 用户报的"空白 Blender Hub 窗口"就是一个只 show() 过、没设标题的
    # QProgressDialog —— 只包 exec 完全抓不到它。
    wrap(QProgressDialog, "show")  # 先包子类，标签才准确
    wrap(QDialog, "show")
    return True
