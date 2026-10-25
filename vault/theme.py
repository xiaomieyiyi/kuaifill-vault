"""界面配色与通用小部件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# 配色
BG = "#F3F6FB"
CARD = "#FFFFFF"
BORDER = "#E2E8F2"
BORDER_STRONG = "#CBD5E4"
TEXT = "#152033"
MUTED = "#69788F"
FAINT = "#98A6BA"
ACCENT = "#2F6BFF"
ACCENT_DARK = "#2456D6"
ACCENT_SOFT = "#EAF0FF"
TEAL = "#0E9F8E"
TEAL_SOFT = "#E6F7F4"
DANGER = "#D92D20"
DANGER_SOFT = "#FEECEB"
WARN = "#B54708"
WARN_SOFT = "#FFF4E5"
SIDEBAR = "#0F1B33"
SIDEBAR_CARD = "#172642"
SIDEBAR_HOVER = "#1D3052"
SIDEBAR_TEXT = "#C9D6EA"
SIDEBAR_MUTED = "#7F92B0"
WHITE = "#FFFFFF"
SHADOW = "#DCE3EF"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = "Segoe UI"

# ---------------------------------------------------------------------------
# 皮肤中心：每套皮肤只改主色、浅色底和侧边栏，文字/卡片保持高对比度
# ---------------------------------------------------------------------------

SKINS: dict[str, dict[str, str]] = {
    "经典蓝": {
        "accent": "#2F6BFF",
        "accent_dark": "#2456D6",
        "accent_soft": "#EAF0FF",
        "sidebar": "#0F1B33",
        "sidebar_card": "#172642",
        "sidebar_hover": "#1D3052",
        "bg": "#F3F6FB",
        "border": "#E2E8F2",
        "swatch": "#2F6BFF",
    },
    "森林绿": {
        "accent": "#0E9F7E",
        "accent_dark": "#0B7D63",
        "accent_soft": "#E4F7F0",
        "sidebar": "#0D2620",
        "sidebar_card": "#153830",
        "sidebar_hover": "#1C463C",
        "bg": "#F2F8F6",
        "border": "#E0ECE7",
        "swatch": "#0E9F7E",
    },
    "葡萄紫": {
        "accent": "#7A4DFF",
        "accent_dark": "#6338DB",
        "accent_soft": "#F0EBFF",
        "sidebar": "#1A1430",
        "sidebar_card": "#241B44",
        "sidebar_hover": "#2E2354",
        "bg": "#F6F3FC",
        "border": "#E9E3F6",
        "swatch": "#7A4DFF",
    },
    "珊瑚红": {
        "accent": "#E8465B",
        "accent_dark": "#C63348",
        "accent_soft": "#FDEBEE",
        "sidebar": "#2A1016",
        "sidebar_card": "#3A1720",
        "sidebar_hover": "#47202B",
        "bg": "#FCF4F5",
        "border": "#F0DFE2",
        "swatch": "#E8465B",
    },
    "琥珀橙": {
        "accent": "#E17A16",
        "accent_dark": "#BF6510",
        "accent_soft": "#FEF2E3",
        "sidebar": "#2A1B0C",
        "sidebar_card": "#3A2712",
        "sidebar_hover": "#482F16",
        "bg": "#FDF7EF",
        "border": "#F1E7D9",
        "swatch": "#E17A16",
    },
    "石墨灰": {
        "accent": "#334155",
        "accent_dark": "#1F2937",
        "accent_soft": "#EEF1F5",
        "sidebar": "#0B0F14",
        "sidebar_card": "#151B23",
        "sidebar_hover": "#1E2632",
        "bg": "#F4F5F7",
        "border": "#E3E6EA",
        "swatch": "#334155",
    },
}

DEFAULT_SKIN = "经典蓝"
CURRENT_SKIN = DEFAULT_SKIN


def skin_names() -> list[str]:
    return list(SKINS)


def skin_colors(name: str) -> dict[str, str]:
    return dict(SKINS.get(name) or SKINS[DEFAULT_SKIN])


def apply_skin(name: str) -> str:
    """切换配色，返回实际使用的皮肤名。只影响之后创建的控件。"""
    global CURRENT_SKIN, ACCENT, ACCENT_DARK, ACCENT_SOFT, SIDEBAR, SIDEBAR_CARD, SIDEBAR_HOVER, BG, BORDER
    chosen = name if name in SKINS else DEFAULT_SKIN
    colors = SKINS[chosen]
    ACCENT = colors["accent"]
    ACCENT_DARK = colors["accent_dark"]
    ACCENT_SOFT = colors["accent_soft"]
    SIDEBAR = colors["sidebar"]
    SIDEBAR_CARD = colors["sidebar_card"]
    SIDEBAR_HOVER = colors["sidebar_hover"]
    BG = colors["bg"]
    BORDER = colors["border"]
    CURRENT_SKIN = chosen
    return chosen


def setup_style(root: tk.Misc) -> ttk.Style:
    global FONT_FAMILY
    families = set(tkfont.families(root))
    family = FONT_FAMILY if FONT_FAMILY in families else FONT_FALLBACK
    FONT_FAMILY = family
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "TCombobox",
        fieldbackground=CARD,
        background=CARD,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", CARD)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background="#D6DFEC",
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=MUTED,
        width=10,
    )
    style.map("Vertical.TScrollbar", background=[("active", "#B9C6D8")])
    style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=(family, 10))
    style.map("TCheckbutton", background=[("active", CARD)])
    return style


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)


def make_button(
    parent,
    text: str,
    command,
    kind: str = "primary",
    width: int | None = None,
    padx: int = 16,
    pady: int = 9,
    font_size: int = 10,
    anchor: str = "center",
) -> tk.Button:
    palette = {
        "primary": (ACCENT, WHITE, ACCENT_DARK),
        "secondary": ("#EEF2F9", TEXT, "#E2E8F2"),
        "ghost": (CARD, MUTED, "#F1F5FB"),
        "danger": (DANGER_SOFT, DANGER, "#FBDDD9"),
        "teal": (TEAL, WHITE, "#0B8578"),
        "sidebar": (SIDEBAR_CARD, SIDEBAR_TEXT, SIDEBAR_HOVER),
        "sidebar_primary": (ACCENT, WHITE, ACCENT_DARK),
        "link": (CARD, ACCENT, ACCENT_SOFT),
    }
    bg, fg, active = palette.get(kind, palette["primary"])
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        font=font(font_size, "bold" if kind in ("primary", "teal", "sidebar_primary") else "normal"),
        cursor="hand2",
        padx=padx,
        pady=pady,
        anchor=anchor,
    )
    if width:
        btn.configure(width=width)
    return btn


def make_entry(
    parent,
    textvariable: tk.Variable | None = None,
    show: str | None = None,
    width: int = 20,
    font_size: int = 11,
    bg: str = CARD,
) -> tk.Entry:
    entry = tk.Entry(
        parent,
        textvariable=textvariable,
        show=show or "",
        width=width,
        font=font(font_size),
        bg=bg,
        fg=TEXT,
        insertbackground=ACCENT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        disabledbackground=bg,
        disabledforeground=MUTED,
    )
    return entry


def make_label(parent, text: str, size: int = 10, weight: str = "normal", fg: str = TEXT, bg: str = CARD, **kw) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        font=font(size, weight),
        fg=fg,
        bg=bg,
        **kw,
    )


def card_frame(parent, bg: str = CARD, border: str = BORDER, radius: int = 0) -> tk.Frame:
    return tk.Frame(
        parent,
        bg=bg,
        highlightbackground=border,
        highlightthickness=1,
        bd=0,
    )


def separator(parent, bg: str = BORDER, **pack_kw) -> tk.Frame:
    line = tk.Frame(parent, bg=bg, height=1)
    line.pack(fill="x", **pack_kw)
    return line


def center_window(win: tk.Misc, width: int, height: int, parent: tk.Misc | None = None) -> None:
    win.update_idletasks()
    if parent is not None and parent.winfo_viewable():
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
    else:
        x = (win.winfo_screenwidth() - width) // 2
        y = max(0, (win.winfo_screenheight() - height) // 2 - 30)
    win.geometry(f"{width}x{height}+{x}+{y}")


class ScrollFrame(tk.Frame):
    """可滚动容器：把内容放进 .inner。"""

    def __init__(self, parent, bg: str = CARD, scrollbar_style: str = "Vertical.TScrollbar", **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview, style=scrollbar_style
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def scroll_by(self, delta: int) -> None:
        if not self.canvas.winfo_exists():
            return
        step = int(-delta / 120)
        if step == 0:
            step = -1 if delta > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def scroll_top(self):
        self.canvas.yview_moveto(0)


