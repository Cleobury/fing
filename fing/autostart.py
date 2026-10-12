"""Start with Windows: a Fing shortcut in the user's Startup folder (the same one setup.ps1 -Startup makes).

The shortcut itself is the setting, so the tray menu, Settings and setup.ps1 always agree.
"""

import os
import subprocess
import sys

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(_PACKAGE_DIR)
_STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
SHORTCUT = os.path.join(_STARTUP, "Fing.lnk")
OLD_SHORTCUT = os.path.join(_STARTUP, "Jev Harness.lnk")  # made before the rename; still counts, replaced on enable


def _target() -> tuple[str, str, str, str]:
    """(program, arguments, working folder, icon) that start this copy of the app: Fing.exe when it's the packaged
    build, otherwise pythonw.exe next to the running interpreter (no console window at sign-in)."""
    if getattr(sys, "frozen", False):
        return sys.executable, "", os.path.dirname(sys.executable), sys.executable
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = candidate if os.path.exists(candidate) else sys.executable
    return exe, "-m fing", PROJECT_DIR, os.path.join(_PACKAGE_DIR, "icon.ico")


def is_enabled() -> bool:
    return os.path.exists(SHORTCUT) or os.path.exists(OLD_SHORTCUT)


def enable() -> None:
    """Create (or refresh) the Startup shortcut, pointing at this copy of the app."""
    def ps(s: str) -> str:
        return s.replace("'", "''")

    exe, args, cwd, icon = _target()
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
        "$s.TargetPath = '{exe}'; $s.Arguments = '{args}'; $s.WorkingDirectory = '{cwd}'; "
        "$s.IconLocation = '{icon}'; $s.Description = 'Fing: your desktop assistant'; $s.Save()"
    ).format(lnk=ps(SHORTCUT), exe=ps(exe), args=ps(args), cwd=ps(cwd), icon=ps(icon))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    _remove(OLD_SHORTCUT)


def disable() -> None:
    _remove(SHORTCUT)
    _remove(OLD_SHORTCUT)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def set_enabled(on: bool) -> None:
    (enable if on else disable)()
