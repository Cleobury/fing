"""The Settings window opened from the tray menu.

A sidebar of pages (General, TypeSafe, AI planner, …) under the logo, in the Windows 11 look (the Sun Valley
ttk theme, light or dark to match Windows), with Save and Cancel at the bottom.
"""

import ctypes
import logging
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import replace
from tkinter import colorchooser, ttk

import keyboard
from typesafe_sdk import TypeSafeAPIError, TypeSafeAuthenticationError, TypeSafeError

from . import autostart, brand, remote, search, wakephrase
from .decide import Decider
from .llm import PROVIDERS, Planner
from .overlay import DEFAULT_BG, DEFAULT_DOTS, DEFAULT_FG
from .scripts import Script, breakdown_key, load_scripts, save_scripts, split_lines
from .settings import get_api_key, set_api_key

log = logging.getLogger(__name__)

_PROVIDER_LABELS = {"off": "Off", "openrouter": "OpenRouter", "ollama": "Ollama (local)"}
_SAME_AS_PLANNER = "Same as the planner"
_SYSTEM_DEFAULT_MIC = "System default"
_MODE_LABELS = {"confused": "Only when the classifier is unsure", "always": "Always (rewrite every command first)"}
_POSITION_LABELS = {
    "bottom-centre": "Bottom centre (above taskbar)",
    "bottom-left": "Bottom left",
    "bottom-right": "Bottom right",
    "top-centre": "Top centre",
    "top-left": "Top left",
    "top-right": "Top right",
    "custom": "Custom (dragged)",
}
# Indicator dot states the user can recolour, with a sample message to preview each.
_DOT_LABELS = {
    "idle": ("Idle", ""),
    "listening": ("Listening", "Listening…"),
    "thinking": ("Working", "“open Steam”"),
    "done": ("Done", "Open Steam"),
    "question": ("Question", "Click which one?  1) …  2) …"),
    "warn": ("Warning", "Couldn't find it on this screen"),
    "error": ("Error", "Couldn't reach TypeSafe"),
    "yolo": ("YOLO idle", ""),
}


# Each page in the sidebar: its icon (Segoe Fluent Icons / MDL2 Assets, as on Windows' own Settings).
_PAGE_ICONS = {
    "General": "\ue713", "TypeSafe": "\ue8d7", "AI planner": "\ue82f", "Hands-free": "\ue720",
    "Phone": "\ue8ea", "PC search": "\ue721", "Scripts": "\ue8fd", "Indicator": "\ue790",
}
_ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
# Sidebar and text colours that go with the Sun Valley theme's light and dark backgrounds.
_PALETTE = {
    "light": {"side": "#f0f0f3", "hover": "#e4e4ea", "selected": "#e0dcff", "text": "#1b1b1f", "muted": "#5f6370",
              "page": "#fafafa", "field": "#ffffff", "link": "#5b3fe0"},
    "dark": {"side": "#202024", "hover": "#2c2c33", "selected": "#352d5c", "text": "#f2f2f5", "muted": "#a2a5b4",
             "page": "#1c1c1c", "field": "#2b2b2b", "link": "#a99bff"},
}


