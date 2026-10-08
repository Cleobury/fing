"""Searching the PC: PowerToys (Command Palette or PowerToys Run) via its hotkey, else the Start menu."""

import json
import logging
import os
import time

import keyboard

from .desktop import foreground_window

log = logging.getLogger(__name__)

DEFAULT_HOTKEY = "left alt+space"

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_CMDPAL_SETTINGS = os.path.join(_LOCAL, "Packages", "Microsoft.CommandPalette_8wekyb3d8bbwe", "LocalState", "settings.json")
_RUN_SETTINGS = os.path.join(_LOCAL, "Microsoft", "PowerToys", "PowerToys Run", "settings.json")
# Virtual-key codes PowerToys stores → `keyboard` key names (letters and digits handled below).
_VK_NAMES = {32: "space", 13: "enter", 9: "tab", 27: "esc", 192: "`", 186: ";", 188: ",", 190: ".", 191: "/"}


def _combo(hk: dict) -> str | None:
    code = hk.get("code") or 0
    if 65 <= code <= 90 or 48 <= code <= 57:
        key = chr(code).lower()
    elif 112 <= code <= 135:
        key = f"f{code - 111}"
    else:
        key = _VK_NAMES.get(code)
    if not key:
        return None
    mods = [m for m, on in (("windows", hk.get("win")), ("ctrl", hk.get("ctrl")), ("alt", hk.get("alt")),
                            ("shift", hk.get("shift"))) if on]
    return "+".join([*mods, key])


def detect_powertoys_hotkey() -> str | None:
    """The hotkey configured in Command Palette, else PowerToys Run; None if neither is set up."""
    for path, extract in (
        (_CMDPAL_SETTINGS, lambda j: j.get("Hotkey")),
        (_RUN_SETTINGS, lambda j: (j.get("properties") or {}).get("open_powerlauncher")),
    ):
        try:
            with open(path, encoding="utf-8-sig") as f:
                hk = extract(json.load(f))
        except (OSError, ValueError):
            continue
        if isinstance(hk, dict) and (combo := _combo(hk)):
            return combo
    return None


def valid_hotkey(combo: str) -> bool:
    try:
        keyboard.parse_hotkey(combo)
        return True
    except ValueError:
        return False


def open_and_type(query: str, hotkey: str | None) -> None:
    """Open the search box (PowerToys via `hotkey`, or the Start menu if None) and type `query`.

    Doesn't press Enter: the caller reads the results and picks the right one, since the top result is
    often something like "search the web".
    """
    before = foreground_window()
    keyboard.send(hotkey or "windows")
    deadline = time.monotonic() + 1.5
    while foreground_window() == before and time.monotonic() < deadline:
        time.sleep(0.03)
    if foreground_window() == before:
        log.warning("Search box didn't take focus after %s", hotkey or "the Windows key")
    time.sleep(0.15)
    keyboard.send("ctrl+a")  # replace anything already in the box
    keyboard.write(query, delay=0.005)
