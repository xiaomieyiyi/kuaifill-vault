"""Windows 全局快捷键与自动填写（SendInput）。

工作方式：
1. 用户在登录页把光标放在账号框，按 Ctrl+Alt+K；
2. 程序记下当前前台窗口，弹出搜索框；
3. 选中账号后，程序把焦点还给原窗口，输入“账号 + Tab + 密码”。

注意：自动填写只适合“账号框 → 密码框”这种常见顺序；如果网站顺序不同，
可以在详情页复制账号/密码后手动粘贴。
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

# ---------------------------------------------------------------------------
# 窗口操作
# ---------------------------------------------------------------------------

SW_RESTORE = 9
GA_ROOT = 2


def get_foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def root_window(hwnd: int) -> int:
    """把 Tk 子窗口/控件句柄归一化成顶层窗口句柄。"""
    if not hwnd:
        return 0
    try:
        root = user32.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)
    except Exception:
        return hwnd


def force_foreground(hwnd: int) -> bool:
    """尽量把窗口切回前台，并恢复最小化状态。"""
    hwnd = root_window(hwnd)
    if not hwnd:
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        if get_foreground_window() == hwnd:
            return True
        current_thread = kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached = False
        if target_thread and target_thread != current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
        time.sleep(0.12)
        return get_foreground_window() == hwnd
    except Exception:
        log.exception("切换前台窗口失败")
        return False


# ---------------------------------------------------------------------------
# SendInput
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_BACK = 0x08

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


def _key_event(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _send_batch(events: list[INPUT]) -> None:
    if not events:
        return
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        err = ctypes.get_last_error()
        raise OSError(f"SendInput 只发送了 {sent}/{len(events)} 个事件（错误 {err}）")


def release_modifiers() -> None:
    """快捷键触发时 Ctrl/Alt 可能仍被按住，先松开，避免输入变成快捷键。"""
    events = []
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
        events.append(_key_event(vk=vk, flags=KEYEVENTF_KEYUP))
    try:
        _send_batch(events)
    except OSError:
        log.exception("释放修饰键失败")


def send_key(vk: int, delay_ms: int = 0) -> None:
    _send_batch([_key_event(vk=vk), _key_event(vk=vk, flags=KEYEVENTF_KEYUP)])
    if delay_ms:
        time.sleep(max(0, delay_ms) / 1000)


def send_text(text: str, delay_ms: int = 8) -> None:
    """用 Unicode 方式输入，绕过键盘布局；\\t 输入 Tab，换行输入 Enter。"""
    if not text:
        return
    delay_ms = max(0, min(int(delay_ms), 100))
    for ch in text:
        if ch == "\t":
            send_key(VK_TAB, delay_ms)
            continue
        if ch in "\r\n":
            send_key(VK_RETURN, delay_ms)
            continue
        encoded = ch.encode("utf-16-le", errors="replace")
        for i in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[i : i + 2], "little")
            # 每个 UTF-16 码元一对 down/up 事件
            _send_batch(
                [
                    _key_event(scan=unit, flags=KEYEVENTF_UNICODE),
                    _key_event(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                ]
            )
        if delay_ms:
            time.sleep(delay_ms / 1000)


def fill_entry(entry, submit: bool = False, delay_ms: int = 8) -> None:
    """输入账号、Tab、密码；submit=True 时最后按回车。"""
    release_modifiers()
    time.sleep(0.05)
    if entry.account:
        send_text(entry.account, delay_ms)
    if entry.account and entry.password:
        send_key(VK_TAB, delay_ms)
    if entry.password:
        send_text(entry.password, delay_ms)
    if submit:
        time.sleep(0.05)
        send_key(VK_RETURN, 0)


# ---------------------------------------------------------------------------
# 全局快捷键
# ---------------------------------------------------------------------------

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

_KEYS = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update({f"F{i}": 0x70 + i - 1 for i in range(1, 25)})
_KEYS.update({"SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D})


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少需要一个修饰键和一个主键，例如 Ctrl+Alt+K")
    mods = 0
    key = None
    for part in parts:
        if part in _MOD_NAMES:
            mods |= _MOD_NAMES[part]
        else:
            upper = part.upper()
            if upper not in _KEYS:
                raise ValueError(f"无法识别的按键：{part}")
            if key is not None:
                raise ValueError("只能有一个主键")
            key = _KEYS[upper]
    if not mods:
        raise ValueError("至少需要一个 Ctrl/Alt/Shift/Win 修饰键")
    if key is None:
        raise ValueError("缺少主键，例如 Ctrl+Alt+K 中的 K")
    return mods, key


def format_hotkey(text: str) -> str:
    mods, key = parse_hotkey(text)
    names = []
    for bit, name in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win")):
        if mods & bit:
            names.append(name)
    key_name = next((k for k, v in _KEYS.items() if v == key and len(k) == 1), None)
    if key_name is None:
        key_name = next((k for k, v in _KEYS.items() if v == key), "?")
    return "+".join(names + [key_name])


class HotkeyManager:
    """在后台线程注册系统级快捷键。回调在后台线程执行，UI 请用队列转交。"""

    _HOTKEY_ID = 0xB001

    def __init__(self, hotkey: str, callback: Callable[[int], None]):
        self.hotkey = hotkey
        self.callback = callback
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._ok = False
        self._error = ""
        self._stop = False

    @property
    def error(self) -> str:
        return self._error

    def start(self, timeout: float = 2.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._ok

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            mods, vk = parse_hotkey(self.hotkey)
        except Exception as exc:
            self._error = str(exc)
            self._ready.set()
            return
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            self._error = f"快捷键 {format_hotkey(self.hotkey)} 注册失败（可能被其他程序占用，错误 {err}）"
            self._ready.set()
            return
        self._ok = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while not self._stop:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == self._HOTKEY_ID:
                    hwnd = get_foreground_window()
                    try:
                        self.callback(hwnd)
                    except Exception:
                        log.exception("快捷键回调失败")
        finally:
            user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def stop(self) -> None:
        self._stop = True
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
