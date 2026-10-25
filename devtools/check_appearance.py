"""开发/测试用：检查皮肤中心与窗口透明度，并截图设置窗口。

用法：
    python devtools/check_appearance.py [--shot 输出.png]

会依次切换所有皮肤、调整透明度、打开设置窗口并截图，最后打印结果。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", default="")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args(argv)

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    tmp = REPO / ".tmp_e2e" / f"skin-{os.getpid()}-{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / "vault.db"
    shutil.copy2(REPO / "demo" / "demo-vault.db", db_path)

    import tkinter as tk

    from vault import theme
    from vault.ui import VaultApp

    root = tk.Tk()
    app = VaultApp(root, db_path=db_path, enable_hotkey=False)
    root.update()

    app.unlock_var.set("DemoOnly!2026")
    app._do_unlock()
    root.update()

    results = []
    for skin in theme.skin_names():
        app.apply_appearance(skin, 0.85)
        root.update()
        results.append(
            {
                "skin": skin,
                "accent": theme.ACCENT,
                "current": theme.CURRENT_SKIN,
                "alpha": float(root.attributes("-alpha")),
            }
        )

    app.apply_appearance(theme.DEFAULT_SKIN, 1.0)
    root.update()
    app.open_settings()
    root.update()
    time.sleep(0.8)
    root.update()

    dialog = next((d for d in app._dialogs if d.winfo_exists()), None)
    if dialog is None:
        print("ERROR 设置窗口没有打开", flush=True)
        return 1
    dialog.update_idletasks()

    if args.shot:
        try:
            from PIL import ImageGrab

            x = dialog.winfo_rootx()
            y = dialog.winfo_rooty()
            w = dialog.winfo_width()
            h = dialog.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(args.shot)
            print(f"SHOT {args.shot} {w}x{h}", flush=True)
        except Exception as exc:
            print(f"SHOT_FAILED {exc}", flush=True)

    before = theme.CURRENT_SKIN
    dialog._pick_skin("葡萄紫")
    root.update()
    picked = theme.CURRENT_SKIN
    dialog._close()  # 取消：应还原
    root.update()
    reverted = theme.CURRENT_SKIN

    app.open_settings()
    root.update()
    dialog2 = next((d for d in app._dialogs if d.winfo_exists()), None)
    dialog2.opacity_var.set(80)
    dialog2._on_opacity(80)
    root.update()
    dialog2._pick_skin("森林绿")
    root.update()
    dialog2._save()
    root.update()
    saved = {
        "skin": app.db.settings.get("skin"),
        "opacity": app.db.settings.get("window_opacity"),
        "app_opacity": app.opacity,
        "current": theme.CURRENT_SKIN,
        "alpha": float(root.attributes("-alpha")),
    }

    print("RESULTS " + json.dumps(results, ensure_ascii=False), flush=True)
    print(
        "CHECKS "
        + json.dumps(
            {
                "before": before,
                "picked": picked,
                "reverted": reverted,
                "revert_ok": reverted == before,
                "saved": saved,
                "save_ok": saved["skin"] == "森林绿"
                and abs((saved["opacity"] or 0) - 0.8) < 1e-6
                and abs(saved["app_opacity"] - 0.8) < 1e-6
                and saved["current"] == "森林绿",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    root.destroy()
    if not args.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
