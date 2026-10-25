"""开发/测试用：从源码启动快填密码库，但跳过“单实例”锁。

用途：端到端测试时，用户自己的快填密码库可能正在运行；测试实例需要能同时启动。
用法：
    python devtools/dev_launcher.py --db <vault.db 路径>
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _set_dpi_awareness() -> None:
    try:
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--unlock", default="", help="启动后自动用这个主密码解锁（仅测试用）")
    parser.add_argument("--status", default="", help="解锁后把实际快捷键写进这个文件（仅测试用）")
    args = parser.parse_args(argv)

    _set_dpi_awareness()

    import tkinter as tk

    from app import setup_logging
    from vault.ui import VaultApp

    setup_logging(True)

    root = tk.Tk()
    app = VaultApp(root, db_path=args.db, enable_hotkey=True)

    # 把界面状态栏文案转发到 stdout，方便自动化断言
    original_status = app._status

    def status_proxy(text, kind="info", timeout=4500):
        print(f"STATUS {kind} {text}", flush=True)
        return original_status(text, kind, timeout)

    app._status = status_proxy

    if args.unlock:
        def _auto_unlock() -> None:
            try:
                app.unlock_var.set(args.unlock)
                app._do_unlock()
                hotkey = app.settings.get("hotkey", "")
                print(f"UNLOCKED hotkey={hotkey}", flush=True)
                if args.status:
                    Path(args.status).write_text(
                        json.dumps({"hotkey": hotkey, "hotkey_error": app._hotkey_error}, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except Exception as exc:  # pragma: no cover - 测试辅助
                print(f"UNLOCK_FAILED {exc}", flush=True)

        root.after(700, _auto_unlock)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        app._on_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
