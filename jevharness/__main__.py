"""Entry point: `pythonw -m jevharness` (no console) or `python -m jevharness` (console logging)."""

import ctypes
import logging
import os
import sys


def main() -> None:
    if sys.platform != "win32":
        sys.exit("Jev Harness runs on Windows 10/11 only (it uses Windows OCR, Win32 input and Credential Manager).")

    # Physical-pixel coordinates everywhere, so OCR boxes, Tk windows and mouse clicks line up on scaled displays.
    if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per-monitor v2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)

    # Our own taskbar identity, so the Settings window shows the app's icon rather than grouping under pythonw.exe.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JevHarness.App")

    # One instance only: two keyboard hooks would both react to Right Ctrl.
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JevHarnessSingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(None, "Jev Harness is already running (see the tray).", "Jev Harness", 0x40)
        return

    from .settings import DATA_DIR, LOG_DIR

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "app.log")
    if sys.stdout is None:  # pythonw: no console, and libraries that print would crash
        sys.stdout = sys.stderr = open(log_path, "a", encoding="utf-8", buffering=1)
    handlers = [logging.FileHandler(log_path, encoding="utf-8")]
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    from .stt import add_cuda_dll_dirs

    add_cuda_dll_dirs()

    from .app import App

    logging.getLogger(__name__).info("Starting; data in %s", DATA_DIR)
    App().run()


if __name__ == "__main__":
    main()
