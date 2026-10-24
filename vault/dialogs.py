"""新建/编辑账号、设置、修改主密码对话框。"""

from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from . import theme
from .crypto import password_strength, random_password
from .db import (
    DEFAULT_CATEGORIES,
    DuplicateEntry,
    Entry,
    ValidationError,
    VaultError,
    WrongPassword,
)


def _tags_to_text(tags: list[str]) -> str:
    return "、".join(tags)


def _parse_tags(text: str) -> list[str]:
    parts = re.split(r"[,，;；、\s]+", text or "")
    return [p.strip() for p in parts if p.strip()]


class BaseDialog(tk.Toplevel):
    def __init__(self, app, title: str, width: int, height: int):
        super().__init__(app.root)
        self.app = app
        self.title(title)
        self.configure(bg=theme.BG)
        self.resizable(False, False)
        self.transient(app.root)
        self.withdraw()
        theme.center_window(self, width, height, app.root)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _e: self._close())
        self._closed = False
        app._register_dialog(self)

    def _show(self) -> None:
        self.deiconify()
        self.lift()
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.grab_current() is self:
                self.grab_release()
        except tk.TclError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass


class EntryDialog(BaseDialog):
    def __init__(self, app, entry: Optional[Entry] = None):
        super().__init__(app, "编辑账号" if entry else "新建账号", 780, 920)
        self.entry = entry
        self.is_new = entry is None
        self.error_var = tk.StringVar()
        self._build()
        if entry:
            self._fill(entry)
        self._show()
        self.after(50, self.title_entry.focus_set)
        self.bind("<Control-Return>", lambda _e: self._save())

    def _build(self) -> None:
        card = tk.Frame(self, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(card, bg=theme.CARD)
        head.pack(fill="x", padx=28, pady=(22, 4))
        tk.Label(
            head,
            text="新建账号" if self.is_new else "编辑账号",
            bg=theme.CARD,
            fg=theme.TEXT,
            font=(theme.FONT_FAMILY, 18, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            head,
            text="同一“名称 + 账号”只能保存一条，方便区分每个账号。",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=680,
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(card, bg=theme.CARD)
        body.pack(fill="both", expand=True, padx=28, pady=(12, 0))

        self.title_var = tk.StringVar()
        self.account_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.url_var = tk.StringVar()
        self.category_var = tk.StringVar(value="未分类")
        self.tags_var = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)

        self._field_label(body, "网站 / 应用名称 *")
        self.title_entry = theme.make_entry(body, self.title_var, width=1, font_size=11)
        self.title_entry.pack(fill="x", ipady=7, pady=(4, 12))

        self._field_label(body, "账号 / 邮箱 / 手机号 *")
        self.account_entry = theme.make_entry(body, self.account_var, width=1, font_size=11)
        self.account_entry.pack(fill="x", ipady=7, pady=(4, 12))

        self._field_label(body, "密码")
        pwd_row = tk.Frame(body, bg=theme.CARD)
        pwd_row.pack(fill="x", pady=(4, 3))
        self.password_entry = theme.make_entry(pwd_row, self.password_var, show="●", width=1, font_size=11)
        self.password_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.show_btn = theme.make_button(
            pwd_row, "显示", self._toggle_password, kind="ghost", padx=10, pady=6, font_size=9
        )
        self.show_btn.pack(side="left", padx=(6, 0))
        theme.make_button(
            pwd_row, "生成", self._generate, kind="secondary", padx=10, pady=6, font_size=9
        ).pack(side="left", padx=(6, 0))
        self.strength_label = tk.Label(
            body, text="", bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 8), anchor="w"
        )
        self.strength_label.pack(fill="x", pady=(0, 10))
        self.password_var.trace_add("write", lambda *_: self._update_strength())

        self._field_label(body, "登录网址（可选）")
        self.url_entry = theme.make_entry(body, self.url_var, width=1, font_size=11)
        self.url_entry.pack(fill="x", ipady=7, pady=(4, 12))

        two_col = tk.Frame(body, bg=theme.CARD)
        two_col.pack(fill="x", pady=(0, 12))
        left = tk.Frame(two_col, bg=theme.CARD)
        left.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._field_label(left, "分类")
        self.category_box = ttk.Combobox(
            left,
            textvariable=self.category_var,
            values=list(dict.fromkeys(DEFAULT_CATEGORIES + self.app.db.categories() + ["未分类"])),
            font=(theme.FONT_FAMILY, 10),
            state="normal",
        )
        self.category_box.pack(fill="x", pady=(4, 0))
        right = tk.Frame(two_col, bg=theme.CARD)
        right.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._field_label(right, "标签（用逗号分隔）")
        theme.make_entry(right, self.tags_var, width=1, font_size=10).pack(fill="x", ipady=7, pady=(4, 0))

        self._field_label(body, "备注（可选）")
        notes_wrap = tk.Frame(body, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        notes_wrap.pack(fill="x", pady=(4, 0))
        self.notes_text = tk.Text(
            notes_wrap,
            height=3,
            width=1,
            font=(theme.FONT_FAMILY, 10),
            bg=theme.CARD,
            fg=theme.TEXT,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            wrap="word",
            padx=8,
            pady=6,
        )
        self.notes_text.pack(fill="both", expand=True)

        tk.Label(
            body,
            textvariable=self.error_var,
            bg=theme.CARD,
            fg=theme.DANGER,
            font=(theme.FONT_FAMILY, 9),
            anchor="w",
            wraplength=690,
        ).pack(fill="x", pady=(10, 0))

        footer = tk.Frame(card, bg=theme.CARD)
        footer.pack(fill="x", padx=28, pady=(8, 20))
        theme.make_button(
            footer, "取消", self._close, kind="secondary", padx=20, pady=9
        ).pack(side="right")
        self.save_btn = theme.make_button(
            footer, "保存账号", self._save, kind="primary", padx=26, pady=9
        )
        self.save_btn.pack(side="right", padx=(0, 10))

    def _field_label(self, parent, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 9, "bold"),
            anchor="w",
        ).pack(fill="x")

    def _fill(self, entry: Entry) -> None:
        self.title_var.set(entry.title)
        self.account_var.set(entry.account)
        self.password_var.set(entry.password)
        self.url_var.set(entry.url)
        self.category_var.set(entry.category or "未分类")
        self.tags_var.set(_tags_to_text(entry.tags))
        self.notes_text.insert("1.0", entry.notes)
        self._update_strength()

    def _toggle_password(self) -> None:
        show = not self.show_password.get()
        self.show_password.set(show)
        self.password_entry.configure(show="" if show else "●")
        self.show_btn.configure(text="隐藏" if show else "显示")

    def _generate(self) -> None:
        self.password_var.set(random_password(20))
        self.password_entry.configure(show="")
        self.show_password.set(True)
        self.show_btn.configure(text="隐藏")
        self._update_strength()

    def _update_strength(self) -> None:
        pwd = self.password_var.get()
        if not pwd:
            self.strength_label.configure(text="建议使用 16 位以上、包含大小写和符号的密码", fg=theme.FAINT)
            return
        score, label = password_strength(pwd)
        colors = [theme.DANGER, theme.DANGER, theme.WARN, theme.TEAL, "#067647"]
        self.strength_label.configure(text=f"密码强度：{label} · {len(pwd)} 位", fg=colors[score])

    def _save(self) -> None:
        self.error_var.set("")
        title = self.title_var.get().strip()
        account = self.account_var.get().strip()
        url = self.url_var.get().strip()
        if not title and url:
            from urllib.parse import urlparse

            title = urlparse(url if "://" in url else "https://" + url).hostname or url
            self.title_var.set(title)
        if not title:
            self.error_var.set("请填写网站 / 应用名称")
            self.title_entry.focus_set()
            return
        if not account:
            self.error_var.set("请填写账号（邮箱、手机号或用户名）")
            self.account_entry.focus_set()
            return
        category = self.category_var.get().strip() or "未分类"
        notes = self.notes_text.get("1.0", "end-1c").strip()
        tags = _parse_tags(self.tags_var.get())
        base = self.entry
        candidate = Entry(
            id=base.id if base else "",
            title=title,
            account=account,
            password=self.password_var.get(),
            url=url,
            notes=notes,
            category=category,
            tags=tags,
            favorite=base.favorite if base else False,
            created_at=base.created_at if base else "",
        )
        self.save_btn.configure(state="disabled")
        self.update_idletasks()
        try:
            if self.is_new:
                saved = self.app.db.add_entry(candidate)
            else:
                saved = self.app.db.update_entry(candidate)
        except (DuplicateEntry, ValidationError, VaultError) as exc:
            self.error_var.set(str(exc))
            self.save_btn.configure(state="normal")
            return
        except Exception as exc:
            self.error_var.set(f"保存失败：{exc}")
            self.save_btn.configure(state="normal")
            return
        self._close()
        self.app.after_entry_saved(saved, new=self.is_new)


class SettingsDialog(BaseDialog):
    def __init__(self, app):
        super().__init__(app, "设置", 740, 980)
        self.db = app.db
        self.error_var = tk.StringVar()
        self._build()
        self._show()

    def _build(self) -> None:
        card = tk.Frame(self, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(card, bg=theme.CARD)
        head.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(
            head, text="设置", bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 18, "bold")
        ).pack(anchor="w")
        tk.Label(
            head, text="调整快捷键、自动锁定与填写行为", bg=theme.CARD, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9)
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(card, bg=theme.CARD)
        body.pack(fill="both", expand=True, padx=28, pady=(10, 0))

        s = self.db.settings
        self.hotkey_var = tk.StringVar(value=s.get("hotkey", "Ctrl+Alt+K"))
        self.lock_var = tk.StringVar(value=str(s.get("auto_lock_minutes", 5)))
        self.clip_var = tk.StringVar(value=str(s.get("clipboard_clear_seconds", 25)))
        self.delay_var = tk.StringVar(value=str(s.get("autotype_delay_ms", 8)))
        self.submit_var = tk.BooleanVar(value=bool(s.get("autotype_submit", False)))

        self._row(body, "快速填充快捷键", "例如 Ctrl+Alt+K；保存后会立即重新注册", self.hotkey_var)
        self._row(body, "无操作自动锁定（分钟）", "1 - 120 分钟；离开电脑时请手动锁定", self.lock_var)
        self._row(body, "剪贴板自动清除（秒）", "5 - 120 秒；只清除本程序复制的密码", self.clip_var)
        self._row(body, "自动填写逐字延迟（毫秒）", "填得太快时调大，例如 15 - 30", self.delay_var)

        check = tk.Frame(body, bg=theme.CARD)
        check.pack(fill="x", pady=(4, 12))
        ttk.Checkbutton(
            check,
            text="填完账号密码后自动按回车提交",
            variable=self.submit_var,
            style="TCheckbutton",
        ).pack(anchor="w")
        tk.Label(
            check,
            text="默认关闭。开启后可能直接提交登录表单，请确认网站没有二次验证风险。",
            bg=theme.CARD,
            fg=theme.FAINT,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        ).pack(anchor="w", padx=(24, 0))

        info = tk.Frame(body, bg="#F7F9FD", highlightbackground=theme.BORDER, highlightthickness=1)
        info.pack(fill="x", pady=(4, 10))
        info_inner = tk.Frame(info, bg="#F7F9FD")
        info_inner.pack(fill="x", padx=14, pady=12)
        tk.Label(
            info_inner,
            text=f"数据库：{self.app.db_path}",
            bg="#F7F9FD",
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
            wraplength=640,
            justify="left",
        ).pack(fill="x")
        tk.Label(
            info_inner,
            text=f"账号数量：{self.db.count()} 个    文件大小：{self.db.db_size() / 1024:.1f} KB",
            bg="#F7F9FD",
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 8),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        actions = tk.Frame(body, bg=theme.CARD)
        actions.pack(fill="x", pady=(2, 10))
        theme.make_button(
            actions, "修改主密码", self._change_password, kind="secondary", padx=14, pady=7, font_size=9
        ).pack(side="left")
        theme.make_button(
            actions, "立即备份", self._backup, kind="secondary", padx=14, pady=7, font_size=9
        ).pack(side="left", padx=(8, 0))
        theme.make_button(
            actions, "打开数据目录", self._open_data_dir, kind="ghost", padx=12, pady=7, font_size=9
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            body, textvariable=self.error_var, bg=theme.CARD, fg=theme.DANGER, font=(theme.FONT_FAMILY, 9), anchor="w"
        ).pack(fill="x", pady=(4, 0))

        footer = tk.Frame(card, bg=theme.CARD)
        footer.pack(fill="x", padx=28, pady=(10, 20))
        theme.make_button(footer, "取消", self._close, kind="secondary", padx=20, pady=9).pack(side="right")
        theme.make_button(footer, "保存设置", self._save, kind="primary", padx=24, pady=9).pack(
            side="right", padx=(0, 10)
        )

    def _row(self, parent, label: str, hint: str, var: tk.StringVar) -> None:
        wrap = tk.Frame(parent, bg=theme.CARD)
        wrap.pack(fill="x", pady=(0, 10))
        tk.Label(
            wrap, text=label, bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
        ).pack(anchor="w")
        theme.make_entry(wrap, var, width=1, font_size=10).pack(fill="x", ipady=6, pady=(4, 1))
        tk.Label(
            wrap, text=hint, bg=theme.CARD, fg=theme.FAINT, font=(theme.FONT_FAMILY, 8), anchor="w"
        ).pack(anchor="w")

    def _save(self) -> None:
        self.error_var.set("")
        try:
            minutes = int(self.lock_var.get().strip())
            seconds = int(self.clip_var.get().strip())
            delay = int(self.delay_var.get().strip())
        except ValueError:
            self.error_var.set("自动锁定、剪贴板和延迟都必须是整数")
            return
        if not 1 <= minutes <= 120:
            self.error_var.set("自动锁定时间需在 1 - 120 分钟之间")
            return
        if not 5 <= seconds <= 120:
            self.error_var.set("剪贴板清除时间需在 5 - 120 秒之间")
            return
        if not 0 <= delay <= 100:
            self.error_var.set("逐字延迟需在 0 - 100 毫秒之间")
            return
        new_hotkey = self.hotkey_var.get().strip().upper()
        try:
            from .autotype import format_hotkey

            new_hotkey = format_hotkey(new_hotkey)
        except Exception as exc:
            self.error_var.set(f"快捷键无效：{exc}")
            return
        old_hotkey = self.db.settings.get("hotkey", "Ctrl+Alt+K")
        try:
            from .autotype import format_hotkey

            old_hotkey = format_hotkey(old_hotkey)
        except Exception:
            pass
        if new_hotkey != old_hotkey:
            ok, error = self.app.change_hotkey(new_hotkey)
            if not ok:
                self.error_var.set(error)
                return
        try:
            self.db.update_settings(
                auto_lock_minutes=minutes,
                clipboard_clear_seconds=seconds,
                autotype_delay_ms=delay,
                autotype_submit=bool(self.submit_var.get()),
                hotkey=new_hotkey,
            )
        except Exception as exc:
            self.error_var.set(f"保存失败：{exc}")
            return
        self.app.settings = self.db.settings
        self.app._status("设置已保存")
        self._close()

    def _change_password(self) -> None:
        ChangePasswordDialog(self.app, parent=self)

    def _backup(self) -> None:
        try:
            path = self.db.backup()
        except Exception as exc:
            self.error_var.set(f"备份失败：{exc}")
            return
        self._show_info("备份完成", f"加密数据库已备份到：\n{path}")

    def _open_data_dir(self) -> None:
        try:
            os.startfile(self.app.db_path.parent)  # type: ignore[attr-defined]
        except Exception as exc:
            self.error_var.set(f"无法打开目录：{exc}")

    def _show_info(self, title: str, message: str) -> None:
        messagebox.showinfo(title, message, parent=self)


class ChangePasswordDialog(BaseDialog):
    def __init__(self, app, parent=None):
        super().__init__(app, "修改主密码", 680, 640)
        self.error_var = tk.StringVar()
        self.old_var = tk.StringVar()
        self.new_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        self.show_var = tk.BooleanVar(value=False)
        self._build()
        self._show()
        self.after(50, self.old_entry.focus_set)

    def _build(self) -> None:
        card = tk.Frame(self, bg=theme.CARD, highlightbackground=theme.BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(card, bg=theme.CARD)
        head.pack(fill="x", padx=28, pady=(24, 4))
        tk.Label(
            head, text="修改主密码", bg=theme.CARD, fg=theme.TEXT, font=(theme.FONT_FAMILY, 18, "bold")
        ).pack(anchor="w")
        tk.Label(
            head,
            text="修改前会自动备份数据库。忘记主密码无法找回，请务必记牢。",
            bg=theme.CARD,
            fg=theme.MUTED,
            font=(theme.FONT_FAMILY, 9),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(card, bg=theme.CARD)
        body.pack(fill="both", expand=True, padx=28, pady=(14, 0))
        self.old_entry = self._field(body, "当前主密码", self.old_var)
        self.new_entry = self._field(body, "新主密码（至少 8 位）", self.new_var)
        self.confirm_entry = self._field(body, "再次输入新主密码", self.confirm_var)
        ttk.Checkbutton(
            body, text="显示密码", variable=self.show_var, command=self._toggle_show, style="TCheckbutton"
        ).pack(anchor="w")
        tk.Label(
            body, textvariable=self.error_var, bg=theme.CARD, fg=theme.DANGER, font=(theme.FONT_FAMILY, 9), anchor="w"
        ).pack(fill="x", pady=(10, 0))

        footer = tk.Frame(card, bg=theme.CARD)
        footer.pack(fill="x", padx=28, pady=(12, 20))
        theme.make_button(footer, "取消", self._close, kind="secondary", padx=20, pady=9).pack(side="right")
        theme.make_button(footer, "确认修改", self._save, kind="primary", padx=24, pady=9).pack(
            side="right", padx=(0, 10)
        )

    def _field(self, parent, label: str, var: tk.StringVar) -> tk.Entry:
        tk.Label(
            parent, text=label, bg=theme.CARD, fg=theme.MUTED, font=(theme.FONT_FAMILY, 9, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 3))
        entry = theme.make_entry(parent, var, show="●", width=1, font_size=11)
        entry.pack(fill="x", ipady=7, pady=(0, 10))
        return entry

    def _toggle_show(self) -> None:
        show = "" if self.show_var.get() else "●"
        for entry in (self.old_entry, self.new_entry, self.confirm_entry):
            entry.configure(show=show)

    def _save(self) -> None:
        self.error_var.set("")
        old = self.old_var.get()
        new = self.new_var.get()
        confirm = self.confirm_var.get()
        if not old:
            self.error_var.set("请输入当前主密码")
            return
        if len(new) < 8:
            self.error_var.set("新主密码至少 8 个字符")
            return
        if new != confirm:
            self.error_var.set("两次输入的新主密码不一致")
            return
        if new == old:
            self.error_var.set("新主密码不能与当前主密码相同")
            return
        backup_path = None
        try:
            backup_path = self.app.db.backup()
        except Exception:
            backup_path = None
        try:
            self.app.db.change_master_password(old, new)
        except WrongPassword:
            self.error_var.set("当前主密码不正确")
            return
        except Exception as exc:
            self.error_var.set(f"修改失败：{exc}")
            return
        self.app._status("主密码已修改")
        self._close()
        text = "主密码已修改。旧主密码立即失效。"
        if backup_path:
            text += f"\n\n修改前的数据库备份：\n{backup_path}"
        messagebox.showinfo("主密码已修改", text, parent=self.app.root)