def _windows_dark() -> bool:
    """Whether Windows is set to dark mode for apps."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def _dark_title_bar(win: tk.Toplevel) -> None:
    """Windows 10 (20H1+) and 11: a dark title bar to match a dark window."""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
    except (AttributeError, OSError):
        pass


def _link(parent, text: str, url: str) -> ttk.Label:
    label = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2")
    label.bind("<Button-1>", lambda _: __import__("webbrowser").open(url))
    return label


def _row(parent, row: int, label: str) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0), padx=(0, 12))


def _heading(parent, row: int, text: str, columns: int = 3, first: bool = False) -> None:
    """A group heading inside a page ("Safety", "Reading the screen", …)."""
    ttk.Label(parent, text=text, style="Heading.TLabel").grid(row=row, column=0, columnspan=columns, sticky="w",
                                                              pady=(0 if first else 18, 0))


class SettingsDialog:
    def __init__(self, app):
        self.app = app
        s = app.settings
        self._served_by_test = False  # a Test switched the local model in memory
        win = self.win = tk.Toplevel(app.root)
        win.withdraw()  # shown once laid out, so it doesn't flash at the wrong size
        win.title(f"{s.name} settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.mode = "dark" if _windows_dark() else "light"
        self.colours = _PALETTE[self.mode]
        self._style()
        c = self.colours

        side = tk.Frame(win, bg=c["side"], width=210)
        side.grid(row=0, column=0, rowspan=2, sticky="ns")
        side.grid_propagate(False)
        self._logo = self._photo(brand.logo(44))
        head = tk.Frame(side, bg=c["side"])
        head.pack(fill="x", padx=18, pady=(22, 18))
        tk.Label(head, image=self._logo, bg=c["side"]).pack(side="left")
        names = tk.Frame(head, bg=c["side"])
        names.pack(side="left", padx=(10, 0))
        self.title_label = tk.Label(names, text=s.name, bg=c["side"], fg=c["text"], font=("Segoe UI Semibold", 15))
        self.title_label.pack(anchor="w")
        tk.Label(names, text="Desktop assistant", bg=c["side"], fg=c["muted"], font=("Segoe UI", 9)).pack(anchor="w")
        self.nav = tk.Frame(side, bg=c["side"])
        self.nav.pack(fill="x", padx=8)
        families = set(tkfont.families(win))
        self._icon_font = next(((f, 12) for f in _ICON_FONTS if f in families), None)

        body = ttk.Frame(win, padding=(28, 22, 28, 0))
        body.grid(row=0, column=1, sticky="nsew")
        self.page_title = ttk.Label(body, style="Title.TLabel")
        self.page_title.grid(row=0, column=0, sticky="w", pady=(0, 12))
        self.pages_frame = ttk.Frame(body)
        self.pages_frame.grid(row=1, column=0, sticky="nsew")
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_items: dict[str, tuple[tk.Frame, ...]] = {}
        self.page = ""

        self._build_general_tab(self, s)
        self._build_typesafe_tab(self, s)
        self._build_planner_tab(self, s)
        self._build_handsfree_tab(self, s)
        self._build_phone_tab(self, s)
        self._build_search_tab(self, s)
        self._build_scripts_tab(self)
        self._build_indicator_tab(self, s)

        foot = ttk.Frame(win, padding=(28, 14, 20, 18))
        foot.grid(row=1, column=1, sticky="we")
        foot.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Keys are stored in Windows Credential Manager.")
        ttk.Label(foot, textvariable=self.status, style="Muted.TLabel", wraplength=420, justify="left").grid(
            row=0, column=0, sticky="w")
        btns = ttk.Frame(foot)
        btns.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.test_btn = ttk.Button(btns, text="Test connections", command=self.test)
        self.test_btn.pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.close).pack(side="left", padx=6)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        win.columnconfigure(1, weight=1)
        win.rowconfigure(0, weight=1)

        self._provider_changed(initial=True)
        self._powertoys_changed(initial=True)
        self.show_page("General" if get_api_key() else "TypeSafe")
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
        y = (win.winfo_screenheight() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        win.deiconify()
        if self.mode == "dark":
            _dark_title_bar(win)
        win.focus_force()

    # ---- look and layout ------------------------------------------------------------

    def _style(self) -> None:
        """The Windows 11 (Sun Valley) theme when it's installed, plus the few styles of our own."""
        c = self.colours
        try:
            import sv_ttk

            sv_ttk.set_theme(self.mode, self.app.root)
        except Exception:  # not installed (older setup): the standard Windows look
            log.info("sv-ttk unavailable; using the default ttk theme")
            self.colours = c = {**c, **{"page": ttk.Style(self.win).lookup("TFrame", "background") or c["page"]}}
        st = ttk.Style(self.win)
        if "sun-valley" in st.theme_use():  # its pages are the window's own background colour
            for name in (".", "TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
                st.configure(name, background=c["page"], foreground=c["text"])
        self.win.configure(bg=c["page"])
        st.configure("Muted.TLabel", foreground=c["muted"])
        st.configure("Link.TLabel", foreground=c["link"])
        st.configure("Title.TLabel", font=("Segoe UI Semibold", 20))
        st.configure("Heading.TLabel", font=("Segoe UI Semibold", 11))
        st.configure("Good.TLabel", foreground="#2f9e55")
        st.configure("Weak.TLabel", foreground="#d9822b")

    def _photo(self, image):
        from PIL import ImageTk

        return ImageTk.PhotoImage(image, master=self.win)

    def _tab(self, _, title: str) -> ttk.Frame:
        """A page, and its entry in the sidebar."""
        frame = ttk.Frame(self.pages_frame)
        frame.grid(row=0, column=0, sticky="nsew")
        self.pages[title] = frame
        c = self.colours
        item = tk.Frame(self.nav, bg=c["side"], cursor="hand2")
        item.pack(fill="x", pady=1)
        bar = tk.Frame(item, bg=c["side"], width=3, height=18)
        bar.pack(side="left", padx=(2, 0))
        parts = [item, bar]
        if self._icon_font:
            icon = tk.Label(item, text=_PAGE_ICONS.get(title, ""), font=self._icon_font, bg=c["side"], fg=c["text"])
            icon.pack(side="left", padx=(10, 0), pady=7)
            parts.append(icon)
        label = tk.Label(item, text=title, font=("Segoe UI", 10), bg=c["side"], fg=c["text"], anchor="w")
        label.pack(side="left", fill="x", expand=True, padx=(12, 8), pady=7)
        parts.append(label)
        self.nav_items[title] = tuple(parts)
        for w in parts:
            w.bind("<Button-1>", lambda _, t=title: self.show_page(t))
            w.bind("<Enter>", lambda _, t=title: self._paint_nav(t, hover=True))
            w.bind("<Leave>", lambda _, t=title: self._paint_nav(t))
        return frame

    def _paint_nav(self, title: str, hover: bool = False) -> None:
        c = self.colours
        bg = c["selected"] if title == self.page else c["hover"] if hover else c["side"]
        item, bar, *rest = self.nav_items[title]
        for w in (item, *rest):
            w.configure(bg=bg)
        bar.configure(bg=c["link"] if title == self.page else bg)

    def show_page(self, title: str) -> None:
        old, self.page = self.page, title
        if old:
            self._paint_nav(old)
        self._paint_nav(title)
        self.page_title.configure(text=title)
        self.pages[title].tkraise()
        if title == "TypeSafe":
            self.key_entry.focus_set()

    def _plain(self, widget: tk.Widget) -> tk.Widget:
        """Colour a classic Tk widget (list, text box) to match the theme."""
        c = self.colours
        widget.configure(background=c["field"], foreground=c["text"], highlightthickness=1,
                         highlightbackground=c["hover"], highlightcolor=c["link"], relief="flat", borderwidth=4)
        if isinstance(widget, (tk.Listbox, tk.Text)):
            widget.configure(selectbackground=c["selected"], selectforeground=c["text"])
        if isinstance(widget, tk.Text):
            widget.configure(insertbackground=c["text"])
        return widget

    # ---- General ----------------------------------------------------------------------

    def _build_general_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "General")
        self.assistant_name = tk.StringVar(value=s.name)
        self._last_name = s.name
        self.dry_run = tk.BooleanVar(value=s.dry_run)

        _heading(f, 0, "Your assistant", first=True)
        _row(f, 1, "Name")
        ttk.Entry(f, textvariable=self.assistant_name, width=24).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.name_hint = ttk.Label(f, style="Muted.TLabel", wraplength=470, justify="left")
        self.name_hint.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.mic = tk.StringVar(value=s.mic_device or _SYSTEM_DEFAULT_MIC)
        _row(f, 3, "Microphone")
        ttk.Combobox(f, textvariable=self.mic, values=self._mic_choices(s.mic_device), state="readonly", width=40).grid(
            row=3, column=1, columnspan=2, sticky="we", pady=(8, 0))
        self.autostart = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(f, text="Start with Windows (when I sign in)", variable=self.autostart).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        _heading(f, 5, "How it acts")
        ttk.Checkbutton(f, text="Dry run: highlight what would happen without doing it", variable=self.dry_run).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.yolo = tk.BooleanVar(value=s.yolo)
        self.yolo_irreversible = tk.BooleanVar(value=not s.yolo_allow_irreversible)
        ttk.Checkbutton(f, text="YOLO mode: decide everything without asking me", variable=self.yolo,
                        command=self._yolo_changed).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.yolo_safety_check = ttk.Checkbutton(
            f, text="…but still refuse irreversible actions (delete, buy, send, sign out…)", variable=self.yolo_irreversible)
        self.yolo_safety_check.grid(row=8, column=0, columnspan=3, sticky="w", padx=(26, 0), pady=(4, 0))
        self._yolo_changed()

        _heading(f, 9, "Reading the screen")
        self.read_controls = tk.BooleanVar(value=s.read_controls)
        ttk.Checkbutton(f, text="Also read button and icon names from the app (Windows accessibility)",
                        variable=self.read_controls).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.all_screens = tk.BooleanVar(value=s.all_screens)
        ttk.Checkbutton(f, text="Read all my screens, not just the one I'm working on", variable=self.all_screens).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.assistant_name.trace_add("write", lambda *_: self._name_changed())

    def _name(self) -> str:
        return self.assistant_name.get().strip() or brand.DEFAULT_NAME

    def _name_changed(self) -> None:
        """Renaming the assistant renames its wake phrase too ("Hey Fing" → "Hey Pointer"), and everything
        in this window that says its name."""
        new = self._name()
        self.wake_phrase.set(wakephrase.renamed(self.wake_phrase.get(), self._last_name, new))
        self._last_name = new
        self.title_label.configure(text=new)
        self.win.title(f"{new} settings")
        self._refresh_name_hint()
        self._wake_phrase_changed()

    def _refresh_name_hint(self) -> None:
        phrase = (self.wake_phrase.get().strip() or wakephrase.for_name(self._name())).title()
        self.name_hint.configure(text=f"Shown on the indicator, and what you call it: say “{phrase}” to start "
                                      "(turn that on and fine-tune it under Hands-free).")

    # ---- TypeSafe ---------------------------------------------------------------------

    def _build_typesafe_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "TypeSafe")
        ttk.Label(f, style="Muted.TLabel", wraplength=540, justify="left", text=(
            "The TypeSafe API decides every action: it reads your request and what's on screen, and picks what to "
            "click, type or press from the choices on the screen. It never makes up coordinates or text.")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.key = tk.StringVar(value=get_api_key() or "")
        self.model = tk.StringVar(value=s.model)
        self.min_action = tk.DoubleVar(value=s.min_action_prob)
        self.min_target = tk.DoubleVar(value=s.min_target_prob)

        _heading(f, 1, "Connection")
        _row(f, 2, "API key")
        self.key_entry = ttk.Entry(f, textvariable=self.key, show="•", width=44)
        self.key_entry.grid(row=2, column=1, columnspan=2, sticky="we", pady=(8, 0))
        _link(f, "Get a key at console.typesafe.ai/keys", "https://console.typesafe.ai/keys").grid(
            row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))
        _row(f, 4, "Model")
        model_row = ttk.Frame(f)
        model_row.grid(row=4, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Entry(model_row, textvariable=self.model, width=20).pack(side="left")
        self.typesafe_test_btn = ttk.Button(model_row, text="Test key", command=self.test_typesafe)
        self.typesafe_test_btn.pack(side="left", padx=(8, 0))
        self.typesafe_result = ttk.Label(f, style="Muted.TLabel", wraplength=420, justify="left")
        self.typesafe_result.grid(row=5, column=1, columnspan=2, sticky="w", pady=(6, 0))

        _heading(f, 6, "Confidence")
        ttk.Label(f, style="Muted.TLabel", wraplength=540, justify="left", text=(
            "How sure it must be before acting on its own. Below these it asks you, or looks further.")).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))
        _row(f, 8, "Min action probability")
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05, textvariable=self.min_action, width=6).grid(
            row=8, column=1, sticky="w", pady=(8, 0))
        _row(f, 9, "Min target probability")
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05, textvariable=self.min_target, width=6).grid(
            row=9, column=1, sticky="w", pady=(8, 0))

    def _typesafe_check(self, key: str, model: str) -> str:
        """One line on whether TypeSafe answers with this key and model (any thread)."""
        if not key:
            return "Enter a TypeSafe API key."
        try:
            p = Decider(key, model).ping()
            return f"Connected to {model} (test answer {p:.2f}, expect ≈1)."
        except TypeSafeAuthenticationError:
            return "Key rejected (401)."
        except TypeSafeAPIError as e:
            return f"API error {e.status}."
        except TypeSafeError as e:
            return f"Could not connect ({e})."

    def test_typesafe(self) -> None:
        key, model = self.key.get().strip(), self.model.get().strip() or "jev-latest"
        self.typesafe_result.configure(text="Testing…", style="Muted.TLabel")
        self.typesafe_test_btn.state(["disabled"])

        def run():
            msg = self._typesafe_check(key, model)
            self.app.ui(self._typesafe_tested, msg)

        threading.Thread(target=run, daemon=True).start()

    def _typesafe_tested(self, msg: str) -> None:
        if self.win.winfo_exists():
            self.typesafe_result.configure(text=msg, style="Good.TLabel" if msg.startswith("Connected") else "Weak.TLabel")
            self.typesafe_test_btn.state(["!disabled"])

    def _mic_choices(self, saved: str) -> list[str]:
        names = []
        # Importing .audio initialises COM, which must wait until Whisper has loaded (see app._load_model).
        if self.app.recorder:
            from .audio import input_devices
            try:
                names = input_devices()
            except Exception:
                log.exception("Could not list microphones")
        if saved and saved not in names:
            names.append(saved)  # unplugged, or not loaded yet: keep it pickable
        return [_SYSTEM_DEFAULT_MIC, *names]

    def _yolo_changed(self) -> None:
        self.yolo_safety_check.state(["!disabled"] if self.yolo.get() else ["disabled"])

    # ---- AI planner -----------------------------------------------------------------

    def _build_planner_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "AI planner")
        ttk.Label(f, text="Rewrites a request into simple steps when the TypeSafe classifier is unsure, and finds "
                          "icons and images.", style="Muted.TLabel", wraplength=540, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.provider = tk.StringVar(value=_PROVIDER_LABELS.get(s.llm_provider, "Off"))
        self.llm_model = tk.StringVar(value=s.llm_model)
        self.llm_key = tk.StringVar()
        self.llm_url = tk.StringVar(value=s.llm_base_url)
        self.llm_mode = tk.StringVar(value=_MODE_LABELS.get(s.llm_mode, _MODE_LABELS["confused"]))
        self.llm_screenshot = tk.BooleanVar(value=s.llm_screenshot)
        self.llm_keep_alive = tk.BooleanVar(value=s.llm_keep_alive)

        _row(f, 1, "Provider")
        prov = ttk.Combobox(f, textvariable=self.provider, values=list(_PROVIDER_LABELS.values()), state="readonly", width=18)
        prov.grid(row=1, column=1, sticky="w", pady=(8, 0))
        prov.bind("<<ComboboxSelected>>", lambda _: self._provider_changed())
        _row(f, 2, "Model")
        self.model_box = ttk.Combobox(f, textvariable=self.llm_model, width=34)
        self.model_box.grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.load_btn = ttk.Button(f, text="Load list", command=self.load_models)
        self.load_btn.grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
        self.llm_key_label = ttk.Label(f, text="OpenRouter key")
        self.llm_key_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.llm_key_entry = ttk.Entry(f, textvariable=self.llm_key, show="•", width=46)
        self.llm_key_entry.grid(row=3, column=1, columnspan=2, sticky="we", pady=(8, 0))
        self.llm_key_link = _link(f, "Get a key at openrouter.ai/keys", "https://openrouter.ai/keys")
        self.llm_key_link.grid(row=4, column=1, columnspan=2, sticky="w")
        _row(f, 5, "Server URL")
        self.url_entry = ttk.Entry(f, textvariable=self.llm_url, width=46)
        self.url_entry.grid(row=5, column=1, columnspan=2, sticky="we", pady=(8, 0))
        _row(f, 6, "Use it")
        ttk.Combobox(f, textvariable=self.llm_mode, values=list(_MODE_LABELS.values()), state="readonly", width=34).grid(
            row=6, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Checkbutton(f, text="Send a screenshot (needs a vision model)", variable=self.llm_screenshot).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.keep_alive_check = ttk.Checkbutton(
            f, text="Keep the model loaded in memory (Ollama; uses GPU memory while the app runs)", variable=self.llm_keep_alive)
        self.keep_alive_check.grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))
        _row(f, 9, "Vision model")
        self.vision_model = tk.StringVar(value=s.vision_model or _SAME_AS_PLANNER)
        self.vision_box = ttk.Combobox(f, textvariable=self.vision_model, values=[_SAME_AS_PLANNER], width=34)
        self.vision_box.grid(row=9, column=1, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Label(f, text="Finds icons and images on the screenshot when text isn't enough. It must point accurately:\n"
                          "Test connections checks it (e.g. qwen3.8 can, gemma4 can't).",
                  style="Muted.TLabel").grid(row=10, column=1, columnspan=2, sticky="w")

    def _vision_choice(self) -> str:
        v = self.vision_model.get().strip()
        return "" if v in ("", _SAME_AS_PLANNER) else v

    def _provider_id(self) -> str:
        return next(k for k, v in _PROVIDER_LABELS.items() if v == self.provider.get())

    def _provider_changed(self, initial: bool = False) -> None:
        pid = self._provider_id()
        info = PROVIDERS.get(pid)
        on = info is not None
        needs_key = bool(info and info["needs_key"])
        for w in (self.model_box, self.load_btn, self.url_entry):
            w.state(["!disabled"] if on else ["disabled"])
        self.keep_alive_check.state(["!disabled"] if pid == "ollama" else ["disabled"])
        self.llm_key_entry.state(["!disabled"] if needs_key else ["disabled"])
        self.llm_key.set((get_api_key(pid) or "") if needs_key else "")
        self.llm_key_label.configure(text=f"{_PROVIDER_LABELS.get(pid, '')} key" if needs_key else "API key")
        if needs_key:
            self.llm_key_link.grid()
        else:
            self.llm_key_link.grid_remove()
        if on and not initial:
            # Switching provider: show that provider's defaults.
            self.llm_url.set("")
            self.llm_model.set(info["model"])
            self.model_box.configure(values=[])
            self.vision_model.set(_SAME_AS_PLANNER)  # a model name from the old provider won't exist on this one
            self.vision_box.configure(values=[_SAME_AS_PLANNER])
            if pid == "ollama":
                self.load_models()
        if on and not self.llm_url.get() and not initial:
            self.status.set(f"Server: {info['base_url']} (leave URL blank for this default)")

    def _planner(self) -> Planner | None:
        pid = self._provider_id()
        if pid not in PROVIDERS:
            return None
        return Planner(pid, self.llm_model.get().strip(), self.llm_key.get().strip() or None,
                       self.llm_url.get().strip() or None, self.llm_screenshot.get(), timeout_s=20,
                       keep_alive=self.llm_keep_alive.get())

    def load_models(self) -> None:
        planner = self._planner()
        if planner is None:
            return
        self.status.set("Loading models…")

        def run():
            try:
                models = planner.list_models()
                vision = planner.list_vision_models()
                if planner.provider == "openrouter":
                    msg = f"{len(models)} models available with structured output ({len(vision)} take images)"
                elif models:
                    msg = f"{len(models)} local models can generate text, {len(vision)} can see images (embedding-only models are hidden)"
                else:
                    msg = "Ollama has no models that can generate text yet: run `ollama pull <model>` first"
            except Exception as e:
                models, vision, msg = [], [], f"Couldn't load models: {e}"
            self.app.ui(self._models_loaded, models, vision, msg)

        threading.Thread(target=run, daemon=True).start()

    def _models_loaded(self, models: list[str], vision: list[str], msg: str) -> None:
        if not self.win.winfo_exists():
            return
        self.model_box.configure(values=models)
        self.vision_box.configure(values=[_SAME_AS_PLANNER, *vision])
        current = self.llm_model.get()
        if models and current not in models:
            # The current choice isn't usable (e.g. an embedding model, or blank): offer the first one that is.
            self.llm_model.set(models[0])
            if current:
                msg += f". “{current}” can't be used for planning, so I picked {models[0]}"
        self.status.set(msg)

    # ---- Hands-free -------------------------------------------------------------------

    _RATING = {"weak": ("Weak: ", "#b06000"), "ok": ("OK: ", "#5f6368"), "good": ("Good ✓", "#188038")}

    def _build_handsfree_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "Hands-free")
        ttk.Label(f, text="Say a phrase to start talking, instead of holding Right Ctrl. "
                          "Listening happens on this PC; nothing is sent anywhere until you give a command.",
                  style="Muted.TLabel", wraplength=480).grid(row=0, column=0, columnspan=3, sticky="w")
        self.wake_on = tk.BooleanVar(value=s.wake_enabled)
        self.wake_phrase = tk.StringVar(value=s.wake_phrase)
        self.wake_sens = tk.DoubleVar(value=s.wake_sensitivity)
        self.auto_listen = tk.BooleanVar(value=s.auto_listen_answers)
        self.mic_sounds = tk.BooleanVar(value=s.mic_sounds)
        ttk.Checkbutton(f, text="Listen for a wake word", variable=self.wake_on).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        _row(f, 2, "Wake phrase")
        ttk.Entry(f, textvariable=self.wake_phrase, width=30).grid(row=2, column=1, sticky="w", pady=(8, 0))
        self.wake_test_btn = ttk.Button(f, text="Test", command=self._toggle_wake_test)
        self.wake_test_btn.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        self.wake_rating = ttk.Label(f, wraplength=360, justify="left")
        self.wake_rating.grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.wake_suggest = ttk.Frame(f)
        self.wake_suggest.grid(row=4, column=1, columnspan=2, sticky="w", pady=(4, 0))
        _row(f, 5, "Sensitivity")
        sens = ttk.Frame(f)
        sens.grid(row=5, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(sens, text="Fewer false triggers", style="Muted.TLabel").pack(side="left")
        ttk.Scale(sens, from_=0.0, to=1.0, variable=self.wake_sens, length=160).pack(side="left", padx=6)
        ttk.Label(sens, text="Catches more", style="Muted.TLabel").pack(side="left")
        self.wake_test_text = tk.StringVar(value="Test listens without triggering anything, and counts how often the "
                                                 "phrase would have gone off.")
        ttk.Label(f, textvariable=self.wake_test_text, style="Muted.TLabel", wraplength=480, justify="left").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Separator(f).grid(row=7, column=0, columnspan=3, sticky="we", pady=(14, 0))
        ttk.Checkbutton(f, text="Listen for my answer automatically when I'm asked a question",
                        variable=self.auto_listen).grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(f, style="Muted.TLabel", wraplength=480, justify="left", text=(
            "The mic opens by itself after the question's beep and closes when you stop talking; number keys still "
            "work. If you gave the command from your phone, the phone listens instead, as long as its page is open "
            "and you've used its mic since opening it.")).grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(f, text="Play a chime when the mic opens and closes", variable=self.mic_sounds).grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self._wake_test_started = 0.0
        self._wake_test_heard = self._wake_test_hits = 0
        self.wake_phrase.trace_add("write", lambda *_: self._wake_phrase_changed())
        self._wake_phrase_changed()

    def _wake_phrase_changed(self) -> None:
        """Rate the phrase as it's typed, and offer stronger ones when it's weak."""
        phrase = self.wake_phrase.get()
        st = wakephrase.strength(phrase, self._name())
        prefix, colour = self._RATING[st.rating]
        self.wake_rating.configure(text=prefix + st.message, foreground=colour)
        for child in self.wake_suggest.winfo_children():
            child.destroy()
        for text in st.suggestions:
            ttk.Button(self.wake_suggest, text=text, command=lambda t=text: self.wake_phrase.set(t)).pack(
                side="left", padx=(0, 6))
        self._refresh_name_hint()
        listener = self.app.listener
        if listener and listener.on_test:
            listener.test_phrase = phrase.strip() or wakephrase.DEFAULT_PHRASE

    def _toggle_wake_test(self) -> None:
        listener = self.app.listener
        if listener is None:
            self.wake_test_text.set("The microphone isn't ready yet.")
            return
        if listener.on_test:
            self._stop_wake_test()
            return
        self._wake_test_started = time.monotonic()
        self._wake_test_heard = self._wake_test_hits = 0
        listener.test_phrase = self.wake_phrase.get().strip() or wakephrase.DEFAULT_PHRASE
        listener.on_test = lambda heard, score: self.app.ui(self._wake_heard, heard, score)
        self.wake_test_btn.configure(text="Stop test")
        self.wake_test_text.set("Testing: say the phrase a few times, then talk normally or play a video. "
                                "Nothing will be triggered.")

    def _wake_heard(self, heard: str, score: float) -> None:
        """Test: something was heard (Tk thread)."""
        if not self.win.winfo_exists() or not (self.app.listener and self.app.listener.on_test):
            return
        hit = score >= wakephrase.threshold(self.wake_sens.get())
        self._wake_test_heard += 1
        self._wake_test_hits += hit
        minutes = (time.monotonic() - self._wake_test_started) / 60
        verdict = "would trigger ✓" if hit else "wouldn't trigger"
        self.wake_test_text.set(f"Heard “{heard}”: {score:.0%} match, {verdict}.\n"
                                f"{self._wake_test_hits} of {self._wake_test_heard} things heard would have "
                                f"triggered, in {minutes:.1f} min.")

    def _stop_wake_test(self) -> None:
        if self.app.listener:
            self.app.listener.on_test = None
        self.wake_test_btn.configure(text="Test")

    # ---- Phone remote -----------------------------------------------------------------

    def _build_phone_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "Phone")
        ttk.Label(f, text="Hold a button on your phone to talk instead of Right Ctrl, over your Wi-Fi.",
                  style="Muted.TLabel", wraplength=480).grid(row=0, column=0, columnspan=3, sticky="w")
        self.remote_on = tk.BooleanVar(value=s.remote_enabled)
        self.remote_pin = tk.StringVar(value=s.remote_pin or remote.new_pin())
        self.remote_port = tk.IntVar(value=s.remote_port)
        self.remote_url = tk.StringVar()
        ttk.Checkbutton(f, text="Let my phone control this PC on this network", variable=self.remote_on,
                        command=self._remote_changed).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        _row(f, 2, "Address")
        ttk.Entry(f, textvariable=self.remote_url, state="readonly", width=34).grid(row=2, column=1, columnspan=2, sticky="we", pady=(8, 0))
        _row(f, 3, "PIN")
        ttk.Label(f, textvariable=self.remote_pin, font=("Consolas", 12, "bold")).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Button(f, text="New PIN", command=self._new_remote_pin).grid(row=3, column=2, sticky="w", pady=(8, 0))
        _row(f, 4, "Port")
        port = ttk.Spinbox(f, from_=1024, to=65535, textvariable=self.remote_port, width=7, command=self._remote_changed)
        port.grid(row=4, column=1, sticky="w", pady=(8, 0))
        port.bind("<KeyRelease>", lambda _: self._remote_changed())
        self.qr = tk.Canvas(f, width=180, height=180, highlightthickness=0, background="white")
        self.qr.grid(row=5, column=0, rowspan=2, sticky="nw", pady=(14, 0))
        ttk.Label(f, wraplength=290, style="Muted.TLabel", justify="left", text=(
            "Scan the code with your phone's camera (it includes the PIN), then Save here. "
            "The first time, your phone warns that the connection isn't private, because this PC made its own "
            "certificate: choose Advanced → Proceed, or Show Details → visit this website. "
            "If Windows asks, allow Python on private networks.\n\n"
            "Anyone with the PIN on your network can control this PC; New PIN signs out every phone.")).grid(
            row=5, column=1, columnspan=2, sticky="nw", padx=(12, 0), pady=(14, 0))
        self._remote_changed()

    def _new_remote_pin(self) -> None:
        self.remote_pin.set(remote.new_pin())
        self._remote_changed()

    def _remote_port(self) -> int | None:
        try:
            port = int(self.remote_port.get())
        except (tk.TclError, ValueError):
            return None
        return port if 1024 <= port <= 65535 else None

    def _remote_changed(self) -> None:
        port = self._remote_port()
        on = self.remote_on.get() and port is not None
        link = remote.url(port, self.remote_pin.get()) if on else ""
        self.remote_url.set(link.split("#")[0] if on else "Turn it on to get the address")
        self.qr.delete("all")
        self.qr.configure(background="white" if on else self.colours["page"])
        if not on:
            return
        import qrcode

        code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(link)
        matrix = code.get_matrix()
        cell = 180 / len(matrix)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    self.qr.create_rectangle(x * cell, y * cell, (x + 1) * cell, (y + 1) * cell, fill="black", width=0)

    # ---- PC search --------------------------------------------------------------------

    def _build_search_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "PC search")
        ttk.Label(f, text="Used to find files, folders and settings that aren't on screen.", style="Muted.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        self.powertoys = tk.BooleanVar(value=s.powertoys_search)
        self.hotkey = tk.StringVar(value=s.search_hotkey)
        ttk.Checkbutton(f, text="Search with PowerToys (Command Palette / Run) instead of the Start menu",
                        variable=self.powertoys, command=self._powertoys_changed).grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))
        _row(f, 2, "Its shortcut")
        self.hotkey_entry = ttk.Entry(f, textvariable=self.hotkey, width=22)
        self.hotkey_entry.grid(row=2, column=1, sticky="w", pady=(8, 0))
        hk_btns = ttk.Frame(f)
        hk_btns.grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(8, 0))
        self.record_btn = ttk.Button(hk_btns, text="Record", width=7, command=self.record_hotkey)
        self.record_btn.pack(side="left")
        self.detect_btn = ttk.Button(hk_btns, text="Detect", width=7, command=self.detect_hotkey)
        self.detect_btn.pack(side="left", padx=(4, 0))

    def _powertoys_changed(self, initial: bool = False) -> None:
        on = self.powertoys.get()
        for w in (self.hotkey_entry, self.record_btn, self.detect_btn):
            w.state(["!disabled"] if on else ["disabled"])
        if on and not initial:
            self.detect_hotkey(quiet_if_same=True)

    def detect_hotkey(self, quiet_if_same: bool = False) -> None:
        found = search.detect_powertoys_hotkey()
        if found is None:
            self.status.set("Couldn't find a PowerToys search shortcut: is Command Palette or PowerToys Run enabled?")
        elif found != self.hotkey.get():
            self.hotkey.set(found)
            self.status.set(f"Using your PowerToys shortcut: {found}")
        elif not quiet_if_same:
            self.status.set(f"Matches your PowerToys shortcut ({found}).")

    def record_hotkey(self) -> None:
        self.status.set("Press the shortcut now…")
        self.record_btn.state(["disabled"])

        def run():
            combo = keyboard.read_hotkey(suppress=True)  # capture it without also triggering it
            self.app.ui(self._hotkey_recorded, combo)

        threading.Thread(target=run, daemon=True).start()

    def _hotkey_recorded(self, combo: str) -> None:
        if not self.win.winfo_exists():
            return
        self.record_btn.state(["!disabled"])
        self.hotkey.set(combo)
        self.status.set(f"Shortcut set to {combo}.")

    # ---- Scripts ------------------------------------------------------------------------

    def _build_scripts_tab(self, tabs) -> None:
        f = self._tab(tabs, "Scripts")
        ttk.Label(f, text="Saved lists of tasks, run the same way every time (app testing, routines). Start one by saying "
                          "“run the <name> script”, or from the tray menu. Write steps in plain words; lines like "
                          "“check that …” are verified and reported, “wait 3 seconds” pauses.",
                  style="Muted.TLabel", wraplength=520, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        self.scripts = load_scripts()
        self._script_idx: int | None = None

        left = ttk.Frame(f)
        left.grid(row=1, column=0, sticky="nsw", pady=(10, 0), padx=(0, 12))
        self.script_list = self._plain(tk.Listbox(left, height=14, width=22, exportselection=False, activestyle="none",
                                                  font=("Segoe UI", 10)))
        self.script_list.pack(fill="y", expand=True)
        self.script_list.bind("<<ListboxSelect>>", lambda _: self._select_script())
        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=(6, 0))
        ttk.Button(lb, text="New", width=8, command=self._new_script).pack(side="left")
        ttk.Button(lb, text="Delete", width=8, command=self._delete_script).pack(side="left", padx=(4, 0))

        right = ttk.Frame(f)
        right.grid(row=1, column=1, sticky="nsew", pady=(10, 0))
        ttk.Label(right, text="Name").grid(row=0, column=0, sticky="w")
        self.script_name = tk.StringVar()
        self.script_name_entry = ttk.Entry(right, textvariable=self.script_name, width=40)
        self.script_name_entry.grid(row=0, column=1, sticky="we")
        self.script_text = self._plain(tk.Text(right, height=9, width=52, wrap="word", font=("Segoe UI", 10), undo=True))
        self.script_text.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self.script_unattended = tk.BooleanVar(value=True)
        self.script_stop = tk.BooleanVar(value=True)
        ttk.Checkbutton(right, text="Run unattended (decide everything itself, like YOLO mode)",
                        variable=self.script_unattended).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(right, text="Stop at the first failed step or check", variable=self.script_stop).grid(
            row=3, column=0, columnspan=2, sticky="w")
        rb = ttk.Frame(right)
        rb.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.break_btn = ttk.Button(rb, text="Break into steps", command=self._break_script)
        self.break_btn.pack(side="left")
        ttk.Button(rb, text="Run now", command=self._run_script_now).pack(side="left", padx=(6, 0))
        ttk.Label(right, text="Steps it will follow", style="Heading.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.steps_preview = self._plain(tk.Text(right, height=7, width=52, wrap="word", font=("Segoe UI", 9),
                                                 state="disabled"))
        self.steps_preview.configure(background=self.colours["page"], foreground=self.colours["muted"])
        self.steps_preview.grid(row=6, column=0, columnspan=2, sticky="nsew")
        self._refresh_script_list(select=0 if self.scripts else None)

    def _refresh_script_list(self, select: int | None = None) -> None:
        self.script_list.delete(0, "end")
        for s in self.scripts:
            self.script_list.insert("end", s.name)
        self._script_idx = None
        if select is not None and self.scripts:
            self.script_list.selection_set(select)
            self._select_script()
        else:
            self._load_script_fields(None)

    def _load_script_fields(self, script: Script | None) -> None:
        state = ["!disabled"] if script else ["disabled"]
        self.script_name_entry.state(state)
        self.script_name.set(script.name if script else "")
        self.script_text.configure(state="normal")
        self.script_text.delete("1.0", "end")
        if script:
            self.script_text.insert("1.0", script.text)
        else:
            self.script_text.configure(state="disabled")
        self.script_unattended.set(script.unattended if script else True)
        self.script_stop.set(script.stop_on_failure if script else True)
        self._show_steps(script)

    def _store_script(self) -> None:
        """Copy the edit fields back into the selected script."""
        if self._script_idx is None or self._script_idx >= len(self.scripts):
            return
        s = self.scripts[self._script_idx]
        name = self.script_name.get().strip() or s.name
        taken = {o.name for i, o in enumerate(self.scripts) if i != self._script_idx}
        base, n = name, 2
        while name in taken:
            name, n = f"{base} ({n})", n + 1
        s.name = name
        s.text = self.script_text.get("1.0", "end").strip()
        s.unattended, s.stop_on_failure = self.script_unattended.get(), self.script_stop.get()
        self.script_list.delete(self._script_idx)
        self.script_list.insert(self._script_idx, s.name)
        self.script_list.selection_set(self._script_idx)

    def _select_script(self) -> None:
        sel = self.script_list.curselection()
        if not sel or sel[0] == self._script_idx:
            return
        self._store_script()
        self._script_idx = sel[0]
        self._load_script_fields(self.scripts[self._script_idx])

    def _new_script(self) -> None:
        self._store_script()
        names = {s.name for s in self.scripts}
        n = len(self.scripts) + 1
        while f"Script {n}" in names:
            n += 1
        self.scripts.append(Script(f"Script {n}"))
        self._refresh_script_list(select=len(self.scripts) - 1)
        self.script_name_entry.focus_set()
        self.script_name_entry.select_range(0, "end")

    def _delete_script(self) -> None:
        if self._script_idx is None:
            return
        del self.scripts[self._script_idx]
        self._refresh_script_list(select=min(self._script_idx, len(self.scripts) - 1) if self.scripts else None)

    def _show_steps(self, script: Script | None) -> None:
        planner = self._planner()
        if script is None:
            text = ""
        elif planner is None:
            text = "\n".join(f"{i}. {s}" for i, s in enumerate(split_lines(script.text), 1)) or "(empty)"
            text += "\n\n(No AI planner: each line is a step.)" if script.text else ""
        elif script.steps and script.steps_key == breakdown_key(script.text, planner.model):
            text = "\n".join(f"{i}. {s}" for i, s in enumerate(script.steps, 1))
        else:
            text = "(Not broken into steps yet: press Break into steps, or it happens on the first run.)"
        self.steps_preview.configure(state="normal")
        self.steps_preview.delete("1.0", "end")
        self.steps_preview.insert("1.0", text)
        self.steps_preview.configure(state="disabled")

    def _break_script(self) -> None:
        self._store_script()
        if self._script_idx is None:
            return
        script = self.scripts[self._script_idx]
        planner = self._planner()
        if planner is None:
            self._show_steps(script)
            return
        self.status.set(f"Breaking “{script.name}” into steps with {planner.model}…")
        self.break_btn.state(["disabled"])

        def run():
            try:
                steps, msg = planner.break_script(script.name, script.text), None
            except Exception as e:
                steps, msg = None, f"Couldn't break it into steps: {str(e)[:120]}"
            self.app.ui(self._script_broken, script, planner.model, steps, msg)

        threading.Thread(target=run, daemon=True).start()

    def _script_broken(self, script: Script, model: str, steps: list[str] | None, msg: str | None) -> None:
        if not self.win.winfo_exists():
            return
        self.break_btn.state(["!disabled"])
        if steps:
            script.steps, script.steps_key = steps, breakdown_key(script.text, model)
            msg = f"{len(steps)} steps. They'll be followed exactly on every run until you edit the script (Save to keep them)."
        self.status.set(msg or "The AI returned no steps.")
        if self._script_idx is not None and self.scripts[self._script_idx] is script:
            self._show_steps(script)

    def _run_script_now(self) -> None:
        self._store_script()
        if self._script_idx is None:
            return
        name = self.scripts[self._script_idx].name
        self.save()  # keeps the scripts (and any other changes) and closes the window
        if self.app.settings_dialog is None:
            self.app.run_script(name)

    # ---- Indicator ----------------------------------------------------------------------

    def _build_indicator_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "Indicator")
        ttk.Label(f, text="The status pill above the taskbar. Changes preview live.", style="Muted.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w")
        self.style = {"bg": s.overlay_bg, "fg": s.overlay_fg, "dots": {**DEFAULT_DOTS, **s.overlay_dots}}
        self.opacity = tk.IntVar(value=s.overlay_opacity)
        self.swatches: dict[str, tk.Button] = {}

        _row(f, 1, "Background")
        self._swatch(f, "bg", 1, 1, "Background", self._sample_idle())
        _row(f, 2, "Text")
        self._swatch(f, "fg", 2, 1, "Text", self._sample_idle())
        _row(f, 3, "Opacity")
        op = ttk.Frame(f)
        op.grid(row=3, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Scale(op, from_=20, to=100, variable=self.opacity, length=220, command=lambda _: self._opacity_moved()).pack(side="left")
        self.opacity_label = ttk.Label(op, width=5)
        self.opacity_label.pack(side="left", padx=(8, 0))

        _row(f, 4, "Position")
        self.position = tk.StringVar(value=_POSITION_LABELS.get(s.overlay_position, _POSITION_LABELS["bottom-centre"]))
        self.custom_xy = (s.overlay_x, s.overlay_y)
        pos = ttk.Frame(f)
        pos.grid(row=4, column=1, columnspan=3, sticky="w", pady=(8, 0))
        box = ttk.Combobox(pos, textvariable=self.position, values=[v for k, v in _POSITION_LABELS.items() if k != "custom"],
                           state="readonly", width=24)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _: self._preview("idle", self._sample_idle()))
        ttk.Button(pos, text="Drag…", command=self._drag_indicator).pack(side="left", padx=(6, 0))
        ttk.Button(pos, text="Reset position", command=self._reset_position).pack(side="left", padx=(6, 0))

        ttk.Label(f, text="Dot colours", style="Heading.TLabel").grid(row=5, column=0, columnspan=4, sticky="w", pady=(18, 0))
        for i, (state, (label, sample)) in enumerate(_DOT_LABELS.items()):
            row, col = 6 + i // 2, (i % 2) * 2
            ttk.Label(f, text=label).grid(row=row, column=col, sticky="w", pady=(8, 0), padx=(0, 12))
            self._swatch(f, state, row, col + 1, f"{label} dot", sample, dot=True)
        ttk.Button(f, text="Reset to defaults", command=self._reset_style).grid(row=10, column=0, columnspan=4, sticky="w", pady=(14, 0))

        ttk.Label(f, text="Hand animations", style="Heading.TLabel").grid(row=11, column=0, columnspan=4, sticky="w", pady=(18, 0))
        self.overlay_fx = tk.BooleanVar(value=s.overlay_fx)
        ttk.Checkbutton(f, text="Show the hand: it taps what it clicks, waves hello and celebrates when it's done",
                        variable=self.overlay_fx).grid(row=12, column=0, columnspan=4, sticky="w", pady=(8, 0))
        demo = ttk.Frame(f)
        demo.grid(row=13, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(demo, text="Try one:", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        for text, name in (("Wave", "wave"), ("Tap", "tap"), ("Confetti", "celebrate"), ("Shake", "shake")):
            ttk.Button(demo, text=text, command=lambda n=name: self._demo_fx(n)).pack(side="left", padx=(0, 6))
        self._opacity_moved(preview=False)

    def _demo_fx(self, name: str) -> None:
        """Play one of the indicator's animations, even with them switched off (to see what they look like)."""
        overlay, fx = self.app.overlay, self.app.fx
        self.app.apply_overlay_style(self._style_settings())
        if name == "shake":
            overlay.show("error", "Something went wrong", 2500)
            overlay.shake()
            return
        overlay.show("done" if name == "celebrate" else overlay.idle_state, self._sample_idle(), 2500)
        x, y = overlay.dot_screen()
        fx.enabled = True
        if name == "tap":  # the middle of this window's title, as if Fing were clicking it
            fx.tap(self.win.winfo_rootx() + self.win.winfo_width() // 2, self.win.winfo_rooty() + 40, "#34a853")
        else:
            getattr(fx, name)(x, y)
        fx.enabled = self.overlay_fx.get()

    def _sample_idle(self) -> str:
        return f"{self._name()} ready · hold Right Ctrl to speak"

    def _swatch(self, parent, key: str, row: int, col: int, title: str, sample: str, dot: bool = False) -> None:
        colour = self.style["dots"][key] if dot else self.style[key]
        b = tk.Button(parent, width=4, relief="groove", bg=colour, activebackground=colour,
                      command=lambda: self._pick(key, title, sample, dot))
        b.grid(row=row, column=col, sticky="w", pady=(8, 0), padx=(0, 18))
        self.swatches[key] = b

    def _pick(self, key: str, title: str, sample: str, dot: bool) -> None:
        current = self.style["dots"][key] if dot else self.style[key]
        _, colour = colorchooser.askcolor(current, parent=self.win, title=f"{title} colour")
        if not colour:
            return
        if dot:
            self.style["dots"][key] = colour
        else:
            self.style[key] = colour
        self.swatches[key].configure(bg=colour, activebackground=colour)
        self._preview(key if dot else "idle", sample)

    def _opacity_moved(self, preview: bool = True) -> None:
        self.opacity_label.configure(text=f"{self.opacity.get():.0f}%")
        if preview:
            self._preview("idle", self._sample_idle())

    def _drag_indicator(self) -> None:
        """Let the user drag the indicator; the spot becomes the "Custom" position (saved on Save)."""
        self.status.set("Drag the indicator where you want it, then double-click it.")

        def done(x: int, y: int) -> None:
            if not self.win.winfo_exists():
                return
            self.custom_xy = (x, y)
            self.position.set(_POSITION_LABELS["custom"])
            self.status.set("Indicator placed. Save to keep it there.")

        self.app.apply_overlay_style(self._style_settings())
        self.app.overlay.start_move(done)

    def _reset_position(self) -> None:
        self.app.overlay.finish_move(keep=False)
        self.position.set(_POSITION_LABELS["bottom-centre"])
        self.custom_xy = (0, 0)
        self.status.set("Indicator back at bottom centre. Save to keep it there.")
        self._preview("idle", self._sample_idle())

    def _position_id(self) -> str:
        return next(k for k, v in _POSITION_LABELS.items() if v == self.position.get())

    def _reset_style(self) -> None:
        self.style = {"bg": DEFAULT_BG, "fg": DEFAULT_FG, "dots": dict(DEFAULT_DOTS)}
        self.position.set(_POSITION_LABELS["bottom-centre"])
        self.opacity.set(100)
        for key, b in self.swatches.items():
            colour = self.style["dots"].get(key) or self.style[key]
            b.configure(bg=colour, activebackground=colour)
        self._opacity_moved()

    def _style_settings(self):
        return replace(self.app.settings, overlay_bg=self.style["bg"], overlay_fg=self.style["fg"],
                       overlay_opacity=round(self.opacity.get()), overlay_dots=self._changed_dots(),
                       overlay_position=self._position_id(), overlay_x=self.custom_xy[0], overlay_y=self.custom_xy[1],
                       overlay_fx=self.overlay_fx.get())

    def _changed_dots(self) -> dict[str, str]:
        return {k: v for k, v in self.style["dots"].items() if v.lower() != DEFAULT_DOTS[k].lower()}

    def _preview(self, state: str, sample: str) -> None:
        """Show the indicator with the unsaved style for a few seconds, in the state being edited."""
        overlay = self.app.overlay
        self.app.apply_overlay_style(self._style_settings())
        overlay.show(state, sample, 3000)

    # ---- test / save / close ---------------------------------------------------------------

    def test(self) -> None:
        key, model = self.key.get().strip(), self.model.get().strip() or "jev-latest"
        planner = self._planner()
        self.status.set("Testing…")
        self.test_btn.state(["disabled"])

        def run():
            lines = ["TypeSafe: " + self._typesafe_check(key, model)]
            if planner is not None:
                try:
                    served = ""
                    if planner.ollama_id:
                        # Testing a local model makes it the one in memory: unload the previous, load this one.
                        self.app.ui(self.status.set, f"Loading {planner.model}…")
                        served = self.app.serve_model(planner)
                        self._served_by_test = True
                    planner.ping()
                    lines.append(f"AI planner: {planner.model} replied" + (f" ({served})." if served else "."))
                    if self.llm_screenshot.get():
                        vision = planner if not self._vision_choice() else Planner(
                            planner.provider, self._vision_choice(), self.llm_key.get().strip() or None,
                            self.llm_url.get().strip() or None, True, timeout_s=120, keep_alive=self.llm_keep_alive.get())
                        self.app.ui(self.status.set, f"Checking whether {vision.model} can point at things…")
                        hits, tries = vision.pointing_check()
                        verdict = "good" if hits == tries else "unreliable: pick another vision model" if hits < tries - 1 else "mostly OK"
                        lines.append(f"Vision: {vision.model} pointed at {hits}/{tries} test shapes ({verdict}).")
                except Exception as e:
                    lines.append(f"AI planner: failed ({str(e)[:120]}).")
            self.app.ui(self._test_done, " ".join(lines))

        threading.Thread(target=run, daemon=True).start()

    def _test_done(self, msg: str) -> None:
        if self.win.winfo_exists():
            self.status.set(msg)
            self.test_btn.state(["!disabled"])

    def save(self) -> None:
        self.app.overlay.finish_move()  # still being dragged: take where it is now
        try:
            min_action, min_target = float(self.min_action.get()), float(self.min_target.get())
        except (tk.TclError, ValueError):
            self.status.set("Probabilities must be numbers between 0 and 1.")
            return
        remote_port = self._remote_port()
        if remote_port is None:
            self.status.set("The phone port must be a number from 1024 to 65535.")
            return
        if self.wake_on.get() and not wakephrase.words(self.wake_phrase.get()):
            self.status.set("Type a wake phrase on the Hands-free tab, or turn the wake word off.")
            return
        hotkey = self.hotkey.get().strip().lower() or search.DEFAULT_HOTKEY
        if self.powertoys.get() and not search.valid_hotkey(hotkey):
            self.status.set(f"“{hotkey}” isn't a shortcut I recognise. Try Record, or e.g. “left alt+space”.")
            return
        s = self.app.settings
        s.assistant_name = self._name()
        s.model = self.model.get().strip() or "jev-latest"
        s.dry_run = self.dry_run.get()
        s.read_controls = self.read_controls.get()
        s.all_screens = self.all_screens.get()
        s.remote_enabled = self.remote_on.get()
        s.remote_pin = self.remote_pin.get()
        s.remote_port = remote_port
        s.mic_device = "" if self.mic.get() == _SYSTEM_DEFAULT_MIC else self.mic.get()
        s.wake_enabled = self.wake_on.get()
        s.wake_phrase = self.wake_phrase.get().strip() or s.wake_phrase
        s.wake_sensitivity = round(min(1.0, max(0.0, float(self.wake_sens.get()))), 2)
        s.auto_listen_answers = self.auto_listen.get()
        s.mic_sounds = self.mic_sounds.get()
        s.yolo = self.yolo.get()
        s.yolo_allow_irreversible = not self.yolo_irreversible.get()
        s.min_action_prob = min(1.0, max(0.0, min_action))
        s.min_target_prob = min(1.0, max(0.0, min_target))
        pid = self._provider_id()
        s.llm_provider = pid
        s.llm_model = self.llm_model.get().strip()
        s.llm_base_url = self.llm_url.get().strip()
        s.llm_mode = next(k for k, v in _MODE_LABELS.items() if v == self.llm_mode.get())
        s.llm_screenshot = self.llm_screenshot.get()
        s.llm_keep_alive = self.llm_keep_alive.get()
        s.vision_model = self._vision_choice()
        s.powertoys_search = self.powertoys.get()
        s.search_hotkey = hotkey
        s.overlay_bg, s.overlay_fg = self.style["bg"], self.style["fg"]
        s.overlay_opacity = round(self.opacity.get())
        s.overlay_dots = self._changed_dots()
        s.overlay_position = self._position_id()
        s.overlay_x, s.overlay_y = self.custom_xy
        s.overlay_fx = self.overlay_fx.get()
        self._store_script()
        save_scripts(self.scripts)
        if self.autostart.get() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.get())
            except Exception as e:
                self.status.set(f"Couldn't change Start with Windows: {str(e)[:100]}")
                return
        s.save()
        set_api_key(self.key.get().strip())
        if PROVIDERS.get(pid, {}).get("needs_key"):
            set_api_key(self.llm_key.get().strip(), pid)
        self.app.on_settings_changed()
        self._destroy()

    def close(self) -> None:
        """Cancel: drop unsaved changes, including any indicator preview or unfinished drag, and any local model
        a Test loaded (going back to the saved one)."""
        self.app.overlay.finish_move(keep=False)
        self.app.apply_overlay_style()
        if self._served_by_test:
            saved = self.app.planner
            self.app.serve_model_in_background(saved, load=bool(saved and saved.keep_alive))
        self._destroy()

    def _destroy(self) -> None:
        self._stop_wake_test()
        self.win.destroy()
        self.app.settings_dialog = None
