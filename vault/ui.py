"""主界面：解锁、创建、列表、详情、自动锁定、快捷键调度。"""

from __future__ import annotations

import logging
import os
import queue
import re
import time
import tkinter as tk
import webbrowser
import ctypes
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from . import autotype, theme
from .crypto import TamperedData, WrongPassword, password_strength
from .db import (
    DEFAULT_SETTINGS,
    VaultDB,
    VaultLocked,
    VaultNotFound,
    Entry,
    local_time,
)
from .dialogs import EntryDialog, SettingsDialog
from .overlay import QuickFillOverlay
from .paths import app_dir, default_db_path, resource_dir

log = logging.getLogger(__name__)


class VaultApp:
    def __init__(self, root: tk.Tk, db_path: Optional[str | Path] = None, enable_hotkey: bool = True):
        self.root = root
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.enable_hotkey = enable_hotkey
        self.db: Optional[VaultDB] = None
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self._selected_id: Optional[str] = None
        self._filter: str = "all"
        self._cards: dict[str, dict] = {}
        self._current_entries: list[Entry] = []
        self._dialogs: set[tk.Toplevel] = set()
        self._overlay: Optional[QuickFillOverlay] = None
        self._hotkey = None
        self._hotkey_error = ""
        self._hotkey_queue: queue.Queue[autotype.TargetWindow] = queue.Queue()
        self._last_external: autotype.TargetWindow = autotype.TargetWindow()
        self._last_activity = time.monotonic()
        self._clipboard_text: Optional[str] = None
        self._clipboard_job = None
        self._status_job = None
        self._search_job = None
        self._closing = False
        self.opacity = 1.0
        self.status_label: Optional[tk.Label] = None
        self.hotkey_hint: Optional[tk.Label] = None
        self._default_status = "已解锁 · 数据仅保存在这台电脑"

        self.root.title("快填密码库 · KuaiFill Vault")
        self.root.configure(bg=theme.BG)
        self.root.minsize(1120, 720)
        self.root.geometry("1500x960")
        theme.setup_style(self.root)
        self._setup_fonts()
        self._set_icon()

        self.container = tk.Frame(self.root, bg=theme.BG)
        self.container.pack(fill="both", expand=True)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_var)

        self.root.bind_all("<KeyPress>", self._touch_activity, add="+")
        self.root.bind_all("<ButtonPress>", self._touch_activity, add="+")
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.root.bind_all("<MouseWheel>", self._touch_activity, add="+")
        self.root.bind("<Control-f>", lambda _e: self._focus_search())
        self.root.bind("<Control-n>", lambda _e: self.new_entry())
        self.root.bind("<Control-l>", lambda _e: self.lock_vault())
        self.root.bind("<Control-b>", lambda _e: self.backup_now())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_callback_exception

        if enable_hotkey:
            self._start_hotkey()
        self.show_initial()
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        self._center_physical()
        self.root.after(700, self._center_physical)
        self.root.after(120, self._poll_hotkey)
        self.root.after(350, self._track_foreground)
        self.root.after(5000, self._auto_lock_tick)

    # ------------------------------------------------------------------ 基础
    def _setup_fonts(self) -> None:
        import tkinter.font as tkfont

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=theme.FONT_FAMILY, size=10)
            except tk.TclError:
                pass

    def _set_icon(self) -> None:
        icon = resource_dir() / "assets" / "icon.ico"
        if icon.exists():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _center_physical(self) -> None:
        """按真实像素把主窗口居中（Tk 在高 DPI 下的坐标可能被缩放）。"""
        try:
            hwnd = autotype.root_window(self.root.winfo_id())
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2 - 24)
            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004)
        except Exception:
            pass

    def _clear_container(self) -> None:
        for child in self.container.winfo_children():
            child.destroy()
        self.status_label = None
        self.hotkey_hint = None
        self._cards = {}
        self._current_entries = []

    def _report_callback_exception(self, exc, val, tb) -> None:
        log.error("界面回调异常", exc_info=(exc, val, tb))
        try:
            messagebox.showerror(
                "程序遇到一个错误",
                f"操作没有完成：{val}\n\n详细信息已写入 data/app.log。",
                parent=self.root,
            )
        except Exception:
            pass

    def show_initial(self) -> None:
        if VaultDB.exists(self.db_path):
            self.show_lock()
        else:
            self.show_setup()

    # ------------------------------------------------------------------ 登录/创建外壳
    def _auth_shell(self):
        self._clear_container()
        self.container.configure(bg=theme.BG)
        brand = tk.Frame(self.container, bg=theme.SIDEBAR, width=470)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        self._build_brand(brand)
        right = tk.Frame(self.container, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True)
        return right

    def _build_brand(self, parent) -> None:
        canvas = tk.Canvas(parent, bg=theme.SIDEBAR, highlightthickness=0)
        canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        canvas.create_oval(-180, 470, 360, 1010, outline="#1B3054", width=2)
        canvas.create_oval(-90, 560, 270, 920, outline="#1B3054", width=2)
        canvas.create_line(54, 610, 260, 610, fill=theme.ACCENT, width=2)
        for x in (292, 322, 352):
            canvas.create_oval(x, 606, x + 8, 614, fill="#2A4672", outline="")

        logo_row = tk.Frame(parent, bg=theme.SIDEBAR)
        logo_row.pack(anchor="w", padx=54, pady=(54, 0))
        logo = tk.Frame(logo_row, bg=theme.ACCENT, width=40, height=40)
        logo.pack(side="left")
        logo.pack_propagate(False)
        tk.Label(
            logo, text="KF", bg=theme.ACCENT, fg=theme.WHITE, font=(theme.FONT_FAMILY, 12, "bold")
        ).pack(expand=True)
        names = tk.Frame(logo_row, bg=theme.SIDEBAR)
        names.pack(side="left", padx=(12, 0))
        tk.Label(
            names, text="快填密码库", bg=theme.SIDEBAR, fg=theme.WHITE, font=(theme.FONT_FAMILY, 13, "bold")
        ).pack(anchor="w")
        tk.Label(
            names, text="LOCAL VAULT", bg=theme.SIDEBAR, fg=theme.SIDEBAR_MUTED, font=(theme.FONT_FAMILY, 7)
        ).pack(anchor="w")

        tk.Label(
            parent,
            text="所有账号，\n一个本地密码库。",
            bg=theme.SIDEBAR,
            fg=theme.WHITE,
            font=(theme.FONT_FAMILY, 22, "bold"),
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=54, pady=(64, 0))
        tk.Label(
            parent,
            text="不用再翻记事本或聊天记录。\n账号与密码分开保存，登录时一键填入。",
            bg=theme.SIDEBAR,
            fg="#9FB0CA",
            font=(theme.FONT_FAMILY, 9),
            justify="left",
            anchor="w",
            wraplength=350,
        ).pack(anchor="w", padx=54, pady=(14, 0))

        bullets = [
            ("AES-256-GCM 认证加密", "数据库里没有明文密码"),
            ("主密码只在你手里", "不联网、不上传、无后门"),
            ("Ctrl+Alt+K 快速填充", "自动输入账号和密码"),
        ]
        bullet_box = tk.Frame(parent, bg=theme.SIDEBAR)
        bullet_box.pack(anchor="w", padx=54, pady=(36, 0))
        for title, sub in bullets:
            row = tk.Frame(bullet_box, bg=theme.SIDEBAR)
            row.pack(anchor="w", pady=7)
            dot = tk.Canvas(row, width=14, height=14, bg=theme.SIDEBAR, highlightthickness=0)
            dot.pack(side="left", pady=(3, 0))
            dot.create_oval(2, 2, 10, 10, fill=theme.TEAL, outline="")
            texts = tk.Frame(row, bg=theme.SIDEBAR)
            texts.pack(side="left", padx=(8, 0))
            tk.Label(
                texts, text=title, bg=theme.SIDEBAR, fg="#DCE6F5", font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
            ).pack(anchor="w")
            tk.Label(
                texts, text=sub, bg=theme.SIDEBAR, fg=theme.SIDEBAR_MUTED, font=(theme.FONT_FAMILY, 8), anchor="w"
            ).pack(anchor="w")

        footer = tk.Frame(parent, bg=theme.SIDEBAR)
        footer.pack(side="bottom", anchor="w", padx=54, pady=36, fill="x")
        tk.Label(
            footer,
            text="数据只保存在这台电脑上",
            bg=theme.SIDEBAR,
            fg="#9FB0CA",
            font=(theme.FONT_FAMILY, 8, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            footer,
            text="请定期备份 data 文件夹。忘记主密码将无法恢复。",
            bg=theme.SIDEBAR,
            fg=theme.SIDEBAR_MUTED,
            font=(theme.FONT_FAMILY, 7),
            anchor="w",
            wraplength=350,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

    def _auth_card(self, right) -> tk.Frame:
        wrap = tk.Frame(right, bg=theme.BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        card = tk.Frame(
            wrap,
            bg=theme.CARD,
            highlightbackground=theme.BORDER,
            highlightthickness=1,
        )
        card.pack()
        body = tk.Frame(card, bg=theme.CARD)
        body.pack(padx=42, pady=34)
        return body

    # ------------------------------------------------------------------ 解锁
    def show_lock(self, message: str = "") -> None:
        right = self._auth_shell()
        body = self._auth_card(right)
        tk.Frame(body, bg=theme.ACCENT, width=42, height=3).pack(anchor="w", pady=(0, 16))
        tk.Label(
            body, text="本地密码库", bg=theme.CARD, fg=theme.ACCENT, font=(theme.FONT_FAMILY, 8, "bold"), anchor="w"
        ).pack(anchor="w")
        tk.Label(
            body, text="欢迎回来", bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 24, "bold"), anchor="w"
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            body,
            text="输入主密码解锁。所有账号只会保存在这台电脑上。",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(6, 20))

        self.unlock_var = tk.StringVar()
        tk.Label(
            body, text="主密码", bg=theme.CARD, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
        ).pack(anchor="w", pady=(0, 5))
        entry = theme.make_entry(body, self.unlock_var, show="●", width=36, font_size=12)
        entry.pack(fill="x", ipady=9)
        self.unlock_entry = entry
        show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body,
            text="显示密码",
            variable=show_var,
            command=lambda: entry.configure(show="" if show_var.get() else "●"),
            style="TCheckbutton",
        ).pack(anchor="w", pady=(8, 0))

        self.auth_error = tk.StringVar(value=message)
        tk.Label(
            body,
            textvariable=self.auth_error,
            bg=theme.CARD,
            fg=theme.WARN if message else theme.DANGER,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x", pady=(10, 4), anchor="w")

        self.unlock_btn = theme.make_button(body, "解锁密码库", self._do_unlock, kind="primary", pady=11)
        self.unlock_btn.pack(fill="x", pady=(4, 10))
        entry.bind("<Return>", lambda _e: self._do_unlock())

        links = tk.Frame(body, bg=theme.CARD)
        links.pack(fill="x", pady=(2, 0))
        forgot = tk.Label(
            links,
            text="忘记主密码？",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            cursor="hand2",
        )
        forgot.pack(side="left")
        forgot.bind("<Button-1>", lambda _e: self._forgot_password())
        tk.Label(
            links,
            text=f"{self.settings.get('hotkey', 'Ctrl+Alt+K')} 快速填充",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        ).pack(side="right")

        tk.Frame(body, bg=theme.BORDER, height=1).pack(fill="x", pady=(22, 10))
        tk.Label(
            body,
            text=str(self.db_path),
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x")
        if self._hotkey_error:
            tk.Label(
                body,
                text=self._hotkey_error,
                bg=theme.CARD,
                fg=theme.WARN,
                font=(theme.FONT_FAMILY, 8),
                anchor="w",
                wraplength=360,
                justify="left",
            ).pack(fill="x", pady=(6, 0))
        self.root.after(120, lambda: self._safe_focus(entry))

    def _forgot_password(self) -> None:
        messagebox.showinfo(
            "无法找回主密码",
            "主密码没有保存在任何地方，因此无法找回，也无法绕过。\n\n"
            "如果只是忘记了，可以尝试你常用的几个主密码。\n\n"
            "如果确定无法想起：请先备份 data 文件夹，再删除其中的 vault.db，"
            "程序会让你重新创建一个空密码库。旧数据将无法解密。",
            parent=self.root,
        )

    def _do_unlock(self) -> None:
        password = self.unlock_var.get()
        if not password:
            self.auth_error.set("请输入主密码")
            self.unlock_entry.focus_set()
            return
        self.unlock_btn.configure(state="disabled", text="正在解锁…")
        self.auth_error.set("")
        self.root.update_idletasks()
        try:
            db = VaultDB.open(self.db_path, password)
        except WrongPassword:
            self.auth_error.set("主密码不正确，请重试")
            self.unlock_btn.configure(state="normal", text="解锁密码库")
            self.unlock_var.set("")
            self.unlock_entry.focus_set()
            return
        except TamperedData as exc:
            self.auth_error.set(f"数据库无法解密：{exc}")
            self.unlock_btn.configure(state="normal", text="解锁密码库")
            return
        except VaultNotFound:
            self.auth_error.set("密码库文件不存在，可能被移动或删除")
            self.unlock_btn.configure(state="normal", text="解锁密码库")
            return
        except Exception as exc:
            log.exception("打开密码库失败")
            self.auth_error.set(f"打开失败：{exc}")
            self.unlock_btn.configure(state="normal", text="解锁密码库")
            return
        self.db = db
        self._apply_saved_hotkey(db)
        self.settings = db.settings
        self._last_activity = time.monotonic()
        try:
            self.unlock_var.set("")
        except Exception:
            pass
        self.apply_appearance(rebuild=False)
        self.show_main()
        self._status("密码库已解锁")

    # ------------------------------------------------------------------ 创建
    def show_setup(self) -> None:
        right = self._auth_shell()
        body = self._auth_card(right)
        tk.Frame(body, bg=theme.TEAL, width=42, height=3).pack(anchor="w", pady=(0, 16))
        tk.Label(
            body, text="首次使用", bg=theme.CARD, fg=theme.TEAL, font=(theme.FONT_FAMILY, 8, "bold"), anchor="w"
        ).pack(anchor="w")
        tk.Label(
            body, text="创建密码库", bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 24, "bold"), anchor="w"
        ).pack(anchor="w", pady=(4, 0))
        tk.Label(
            body,
            text="设置一个只有你知道的主密码。账号密码会用 AES-256-GCM 加密后保存。",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=370,
            justify="left",
        ).pack(anchor="w", pady=(6, 18))

        self.master_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        self.ack_var = tk.BooleanVar(value=False)
        self.setup_show_var = tk.BooleanVar(value=False)

        tk.Label(
            body, text="主密码（至少 8 位）", bg=theme.CARD, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
        ).pack(anchor="w", pady=(0, 5))
        master_entry = theme.make_entry(body, self.master_var, show="●", width=36, font_size=12)
        master_entry.pack(fill="x", ipady=9)
        self.setup_strength = tk.Canvas(body, width=360, height=7, bg=theme.CARD, highlightthickness=0)
        self.setup_strength.pack(fill="x", pady=(8, 0))
        self.setup_strength_label = tk.Label(
            body, text="", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 8), anchor="w"
        )
        self.setup_strength_label.pack(fill="x", pady=(3, 10))

        tk.Label(
            body, text="再次输入主密码", bg=theme.CARD, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
        ).pack(anchor="w", pady=(0, 5))
        confirm_entry = theme.make_entry(body, self.confirm_var, show="●", width=36, font_size=12)
        confirm_entry.pack(fill="x", ipady=9)

        ttk.Checkbutton(
            body,
            text="显示密码",
            variable=self.setup_show_var,
            command=lambda: self._toggle_setup_show(master_entry, confirm_entry),
            style="TCheckbutton",
        ).pack(anchor="w", pady=(8, 0))

        warning = tk.Frame(body, bg=theme.WARN_SOFT)
        warning.pack(fill="x", pady=(14, 10))
        tk.Label(
            warning,
            text="请记住主密码。它不会保存在任何地方，忘记后无法找回，也无法绕过。",
            bg=theme.WARN_SOFT,
            fg=theme.WARN,
            font=(theme.FONT_FAMILY, 8),
            wraplength=340,
            justify="left",
            anchor="w",
        ).pack(padx=12, pady=10, fill="x")

        self.ack_check = ttk.Checkbutton(
            body,
            text="我已记住主密码，并理解无法找回",
            variable=self.ack_var,
            command=self._update_create_state,
            style="TCheckbutton",
        )
        self.ack_check.pack(anchor="w", pady=(0, 8))

        self.auth_error = tk.StringVar()
        tk.Label(
            body,
            textvariable=self.auth_error,
            bg=theme.CARD,
            fg=theme.DANGER,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x", pady=(0, 4))

        self.create_btn = theme.make_button(body, "创建密码库", self._do_create, kind="teal", pady=11)
        self.create_btn.pack(fill="x", pady=(2, 10))
        self.create_btn.configure(state="disabled")
        self.master_var.trace_add("write", lambda *_: self._update_setup_strength())
        self.master_var.trace_add("write", lambda *_: self._update_create_state())
        self.confirm_var.trace_add("write", lambda *_: self._update_create_state())
        confirm_entry.bind("<Return>", lambda _e: self._do_create())

        tk.Frame(body, bg=theme.BORDER, height=1).pack(fill="x", pady=(16, 10))
        tk.Label(
            body,
            text=f"将创建：{self.db_path}",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x")
        self.root.after(120, lambda: self._safe_focus(master_entry))
        self.root.after(60, self._update_setup_strength)

    def _safe_focus(self, widget) -> None:
        try:
            if widget.winfo_exists():
                widget.focus_set()
        except tk.TclError:
            pass

    def _toggle_setup_show(self, master_entry, confirm_entry) -> None:
        show = "" if self.setup_show_var.get() else "●"
        master_entry.configure(show=show)
        confirm_entry.configure(show=show)

    def _update_setup_strength(self) -> None:
        canvas = getattr(self, "setup_strength", None)
        if not canvas:
            return
        try:
            if not canvas.winfo_exists():
                return
        except tk.TclError:
            return
        pwd = self.master_var.get()
        canvas.delete("all")
        if not pwd:
            self.setup_strength_label.configure(text="建议至少 12 位，混合大小写、数字和符号", fg=theme.FAINT)
            return
        score, label = password_strength(pwd)
        colors = [theme.DANGER, theme.DANGER, theme.WARN, theme.TEAL, "#067647"]
        width = max(1, self.setup_strength.winfo_width() - 8)
        if width < 50:
            width = 340
        for i in range(4):
            x0 = i * (width / 4) + (i * 4)
            x1 = (i + 1) * (width / 4) - 4
            color = colors[score] if i < max(1, score) else "#E8EDF5"
            self.setup_strength.create_rectangle(x0, 1, x1, 6, fill=color, outline="")
        self.setup_strength_label.configure(text=f"密码强度：{label} · {len(pwd)} 位", fg=colors[score])

    def _update_create_state(self) -> None:
        if not hasattr(self, "create_btn"):
            return
        try:
            if not self.create_btn.winfo_exists():
                return
            self.create_btn.configure(state="normal" if self.ack_var.get() else "disabled")
        except tk.TclError:
            pass

    def _do_create(self) -> None:
        if not self.ack_var.get():
            self.auth_error.set("请先勾选“我已记住主密码”")
            return
        pwd = self.master_var.get()
        confirm = self.confirm_var.get()
        if len(pwd) < 8:
            self.auth_error.set("主密码至少需要 8 个字符")
            return
        if pwd != confirm:
            self.auth_error.set("两次输入的主密码不一致")
            return
        if VaultDB.exists(self.db_path):
            self.auth_error.set("密码库文件已存在，请返回解锁")
            return
        self.create_btn.configure(state="disabled", text="正在创建…")
        self.auth_error.set("")
        self.root.update_idletasks()
        try:
            db = VaultDB.create(self.db_path, pwd)
        except Exception as exc:
            log.exception("创建密码库失败")
            self.auth_error.set(f"创建失败：{exc}")
            self.create_btn.configure(state="normal", text="创建密码库")
            return
        self.db = db
        self._apply_saved_hotkey(db)
        self.settings = db.settings
        self._last_activity = time.monotonic()
        try:
            self.master_var.set("")
            self.confirm_var.set("")
        except Exception:
            pass
        self.apply_appearance(rebuild=False)
        self.show_main()
        self._status("密码库已创建，按 Ctrl+N 添加第一个账号")

    # ------------------------------------------------------------------ 外观
    def apply_appearance(self, skin: Optional[str] = None, opacity: Optional[float] = None, rebuild: bool = True) -> None:
        """切换皮肤和窗口透明度；rebuild=True 时重建当前界面。"""
        chosen = theme.apply_skin(skin or self.settings.get("skin") or theme.DEFAULT_SKIN)
        try:
            value = float(opacity if opacity is not None else self.settings.get("window_opacity", 1.0))
        except (TypeError, ValueError):
            value = 1.0
        self.apply_opacity(value)
        try:
            theme.setup_style(self.root)
        except Exception:
            log.exception("刷新控件样式失败")
        if rebuild:
            self._rebuild_view()
        log.info("外观已更新：皮肤 %s，透明度 %.2f", chosen, self.opacity)

    def apply_opacity(self, value: float) -> None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 1.0
        self.opacity = max(0.7, min(1.0, value))
        try:
            self.root.configure(bg=theme.BG)
            self.root.attributes("-alpha", self.opacity)
        except tk.TclError:
            pass

    def _rebuild_view(self) -> None:
        try:
            if self.db:
                selected = self._selected_id
                self.show_main()
                if selected:
                    self._refresh_all(select_id=selected)
            elif getattr(self, "unlock_entry", None) is not None and self.unlock_entry.winfo_exists():
                self.show_lock()
            else:
                self.show_setup()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 主界面
    def show_main(self) -> None:
        if not self.db:
            return
        self._clear_container()
        self._build_sidebar()
        self._build_main_content()
        self._refresh_all(select_first=True)
        if self._hotkey_error:
            self._status(self._hotkey_error, "warn", timeout=12000)
        else:
            self._status(self._default_status)

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self.container, bg=theme.SIDEBAR, width=238)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.sidebar = sidebar

        brand = tk.Frame(sidebar, bg=theme.SIDEBAR, height=100)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        logo = tk.Frame(brand, bg=theme.ACCENT, width=34, height=34)
        logo.pack(side="left", padx=(18, 10), pady=27)
        logo.pack_propagate(False)
        tk.Label(
            logo, text="KF", bg=theme.ACCENT, fg=theme.WHITE, font=(theme.FONT_FAMILY, 10, "bold")
        ).pack(expand=True)
        names = tk.Frame(brand, bg=theme.SIDEBAR)
        names.pack(side="left", pady=16)
        tk.Label(
            names, text="快填密码库", bg=theme.SIDEBAR, fg=theme.WHITE, font=(theme.FONT_FAMILY, 11, "bold")
        ).pack(anchor="w")
        tk.Label(
            names, text="本地加密 · v1.0.2", bg=theme.SIDEBAR, fg=theme.SIDEBAR_MUTED, font=(theme.FONT_FAMILY, 7)
        ).pack(anchor="w")

        style = ttk.Style(self.root)
        style.configure(
            "Dark.Vertical.TScrollbar",
            background="#263B5D",
            troughcolor=theme.SIDEBAR,
            bordercolor=theme.SIDEBAR,
            arrowcolor="#7F92B0",
            width=8,
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#33507A")])

        nav_host = tk.Frame(sidebar, bg=theme.SIDEBAR)
        nav_host.pack(fill="both", expand=True, padx=(6, 2), pady=(2, 0))
        self.nav_scroll = theme.ScrollFrame(
            nav_host, bg=theme.SIDEBAR, scrollbar_style="Dark.Vertical.TScrollbar"
        )
        self.nav_scroll.pack(fill="both", expand=True)
        self.nav_inner = self.nav_scroll.inner

        bottom = tk.Frame(sidebar, bg=theme.SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(4, 16))
        theme.make_button(
            bottom, "＋  新建账号", self.new_entry, kind="sidebar_primary", pady=10
        ).pack(fill="x")
        tools = tk.Frame(bottom, bg=theme.SIDEBAR)
        tools.pack(fill="x", pady=(8, 0))
        for i, (text, cmd) in enumerate(
            [("设置", self.open_settings), ("备份", self.backup_now), ("锁定", self.lock_vault)]
        ):
            btn = theme.make_button(tools, text, cmd, kind="sidebar", padx=4, pady=7, font_size=8)
            btn.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
            tools.grid_columnconfigure(i, weight=1)
        self.hotkey_hint = tk.Label(
            bottom,
            text=f"{self.settings.get('hotkey', 'Ctrl+Alt+K')} 快速填充",
            bg=theme.SIDEBAR,
            fg=theme.SIDEBAR_MUTED,
            font=(theme.FONT_FAMILY, 7),
            anchor="w",
        )
        self.hotkey_hint.pack(fill="x", pady=(10, 0))
        links = tk.Frame(bottom, bg=theme.SIDEBAR)
        links.pack(fill="x", pady=(2, 0))
        help_link = tk.Label(
            links,
            text="使用说明",
            bg=theme.SIDEBAR,
            fg="#6F84A5",
            font=(theme.FONT_FAMILY, 7),
            cursor="hand2",
        )
        help_link.pack(side="left")
        help_link.bind("<Button-1>", lambda _e: self.open_help())
        tk.Label(
            links, text="  ·  ", bg=theme.SIDEBAR, fg="#4E6485", font=(theme.FONT_FAMILY, 7)
        ).pack(side="left")
        data_link = tk.Label(
            links,
            text="数据目录",
            bg=theme.SIDEBAR,
            fg="#6F84A5",
            font=(theme.FONT_FAMILY, 7),
            cursor="hand2",
        )
        data_link.pack(side="left")
        data_link.bind("<Button-1>", lambda _e: self.open_data_dir())

    def _build_main_content(self) -> None:
        content = tk.Frame(self.container, bg=theme.BG)
        content.pack(side="left", fill="both", expand=True)
        self.content = content

        header = tk.Frame(content, bg=theme.BG)
        header.pack(fill="x", padx=30, pady=(22, 0))
        title_box = tk.Frame(header, bg=theme.BG)
        title_box.pack(side="left", fill="y")
        self.header_title = tk.Label(
            title_box, text="全部账号", bg=theme.BG, fg=theme.TEXT, font=(theme.FONT_FAMILY, 20, "bold"), anchor="w"
        )
        self.header_title.pack(anchor="w")
        self.header_sub = tk.Label(
            title_box, text="", bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9), anchor="w"
        )
        self.header_sub.pack(anchor="w", pady=(2, 0))

        right_tools = tk.Frame(header, bg=theme.BG)
        right_tools.pack(side="right", fill="y", pady=(4, 0))
        search_box = tk.Frame(
            right_tools,
            bg=theme.CARD,
            highlightbackground=theme.BORDER,
            highlightthickness=1,
        )
        search_box.pack(side="left", ipady=0)
        tk.Label(
            search_box, text="⌕", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 14), padx=9
        ).pack(side="left")
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.search_var,
            width=24,
            bg=theme.CARD,
            fg=theme.TEXT,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            font=(theme.FONT_FAMILY, 10),
        )
        self.search_entry.pack(side="left", ipady=10, padx=(0, 10))
        self.search_placeholder = tk.Label(
            search_box,
            text="搜索名称、账号、备注…",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 9),
            cursor="xterm",
        )
        self.search_placeholder.place(x=32, rely=0.5, anchor="w")
        self.search_placeholder.bind("<Button-1>", lambda _e: self._focus_search())
        self._update_search_placeholder()
        self.search_entry.bind("<Escape>", lambda _e: self._clear_search())
        theme.make_button(
            right_tools, "＋ 新建账号", self.new_entry, kind="primary", padx=18, pady=9
        ).pack(side="left", padx=(10, 0))

        status = tk.Frame(content, bg=theme.BG, height=34)
        status.pack(side="bottom", fill="x", padx=30, pady=(2, 10))
        status.pack_propagate(False)
        self.status_var = tk.StringVar(value=self._default_status)
        self.status_label = tk.Label(
            status,
            textvariable=self.status_var,
            bg=theme.BG,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        )
        self.status_label.pack(side="left", fill="y")
        self.hotkey_hint = tk.Label(
            status,
            text=f"{self.settings.get('hotkey', 'Ctrl+Alt+K')} 呼出快速填充",
            bg=theme.BG,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        )
        self.hotkey_hint.pack(side="right", fill="y")

        body = tk.Frame(content, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=30, pady=(18, 0))

        list_panel = tk.Frame(
            body, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1, width=382
        )
        list_panel.pack(side="left", fill="y")
        list_panel.pack_propagate(False)
        self.list_panel = list_panel
        list_head = tk.Frame(list_panel, bg=theme.CARD, height=54)
        list_head.pack(fill="x")
        list_head.pack_propagate(False)
        tk.Label(
            list_head,
            text="账号列表",
            bg=theme.CARD,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 10, "bold"),
        ).pack(side="left", padx=16, pady=18)
        self.list_count = tk.Label(
            list_head, text="", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 8)
        )
        self.list_count.pack(side="right", padx=16)
        tk.Frame(list_panel, bg=theme.BORDER, height=1).pack(fill="x")
        self.list_scroll = theme.ScrollFrame(list_panel, bg=theme.CARD)
        self.list_scroll.pack(fill="both", expand=True, pady=(4, 6))
        self.list_inner = self.list_scroll.inner

        detail_panel = tk.Frame(
            body, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1
        )
        detail_panel.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.detail_panel = detail_panel

    # ------------------------------------------------------------------ 导航/刷新
    def _refresh_all(self, select_first: bool = False, select_id: Optional[str] = None) -> None:
        if not self.db:
            return
        self._refresh_nav()
        self._refresh_header()
        self._refresh_list(select_first=select_first, select_id=select_id)

    def _refresh_nav(self) -> None:
        if not hasattr(self, "nav_inner"):
            return
        for child in self.nav_inner.winfo_children():
            child.destroy()
        entries = self.db.list_entries() if self.db else []
        fav_count = sum(1 for e in entries if e.favorite)
        self._nav_item("全部账号", "▦", "all", len(entries))
        self._nav_item("收藏", "★", "fav", fav_count)
        spacer = tk.Frame(self.nav_inner, bg=theme.SIDEBAR, height=10)
        spacer.pack(fill="x")
        tk.Label(
            self.nav_inner,
            text="分 类",
            bg=theme.SIDEBAR,
            fg="#667B9C",
            font=(theme.FONT_FAMILY, 7, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(4, 4))
        categories = self.db.categories() if self.db else []
        if categories:
            for cat in categories:
                count = sum(1 for e in entries if e.category == cat)
                self._nav_item(cat, "•", f"cat:{cat}", count)
        else:
            tk.Label(
                self.nav_inner,
                text="添加账号后自动汇总",
                bg=theme.SIDEBAR,
                fg="#5E7394",
                font=(theme.FONT_FAMILY, 8),
                anchor="w",
            ).pack(fill="x", padx=20, pady=4)

    def _nav_item(self, label: str, icon: str, key: str, count: int) -> None:
        selected = self._filter == key
        bg = theme.SIDEBAR_HOVER if selected else theme.SIDEBAR
        row = tk.Frame(self.nav_inner, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=1)
        bar = tk.Frame(row, bg=theme.ACCENT if selected else bg, width=3)
        bar.pack(side="left", fill="y")
        inner = tk.Frame(row, bg=bg)
        inner.pack(side="left", fill="both", expand=True, padx=(13, 10), pady=7)
        icon_label = tk.Label(
            inner,
            text=icon,
            bg=bg,
            fg=theme.ACCENT if selected else "#7890B2",
            font=(theme.FONT_FAMILY, 10),
            width=2,
            anchor="w",
        )
        icon_label.pack(side="left")
        text_label = tk.Label(
            inner,
            text=label,
            bg=bg,
            fg=theme.WHITE if selected else theme.SIDEBAR_TEXT,
            font=(theme.FONT_FAMILY, 9, "bold" if selected else "normal"),
            anchor="w",
        )
        text_label.pack(side="left", padx=(6, 0), fill="x", expand=True)
        count_label = tk.Label(
            inner,
            text=str(count) if count else "",
            bg=bg,
            fg="#7890B2",
            font=(theme.FONT_FAMILY, 8),
        )
        count_label.pack(side="right")
        for w in (row, bar, inner, icon_label, text_label, count_label):
            w.bind("<Button-1>", lambda _e, k=key: self._set_filter(k))

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._refresh_all(select_first=True)

    def _filter_title(self) -> tuple[str, str]:
        if self._filter == "fav":
            return "收藏", "你标记为重要的账号"
        if self._filter.startswith("cat:"):
            return self._filter[4:], "按分类筛选"
        return "全部账号", "每个网站或应用的账号都分开保存"

    def _refresh_header(self) -> None:
        if not hasattr(self, "header_title"):
            return
        title, sub = self._filter_title()
        self.header_title.configure(text=title)
        total = self.db.count() if self.db else 0
        self.header_sub.configure(text=f"{sub} · 共 {total} 个账号")

    def _refresh_list(self, select_first: bool = False, select_id: Optional[str] = None) -> None:
        if not hasattr(self, "list_inner") or not self.db:
            return
        query = self.search_var.get().strip()
        try:
            entries = self.db.search(query)
        except VaultLocked:
            return
        if self._filter == "fav":
            entries = [e for e in entries if e.favorite]
        elif self._filter.startswith("cat:"):
            cat = self._filter[4:]
            entries = [e for e in entries if e.category == cat]
        self._current_entries = entries
        ids = {e.id for e in entries}
        selected = select_id or self._selected_id
        if selected not in ids:
            selected = entries[0].id if (select_first or selected is None) and entries else None
        self._selected_id = selected
        for child in self.list_inner.winfo_children():
            child.destroy()
        self._cards = {}
        if not entries:
            self._build_list_empty(bool(query))
        else:
            for entry in entries:
                self._build_card(entry)
        self.list_count.configure(text=f"{len(entries)} 个")
        self._render_detail()
        self._update_card_selection()

    def _build_list_empty(self, filtered: bool) -> None:
        box = tk.Frame(self.list_inner, bg=theme.CARD)
        box.pack(fill="both", expand=True, pady=60)
        tk.Label(
            box,
            text="没有找到账号" if filtered else "还没有账号",
            bg=theme.CARD,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 11, "bold"),
        ).pack()
        tk.Label(
            box,
            text="换个关键词或分类试试" if filtered else "点“新建账号”保存第一条账号密码",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        ).pack(pady=(5, 0))
        if not filtered:
            theme.make_button(box, "＋ 新建账号", self.new_entry, kind="secondary", padx=14, pady=7, font_size=9).pack(
                pady=(12, 0)
            )

    def _build_card(self, entry: Entry) -> None:
        selected = entry.id == self._selected_id
        bg = theme.ACCENT_SOFT if selected else theme.CARD
        row = tk.Frame(self.list_inner, bg=bg, cursor="hand2")
        row.pack(fill="x", pady=1)
        bar = tk.Frame(row, bg=theme.ACCENT if selected else bg, width=3)
        bar.pack(side="left", fill="y")
        body = tk.Frame(row, bg=bg)
        body.pack(side="left", fill="both", expand=True, padx=(10, 8), pady=9)

        avatar = tk.Canvas(body, width=36, height=36, bg=bg, highlightthickness=0)
        avatar.pack(side="left", padx=(0, 10))
        avatar.create_oval(2, 2, 34, 34, fill=self.avatar_color(entry.title), outline="")
        avatar.create_text(
            18,
            18,
            text=(entry.title.strip() or "?")[0].upper(),
            fill=theme.WHITE,
            font=(theme.FONT_FAMILY, 11, "bold"),
        )

        text_box = tk.Frame(body, bg=bg)
        text_box.pack(side="left", fill="x", expand=True)
        title = tk.Label(
            text_box,
            text=entry.title,
            bg=bg,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 10, "bold"),
            anchor="w",
        )
        title.pack(anchor="w", fill="x")
        account = tk.Label(
            text_box,
            text=entry.account or "未填写账号",
            bg=bg,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        )
        account.pack(anchor="w", fill="x", pady=(1, 0))

        right = tk.Frame(body, bg=bg)
        right.pack(side="right")
        star = tk.Label(
            right,
            text="★" if entry.favorite else "☆",
            bg=bg,
            fg="#F5A524" if entry.favorite else "#C9D4E3",
            font=(theme.FONT_FAMILY, 11),
            cursor="hand2",
        )
        star.pack(anchor="e")
        cat = tk.Label(
            right,
            text=entry.category or "未分类",
            bg=bg,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
        )
        cat.pack(anchor="e", pady=(3, 0))

        widgets = [row, bar, body, avatar, text_box, title, account, right, cat]
        for w in widgets:
            w.bind("<Button-1>", lambda _e, i=entry.id: self._select_entry(i))
        star.bind("<Button-1>", lambda _e, e=entry: self._toggle_favorite(e))
        self._cards[entry.id] = {
            "row": row,
            "bar": bar,
            "body": body,
            "avatar": avatar,
            "text_box": text_box,
            "title": title,
            "account": account,
            "right": right,
            "cat": cat,
            "star": star,
            "widgets": widgets,
        }

    def _update_card_selection(self) -> None:
        for entry_id, card in self._cards.items():
            selected = entry_id == self._selected_id
            bg = theme.ACCENT_SOFT if selected else theme.CARD
            for w in card["widgets"]:
                try:
                    w.configure(bg=bg)
                except tk.TclError:
                    pass
            card["bar"].configure(bg=theme.ACCENT if selected else bg)
            card["avatar"].configure(bg=bg)

    def _select_entry(self, entry_id: str) -> None:
        if self._selected_id == entry_id:
            return
        self._selected_id = entry_id
        self._update_card_selection()
        self._render_detail()

    def _toggle_favorite(self, entry: Entry) -> None:
        if not self.db:
            return
        entry.favorite = not entry.favorite
        try:
            self.db.update_entry(entry)
        except Exception as exc:
            self._status(f"收藏失败：{exc}", "danger")
            return
        self._refresh_nav()
        self._refresh_list(select_id=entry.id)
        self._status("已加入收藏" if entry.favorite else "已取消收藏")

    def avatar_color(self, text: str) -> str:
        palette = ["#2F6BFF", "#0E9F8E", "#7A5AF8", "#E4780D", "#D6455D", "#2E86C1", "#8E5A3C"]
        if not text:
            return palette[0]
        return palette[sum(ord(c) for c in text) % len(palette)]

    # ------------------------------------------------------------------ 详情
    def _empty_detail(self) -> None:
        for child in self.detail_panel.winfo_children():
            child.destroy()
        box = tk.Frame(self.detail_panel, bg=theme.CARD)
        box.place(relx=0.5, rely=0.44, anchor="center")
        icon = tk.Canvas(box, width=76, height=76, bg=theme.CARD, highlightthickness=0)
        icon.pack()
        icon.create_oval(4, 4, 72, 72, fill=theme.ACCENT_SOFT, outline="")
        icon.create_rectangle(31, 32, 45, 51, fill=theme.ACCENT, outline="")
        icon.create_arc(25, 20, 51, 46, start=0, extent=180, style="arc", outline=theme.ACCENT, width=4)
        icon.create_oval(35, 37, 41, 43, fill=theme.WHITE, outline="")
        tk.Label(
            box, text="选择一个账号查看详情", bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 12, "bold")
        ).pack(pady=(14, 4))
        tk.Label(
            box,
            text="账号和密码分开显示；也可以按 Ctrl+Alt+K 呼出快速填充。",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
        ).pack()
        theme.make_button(box, "＋ 新建账号", self.new_entry, kind="secondary", padx=16, pady=8, font_size=9).pack(
            pady=(16, 0)
        )

    def _render_detail(self) -> None:
        if not hasattr(self, "detail_panel"):
            return
        entry = self.db.get_entry(self._selected_id) if (self.db and self._selected_id) else None
        if not entry:
            self._empty_detail()
            return
        for child in self.detail_panel.winfo_children():
            child.destroy()

        head = tk.Frame(self.detail_panel, bg=theme.CARD)
        head.pack(fill="x", padx=24, pady=(20, 0))
        avatar = tk.Canvas(head, width=54, height=54, bg=theme.CARD, highlightthickness=0)
        avatar.pack(side="left", padx=(0, 14))
        avatar.create_oval(2, 2, 52, 52, fill=self.avatar_color(entry.title), outline="")
        avatar.create_text(
            27,
            27,
            text=(entry.title.strip() or "?")[0].upper(),
            fill=theme.WHITE,
            font=(theme.FONT_FAMILY, 18, "bold"),
        )
        title_box = tk.Frame(head, bg=theme.CARD)
        title_box.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_box,
            text=entry.title,
            bg=theme.CARD,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 18, "bold"),
            anchor="w",
            wraplength=320,
            justify="left",
        ).pack(anchor="w")
        meta = tk.Frame(title_box, bg=theme.CARD)
        meta.pack(anchor="w", pady=(4, 0))
        tk.Label(
            meta,
            text=entry.category or "未分类",
            bg=theme.TEAL_SOFT,
            fg=theme.TEAL,
            font=(theme.FONT_FAMILY, 7, "bold"),
            padx=7,
            pady=2,
        ).pack(side="left")
        tk.Label(
            meta,
            text=f"更新于 {local_time(entry.updated_at)}",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
        ).pack(side="left", padx=(8, 0))

        actions_head = tk.Frame(head, bg=theme.CARD)
        actions_head.pack(side="right", anchor="n")
        star = tk.Label(
            actions_head,
            text="★" if entry.favorite else "☆",
            bg=theme.CARD,
            fg="#F5A524" if entry.favorite else "#C9D4E3",
            font=(theme.FONT_FAMILY, 18),
            cursor="hand2",
        )
        star.pack(side="right", padx=(8, 0))
        star.bind("<Button-1>", lambda _e, e=entry: self._toggle_favorite(e))
        edit = theme.make_button(
            actions_head, "编辑", lambda: self.edit_entry(entry), kind="ghost", padx=10, pady=4, font_size=8
        )
        edit.pack(side="right")

        tk.Frame(self.detail_panel, bg=theme.BORDER, height=1).pack(fill="x", padx=24, pady=(14, 0))

        action_bar = tk.Frame(self.detail_panel, bg=theme.CARD)
        action_bar.pack(side="bottom", fill="x", padx=24, pady=(8, 16))
        tk.Frame(action_bar, bg=theme.BORDER, height=1).pack(fill="x", pady=(0, 10))
        theme.make_button(
            action_bar, "填入当前登录窗口", self._fill_selected, kind="primary", pady=11
        ).pack(fill="x")
        tk.Label(
            action_bar,
            text="先打开登录页并把光标放到账号框，再点这里；或按 Ctrl+Alt+K。",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
        ).pack(anchor="w", pady=(5, 0))
        ops = tk.Frame(action_bar, bg=theme.CARD)
        ops.pack(fill="x", pady=(8, 0))
        theme.make_button(
            ops, "编辑账号", lambda: self.edit_entry(entry), kind="secondary", padx=12, pady=6, font_size=8
        ).pack(side="left")
        theme.make_button(
            ops, "删除", lambda: self.delete_entry(entry), kind="danger", padx=12, pady=6, font_size=8
        ).pack(side="right")

        scroll = theme.ScrollFrame(self.detail_panel, bg=theme.CARD)
        scroll.pack(fill="both", expand=True, padx=24, pady=(8, 0))
        body = scroll.inner

        def label(text: str, pady=(0, 4)) -> None:
            tk.Label(
                body,
                text=text,
                bg=theme.CARD,
                fg=theme.MUTED,
                font=(theme.FONT_FAMILY, 8, "bold"),
                anchor="w",
            ).pack(fill="x", pady=pady)

        label("账号")
        acc_row = tk.Frame(body, bg=theme.CARD)
        acc_row.pack(fill="x", pady=(0, 14))
        acc_var = tk.StringVar(value=entry.account or "（未填写）")
        acc_entry = tk.Entry(
            acc_row,
            textvariable=acc_var,
            state="readonly",
            readonlybackground="#F7F9FD",
            fg=theme.TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            font=(theme.FONT_FAMILY, 10),
        )
        acc_entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 6))
        theme.make_button(
            acc_row, "复制", lambda: self.copy_text(entry.account, "账号已复制"), kind="secondary", padx=10, pady=6, font_size=8
        ).pack(side="left")

        label("密码")
        pwd_row = tk.Frame(body, bg=theme.CARD)
        pwd_row.pack(fill="x", pady=(0, 14))
        pwd_entry = tk.Entry(
            pwd_row,
            show="●",
            state="readonly",
            readonlybackground="#F7F9FD",
            fg=theme.TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            font=(theme.FONT_FAMILY, 10),
        )
        pwd_entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 6))
        pwd_entry.configure(state="normal")
        pwd_entry.insert(0, entry.password)
        pwd_entry.configure(state="readonly")
        pwd_entry.configure(show="●")
        shown = {"value": False}

        def toggle_pwd() -> None:
            shown["value"] = not shown["value"]
            pwd_entry.configure(show="" if shown["value"] else "●")
            show_btn.configure(text="隐藏" if shown["value"] else "显示")

        show_btn = theme.make_button(pwd_row, "显示", toggle_pwd, kind="ghost", padx=8, pady=6, font_size=8)
        show_btn.pack(side="left", padx=(0, 4))
        theme.make_button(
            pwd_row, "复制", lambda: self.copy_text(entry.password, f"已复制“{entry.title}”的密码"), kind="secondary", padx=10, pady=6, font_size=8
        ).pack(side="left")

        label("登录网址")
        if entry.url:
            url_row = tk.Frame(body, bg=theme.CARD)
            url_row.pack(fill="x", pady=(0, 14))
            url_label = tk.Label(
                url_row,
                text=entry.url,
                bg=theme.CARD,
                fg=theme.ACCENT,
                font=(theme.FONT_FAMILY, 9, "underline"),
                cursor="hand2",
                anchor="w",
                wraplength=300,
                justify="left",
            )
            url_label.pack(side="left", fill="x", expand=True)
            url_label.bind("<Button-1>", lambda _e: self.open_url(entry.url))
            theme.make_button(
                url_row, "打开", lambda: self.open_url(entry.url), kind="ghost", padx=10, pady=6, font_size=8
            ).pack(side="right", padx=(4, 0))
            theme.make_button(
                url_row, "复制", lambda: self.copy_text(entry.url, "网址已复制"), kind="secondary", padx=10, pady=6, font_size=8
            ).pack(side="right")
        else:
            tk.Label(
                body, text="未填写", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 9), anchor="w"
            ).pack(fill="x", pady=(0, 14))

        label("备注")
        notes = tk.Text(
            body,
            height=3,
            width=1,
            font=(theme.FONT_FAMILY, 9),
            bg="#F7F9FD",
            fg=theme.TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            wrap="word",
            padx=8,
            pady=6,
            cursor="arrow",
        )
        notes.pack(fill="x", pady=(0, 14))
        notes.insert("1.0", entry.notes or "（无备注）")
        notes.configure(state="disabled")

        label("标签")
        tags_row = tk.Frame(body, bg=theme.CARD)
        tags_row.pack(fill="x", pady=(0, 14))
        if entry.tags:
            for tag in entry.tags:
                tk.Label(
                    tags_row,
                    text=tag,
                    bg="#F0F4FB",
                    fg=theme.MUTED,
                    font=(theme.FONT_FAMILY, 8),
                    padx=8,
                    pady=3,
                ).pack(side="left", padx=(0, 6))
        else:
            tk.Label(
                tags_row, text="无", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 9)
            ).pack(side="left")

        tk.Frame(body, bg=theme.BORDER, height=1).pack(fill="x", pady=(4, 10))
        tk.Label(
            body,
            text=f"创建于 {local_time(entry.created_at)}    ·    更新于 {local_time(entry.updated_at)}",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 7),
            anchor="w",
        ).pack(fill="x")

    # ------------------------------------------------------------------ 操作
    def new_entry(self) -> None:
        if not self.db:
            return
        EntryDialog(self)

    def edit_entry(self, entry: Entry) -> None:
        if not self.db:
            return
        EntryDialog(self, entry)

    def after_entry_saved(self, entry: Entry, new: bool = False) -> None:
        if new:
            self._filter = "all"
            self.search_var.set("")
        self._selected_id = entry.id
        self._refresh_all(select_first=False, select_id=entry.id)
        self._status(f"已保存“{entry.title}”的账号")
        if new:
            self._status("已保存。登录时按 " + self.settings.get("hotkey", "Ctrl+Alt+K") + " 快速填入")

    def delete_entry(self, entry: Entry) -> None:
        if not messagebox.askyesno(
            "删除账号",
            f"确定删除“{entry.title}”下的账号：\n{entry.account}\n\n删除后无法撤销。",
            parent=self.root,
        ):
            return
        try:
            self.db.delete_entry(entry.id)
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self.root)
            return
        self._selected_id = None
        self._refresh_all(select_first=True)
        self._status(f"已删除“{entry.title}”")

    def _fill_selected(self) -> None:
        if not (self.db and self._selected_id):
            return
        entry = self.db.get_entry(self._selected_id)
        if not entry:
            return
        target = self._last_external
        if not self._valid_target(target):
            messagebox.showinfo(
                "先打开登录窗口",
                "没有找到可以填写的登录窗口。\n\n"
                "请先打开要登录的网站或软件，把光标放在账号框，再按 "
                f"{self.settings.get('hotkey', 'Ctrl+Alt+K')} 呼出快速填充；\n"
                "也可以点“复制”后手动粘贴。",
                parent=self.root,
            )
            return
        self.fill_to_window(entry, target)

    def fill_to_window(self, entry: Entry, target) -> None:
        if isinstance(target, int):
            target = autotype.capture_target(target, source="manual")
        if not self._valid_target(target):
            target = self._last_external
        if not self._valid_target(target):
            if entry.password:
                self.copy_text(entry.password, "未找到可填写窗口，已复制密码")
            else:
                self._status("没有可填写的内容", "warn")
            return
        label = target.title or target.class_name or "目标窗口"
        self._status(f"正在把“{entry.title}”填入“{label}”…")
        self.root.after(120, lambda: self._do_fill(entry, target))

    def _do_fill(self, entry: Entry, target: autotype.TargetWindow) -> None:
        ok, message = autotype.fill_target(
            target,
            entry,
            submit=bool(self.settings.get("autotype_submit", False)),
            delay_ms=int(self.settings.get("autotype_delay_ms", 8)),
        )
        if ok:
            suffix = "，并已按回车" if self.settings.get("autotype_submit") else ""
            extra = f"（{message}）" if message else ""
            self._status(f"已填入“{entry.title}”的账号和密码{suffix}{extra}", "ok")
            return
        log.warning("填写失败：%s（目标 %s）", message, target.describe())
        if entry.password:
            self.copy_text(entry.password, f"{message}，已复制密码")
        else:
            self._status(message, "danger", timeout=8000)

    def _valid_target(self, target) -> bool:
        if isinstance(target, int):
            target = autotype.TargetWindow(hwnd=target)
        if not target or not target.hwnd:
            return False
        try:
            hwnd = autotype.root_window(target.hwnd)
            if not autotype.user32.IsWindow(hwnd) or not autotype.user32.IsWindowVisible(hwnd):
                return False
            if autotype.window_pid(hwnd) == os.getpid():
                return False
            return True
        except Exception:
            return False

    def copy_text(self, text: str, message: str = "已复制") -> None:
        if not text:
            self._status("没有可复制的内容", "warn")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except tk.TclError as exc:
            self._status(f"复制失败：{exc}", "danger")
            return
        self._clipboard_text = text
        if self._clipboard_job:
            try:
                self.root.after_cancel(self._clipboard_job)
            except Exception:
                pass
        seconds = int(self.settings.get("clipboard_clear_seconds", 25))
        self._clipboard_job = self.root.after(seconds * 1000, self._clear_clipboard)
        self._status(f"{message}（{seconds} 秒后自动清除剪贴板）")

    def _clear_clipboard(self) -> None:
        self._clipboard_job = None
        if not self._clipboard_text:
            return
        try:
            current = self.root.clipboard_get()
        except tk.TclError:
            current = None
        if current == self._clipboard_text:
            try:
                self.root.clipboard_clear()
                self.root.update()
            except tk.TclError:
                pass
        self._clipboard_text = None

    def _clear_clipboard_now(self) -> None:
        if self._clipboard_text:
            self._clear_clipboard()

    def open_url(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url
        try:
            webbrowser.open(url)
        except Exception as exc:
            self._status(f"无法打开网址：{exc}", "danger")

    # ------------------------------------------------------------------ 搜索
    def _focus_search(self) -> None:
        if hasattr(self, "search_entry") and self.search_entry.winfo_exists():
            self.search_entry.focus_set()
            self.search_entry.select_range(0, "end")

    def _clear_search(self) -> None:
        self.search_var.set("")
        self.search_entry.focus_set()

    def _on_search_var(self, *_args) -> None:
        self._update_search_placeholder()
        if not self.db:
            return
        if self._search_job:
            try:
                self.root.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.root.after(120, self._run_search)

    def _run_search(self) -> None:
        self._search_job = None
        if self.db:
            self._refresh_list(select_first=True)

    def _update_search_placeholder(self) -> None:
        label = getattr(self, "search_placeholder", None)
        if not label or not label.winfo_exists():
            return
        if self.search_var.get():
            label.place_forget()
        else:
            label.place(x=32, rely=0.5, anchor="w")

    # ------------------------------------------------------------------ 设置/备份/锁定
    def open_settings(self) -> None:
        if self.db:
            SettingsDialog(self)

    def backup_now(self) -> None:
        if not self.db:
            return
        try:
            path = self.db.backup()
        except Exception as exc:
            messagebox.showerror("备份失败", str(exc), parent=self.root)
            return
        self._status(f"已备份到 {path.name}")
        messagebox.showinfo(
            "备份完成",
            f"加密数据库已备份到：\n{path}\n\n备份文件同样受主密码保护，可以复制到 U 盘或网盘。",
            parent=self.root,
        )

    def open_data_dir(self) -> None:
        try:
            os.startfile(self.db_path.parent)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def open_help(self) -> None:
        for name in ("使用说明.md", "使用说明.txt"):
            path = app_dir() / name
            if path.exists():
                try:
                    os.startfile(path)  # type: ignore[attr-defined]
                    return
                except Exception:
                    break
        self.open_data_dir()

    def lock_vault(self, auto: bool = False) -> None:
        if not self.db:
            return
        self._close_overlay()
        for dialog in list(self._dialogs):
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        self._dialogs.clear()
        self._clear_clipboard_now()
        try:
            self.db.close()
        except Exception:
            pass
        self.db = None
        self._selected_id = None
        self._filter = "all"
        self._current_entries = []
        self.search_var.set("")
        self.show_lock("已自动锁定，请重新输入主密码" if auto else "已锁定。需要主密码才能重新打开。")

    def _register_dialog(self, dialog: tk.Toplevel) -> None:
        self._dialogs.add(dialog)

        def on_destroy(event):
            if event.widget is dialog:
                self._dialogs.discard(dialog)

        dialog.bind("<Destroy>", on_destroy, add="+")

    def _apply_saved_hotkey(self, db: VaultDB) -> None:
        """解锁后切换到数据库里保存的快捷键（启动时只能先注册默认值）。"""
        if not self.enable_hotkey or not self._hotkey:
            return
        active = self.settings.get("hotkey")
        saved = db.settings.get("hotkey")
        if not saved or saved == active:
            return
        try:
            saved = autotype.format_hotkey(saved)
        except Exception:
            self._hotkey_error = f"数据库中的快捷键“{saved}”无效，暂用 {active}"
            return
        ok, error = self.change_hotkey(saved)
        if ok:
            self.settings["hotkey"] = saved
            self._hotkey_error = ""
        else:
            self.settings["hotkey"] = active
            self._hotkey_error = f"保存的快捷键 {saved} 无法注册，暂用 {active}"
            try:
                db.update_settings(hotkey=active)
            except Exception:
                log.exception("保存实际使用的快捷键失败")

    # ------------------------------------------------------------------ 快捷键
    def _start_hotkey(self) -> None:
        preferred = self.settings.get("hotkey", "Ctrl+Alt+K")
        candidates = [preferred]
        for fallback in ("Ctrl+Alt+K", "Ctrl+Shift+L", "Ctrl+Alt+F9"):
            if fallback not in candidates:
                candidates.append(fallback)
        last_error = ""
        for hotkey in candidates:
            manager = autotype.HotkeyManager(hotkey, lambda hwnd: self._hotkey_queue.put(hwnd))
            if manager.start():
                self._hotkey = manager
                if hotkey != preferred:
                    self.settings["hotkey"] = hotkey
                    self._hotkey_error = f"快捷键 {preferred} 被占用，已自动改用 {hotkey}"
                else:
                    self._hotkey_error = ""
                return
            last_error = manager.error or "全局快捷键注册失败"
        self._hotkey = None
        self._hotkey_error = last_error

    def change_hotkey(self, new_hotkey: str) -> tuple[bool, str]:
        if not self._hotkey and not self.enable_hotkey:
            return True, ""
        old = self.settings.get("hotkey", "Ctrl+Alt+K")
        if self._hotkey:
            self._hotkey.stop()
            self._hotkey = None
        manager = autotype.HotkeyManager(new_hotkey, lambda hwnd: self._hotkey_queue.put(hwnd))
        if manager.start():
            self._hotkey = manager
            self.settings["hotkey"] = new_hotkey
            self._hotkey_error = ""
            if self.hotkey_hint and self.hotkey_hint.winfo_exists():
                self.hotkey_hint.configure(text=f"{new_hotkey} 呼出快速填充")
            return True, ""
        error = manager.error or "快捷键注册失败"
        # 回滚
        fallback = autotype.HotkeyManager(old, lambda hwnd: self._hotkey_queue.put(hwnd))
        if fallback.start():
            self._hotkey = fallback
        else:
            self._hotkey_error = fallback.error
        return False, error

    def _poll_hotkey(self) -> None:
        if self._closing:
            return
        try:
            while True:
                target = self._hotkey_queue.get_nowait()
                self._on_hotkey(target)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_hotkey)

    def _on_hotkey(self, target) -> None:
        if not self.db:
            self._bring_root_front()
            return
        if not self._valid_target(target):
            target = self._last_external
        if self._overlay and self._overlay.winfo_exists():
            if self._valid_target(target):
                self._overlay.target = target
                self._overlay.target_hwnd = target.hwnd
                try:
                    self._overlay.target_label.configure(text=self._overlay._target_text())
                except tk.TclError:
                    pass
            self._overlay.lift()
            self._overlay._focus_search()
            return
        try:
            self._overlay = QuickFillOverlay(self, target)
        except Exception:
            log.exception("打开快速填充失败")
            self._status("快速填充窗口打开失败", "danger")

    def _bring_root_front(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            if hasattr(self, "unlock_entry") and self.unlock_entry.winfo_exists():
                self.unlock_entry.focus_set()
        except tk.TclError:
            pass

    def _close_overlay(self) -> None:
        if self._overlay and self._overlay.winfo_exists():
            self._overlay._close()
        self._overlay = None

    # ------------------------------------------------------------------ 前台/自动锁定
    def _track_foreground(self) -> None:
        if self._closing:
            return
        try:
            hwnd = autotype.get_foreground_window()
            if hwnd and autotype.window_pid(hwnd) != os.getpid() and autotype.user32.IsWindowVisible(hwnd):
                self._last_external = autotype.capture_target(hwnd, source="tracked")
        except Exception:
            pass
        self.root.after(500, self._track_foreground)

    def _touch_activity(self, _event=None) -> None:
        if self.db:
            self._last_activity = time.monotonic()

    def _auto_lock_tick(self) -> None:
        if self._closing:
            return
        if self.db:
            try:
                minutes = int(self.settings.get("auto_lock_minutes", 5))
            except Exception:
                minutes = 5
            if minutes > 0 and time.monotonic() - self._last_activity >= minutes * 60:
                self.lock_vault(auto=True)
        self.root.after(5000, self._auto_lock_tick)

    def _on_mousewheel(self, event):
        try:
            x, y = self.root.winfo_pointerxy()
            widget = self.root.winfo_containing(x, y)
            while widget is not None:
                if isinstance(widget, theme.ScrollFrame):
                    widget.scroll_by(event.delta)
                    return "break"
                widget = getattr(widget, "master", None)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ 状态
    def _status(self, text: str, kind: str = "info", timeout: int = 4500) -> None:
        if not self.status_label or not self.status_label.winfo_exists():
            return
        colors = {"info": theme.MUTED, "ok": theme.TEAL, "warn": theme.WARN, "danger": theme.DANGER}
        self.status_var.set(text)
        self.status_label.configure(fg=colors.get(kind, theme.MUTED))
        if self._status_job:
            try:
                self.root.after_cancel(self._status_job)
            except Exception:
                pass
        self._status_job = self.root.after(timeout, self._reset_status)

    def _reset_status(self) -> None:
        self._status_job = None
        if self.status_label and self.status_label.winfo_exists():
            self.status_var.set(self._default_status)
            self.status_label.configure(fg=theme.MUTED)

    # ------------------------------------------------------------------ 关闭
    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._close_overlay()
        for dialog in list(self._dialogs):
            try:
                dialog.destroy()
            except tk.TclError:
                pass
        if self._hotkey:
            self._hotkey.stop()
        self._clear_clipboard_now()
        if self.db:
            self.db.close()
            self.db = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass
