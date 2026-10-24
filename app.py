"""快填密码库启动入口。"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler

from vault.paths import default_db_path, log_path

APP_TITLE = "快填密码库 · KuaiFill Vault"
MUTEX_NAME = "Local\\KuaiFillVault.SingleInstance"


def _set_dpi_awareness() -> None:
    try:
        # Windows 10 1703+：按显示器 DPI 感知，字体更清晰
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _set_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KuaiFill.Vault.1")
    except Exception:
        pass


def _acquire_single_instance():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return None
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return False
    return handle


def _focus_existing() -> None:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def setup_logging(verbose: bool = False) -> None:
    handlers: list[logging.Handler] = []
    try:
        path = log_path()
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handlers.append(handler)
    except Exception:
        pass
    if verbose or sys.stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handlers.append(stream)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地加密的账号密码库")
    parser.add_argument("--db", help="指定 vault.db 路径（默认 data/vault.db）")
    parser.add_argument("--no-hotkey", action="store_true", help="不注册全局快捷键（调试用）")
    parser.add_argument("--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version", version="快填密码库 1.0.0")
    args = parser.parse_args(argv)

    _set_dpi_awareness()
    _set_app_id()
    setup_logging(args.verbose)

    mutex = _acquire_single_instance()
    if mutex is False:
        _focus_existing()
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("快填密码库", "程序已经在运行。\n请查看任务栏中的窗口。", parent=root)
        root.destroy()
        return 0

    import tkinter as tk

    from vault.ui import VaultApp

    root = tk.Tk()
    app = VaultApp(
        root,
        db_path=args.db or default_db_path(),
        enable_hotkey=not args.no_hotkey,
    )
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app._on_close()
    finally:
        logging.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
