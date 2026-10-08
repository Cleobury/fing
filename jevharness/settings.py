"""User settings (JSON in %APPDATA%) and API keys (Windows Credential Manager)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields

import keyring

from .brand import DEFAULT_NAME

# The folder and Credential Manager entries keep the app's old name, so settings and keys carry over.
APP_NAME = "JevHarness"
DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
LOG_DIR = os.path.join(DATA_DIR, "logs")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

_KEYRING_SERVICE = APP_NAME
_KEYRING_USER = "typesafe_api_key"

log = logging.getLogger(__name__)


@dataclass
class Settings:
    # What the assistant is called: on the indicator, in Settings, and in the wake phrase ("Hey Fing").
    assistant_name: str = DEFAULT_NAME
    dry_run: bool = False
    # YOLO mode: decide everything without asking (top option, AI's choice, no "keep going?" check-ins).
    yolo: bool = False
    yolo_allow_irreversible: bool = False  # in YOLO mode, also allow deleting, buying, sending, signing out...
    model: str = "jev-latest"  # the TypeSafe classifier model
    # Minimum probability of the classifier's chosen action / on-screen target before we act.
    min_action_prob: float = 0.5
    min_target_prob: float = 0.4
    # Also read the active window's named controls (icon buttons, tabs, fields) from Windows UI Automation.
    read_controls: bool = True
    # Read every monitor at once, not just the one the active window is on.
    all_screens: bool = True
    whisper_model: str = "large-v3-turbo"
    language: str = "en"
    mic_device: str = ""  # input device name; blank = the Windows default microphone
    # Hands-free: say a phrase instead of holding Right Ctrl (see listen.py).
    wake_enabled: bool = False
    wake_phrase: str = "hey fing"
    wake_sensitivity: float = 0.5  # 0 = strict (fewer false triggers) … 1 = loose (catches more)
    # Continuous conversation: open the mic by itself when Jev asks a question (on the phone too, if it asked there).
    auto_listen_answers: bool = False
    mic_sounds: bool = True  # chime when the mic opens and closes
    # Phone remote: a hold-to-talk page served on the local network (see remote.py).
    remote_enabled: bool = False
    remote_port: int = 8765
    remote_pin: str = ""
    # AI planner used when Jev is confused: "off", "openrouter" or "ollama".
    llm_provider: str = "off"
    llm_model: str = "google/gemini-3.8-flash"
    llm_base_url: str = ""  # blank = the provider's default
    llm_mode: str = "confused"  # "confused": only when Jev struggles; "always": rewrite every command first
    llm_screenshot: bool = True
    llm_keep_alive: bool = False  # Ollama: keep the model loaded in memory instead of unloading after 5 minutes idle
    # Model that finds icons/images on the screenshot (needs to point accurately); blank = the planner model.
    vision_model: str = ""
    # Search the PC with PowerToys (Command Palette / PowerToys Run) via this hotkey instead of the Start menu.
    powertoys_search: bool = False
    search_hotkey: str = "left alt+space"
    # The indicator above the taskbar.
    overlay_bg: str = "#202124"
    overlay_fg: str = "#f1f3f4"
    overlay_opacity: int = 100  # percent
    overlay_dots: dict = field(default_factory=dict)  # state -> colour, overriding the defaults
    overlay_position: str = "bottom-centre"  # a preset on the main screen, or "custom" (dragged there)
    overlay_x: int = 0  # custom position: the pill's centre, in screen pixels
    overlay_y: int = 0
    overlay_fx: bool = True  # the hand animations: tapping what it clicks, waving, confetti when done

    @property
    def name(self) -> str:
        return self.assistant_name.strip() or DEFAULT_NAME

    @classmethod
    def load(cls) -> Settings:
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.exception("Could not read settings; using defaults")
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)


def get_api_key(name: str = "typesafe") -> str | None:
    """The <NAME>_API_KEY environment variable wins; otherwise the key saved in Credential Manager."""
    if key := os.environ.get(f"{name.upper()}_API_KEY"):
        return key
    try:
        return keyring.get_password(_KEYRING_SERVICE, _keyring_user(name))
    except keyring.errors.KeyringError:
        log.exception("Could not read %s API key from Credential Manager", name)
        return None


def set_api_key(key: str, name: str = "typesafe") -> None:
    if key:
        keyring.set_password(_KEYRING_SERVICE, _keyring_user(name), key)
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, _keyring_user(name))
    except keyring.errors.PasswordDeleteError:
        pass


def _keyring_user(name: str) -> str:
    return _KEYRING_USER if name == "typesafe" else f"{name}_api_key"
