"""Generative-AI planner for when Jev is confused, via any OpenAI-compatible chat API (OpenRouter, Ollama).

The model never drives the mouse. It rewrites the user's spoken request into short, literal commands
("click "LIBRARY"", "type "Witcher 3" into "Search"") that the normal Jev step loop then grounds
against the real screen, one at a time.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .perception import Screen

log = logging.getLogger(__name__)

PROVIDERS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "google/gemini-3.8-flash", "needs_key": True},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "", "needs_key": False},
}
# OpenRouter's Auto Router: it picks a model per request (standard price of whichever model it picks, no extra fee).
OPENROUTER_AUTO = "openrouter/auto"

SYSTEM = """You help a voice-controlled computer assistant. A fast executor carries out one simple step at a time: it reads the
text visible on screen (OCR) and matches each step to it. It gets confused by casual speech, implied steps, or requests
that need a different page first. Your job: rewrite the user's request into the simple steps still needed.

Actions:
- open_app: open or switch to an installed app. target = app name ("Steam").
- click / double_click / right_click: target = the exact visible text of the thing to click, copied from screen_elements
  when it is visible. If it will only appear after earlier steps, give its most likely label.
- type: text = exactly what to type; target = the text box's visible label or placeholder if one needs clicking first
  (else null); submit = true to press Enter afterwards (searches, URLs, sending a chat message).
- press_key: key = one key or shortcut, e.g. "enter", "escape", "ctrl+l", "alt+left".
- scroll: direction = "up" or "down".
- check: text = a condition to verify on screen (for scripts and tests); wait: text = seconds to pause.
- drag: press on `target` (its visible text) and drop it on `destination` (another element's visible text), or
  `direction` "left"/"right"/"up"/"down" for a short move (a slider, a window).
- search_pc: search the computer (files, folders, settings, programs not in the app list) with the system search
  box. text = what to search for. It types the query and shows results; add a click step for the right result.

Rules:
- Only list steps still needed: skip anything in steps_done. Use as few steps as possible.
- Prefer what is on screen now: switch tabs or views (e.g. Store vs Library) when the needed control is elsewhere.
- With several screens (`screens`), screen_elements and the screenshot cover all of them and each element's `where`
  names its screen, so "on my left screen" means elements there.
- Use `problem` to understand what went wrong last time and route around it.
- Never ask the user where things are on screen or how an app is laid out: work that out yourself from screen_elements
  (and the screenshot, if given), or give your best-guess steps; the executor will look around and report back if a
  label is missing.
- Only ask a `question` (with no steps) when you can't tell what the user *wants*, or when the request would delete data,
  spend money, send a message or change security settings and they haven't clearly asked for exactly that.
- Whenever you ask a `question`, also give 2-4 short likely answers in `options` (they're shown numbered, so the
  user can reply with a number). Otherwise `options` is empty.
- If the request already looks done, return no steps and no question.
- `history` is everything done so far for this request, with what changed on screen and whether it worked. Build
  on what worked; don't repeat actions that failed or changed nothing; try a different route instead.
- `understanding`: one short sentence restating the goal."""

SCHEMA = {
    "type": "object",
    "properties": {
        "understanding": {"type": "string"},
        "question": {"type": ["string", "null"]},
        "options": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["open_app", "click", "double_click", "right_click", "type",
                                                          "press_key", "scroll", "search_pc", "drag", "check", "wait"]},
                    "target": {"type": ["string", "null"]},
                    "text": {"type": ["string", "null"]},
                    "key": {"type": ["string", "null"]},
                    "direction": {"type": ["string", "null"]},
                    "destination": {"type": ["string", "null"]},
                    "submit": {"type": "boolean"},
                },
                "required": ["action", "target", "text", "key", "direction", "destination", "submit"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["understanding", "question", "options", "steps"],
    "additionalProperties": False,
}


EXPLORE_SYSTEM = """You help a voice-controlled computer assistant find its way around an app. It sees the screen only
through OCR: the text elements in screen_elements, each with an id and where it is (and a screenshot, if given). It
can't carry out `step` on the current screen: what it needs isn't visible, or it can't recognise it. Suggest up to 4
probes to explore, most promising first. The assistant tries them one at a time and re-reads the screen after each.

Probes:
- click / double_click / right_click: element = the id of a visible element (e.g. "e12"). Good: tabs, sidebar and
  menu items, "More", "...", "Show all", section headers that expand, a profile or app-menu button.
- scroll: direction "up" or "down", to reveal more of the current page or list.
- press_key: key, e.g. "escape" (close a popup or menu), "alt+left" (go back), "ctrl+f" (find), "tab", "f10"
  (menu bar), "alt+space" (window menu).
- zoom: region = one of top-left, top-right, bottom-left, bottom-right, top, bottom, left, right, centre. Re-reads
  that part of the active window's screen more closely, to find small text or controls OCR missed (often icon bars
  and corners).

