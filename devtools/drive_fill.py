"""开发/测试用：端到端验证“双窗口目标软件”的自动填写。

流程：
1. 启动 devtools/two_window_target.py（主窗口 + 独立登录窗口，登录窗口会清空焦点）；
2. 用演示数据库启动源码版快填密码库，并解锁；
3. 切到登录窗口、点击账号输入框，模拟用户按快捷键；
4. 在快速填充浮窗里搜索并按 Enter；
5. 关闭目标窗口，读取它记录的输入框内容并断言。

用法：
    python devtools/drive_fill.py [--hotkey Ctrl+Alt+J]
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from vault import autotype  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetFocus.argtypes = [wintypes.HWND]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]


def log(msg: str) -> None:
    print(f"[driver] {msg}", flush=True)


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def enum_windows(pid: int | None = None) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            p = window_pid(hwnd)
            if pid is None or p == pid:
                found.append((int(hwnd), window_title(hwnd)))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def all_windows_of_pid(pid: int) -> list[tuple[int, bool, str, str]]:
    found: list[tuple[int, bool, str, str]] = []

    def cb(hwnd, _lparam):
        if window_pid(hwnd) == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            found.append((int(hwnd), bool(user32.IsWindowVisible(hwnd)), window_title(hwnd), buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def wait_window(title: str, timeout: float = 25.0) -> int:
    end = time.time() + timeout
    while time.time() < end:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return int(hwnd)
        time.sleep(0.2)
    raise RuntimeError(f"等不到窗口：{title}")


def wait_window_of_pid(pid: int, title: str = "", timeout: float = 25.0) -> int:
    end = time.time() + timeout
    while time.time() < end:
        for hwnd, name in enum_windows(pid):
            if not title or title in name:
                return hwnd
        time.sleep(0.2)
    raise RuntimeError(f"等不到进程 {pid} 的窗口：{title or '(任意)'}")


def force_foreground(hwnd: int) -> bool:
    for _attempt in range(3):
        if user32.GetForegroundWindow() == hwnd:
            return True
        # 模拟一次 Alt 按键，解除 Windows 的前台锁定
        autotype._send_batch(
            [
                autotype._key_event(vk=autotype.VK_MENU),
                autotype._key_event(vk=autotype.VK_MENU, flags=autotype.KEYEVENTF_KEYUP),
            ]
        )
        ctid = kernel32.GetCurrentThreadId()
        fg = user32.GetForegroundWindow()
        ftid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if ftid and ftid != ctid:
            attached = bool(user32.AttachThreadInput(ctid, ftid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            try:
                user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass
        finally:
            if attached:
                user32.AttachThreadInput(ctid, ftid, False)
        time.sleep(0.3)
    return user32.GetForegroundWindow() == hwnd


def click_window(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    log(f"点击控件 0x{hwnd:X} 矩形=({rect.left},{rect.top})-({rect.right},{rect.bottom}) 点=({x},{y})")
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, None)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, None)  # LEFTUP
    time.sleep(0.25)
    return x, y


def focus_info() -> tuple[int, str]:
    fg = user32.GetForegroundWindow()
    hwnd = int(fg or 0)
    if not hwnd:
        return 0, ""
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    focus = 0
    if user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        focus = int(info.hwndFocus or 0)
    name = ""
    if focus:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(focus, buf, 256)
        name = buf.value
    return focus, name


def shot_window(hwnd: int, path: str) -> None:
    """把指定窗口截成 PNG（只截这个窗口，不截整个桌面）。"""
    try:
        from PIL import ImageGrab

        left, top, right, bottom = 0, 0, 0, 0
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        image = ImageGrab.grab(bbox=(left, top, right, bottom))
        image.save(path)
        log(f"已截图 {path} ({image.width}x{image.height})")
    except Exception as exc:
        log(f"截图失败：{exc}")


def send_hotkey(mods: list[int], vk: int) -> None:
    events = [autotype._key_event(vk=m) for m in mods]
    events.append(autotype._key_event(vk=vk))
    events.append(autotype._key_event(vk=vk, flags=autotype.KEYEVENTF_KEYUP))
    for m in reversed(mods):
        events.append(autotype._key_event(vk=m, flags=autotype.KEYEVENTF_KEYUP))
    autotype._send_batch(events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hotkey", default="Ctrl+Alt+J")
    parser.add_argument("--query", default="Git")
    parser.add_argument("--password", default="DemoOnly!2026")
    parser.add_argument("--expect-account", default="demo-alice@example.com")
    parser.add_argument("--expect-password", default="DemoPass!2026")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--shot", default="", help="在浮窗上截图保存到该路径（调试用）")
    args = parser.parse_args(argv)

    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    base = REPO / ".tmp_e2e"
    tmp = base / f"run-{os.getpid()}-{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / "vault.db"
    out_path = tmp / "result.json"
    status_path = tmp / "vault-status.json"
    shutil.copy2(REPO / "demo" / "demo-vault.db", db_path)

    from vault.db import VaultDB

    db = VaultDB.open(db_path, args.password)
    db.update_settings(hotkey=args.hotkey)
    db.close()

    env = dict(os.environ)
    env["KUAIFILL_DATA_DIR"] = str(tmp / "appdata")
    env["PYTHONIOENCODING"] = "utf-8"
    python = sys.executable

    target = subprocess.Popen(
        [python, str(REPO / "devtools" / "two_window_target.py"), "--out", str(out_path), "--autoclose", "90"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=env,
    )
    vault = None
    try:
        login_hwnd = wait_window("测试登录窗口")
        line = target.stdout.readline().strip()
        info = json.loads(line) if line.startswith("{") else {}
        account_hwnd = int(info.get("account") or 0)
        log(f"目标程序已启动 login=0x{login_hwnd:X} account=0x{account_hwnd:X}")

        vault = subprocess.Popen(
            [
                python,
                str(REPO / "devtools" / "dev_launcher.py"),
                "--db",
                str(db_path),
                "--unlock",
                args.password,
                "--status",
                str(status_path),
            ],
            cwd=str(REPO),
            env=env,
        )
        vault_hwnd = wait_window_of_pid(vault.pid, "快填密码库")
        vault_pid = window_pid(vault_hwnd)
        log(f"快填密码库已启动 hwnd=0x{vault_hwnd:X} pid={vault_pid}")
        time.sleep(2.2)  # 等自动解锁完成
        status = {}
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            log(f"密码库已解锁，实际快捷键={status.get('hotkey')!r} 提示={status.get('hotkey_error')!r}")
        else:
            log("警告：没有拿到解锁状态")

        user32.SetWindowPos(vault_hwnd, wintypes.HWND(-1), 0, 0, 1180, 760, 0x0004 | 0x0010)  # TOPMOST|NOACTIVATE
        time.sleep(0.4)

        # 模拟用户：点登录窗口的账号框（点击会让登录窗口成为前台）
        click_window(account_hwnd)
        fg = user32.GetForegroundWindow()
        focus, focus_cls = focus_info()
        log(f"点击后前台=0x{int(fg or 0):X} 焦点控件=0x{focus:X} class={focus_cls!r}")
        if int(fg or 0) != login_hwnd:
            log("警告：登录窗口没有成为前台")

        before = len(enum_windows(vault_pid))
        hotkey_text = status.get("hotkey") or args.hotkey
        try:
            mods, vk = autotype.parse_hotkey(hotkey_text)
        except Exception:
            mods, vk = autotype.MOD_CONTROL | autotype.MOD_ALT, ord("J")
        mod_vks = []
        if mods & autotype.MOD_CONTROL:
            mod_vks.append(autotype.VK_CONTROL)
        if mods & autotype.MOD_ALT:
            mod_vks.append(autotype.VK_MENU)
        if mods & autotype.MOD_SHIFT:
            mod_vks.append(autotype.VK_SHIFT)
        if mods & autotype.MOD_WIN:
            mod_vks.append(autotype.VK_LWIN)
        log(f"发送快捷键 {hotkey_text}（修饰键 {mod_vks}，主键 {vk}）")
        send_hotkey(mod_vks, vk)
        time.sleep(1.2)
        after = enum_windows(vault_pid)
        log(f"快填密码库窗口：{all_windows_of_pid(vault_pid)}")

        autotype.send_text(args.query, 30)
        time.sleep(0.7)
        if args.shot:
            shot_window(user32.GetForegroundWindow(), args.shot)
        autotype.send_key(autotype.VK_RETURN)
        time.sleep(2.5)
        log("已按 Enter，等待填写结果")

        if not args.keep_open:
            user32.SendMessageW(login_hwnd, 0x0010, 0, 0)  # WM_CLOSE -> 写出结果
        time.sleep(1.0)
        data = {}
        if out_path.exists():
            data = json.loads(out_path.read_text(encoding="utf-8"))
        account = data.get("account", "")
        password = data.get("password", "")
        ok = account == args.expect_account and password == args.expect_password
        log(f"结果：account={account!r} password={password!r} ok={ok}")
        print("E2E_RESULT " + json.dumps({"ok": ok, **data}, ensure_ascii=False), flush=True)
        return 0 if ok else 2
    finally:
        for proc in (vault, target):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        time.sleep(0.2)
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
