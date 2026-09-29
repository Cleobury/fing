"""System-tray icon and its right-click menu."""

import os
import threading

import pystray
from PIL import Image, ImageDraw, ImageFont

from .settings import LOG_DIR


def _icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill="#202124", outline=color, width=5)
    try:
        font = ImageFont.truetype("segoeuib.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    d.text((32, 33), "J", fill="#f1f3f4", font=font, anchor="mm")
    return img


ICONS = {"idle": "#80868b", "listening": "#ff3b30", "thinking": "#fbbc04", "error": "#ea4335"}


class Tray:
    def __init__(self, app):
        self.app = app
        self._images = {k: _icon(v) for k, v in ICONS.items()}
        self.icon = pystray.Icon(
            "JevHarness",
            self._images["idle"],
            "Jev Harness",
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: app.status_line(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…", lambda: app.ui(app.open_settings), default=True),
                pystray.MenuItem("Dry run (show, don't act)", lambda: app.ui(app.toggle_dry_run), checked=lambda _: app.settings.dry_run),
                pystray.MenuItem("Move indicator", lambda: app.ui(app.move_overlay)),
                pystray.MenuItem("Reset indicator position", lambda: app.ui(app.reset_overlay_position)),
                pystray.MenuItem("Open logs folder", lambda: os.startfile(LOG_DIR)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda: app.ui(app.quit)),
            ),
        )
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def set_state(self, state: str) -> None:
        self.icon.icon = self._images.get(state, self._images["idle"])

    def refresh(self) -> None:
        self.icon.update_menu()

    def stop(self) -> None:
        self.icon.stop()
