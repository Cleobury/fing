"""The Settings window opened from the tray menu."""

import threading
import tkinter as tk
from dataclasses import replace
from tkinter import colorchooser, ttk

import keyboard
from typesafe_sdk import TypeSafeAPIError, TypeSafeAuthenticationError, TypeSafeError

from . import autostart, search
from .decide import Decider
from .llm import PROVIDERS, Planner
from .overlay import DEFAULT_BG, DEFAULT_DOTS, DEFAULT_FG
from .scripts import Script, breakdown_key, load_scripts, save_scripts, split_lines
from .settings import get_api_key, set_api_key

_PROVIDER_LABELS = {"off": "Off", "openrouter": "OpenRouter", "ollama": "Ollama (local)"}
_SAME_AS_PLANNER = "Same as the planner"
_MODE_LABELS = {"confused": "Only when Jev is confused", "always": "Always (rewrite every command first)"}
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


def _link(parent, text: str, url: str) -> ttk.Label:
    label = ttk.Label(parent, text=text, foreground="#1a73e8", cursor="hand2")
    label.bind("<Button-1>", lambda _: __import__("webbrowser").open(url))
    return label


def _row(parent, row: int, label: str) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=(8, 0), padx=(0, 12))


class SettingsDialog:
    def __init__(self, app):
        self.app = app
        s = app.settings
        self._served_by_test = False  # a Test switched the local model in memory
        win = self.win = tk.Toplevel(app.root)
        win.title("Jev Harness settings")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(win, padding=12)
        outer.grid(sticky="nsew")
        tabs = ttk.Notebook(outer)
        tabs.grid(row=0, column=0, sticky="nsew")

        self._build_jev_tab(tabs, s)
        self._build_planner_tab(tabs, s)
        self._build_search_tab(tabs, s)
        self._build_scripts_tab(tabs)
        self._build_indicator_tab(tabs, s)

        self.status = tk.StringVar(value="Keys are stored in Windows Credential Manager.")
        ttk.Label(outer, textvariable=self.status, foreground="#5f6368", wraplength=480).grid(row=1, column=0, sticky="w", pady=(10, 0))
        btns = ttk.Frame(outer)
        btns.grid(row=2, column=0, sticky="e", pady=(10, 0))
        self.test_btn = ttk.Button(btns, text="Test connections", command=self.test)
        self.test_btn.pack(side="left")
        ttk.Button(btns, text="Cancel", command=self.close).pack(side="left", padx=6)
        ttk.Button(btns, text="Save", command=self.save).pack(side="left")

        self._provider_changed(initial=True)
        self._powertoys_changed(initial=True)
        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = (win.winfo_screenheight() - win.winfo_height()) // 3
        win.geometry(f"+{x}+{y}")
        win.focus_force()
        self.key_entry.focus_set()

    def _tab(self, tabs: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(tabs, padding=14)
        tabs.add(frame, text=title)
        return frame

    # ---- Jev ----------------------------------------------------------------------

    def _build_jev_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "Jev")
        self.key = tk.StringVar(value=get_api_key() or "")
        self.model = tk.StringVar(value=s.model)
        self.dry_run = tk.BooleanVar(value=s.dry_run)
        self.min_action = tk.DoubleVar(value=s.min_action_prob)
        self.min_target = tk.DoubleVar(value=s.min_target_prob)

        _row(f, 0, "TypeSafe API key")
        self.key_entry = ttk.Entry(f, textvariable=self.key, show="•", width=46)
        self.key_entry.grid(row=0, column=1, columnspan=2, sticky="we", pady=(8, 0))
        _link(f, "Get a key at console.typesafe.ai/keys", "https://console.typesafe.ai/keys").grid(row=1, column=1, columnspan=2, sticky="w")
        _row(f, 2, "Model")
        ttk.Entry(f, textvariable=self.model, width=20).grid(row=2, column=1, sticky="w", pady=(8, 0))
        _row(f, 3, "Min action probability")
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05, textvariable=self.min_action, width=6).grid(row=3, column=1, sticky="w", pady=(8, 0))
        _row(f, 4, "Min target probability")
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05, textvariable=self.min_target, width=6).grid(row=4, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(f, text="Dry run: highlight what would happen without doing it", variable=self.dry_run).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.yolo = tk.BooleanVar(value=s.yolo)
        self.yolo_irreversible = tk.BooleanVar(value=not s.yolo_allow_irreversible)
        ttk.Checkbutton(f, text="YOLO mode: decide everything without asking me", variable=self.yolo,
                        command=self._yolo_changed).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.yolo_safety_check = ttk.Checkbutton(
            f, text="…but still refuse irreversible actions (delete, buy, send, sign out…)", variable=self.yolo_irreversible)
        self.yolo_safety_check.grid(row=7, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(4, 0))
        self._yolo_changed()
        self.autostart = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(f, text="Start with Windows (when I sign in)", variable=self.autostart).grid(
            row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _yolo_changed(self) -> None:
        self.yolo_safety_check.state(["!disabled"] if self.yolo.get() else ["disabled"])

    # ---- AI planner -----------------------------------------------------------------

    def _build_planner_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "AI planner")
        ttk.Label(f, text="Rewrites a request into simple steps when Jev is confused.", foreground="#5f6368").grid(
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
                  foreground="#5f6368").grid(row=10, column=1, columnspan=2, sticky="w")

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

    # ---- PC search --------------------------------------------------------------------

    def _build_search_tab(self, tabs, s) -> None:
        f = self._tab(tabs, "PC search")
        ttk.Label(f, text="Used to find files, folders and settings that aren't on screen.", foreground="#5f6368").grid(
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
                  foreground="#5f6368", wraplength=520, justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
        self.scripts = load_scripts()
        self._script_idx: int | None = None

        left = ttk.Frame(f)
        left.grid(row=1, column=0, sticky="nsw", pady=(10, 0), padx=(0, 12))
        self.script_list = tk.Listbox(left, height=14, width=22, exportselection=False)
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
        self.script_text = tk.Text(right, height=9, width=52, wrap="word", font=("Segoe UI", 10), undo=True)
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
        ttk.Label(right, text="Steps it will follow", font=("Segoe UI", 9, "bold")).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.steps_preview = tk.Text(right, height=7, width=52, wrap="word", font=("Segoe UI", 9), foreground="#3c4043",
                                     background="#f8f9fa", relief="flat", state="disabled")
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
        ttk.Label(f, text="The status pill above the taskbar. Changes preview live.", foreground="#5f6368").grid(
            row=0, column=0, columnspan=4, sticky="w")
        self.style = {"bg": s.overlay_bg, "fg": s.overlay_fg, "dots": {**DEFAULT_DOTS, **s.overlay_dots}}
        self.opacity = tk.IntVar(value=s.overlay_opacity)
        self.swatches: dict[str, tk.Button] = {}

        _row(f, 1, "Background")
        self._swatch(f, "bg", 1, 1, "Background", "Jev ready · hold Right Ctrl to speak")
        _row(f, 2, "Text")
        self._swatch(f, "fg", 2, 1, "Text", "Jev ready · hold Right Ctrl to speak")
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
        box.bind("<<ComboboxSelected>>", lambda _: self._preview("idle", "Jev ready · hold Right Ctrl to speak"))
        ttk.Button(pos, text="Drag…", command=self._drag_indicator).pack(side="left", padx=(6, 0))
        ttk.Button(pos, text="Reset position", command=self._reset_position).pack(side="left", padx=(6, 0))

        ttk.Label(f, text="Dot colours", font=("Segoe UI", 9, "bold")).grid(row=5, column=0, columnspan=4, sticky="w", pady=(14, 0))
        for i, (state, (label, sample)) in enumerate(_DOT_LABELS.items()):
            row, col = 6 + i // 2, (i % 2) * 2
            ttk.Label(f, text=label).grid(row=row, column=col, sticky="w", pady=(8, 0), padx=(0, 12))
            self._swatch(f, state, row, col + 1, f"{label} dot", sample, dot=True)
        ttk.Button(f, text="Reset to defaults", command=self._reset_style).grid(row=10, column=0, columnspan=4, sticky="w", pady=(14, 0))
        self._opacity_moved(preview=False)

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
            self._preview("idle", "Jev ready · hold Right Ctrl to speak")

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
        self._preview("idle", "Jev ready · hold Right Ctrl to speak")

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
                       overlay_position=self._position_id(), overlay_x=self.custom_xy[0], overlay_y=self.custom_xy[1])

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
            lines = []
            if not key:
                lines.append("Jev: enter a TypeSafe API key.")
            else:
                try:
                    p = Decider(key, model).ping()
                    lines.append(f"Jev: connected to {model} (test answer {p:.2f}, expect ≈1).")
                except TypeSafeAuthenticationError:
                    lines.append("Jev: key rejected (401).")
                except TypeSafeAPIError as e:
                    lines.append(f"Jev: API error {e.status}.")
                except TypeSafeError as e:
                    lines.append(f"Jev: could not connect ({e}).")
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
        hotkey = self.hotkey.get().strip().lower() or search.DEFAULT_HOTKEY
        if self.powertoys.get() and not search.valid_hotkey(hotkey):
            self.status.set(f"“{hotkey}” isn't a shortcut I recognise. Try Record, or e.g. “left alt+space”.")
            return
        s = self.app.settings
        s.model = self.model.get().strip() or "jev-latest"
        s.dry_run = self.dry_run.get()
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
        self.win.destroy()
        self.app.settings_dialog = None
