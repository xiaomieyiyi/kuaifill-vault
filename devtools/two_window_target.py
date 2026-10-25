"""开发/测试用：模拟“主窗口 + 独立登录窗口”的登录软件。

用途：复现并验证“目标软件有两个窗口时，快填密码库填不进去”的问题。

模拟的关键行为（这类软件里很常见）：
1. 主窗口和登录窗口是两个独立的顶层窗口；
2. 登录窗口失去激活时清空键盘焦点（SetFocus(0)），重新激活后不恢复焦点；
3. 登录窗口用的是普通 EDIT 控件。

用法：
    python devtools/two_window_target.py --out result.json [--autoclose 60]

关闭窗口（或 --autoclose 超时）时，把两个输入框的文本写进 --out 指定的 JSON。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_CONTROLPARENT = 0x00010000
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
WS_TABSTOP = 0x00010000
ES_AUTOHSCROLL = 0x0080
ES_PASSWORD = 0x0020
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_ACTIVATE = 0x0006
WM_TIMER = 0x0113
WA_INACTIVE = 0
COLOR_WINDOW = 5
IDC_ARROW = 32512
SW_SHOW = 5


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SendMessageW.restype = LRESULT
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

g = {
    "main": 0,
    "login": 0,
    "account": 0,
    "password": 0,
    "out": "",
    "done": False,
}
_procs: list = []


def _text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, buf, 1024)
    return buf.value


def _finish() -> None:
    if g["done"]:
        return
    g["done"] = True
    data = {
        "account": _text(g["account"]),
        "password": _text(g["password"]),
        "windows": {"main": int(g["main"] or 0), "login": int(g["login"] or 0)},
    }
    if g["out"]:
        with open(g["out"], "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    for key in ("login", "main"):
        if g[key]:
            user32.DestroyWindow(g[key])
            g[key] = 0


def _main_proc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        _finish()
        return 0
    if msg == WM_TIMER:
        _finish()
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _login_proc(hwnd, msg, wparam, lparam):
    if msg == WM_CLOSE:
        _finish()
        return 0
    if msg == WM_ACTIVATE:
        # 模拟“失去激活就清空焦点、重新激活不恢复”的登录窗口
        if (int(wparam) & 0xFFFF) == WA_INACTIVE:
            user32.SetFocus(None)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--autoclose", type=int, default=90)
    args = parser.parse_args(argv)
    g["out"] = args.out

    try:  # 与快填密码库一致：按显示器 DPI 感知，坐标都是物理像素
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    hinst = kernel32.GetModuleHandleW(None)
    cursor = user32.LoadCursorW(None, IDC_ARROW)

    main_proc = WNDPROC(_main_proc)
    login_proc = WNDPROC(_login_proc)
    _procs.extend([main_proc, login_proc])

    for cls, proc in (("KFTestMainWindow", main_proc), ("KFTestLoginWindow", login_proc)):
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hCursor = cursor
        wc.hbrBackground = COLOR_WINDOW + 1
        wc.lpszClassName = cls
        if not user32.RegisterClassW(ctypes.byref(wc)):
            print("RegisterClass failed:", ctypes.get_last_error(), file=sys.stderr)

    g["main"] = user32.CreateWindowExW(
        WS_EX_TOPMOST, "KFTestMainWindow", "测试主窗口", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        1420, 60, 520, 260, None, None, hinst, None,
    )
    g["login"] = user32.CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_CONTROLPARENT, "KFTestLoginWindow", "测试登录窗口", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
        1420, 360, 520, 300, None, None, hinst, None,
    )

    font = gdi32.GetStockObject(17)  # DEFAULT_GUI_FONT
    for text, y in (("账号", 24), ("密码", 82)):
        user32.CreateWindowExW(0, "STATIC", text, WS_CHILD | WS_VISIBLE, 28, y, 80, 24, g["login"], None, hinst, None)
    g["account"] = user32.CreateWindowExW(
        0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL,
        28, 46, 450, 30, g["login"], None, hinst, None,
    )
    g["password"] = user32.CreateWindowExW(
        0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_PASSWORD | ES_AUTOHSCROLL,
        28, 104, 450, 30, g["login"], None, hinst, None,
    )
    for hwnd in (g["account"], g["password"]):
        user32.SendMessageW(hwnd, 0x0030, font, 0)  # WM_SETFONT
    user32.CreateWindowExW(
        0, "BUTTON", "登录", WS_CHILD | WS_VISIBLE | WS_TABSTOP, 388, 160, 90, 34, g["login"], None, hinst, None
    )

    user32.ShowWindow(g["main"], SW_SHOW)
    user32.ShowWindow(g["login"], SW_SHOW)
    user32.SetForegroundWindow(g["login"])
    user32.SetFocus(g["account"])
    user32.SetTimer(g["main"], 1, max(1, args.autoclose) * 1000, None)

    print(json.dumps({"login": int(g["login"]), "account": int(g["account"])}), flush=True)

    msg = wintypes.MSG()
    user32.IsDialogMessageW.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.MSG)]
    user32.IsDialogMessageW.restype = wintypes.BOOL
    user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.IsChild.restype = wintypes.BOOL
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        # 让 Tab 能在两个输入框之间切换（和真实登录窗口一致）
        if g["login"] and (
            int(msg.hWnd or 0) == g["login"] or user32.IsChild(g["login"], msg.hWnd)
        ):
            if user32.IsDialogMessageW(g["login"], ctypes.byref(msg)):
                continue
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    if not g["done"]:
        _finish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