Rules:
- Think about where this app usually keeps what the step needs, and aim there.
- Don't repeat anything in `tried`; learn from their results. `history` is the whole run so far (actions, whether
  they worked, earlier plans): use it too.
- Never click anything that deletes, removes, uninstalls, buys, pays, sends, posts, signs out, or changes security
  or privacy settings.
- `thinking`: one short sentence on where you expect to find it."""

EXPLORE_SCHEMA = {
    "type": "object",
    "properties": {
        "thinking": {"type": "string"},
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["click", "double_click", "right_click", "scroll", "press_key", "zoom"]},
                    "element": {"type": ["string", "null"]},
                    "key": {"type": ["string", "null"]},
                    "direction": {"type": ["string", "null"]},
                    "region": {"type": ["string", "null"]},
                    "reason": {"type": "string"},
                },
                "required": ["action", "element", "key", "direction", "region", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["thinking", "probes"],
    "additionalProperties": False,
}


def describe_elements(screen: Screen, limit: int = 250) -> list[dict]:
    """Screen elements for the AI: id, text, the control kind if UI Automation named it, and position (a region
    name, with which screen when there are several, plus the centre in pixels from the screenshot's top-left)."""
    whole = screen.monitor
    out = []
    for e in screen.elements[:limit]:
        cx, cy = e.center
        mon = screen.monitor_of(e)
        h = ("left", "centre", "right")[min(2, max(0, 3 * (cx - mon["left"]) // mon["width"]))]
        v = ("top", "middle", "bottom")[min(2, max(0, 3 * (cy - mon["top"]) // mon["height"]))]
        where = f"{v} {h}" + (f" of the {name}" if (name := screen.screen_name(mon)) else "")
        out.append({"id": e.id, "text": e.text[:80], **({"kind": e.kind} if e.kind else {}), "where": where,
                    "x": cx - whole["left"], "y": cy - whole["top"]})
    return out


def describe_screens(screen: Screen) -> list[dict]:
    """The user's monitors, when there are several: name and area in screenshot pixels."""
    whole = screen.monitor
    return [{"name": screen.screen_name(m), "x": m["left"] - whole["left"], "y": m["top"] - whole["top"],
             "width": m["width"], "height": m["height"]} for m in screen.monitors] if len(screen.monitors) > 1 else []


LOCATE_SYSTEM = """You look at a screenshot for a voice-controlled computer assistant. It reads the screen with OCR, so it
can't see icons, images, colours, or buttons without text. Find the things on screen that `step` could be referring
to (or that would help carry it out) which are NOT already in ocr_text: icons, image thumbnails, coloured or
shape-only buttons, toggles, avatars, logos. List at most 5, most likely first. Skip anything whose text is already
in ocr_text.

For each: `label` = a short description of what it is and looks like, e.g. "settings gear icon", "red record
button", "thumbnail of a cat". `box` = [x0, y0, x1, y1], its bounding box in pixels of the image you were given
(`image_size` is its width and height). Keep boxes tight around the item.
If nothing fits, return an empty list."""

LOCATE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "box": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["label", "box"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


SCRIPT_SYSTEM = """You help a voice-controlled computer assistant run a saved script: a list of tasks written in
plain language by the user, e.g. for testing an app or a routine they repeat. Break it into the simple steps the
assistant's executor carries out one at a time (it reads the screen with OCR and matches each step to it).

Actions: the same as ever (open_app, click, double_click, right_click, type, press_key, scroll, search_pc, drag),
plus:
- check: text = a condition to verify on screen at that point, e.g. "the title bar says Untitled - Notepad". Use it
  wherever the script says check, verify, make sure, expect or assert, and for the expected outcome of a test.
- wait: text = seconds to pause, where the script asks to wait.

Rules:
- Keep the script's order and meaning exactly. One simple action per step; split compound lines.
- Don't add anything the script doesn't ask for or clearly imply (e.g. opening the app it's about).
- Use the exact labels the script gives for buttons and fields.
- Never ask a question: the script must run unattended. `question` is always null; `options` is empty.
- `understanding`: one short sentence saying what the script does."""


@dataclass
class LlmPlan:
    understanding: str
    steps: list[str]  # plain commands for the Jev step loop
    question: str | None
    options: list[str]  # likely answers to `question`, shown numbered
    raw: dict


def to_command(s: dict) -> str | None:
    """Turn one structured step into the literal phrasing Jev handles best."""
    target, text = (s.get("target") or "").strip(), (s.get("text") or "").strip()
    match s.get("action"):
        case "open_app" if target:
            return f"open {target}"
        case "click" | "double_click" | "right_click" as a if target:
            return f'{a.replace("_", "-")} "{target}"'
        case "type" if text:
            return f'type "{text}"' + (f' into "{target}"' if target else "") + (" and press Enter" if s.get("submit") else "")
        case "press_key" if s.get("key"):
            return f"press {s['key']}"
        case "scroll":
            return f"scroll {s.get('direction') or 'down'}"
        case "drag" if target and (s.get("destination") or s.get("direction")):
            where = f'onto "{s["destination"]}"' if s.get("destination") else f"{s['direction']}"
            return f'drag "{target}" {where}'
        case "check" if text:
            return f"check: {text}"
        case "wait":
            return f"wait: {text or 2}"
        case "search_pc" if text or target:
            return f'search the PC for "{text or target}"'
    return None


SCREENSHOT_WIDTH = 1600  # screenshots are scaled down to this width per screen before sending (screens side by side)
SCREENSHOT_MAX_WIDTH = 3200  # and to no more than this in all


def _screenshot_data_url(screen: Screen) -> str | None:
    if screen.shot is None:
        return None
    from PIL import Image

    img = Image.frombytes("RGB", screen.shot.size, screen.shot.rgb)
    w, h = _sent_size(screen)
    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _sent_size(screen: Screen) -> tuple[int, int]:
    """Size of the screenshot as the model sees it (after _screenshot_data_url's scaling)."""
    w, h = screen.shot.size
    across = len({m["left"] for m in screen.monitors})  # screens side by side
    max_w = min(SCREENSHOT_WIDTH * across, SCREENSHOT_MAX_WIDTH)
    return (w, h) if w <= max_w else (max_w, round(h * max_w / w))


def _parse_json(content: str) -> dict:
    """Models without structured-output support sometimes wrap JSON in prose or code fences."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


class Planner:
    def __init__(self, provider: str, model: str, api_key: str | None = None, base_url: str | None = None,
                 send_screenshot: bool = True, timeout_s: float = 60, keep_alive: bool = False):
        self.provider = provider
        self.model = model
        self.base_url = (base_url or PROVIDERS[provider]["base_url"]).rstrip("/")
        self.send_screenshot = send_screenshot
        # Ollama only: keep the model loaded indefinitely. Its OpenAI-compatible API can't do this (and resets the
        # unload timer to the default on every call), so these requests go through Ollama's own /api/chat instead.
        self.keep_alive = keep_alive and provider == "ollama"
        self.autonomous = False  # YOLO mode: the user can't be asked anything
        self.allow_irreversible = False  # YOLO mode with irreversible actions allowed
        self._ollama_root = self.base_url.removesuffix("/v1")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider == "openrouter":
            headers["X-Title"] = "Jev Harness"
        self.http = httpx.Client(headers=headers, timeout=timeout_s)

    @property
    def name(self) -> str:
        return self.model.split("/")[-1]

    def plan(self, request: str, steps_done: list[str], screen: Screen, problem: str | None,
             answers: list[tuple[str, str]] = (), history: list[str] = ()) -> LlmPlan:
        context = {
            "request": request,
            "steps_done": steps_done,
            "history": list(history),
            "problem": problem,
            "active_window": screen.window_title,
            **({"screens": screens} if (screens := describe_screens(screen)) else {}),
            "screen_elements": describe_elements(screen),
            "user_answers": [{"question": q, "answer": a} for q, a in answers],
        }
        content: list[dict] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if self.send_screenshot and (url := _screenshot_data_url(screen)):
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM + self._autonomy_rules()}, {"role": "user", "content": content}],
            "temperature": 0.2,
            "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "strict": True, "schema": SCHEMA}},
        }
        if self.provider == "openrouter":
            body["provider"] = {"require_parameters": True}  # only route to endpoints that honour the schema
        data = self._chat(body)
        steps = [c for c in (to_command(s) for s in data.get("steps") or []) if c]
        options = [o.strip() for o in data.get("options") or [] if isinstance(o, str) and o.strip()][:4]
        return LlmPlan(data.get("understanding") or "", steps, data.get("question") or None, options, data)

    # ---- Ollama model memory ----

    @property
    def ollama_id(self) -> tuple[str, str] | None:
        """(server, model) for a local Ollama model, else None."""
        return (self._ollama_root, self.model) if self.provider == "ollama" and self.model else None

    def load(self) -> None:
        """Load the model into memory now, so the next command doesn't wait for it. Pinned indefinitely with
        keep_alive, otherwise under Ollama's default (unloaded after 5 minutes idle)."""
        body: dict = {"model": self.model}
        if self.keep_alive:
            body["keep_alive"] = -1
        r = self.http.post(f"{self._ollama_root}/api/generate", json=body, timeout=300)
        if r.status_code >= 400:
            try:
                reason = r.json().get("error") or r.text
            except ValueError:
                reason = r.text
            raise RuntimeError(reason)  # e.g. '"all-minilm:latest" does not support generate'

    def ollama_capabilities(self, model: str) -> set[str]:
        """What a local model can do, e.g. {"completion", "vision", "tools"} or {"embedding"}."""
        r = self.http.post(f"{self._ollama_root}/api/show", json={"model": model})
        r.raise_for_status()
        return set(r.json().get("capabilities") or [])

    @staticmethod
    def running_ollama_models(root: str) -> set[str]:
        r = httpx.get(f"{root}/api/ps", timeout=10)
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}

    @staticmethod
    def unload_ollama_model(root: str, model: str) -> bool:
        """Free a model's memory now. Returns False if it wasn't loaded (nothing to do)."""
        if model not in Planner.running_ollama_models(root):
            return False
        httpx.post(f"{root}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60).raise_for_status()
        return True

    def ping(self) -> None:
        """A tiny request to check the model answers. For Ollama it goes through the native API, so testing a
        pinned model doesn't reset it to the 5-minute default."""
        if self.provider == "ollama":
            body = {"model": self.model, "stream": False, "messages": [{"role": "user", "content": "Reply with the word ok."}],
                    "options": {"num_predict": 5}}
            if self.keep_alive:
                body["keep_alive"] = -1
            self.http.post(f"{self._ollama_root}/api/chat", json=body, timeout=300).raise_for_status()
            return
        self.http.post(f"{self.base_url}/chat/completions", json={
            "model": self.model, "max_tokens": 5, "messages": [{"role": "user", "content": "Reply with the word ok."}],
        }).raise_for_status()

    def _autonomy_rules(self) -> str:
        if not self.autonomous:
            return ""
        rules = ("\n\nAUTONOMOUS MODE (overrides the rules above about asking): the user isn't available to answer. Never "
                 "ask a `question`; when unsure, choose the most likely meaning and give steps.")
        if self.allow_irreversible:
            return rules + " The user has allowed irreversible actions (deleting, buying, sending, signing out) without confirmation."
        return rules + (" Never plan steps that delete data, spend money, send or post messages, sign out or change security "
                        "settings; if the request needs that, return no steps.")

    def _explore_permission(self) -> str:
        if self.allow_irreversible:
            return ("\n\nThe user has allowed irreversible actions in this mode: you may click such controls when the step "
                    "clearly needs them.")
        return ""

    def explore(self, step: str, request: str, done: list[str], screen: Screen, tried: list[dict],
                history: list[str] = ()) -> tuple[str, list[dict]]:
        """Suggest ways to explore the current screen for where `step` can be done. Returns (thinking, probes)."""
        context = {
            "step": step,
            "full_request": request,
            "steps_done": done,
            "history": list(history),
            "active_window": screen.window_title,
            "screen_size": [screen.monitor["width"], screen.monitor["height"]],
            **({"screens": screens} if (screens := describe_screens(screen)) else {}),
            "screen_elements": describe_elements(screen),
            "tried": tried,
        }
        content: list[dict] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if self.send_screenshot and (url := _screenshot_data_url(screen)):
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": EXPLORE_SYSTEM + self._explore_permission()}, {"role": "user", "content": content}],
            "temperature": 0.3,
            "response_format": {"type": "json_schema", "json_schema": {"name": "explore", "strict": True, "schema": EXPLORE_SCHEMA}},
        }
        if self.provider == "openrouter":
            body["provider"] = {"require_parameters": True}
        data = self._chat(body, EXPLORE_SCHEMA)
        probes = [p for p in data.get("probes") or [] if isinstance(p, dict) and p.get("action")][:4]
        return data.get("thinking") or "", probes

    def locate(self, step: str, screen: Screen) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Ask the vision model where the things `step` refers to are, when OCR can't see them (icons, images,
        colours). Returns (label, (left, top, right, bottom)) in screen pixels; [] if it has no screenshot to look at."""
        url = _screenshot_data_url(screen)
        if url is None:
            return []
        sent_w, sent_h = _sent_size(screen)
        context = {"step": step, "active_window": screen.window_title, "image_size": [sent_w, sent_h],
                   "ocr_text": [e.text[:60] for e in screen.elements][:200]}
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LOCATE_SYSTEM},
                {"role": "user", "content": [{"type": "text", "text": json.dumps(context, ensure_ascii=False)},
                                             {"type": "image_url", "image_url": {"url": url}}]},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_schema", "json_schema": {"name": "locate", "strict": True, "schema": LOCATE_SCHEMA}},
        }
        if self.provider == "openrouter":
            body["provider"] = {"require_parameters": True}
        data = self._chat(body, LOCATE_SCHEMA)
        mon = screen.monitor
        sx, sy = mon["width"] / sent_w, mon["height"] / sent_h  # image pixels → screen pixels
        found = []
        for item in data.get("items") or []:
            box, label = item.get("box"), (item.get("label") or "").strip()
            if not label or not isinstance(box, list) or len(box) != 4:
                continue
            x0, x1 = sorted(max(0, min(sent_w, int(v))) for v in (box[0], box[2]))
            y0, y1 = sorted(max(0, min(sent_h, int(v))) for v in (box[1], box[3]))
            if x1 - x0 < 2 or y1 - y0 < 2 or (x1 - x0) * (y1 - y0) > sent_w * sent_h / 4:  # a dot, or a quarter of the screen
                continue
            found.append((label, (mon["left"] + round(x0 * sx), mon["top"] + round(y0 * sy),
                                  mon["left"] + round(x1 * sx), mon["top"] + round(y1 * sy))))
        return found[:5]

    def pointing_check(self) -> tuple[int, int]:
        """How well this model points: find 3 shapes on a test image. Returns (hits, tries)."""
        from types import SimpleNamespace

        from PIL import Image, ImageDraw

        w, h = 1920, 1080
        img = Image.new("RGB", (w, h), "#f3f3f3")
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, w, 60), fill="#2b2b2b")
        targets = {"click the red circle": (450, 450, 550, 550), "press the blue play triangle": (1000, 425, 1130, 575),
                   "click the green square": (1500, 750, 1700, 900)}
        d.ellipse(targets["click the red circle"], fill="#e53935")
        d.polygon([(1000, 425), (1000, 575), (1130, 500)], fill="#1e88e5")
        d.rectangle(targets["click the green square"], fill="#43a047")
        from .perception import Screen  # only for its shape; WinRT was loaded long ago by the time this runs

        screen = Screen({"left": 0, "top": 0, "width": w, "height": h}, "Test", [], 0,
                        SimpleNamespace(size=(w, h), rgb=img.tobytes()))
        hits = 0
        for step, (x0, y0, x1, y1) in targets.items():
            found = self.locate(step, screen)
            if found:
                l, t, r, b = found[0][1]
                cx, cy = (l + r) / 2, (t + b) / 2
                hits += x0 - 20 <= cx <= x1 + 20 and y0 - 20 <= cy <= y1 + 20
        return hits, len(targets)

    def break_script(self, name: str, text: str) -> list[str]:
        """Break a saved script into simple steps (including "check: …" and "wait: …" steps)."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SCRIPT_SYSTEM},
                         {"role": "user", "content": json.dumps({"script_name": name, "script": text}, ensure_ascii=False)}],
            "temperature": 0.0,
            "max_tokens": 4096,  # a long script can break into many steps
            "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "strict": True, "schema": SCHEMA}},
        }
        if self.provider == "openrouter":
            body["provider"] = {"require_parameters": True}
        data = self._chat(body)
        return [c for c in (to_command(s) for s in data.get("steps") or []) if c]

    def _chat(self, body: dict, schema: dict = SCHEMA) -> dict:
        if self.keep_alive:
            return self._ollama_chat(body, schema)
        if self.provider == "openrouter":
            # Replies are small JSON. Without a cap OpenRouter reserves the model's whole output limit against the
            # balance (e.g. 65536 tokens for Sonnet), and a low balance then fails with 402.
            body.setdefault("max_tokens", 2048)
        r = self.http.post(f"{self.base_url}/chat/completions", json=body)
        if r.status_code == 400 and "response_format" in body:
            # Local/older models may reject JSON-schema mode: fall back to plain JSON mode, then prompt-only.
            log.info("Structured output rejected (%s); retrying in JSON mode", r.text[:200])
            body = {**body, "response_format": {"type": "json_object"}}
            body.pop("provider", None)
            r = self.http.post(f"{self.base_url}/chat/completions", json=body)
            if r.status_code == 400:
                body.pop("response_format")
                r = self.http.post(f"{self.base_url}/chat/completions", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}: {_api_error(r)}")
        reply = r.json()
        if self.model == OPENROUTER_AUTO:
            log.info("Auto Router picked %s", reply.get("model"))
        return _parse_json(reply["choices"][0]["message"]["content"])

    def _ollama_chat(self, body: dict, schema: dict = SCHEMA) -> dict:
        """The same request through Ollama's native /api/chat, which honours keep_alive."""
        messages = []
        for m in body["messages"]:
            if isinstance(m["content"], str):
                messages.append({"role": m["role"], "content": m["content"]})
                continue
            parts = m["content"]
            messages.append({
                "role": m["role"],
                "content": "\n".join(p["text"] for p in parts if p["type"] == "text"),
                "images": [p["image_url"]["url"].split(",", 1)[1] for p in parts if p["type"] == "image_url"],
            })
        r = self.http.post(f"{self._ollama_root}/api/chat", json={
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "keep_alive": -1,
            "options": {"temperature": body.get("temperature", 0.2)},
        })
        r.raise_for_status()
        return _parse_json(r.json()["message"]["content"])

    def list_vision_models(self) -> list[str]:
        """Models that accept images: for Ollama, those with the "vision" capability."""
        if self.provider == "ollama":
            return [m for m in self.list_models() if "vision" in self.ollama_capabilities(m)]
        r = self.http.get(f"{self.base_url}/models")
        r.raise_for_status()
        return sorted(m["id"] for m in r.json()["data"]
                      if "image" in ((m.get("architecture") or {}).get("input_modalities") or [])
                      and "structured_outputs" in (m.get("supported_parameters") or []))

    def list_models(self) -> list[str]:
        if self.provider == "ollama":
            # Native Ollama endpoint, not the /v1 compatibility layer. Only models that can generate text:
            # embedding models (e.g. all-minilm) can't plan.
            r = self.http.get(f"{self._ollama_root}/api/tags")
            r.raise_for_status()
            names = sorted(m["name"] for m in r.json().get("models", []))
            return [n for n in names if "completion" in self.ollama_capabilities(n)]
        r = self.http.get(f"{self.base_url}/models")
        r.raise_for_status()
        models = r.json()["data"]
        usable = [m for m in models if "structured_outputs" in (m.get("supported_parameters") or [])]
        # The Auto Router first, so "let OpenRouter choose" is always on offer (it may not list structured output itself).
        return [OPENROUTER_AUTO, *sorted(m["id"] for m in usable if m["id"] != OPENROUTER_AUTO)]


def _api_error(r: httpx.Response) -> str:
    """The reason an OpenAI-compatible API gave for rejecting a request, e.g. OpenRouter's {"error": {"message": …}}."""
    try:
        err = r.json().get("error")
    except ValueError:
        return r.text[:300]
    if isinstance(err, dict):
        raw = (err.get("metadata") or {}).get("raw")
        return str(err.get("message") or err) + (f" ({str(raw)[:200]})" if raw else "")
    return str(err or r.text[:300])
