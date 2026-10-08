"""System-tray icon and its right-click menu."""

import os
import threading

import pystray
from . import autostart, brand
from .scripts import load_scripts
from .settings import LOG_DIR


# The logo; a coloured ring round it says what Fing is doing (none while idle).
ICONS = {"idle": None, "listening": "#ff3b30", "thinking": "#fbbc04", "error": "#ea4335"}


class Tray:
    def __init__(self, app):
        self.app = app
        self._images = {k: brand.logo(64, ring=v) for k, v in ICONS.items()}
        self.icon = pystray.Icon(
            "JevHarness",  # the id stays the same, so Windows keeps the tray icon where the user put it
            self._images["idle"],
            app.settings.name,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: app.status_line(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…", lambda: app.ui(app.open_settings), default=True),
                pystray.MenuItem("Dry run (show, don't act)", lambda: app.ui(app.toggle_dry_run), checked=lambda _: app.settings.dry_run),
                pystray.MenuItem("YOLO mode (decide everything itself)", lambda: app.ui(app.toggle_yolo),
                                 checked=lambda _: app.settings.yolo),
                pystray.MenuItem("Listen for wake word", lambda: app.ui(app.toggle_wake),
                                 checked=lambda _: app.settings.wake_enabled),
                pystray.MenuItem("Run script", pystray.Menu(lambda: self._script_items())),
                pystray.MenuItem("Start with Windows", lambda: app.ui(app.toggle_autostart),
                                 checked=lambda _: autostart.is_enabled()),
                pystray.MenuItem("Move indicator", lambda: app.ui(app.move_overlay)),
                pystray.MenuItem("Reset indicator position", lambda: app.ui(app.reset_overlay_position)),
                pystray.MenuItem("Open logs folder", lambda: os.startfile(LOG_DIR)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: app.ui(app.quit)),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def _script_items(self):
        """The Run script submenu, rebuilt each time it opens so it lists the scripts saved now."""
        def run(name: str):
            return lambda: self.app.ui(self.app.run_script, name)

        scripts = load_scripts()
        if not scripts:
            yield pystray.MenuItem("(no scripts yet: add them in Settings → Scripts)", None, enabled=False)
        for s in scripts:
            yield pystray.MenuItem(s.name, run(s.name))

    def set_state(self, state: str) -> None:
        self.icon.icon = self._images.get(state, self._images["idle"])

    def rename(self, name: str) -> None:
        self.icon.title = name

    def refresh(self) -> None:
        self.icon.update_menu()

    def stop(self) -> None:
        self.icon.stop()
