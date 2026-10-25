"""Windows 全局快捷键与自动填写（SendInput）。

工作方式：
1. 用户在登录页把光标放在账号框，按 Ctrl+Alt+K；
2. 程序记下当前前台窗口、**键盘焦点所在控件**、光标位置和窗口矩形；
3. 选中账号后，程序把前台和焦点还给原窗口，再输入“账号 + Tab + 密码”。

多窗口软件（主窗口 + 独立登录窗口、启动器 + 登录窗）的常见问题是：
窗口切回前台后，软件不会把键盘焦点还给输入框，此时 SendInput 仍会返回成功，
但按键被丢到顶层窗口，看起来就是“提示已填入，实际什么都没输”。
所以这里会显式把焦点还给快捷键按下时的那个控件，必要时在该控件位置补一次点击。

注意：自动填写只适合“账号框 → 密码框”这种常见顺序；如果网站顺序不同，
可以在详情页复制账号/密码后手动粘贴。
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
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
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
user32.IsChild.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

# ---------------------------------------------------------------------------
# 窗口操作
# ---------------------------------------------------------------------------

SW_RESTORE = 9
GA_ROOT = 2

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


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
user32.GetGUIThreadInfo.restype = wintypes.BOOL


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


def window_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_tid(hwnd: int) -> int:
    if not hwnd:
        return 0
    return int(user32.GetWindowThreadProcessId(hwnd, None) or 0)


def window_title(hwnd: int) -> str:
    hwnd = root_window(hwnd)
    if not hwnd:
        return ""
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except Exception:
        return ""


def window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:
        return ""


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    if not hwnd:
        return (0, 0, 0, 0)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


_advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_advapi32.GetTokenInformation.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

INTEGRITY_LOW = 0x1000
INTEGRITY_MEDIUM = 0x2000
INTEGRITY_HIGH = 0x3000
INTEGRITY_SYSTEM = 0x4000


def process_integrity(pid: int) -> int:
    """进程完整性级别：Low=0x1000 / Medium=0x2000 / High=0x3000；查询失败返回 0。

    Windows 的 UIPI 会静默丢弃低完整性进程发往更高完整性窗口的模拟输入，
    表现就是“提示已填入，但目标窗口没有任何内容”。
    """
    if not pid:
        return 0
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return 0
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
            if not size.value:
                return 0
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
                return 0
            label = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_MANDATORY_LABEL)).contents
            sid = ctypes.c_void_p(label.Label.Sid)
            count = _advapi32.GetSidSubAuthorityCount(sid).contents.value
            if not count:
                return 0
            return int(_advapi32.GetSidSubAuthority(sid, count - 1).contents.value)
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        return 0
    finally:
        kernel32.CloseHandle(handle)


def integrity_blocked(target_pid: int) -> str:
    """目标程序权限更高、Windows 会拦截输入时返回提示文字，否则返回空字符串。"""
    if not target_pid:
        return ""
    target_level = process_integrity(target_pid)
    own_level = process_integrity(os.getpid())
    if not target_level or not own_level or target_level <= own_level:
        return ""
    return "目标程序权限更高，Windows 会拦截输入。请把快填密码库放到普通目录运行，或与目标程序使用相同权限"


def thread_gui_info(tid: int) -> Optional[GUITHREADINFO]:
    if not tid:
        return None
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None
    return info


_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def list_windows(exclude_pid: int = 0, prefer_pid: int = 0) -> list[dict]:
    """列出所有可见、带标题的顶层窗口；同一程序的窗口排在前面。"""
    found: list[dict] = []
    own = int(exclude_pid or 0)

    def callback(hwnd, _lparam):
        try:
            hwnd = int(hwnd)
            if not user32.IsWindowVisible(hwnd):
                return True
            title = window_title(hwnd)
            if not title:
                return True
            pid = window_pid(hwnd)
            if own and pid == own:
                return True
            found.append(
                {
                    "hwnd": hwnd,
                    "title": title,
                    "pid": pid,
                    "class_name": window_class(hwnd),
                }
            )
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_ENUM_WINDOWS_PROC(callback), 0)
    except Exception:
        log.exception("枚举窗口失败")
    found.sort(key=lambda item: (0 if prefer_pid and item["pid"] == prefer_pid else 1, item["title"].casefold()))
    return found


def focused_window(hwnd: int) -> int:
    """返回某个窗口所在 GUI 线程当前拥有键盘焦点的控件。"""
    if not hwnd:
        return 0
    info = thread_gui_info(window_tid(hwnd))
    if not info:
        return 0
    return int(info.hwndFocus or 0)


def _cursor_pos() -> Optional[tuple[int, int]]:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return (int(point.x), int(point.y))


def _caret_point(info: Optional[GUITHREADINFO], focus: int) -> Optional[tuple[int, int]]:
    """把文本框里的插入符位置换算成屏幕坐标，用作“点击恢复焦点”的落点。"""
    if not info or not info.hwndCaret or not focus:
        return None
    rect = info.rcCaret
    if rect.right <= rect.left and rect.bottom <= rect.top:
        return None
    point = wintypes.POINT(int(rect.left + max(0, rect.right - rect.left) // 2), int(rect.bottom))
    if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
        return None
    return (int(point.x), int(point.y))


@dataclass
class TargetWindow:
    """快捷键按下时的目标窗口快照。"""

    hwnd: int = 0
    pid: int = 0
    tid: int = 0
    focus: int = 0
    caret: Optional[tuple[int, int]] = None
    cursor: Optional[tuple[int, int]] = None
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    title: str = ""
    class_name: str = ""
    source: str = "hotkey"

    @property
    def valid(self) -> bool:
        return bool(self.hwnd) and bool(user32.IsWindow(self.hwnd))

    def describe(self) -> str:
        name = self.title or self.class_name or f"0x{self.hwnd:X}"
        return f"{name}（{self.class_name}，pid {self.pid}）"


def capture_target(hwnd: Optional[int] = None, source: str = "hotkey") -> TargetWindow:
    """快照目标窗口：顶层句柄、焦点控件、插入符位置、光标位置。"""
    try:
        foreground = get_foreground_window()
        base = int(hwnd or foreground or 0)
        if not base:
            return TargetWindow()
        pid = window_pid(base)
        info = thread_gui_info(window_tid(base))
        focus = int(info.hwndFocus or 0) if info else 0
        if focus and window_pid(focus) != pid:
            focus = 0
        # 焦点控件所属的顶层窗口才是真正的登录窗口（多窗口软件里往往不是前台那个）
        root = root_window(focus) if focus else 0
        if not root or window_pid(root) != pid:
            root = root_window(base)
        caret = _caret_point(info, focus)
        return TargetWindow(
            hwnd=root,
            pid=pid,
            tid=window_tid(root),
            focus=focus if focus and root_window(focus) == root else 0,
            caret=caret,
            cursor=_cursor_pos(),
            rect=window_rect(root),
            title=window_title(root),
            class_name=window_class(root),
            source=source,
        )
    except Exception:
        log.exception("记录目标窗口失败")
        return TargetWindow(hwnd=int(hwnd or 0))


def _bring_to_front(hwnd: int) -> None:
    try:
        current = kernel32.GetCurrentThreadId()
        foreground = get_foreground_window()
        foreground_thread = window_tid(foreground) if foreground else 0
        attached = False
        if foreground_thread and foreground_thread != current:
            attached = bool(user32.AttachThreadInput(current, foreground_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current, foreground_thread, False)
    except Exception:
        log.exception("切换前台窗口失败")


def _tap_alt() -> None:
    """按一下 Alt，解除 Windows 的前台锁定限制。"""
    try:
        _send_batch(
            [
                _key_event(vk=VK_MENU),
                _key_event(vk=VK_MENU, flags=KEYEVENTF_KEYUP),
            ]
        )
    except OSError:
        pass


def activate_window(hwnd: int) -> int:
    """把窗口切回前台，返回实际处于前台的窗口句柄（0 表示失败）。"""
    hwnd = root_window(hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return 0
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.15)
    except Exception:
        pass

    target_pid = window_pid(hwnd)
    for attempt in range(3):
        if get_foreground_window() == hwnd:
            return hwnd
        _bring_to_front(hwnd)
        time.sleep(0.08 if attempt == 0 else 0.16)
        current = get_foreground_window()
        if current == hwnd:
            return hwnd
        _tap_alt()
        time.sleep(0.06)
        _bring_to_front(hwnd)
        time.sleep(0.1)
        current = get_foreground_window()
        if current == hwnd:
            return hwnd
        if current and window_pid(current) == target_pid:
            # 同一个程序的其他窗口在前台：也算切回来了，下面会再纠正焦点
            return current
    log.warning("切换前台窗口失败：目标 0x%X，当前 0x%X", hwnd, get_foreground_window())
    return 0


def _set_focus_cross(hwnd: int) -> bool:
    """跨线程/跨进程把键盘焦点设置到某个控件。"""
    if not hwnd:
        return False
    current = kernel32.GetCurrentThreadId()
    target_thread = window_tid(hwnd)
    attached = False
    try:
        if target_thread and target_thread != current:
            attached = bool(user32.AttachThreadInput(current, target_thread, True))
        user32.SetFocus(hwnd)
    except Exception:
        log.exception("设置焦点失败")
    finally:
        if attached:
            try:
                user32.AttachThreadInput(current, target_thread, False)
            except Exception:
                pass
    return focused_window(get_foreground_window()) == hwnd or focused_window(hwnd) == hwnd


def focus_level(target: TargetWindow) -> int:
    """2=焦点在目标窗口的控件上，1=焦点在同一程序的其他窗口/窗口本身，0=焦点不在这个程序。"""
    if not target.hwnd:
        return 0
    foreground = get_foreground_window()
    if not foreground or window_pid(foreground) != target.pid:
        return 0
    focus = focused_window(foreground)
    if not focus or window_pid(focus) != target.pid:
        return 0
    if focus == target.hwnd:
        # 焦点落在外框上而不是输入框里：按键会被丢弃，需要继续纠正
        return 1
    if user32.IsChild(target.hwnd, focus) or root_window(focus) == target.hwnd:
        return 2
    return 1


def click_point(target: TargetWindow) -> Optional[tuple[int, int]]:
    """挑一个既在目标窗口里、又尽量落在输入框上的点击位置。"""
    left, top, right, bottom = window_rect(target.hwnd)
    if right <= left or bottom <= top:
        left, top, right, bottom = target.rect
    if right <= left or bottom <= top:
        return None

    def inside(point: Optional[tuple[int, int]]) -> bool:
        if not point:
            return False
        x, y = point
        return left + 4 <= x <= right - 4 and top + 4 <= y <= bottom - 4

    if inside(target.caret):
        return target.caret
    if inside(target.cursor):
        return target.cursor
    if target.focus:
        fl, ft, fr, fb = window_rect(target.focus)
        if fr > fl and fb > ft and inside(((fl + fr) // 2, (ft + fb) // 2)):
            return ((fl + fr) // 2, (ft + fb) // 2)
    return None


def click_at(x: int, y: int) -> None:
    """在屏幕坐标处点一下左键（用于把焦点真正放进输入框）。"""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    vh = max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    user32.SetCursorPos(int(x), int(y))
    move = INPUT()
    move.type = INPUT_MOUSE
    move.mi = MOUSEINPUT(
        dx=nx,
        dy=ny,
        mouseData=0,
        dwFlags=0x0001 | 0x8000,  # MOVE | ABSOLUTE
        time=0,
        dwExtraInfo=0,
    )
    down = INPUT()
    down.type = INPUT_MOUSE
    down.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0, dwFlags=0x0002 | 0x8000, time=0, dwExtraInfo=0)
    up = INPUT()
    up.type = INPUT_MOUSE
    up.mi = MOUSEINPUT(dx=nx, dy=ny, mouseData=0, dwFlags=0x0004 | 0x8000, time=0, dwExtraInfo=0)
    _send_batch([move, down, up])


def place_focus(target: TargetWindow) -> tuple[int, str]:
    """把键盘焦点放回目标窗口：先还原焦点控件，再不行就点一下输入框。"""
    level = focus_level(target)
    if level >= 2:
        return level, "焦点已在目标窗口"

    log.info(
        "焦点检查：目标 0x%X，记录的焦点控件 0x%X，当前前台 0x%X，当前焦点 0x%X（%s）",
        target.hwnd,
        target.focus,
        get_foreground_window(),
        focused_window(get_foreground_window()),
        window_class(focused_window(get_foreground_window())),
    )

    if target.focus and user32.IsWindow(target.focus) and user32.IsWindowVisible(target.focus):
        if window_pid(target.focus) == target.pid and root_window(target.focus) == target.hwnd:
            _set_focus_cross(target.focus)
            time.sleep(0.06)
            level = focus_level(target)
            if level >= 2:
                return level, "已把焦点还给输入框"

    point = click_point(target)
    if point:
        click_at(*point)
        time.sleep(0.12)
        level = focus_level(target)
        if level >= 2:
            return level, "已点击输入框恢复焦点"

    return focus_level(target), "无法确认焦点在输入框"


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


def fill_target(target: TargetWindow, entry, submit: bool = False, delay_ms: int = 8) -> tuple[bool, str]:
    """切回目标窗口 → 把焦点还给输入框 → 输入账号和密码。

    返回 (是否已输入, 说明)。焦点确认不在目标程序时不会输入，
    避免把密码打进别的窗口。
    """
    if not target or not target.hwnd:
        return False, "没有目标窗口"
    try:
        blocked = integrity_blocked(target.pid)
        if blocked:
            log.warning("权限不一致，已放弃输入：%s", blocked)
            return False, blocked
        release_modifiers()
        actual = activate_window(target.hwnd)
        if not actual:
            return False, "无法切回目标窗口"
        if window_pid(actual) != target.pid:
            return False, "目标窗口被其他程序挡住"

        level, how = place_focus(target)
        if level == 0:
            return False, "无法把焦点放进登录框（可先在输入框点一下再重试）"
        log.info(
            "填写目标 %s：前台 0x%X，焦点级别 %d（%s）",
            target.describe(),
            actual,
            level,
            how,
        )
        time.sleep(0.05)
        fill_entry(entry, submit=submit, delay_ms=delay_ms)
        time.sleep(0.05)
        after = focus_level(target)
        if after == 0:
            return False, "输入时焦点被抢走，请先点一下输入框"
        if after < level:
            return True, "焦点可能不在输入框"
        return True, ""
    except OSError as exc:
        log.exception("自动填写失败")
        return False, f"输入失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        log.exception("自动填写失败")
        return False, f"输入失败：{exc}"


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
    """在后台线程注册系统级快捷键。回调在后台线程执行，UI 请用队列转交。

    回调参数是快捷键按下瞬间的 TargetWindow 快照：那一刻用户的光标还在登录框里，
    能拿到最准确的焦点控件和光标位置。
    """

    _HOTKEY_ID = 0xB001

    def __init__(self, hotkey: str, callback: Callable[[TargetWindow], None]):
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
                    target = capture_target(get_foreground_window(), source="hotkey")
                    try:
                        self.callback(target)
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
