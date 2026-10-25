"""生成文档截图：主界面（演示数据）。"""

from __future__ import annotations

import ctypes
import shutil
import sys
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vault import autotype, theme  # noqa: E402
from vault.ui import VaultApp  # noqa: E402

SHOTS = REPO / "docs" / "screenshots"
TMP = REPO / ".tmp_shots"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]


def main() -> int:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    TMP.mkdir(exist_ok=True)
    db_path = TMP / "demo-vault.db"
    shutil.copy2(REPO / "demo" / "demo-vault.db", db_path)

    import tkinter as tk

    root = tk.Tk()
    app = VaultApp(root, db_path=db_path, enable_hotkey=False)
    root.update()
    app.unlock_var.set("DemoOnly!2026")
    app._do_unlock()
    root.update()
    time.sleep(0.6)
    root.update()

    hwnd = autotype.root_window(root.winfo_id())
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 40, 40, 1522, 1016, 0x0004)  # TOPMOST|NOZORDER
    time.sleep(0.8)
    root.update()

    from PIL import ImageGrab

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
    SHOTS.mkdir(parents=True, exist_ok=True)
    img.save(SHOTS / "main.png")
    print("saved main.png", img.size, flush=True)

    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
