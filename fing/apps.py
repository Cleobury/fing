"""Installed Start-menu apps (Get-StartApps), for "open <app>" commands."""

import difflib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

_JUNK = re.compile(r"uninstall|readme|read me|help|documentation|manual|website|release notes|license|changelog", re.I)


@dataclass(frozen=True)
class App:
    name: str
    app_id: str


def load_start_apps() -> list[App]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raw = json.loads(out.stdout or "[]")
    seen: dict[str, App] = {}
    for item in raw if isinstance(raw, list) else [raw]:
        name, app_id = (item.get("Name") or "").strip(), item.get("AppID")
        if name and app_id and not _JUNK.search(name):
            seen.setdefault(name.lower(), App(name, app_id))
    log.info("Found %d Start menu apps", len(seen))
    return sorted(seen.values(), key=lambda a: a.name.lower())


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def shortlist(command: str, apps: list[App], n: int = 60) -> list[App]:
    """The apps whose names look most like something said in the command."""
    words = _tokens(command)
    low = command.lower()

    def score(a: App) -> float:
        name = a.name.lower()
        overlap = len(words & _tokens(name)) / max(1, len(_tokens(name)))
        best_word = max((difflib.SequenceMatcher(None, w, name).ratio() for w in words), default=0)
        return overlap + best_word + (1.0 if name in low else 0)

    return sorted(apps, key=score, reverse=True)[:n]


def launch(app: App) -> None:
    os.startfile(f"shell:AppsFolder\\{app.app_id}")
