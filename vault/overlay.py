"""全局快捷键呼出的“快速填充”浮窗。"""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk
from typing import Optional

from . import theme
from .autotype import root_window, user32
from .db import Entry

log = logging.getLogger(__name__)


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


def _truncate(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


class QuickFillOverlay(tk.Toplevel):
    WIDTH = 660
    HEIGHT = 720

    def __init__(self, app, target_hwnd: int):
        super().__init__(app.root)
        self.app = app
        self.db = app.db
        self.target_hwnd = target_hwnd or 0
        self.query_var = tk.StringVar()
        self.entries: list[Entry] = []
        self.rows: list[dict] = []
        self.index = -1
        self._after_id: Optional[str] = None

        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=theme.BORDER_STRONG)
        self._build()
        self._position()
        self.deiconify()
        self.lift()
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.after(30, self._focus_search)
        self.bind("<Escape>", lambda _e: self._close())
        self.bind("<Up>", lambda _e: self._move(-1))
        self.bind("<Down>", lambda _e: self._move(1))
        self.bind("<Return>", lambda _e: self._activate())
        self.bind("<Shift-Return>", lambda _e: self._copy_password())
        self.bind("<Control-Return>", lambda _e: self._copy_password())
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<MouseWheel>", self._on_wheel)
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        shell = tk.Frame(self, bg=theme.CARD)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(shell, bg=theme.SIDEBAR, height=70)
        header.pack(fill="x")
        header.pack_propagate(False)

        logo = tk.Label(
            header,
            text="KF",
            bg=theme.ACCENT,
            fg=theme.WHITE,
            font=(theme.FONT_FAMILY, 9, "bold"),
            width=3,
            height=1,
        )
        logo.pack(side="left", padx=(16, 10), pady=20)
        title_box = tk.Frame(header, bg=theme.SIDEBAR)
        title_box.pack(side="left", fill="y", pady=8)
        tk.Label(
            title_box,
            text="快速填充",
            bg=theme.SIDEBAR,
            fg=theme.WHITE,
            font=(theme.FONT_FAMILY, 12, "bold"),
            anchor="w",
        ).pack(anchor="w")
        target_text = "未找到目标窗口 · Enter 将复制密码"
        if self.target_hwnd:
            name = window_title(self.target_hwnd)
            target_text = f"目标窗口：{_truncate(name, 52)}" if name else "目标窗口已锁定（按标题确认）"
        self.target_label = tk.Label(
            title_box,
            text=target_text,
            bg=theme.SIDEBAR,
            fg=theme.SIDEBAR_MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        )
        self.target_label.pack(anchor="w")

        close = tk.Label(
            header,
            text="×",
            bg=theme.SIDEBAR,
            fg=theme.SIDEBAR_MUTED,
            font=(theme.FONT_FAMILY, 18),
            cursor="hand2",
            padx=14,
        )
        close.pack(side="right", fill="y")
        close.bind("<Button-1>", lambda _e: self._close())
        close.bind("<Enter>", lambda _e: close.configure(fg=theme.WHITE))
        close.bind("<Leave>", lambda _e: close.configure(fg=theme.SIDEBAR_MUTED))

        search_wrap = tk.Frame(shell, bg=theme.CARD)
        search_wrap.pack(fill="x", padx=16, pady=(14, 8))
        search_box = tk.Frame(
            search_wrap,
            bg="#F4F7FC",
            highlightbackground=theme.BORDER,
            highlightthickness=1,
        )
        search_box.pack(fill="x")
        tk.Label(
            search_box,
            text="⌕",
            bg="#F4F7FC",
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 16),
            padx=10,
        ).pack(side="left")
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.query_var,
            bg="#F4F7FC",
            fg=theme.TEXT,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            font=(theme.FONT_FAMILY, 12),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=9)
        self.search_entry.insert(0, "")
        self.search_entry.bind("<KeyRelease>", self._on_query)
        self.placeholder = tk.Label(
            search_box,
            text="输入名称、账号、网址或备注…",
            bg="#F4F7FC",
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 11),
            cursor="xterm",
        )
        self.placeholder.place(x=34, rely=0.5, anchor="w")
        self.placeholder.bind("<Button-1>", lambda _e: self._focus_search())
        self.query_var.trace_add("write", lambda *_: self._placeholder_update())

        self.result_meta = tk.Frame(shell, bg=theme.CARD)
        self.result_meta.pack(fill="x", padx=18)
        self.count_label = tk.Label(
            self.result_meta,
            text="",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        )
        self.count_label.pack(side="left")
        tk.Label(
            self.result_meta,
            text="↑↓ 选择",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        ).pack(side="right")

        self.scroll = theme.ScrollFrame(shell, bg=theme.CARD)
        self.rows_frame = self.scroll.inner

        footer = tk.Frame(shell, bg=theme.CARD, height=66)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=theme.BORDER, height=1).pack(fill="x")
        hint = tk.Label(
            footer,
            text="Enter 填入  ·  Shift+Enter 复制密码  ·  Esc 关闭",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        )
        hint.pack(side="left", padx=18)
        self.copy_btn = theme.make_button(
            footer, "复制密码", self._copy_password, kind="secondary", padx=12, pady=6, font_size=9
        )
        self.copy_btn.pack(side="right", padx=(6, 14), pady=11)
        self.fill_btn = theme.make_button(
            footer, "填入", self._activate, kind="primary", padx=18, pady=6, font_size=9
        )
        self.fill_btn.pack(side="right", pady=11)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(4, 0))

    def _position(self) -> None:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - self.WIDTH) // 2)
        y = max(10, int(sh * 0.15))
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _focus_search(self) -> None:
        try:
            self.search_entry.focus_force()
            self.search_entry.icursor("end")
        except tk.TclError:
            pass

    def _on_wheel(self, event):
        self.scroll.scroll_by(event.delta)
        return "break"

    def _placeholder_update(self) -> None:
        if self.query_var.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=34, rely=0.5, anchor="w")

    def _on_query(self, _event=None) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(60, self._refresh)

    # ------------------------------------------------------------------ 结果
    def _refresh(self) -> None:
        self._after_id = None
        if not self.winfo_exists():
            return
        query = self.query_var.get().strip()
        try:
            results = self.db.search(query)
        except Exception:
            log.exception("搜索失败")
            results = []
        self.entries = results
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self.rows = []
        self.index = -1
        self.count_label.configure(text=f"找到 {len(results)} 个账号" if query else f"共 {len(results)} 个账号")
        if not results:
            empty = tk.Frame(self.rows_frame, bg=theme.CARD)
            empty.pack(fill="both", expand=True, pady=60)
            tk.Label(
                empty,
                text="没有匹配的账号",
                bg=theme.CARD,
                fg=theme.MUTED,
                font=(theme.FONT_FAMILY, 11, "bold"),
            ).pack()
            tk.Label(
                empty,
                text="换个关键词，或先在主界面新建账号",
                bg=theme.CARD,
                fg=theme.FAINT,
                font=(theme.FONT_FAMILY, 9),
            ).pack(pady=(5, 0))
            self._update_actions()
            return
        for i, entry in enumerate(results[:80]):
            self._build_row(i, entry)
        self._select(0)
        self._update_actions()

    def _build_row(self, index: int, entry: Entry) -> None:
        row = tk.Frame(self.rows_frame, bg=theme.CARD, cursor="hand2")
        row.pack(fill="x", pady=1, padx=2)
        bar = tk.Frame(row, bg=theme.CARD, width=3)
        bar.pack(side="left", fill="y")
        body = tk.Frame(row, bg=theme.CARD)
        body.pack(side="left", fill="both", expand=True, padx=(10, 8), pady=8)

        avatar = tk.Canvas(body, width=34, height=34, bg=theme.CARD, highlightthickness=0)
        avatar.pack(side="left", padx=(0, 10))
        color = self.app.avatar_color(entry.title)
        avatar.create_oval(2, 2, 32, 32, fill=color, outline="")
        initial = (entry.title.strip() or "?")[0].upper()
        avatar.create_text(17, 17, text=initial, fill=theme.WHITE, font=(theme.FONT_FAMILY, 10, "bold"))

        text_box = tk.Frame(body, bg=theme.CARD)
        text_box.pack(side="left", fill="x", expand=True)
        title = tk.Label(
            text_box,
            text=entry.title,
            bg=theme.CARD,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 10, "bold"),
            anchor="w",
        )
        title.pack(anchor="w")
        account = tk.Label(
            text_box,
            text=entry.account or "未填写账号",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        )
        account.pack(anchor="w")

        right = tk.Frame(body, bg=theme.CARD)
        right.pack(side="right")
        star = tk.Label(
            right,
            text="★" if entry.favorite else "",
            bg=theme.CARD,
            fg="#F5A524",
            font=(theme.FONT_FAMILY, 10),
        )
        star.pack(anchor="e")
        cat = tk.Label(
            right,
            text=entry.category or "未分类",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
        )
        cat.pack(anchor="e")

        widgets = [row, bar, body, avatar, text_box, title, account, right, star, cat]
        for w in widgets:
            w.bind("<Button-1>", lambda _e, i=index: self._select(i))
        row.bind("<Double-Button-1>", lambda _e, i=index: self._activate())
        for w in (title, account, body, text_box, avatar):
            w.bind("<Double-Button-1>", lambda _e, i=index: self._activate())
        row.bind("<Enter>", lambda _e, i=index: self._hover(i, True))
        row.bind("<Leave>", lambda _e, i=index: self._hover(i, False))
        self.rows.append(
            {
                "row": row,
                "bar": bar,
                "body": body,
                "avatar": avatar,
                "text_box": text_box,
                "title": title,
                "account": account,
                "right": right,
                "star": star,
                "cat": cat,
                "widgets": widgets,
                "entry": entry,
            }
        )

    def _paint_row(self, i: int, bg: str) -> None:
        if not (0 <= i < len(self.rows)):
            return
        row = self.rows[i]
        for w in row["widgets"]:
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass
        row["bar"].configure(bg=theme.ACCENT if i == self.index else bg)
        row["avatar"].configure(bg=bg)

    def _select(self, index: int) -> None:
        if not self.rows:
            self.index = -1
            return
        self.index = max(0, min(index, len(self.rows) - 1))
        for i in range(len(self.rows)):
            self._paint_row(i, theme.ACCENT_SOFT if i == self.index else theme.CARD)
        self._scroll_to(self.index)

    def _hover(self, index: int, entering: bool) -> None:
        if index == self.index:
            return
        if 0 <= index < len(self.rows):
            self._paint_row(index, "#F7F9FD" if entering else theme.CARD)

    def _move(self, delta: int) -> None:
        if self.rows:
            self._select(self.index + delta)

    def _scroll_to(self, index: int) -> None:
        if not (0 <= index < len(self.rows)):
            return
        try:
            self.rows_frame.update_idletasks()
            total = max(1, self.rows_frame.winfo_height())
            row = self.rows[index]["row"]
            top = row.winfo_y()
            bottom = top + row.winfo_height()
            view_top = self.scroll.canvas.canvasy(0)
            view_bottom = view_top + self.scroll.canvas.winfo_height()
            if top < view_top:
                self.scroll.canvas.yview_moveto(top / total)
            elif bottom > view_bottom:
                self.scroll.canvas.yview_moveto(max(0, (bottom - self.scroll.canvas.winfo_height()) / total))
        except Exception:
            pass

    def _update_actions(self) -> None:
        has = bool(self.rows)
        self.fill_btn.configure(state="normal" if has else "disabled")
        self.copy_btn.configure(state="normal" if has else "disabled")
        if self.entries:
            self.target_label.configure(
                text=(
                    f"目标窗口：{_truncate(window_title(self.target_hwnd), 52)}"
                    if self.target_hwnd and window_title(self.target_hwnd)
                    else (
                        "目标窗口：已锁定（请确认没有切错窗口）"
                        if self.target_hwnd
                        else "未找到目标窗口 · Enter 将复制密码"
                    )
                )
            )

    # ------------------------------------------------------------------ 动作
    def _activate(self) -> None:
        if not (0 <= self.index < len(self.entries)):
            return
        entry = self.entries[self.index]
        target = self.target_hwnd
        self._close()
        self.app.fill_to_window(entry, target)

    def _copy_password(self) -> None:
        if not (0 <= self.index < len(self.entries)):
            return
        entry = self.entries[self.index]
        self._close()
        self.app.copy_text(entry.password, f"已复制“{entry.title}”的密码")

    def _close(self) -> None:
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _on_destroy(self, event) -> None:
        if event.widget is self:
            self.app._overlay = None
