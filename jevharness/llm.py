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
- search_pc: search the computer (files, folders, settings, programs not in the app list) with the system search
  box. text = what to search for. It types the query and shows results; add a click step for the right result.

Rules:
- Only list steps still needed: skip anything in steps_done. Use as few steps as possible.
- Prefer what is on screen now: switch tabs or views (e.g. Store vs Library) when the needed control is elsewhere.
- Use `problem` to understand what went wrong last time and route around it.
- Never ask the user where things are on screen or how an app is laid out: work that out yourself from screen_elements
  (and the screenshot, if given), or give your best-guess steps; the executor will look around and report back if a
  label is missing.
- Only ask a `question` (with no steps) when you can't tell what the user *wants*, or when the request would delete data,
  spend money, send a message or change security settings and they haven't clearly asked for exactly that.
- Whenever you ask a `question`, also give 2-4 short likely answers in `options` (they're shown numbered, so the
  user can reply with a number). Otherwise `options` is empty.
- If the request already looks done, return no steps and no question.
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
                                                          "press_key", "scroll", "search_pc"]},
                    "target": {"type": ["string", "null"]},
                    "text": {"type": ["string", "null"]},
                    "key": {"type": ["string", "null"]},
                    "direction": {"type": ["string", "null"]},
                    "submit": {"type": "boolean"},
                },
                "required": ["action", "target", "text", "key", "direction", "submit"],
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
  that part of the screen more closely, to find small text or controls OCR missed (often icon bars and corners).

Rules:
- Think about where this app usually keeps what the step needs, and aim there.
- Don't repeat anything in `tried`; learn from their results.
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
    """OCR elements for the AI: id, text and position (a region name plus the centre in screen pixels)."""
    mon = screen.monitor
    out = []
    for e in screen.elements[:limit]:
        cx, cy = e.center
        h = ("left", "centre", "right")[min(2, max(0, 3 * (cx - mon["left"]) // mon["width"]))]
        v = ("top", "middle", "bottom")[min(2, max(0, 3 * (cy - mon["top"]) // mon["height"]))]
        out.append({"id": e.id, "text": e.text[:80], "where": f"{v} {h}", "x": cx - mon["left"], "y": cy - mon["top"]})
    return out


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
        case "search_pc" if text or target:
            return f'search the PC for "{text or target}"'
    return None


def _screenshot_data_url(screen: Screen, max_width: int = 1600) -> str | None:
    if screen.shot is None:
        return None
    from PIL import Image

    img = Image.frombytes("RGB", screen.shot.size, screen.shot.rgb)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


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
             answers: list[tuple[str, str]] = ()) -> LlmPlan:
        context = {
            "request": request,
            "steps_done": steps_done,
            "problem": problem,
            "active_window": screen.window_title,
            "screen_elements": describe_elements(screen),
            "user_answers": [{"question": q, "answer": a} for q, a in answers],
        }
        content: list[dict] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if self.send_screenshot and (url := _screenshot_data_url(screen)):
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
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

    def explore(self, step: str, request: str, done: list[str], screen: Screen, tried: list[dict]) -> tuple[str, list[dict]]:
        """Suggest ways to explore the current screen for where `step` can be done. Returns (thinking, probes)."""
        context = {
            "step": step,
            "full_request": request,
            "steps_done": done,
            "active_window": screen.window_title,
            "screen_size": [screen.monitor["width"], screen.monitor["height"]],
            "screen_elements": describe_elements(screen),
            "tried": tried,
        }
        content: list[dict] = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
        if self.send_screenshot and (url := _screenshot_data_url(screen)):
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": EXPLORE_SYSTEM}, {"role": "user", "content": content}],
            "temperature": 0.3,
            "response_format": {"type": "json_schema", "json_schema": {"name": "explore", "strict": True, "schema": EXPLORE_SCHEMA}},
        }
        if self.provider == "openrouter":
            body["provider"] = {"require_parameters": True}
        data = self._chat(body, EXPLORE_SCHEMA)
        probes = [p for p in data.get("probes") or [] if isinstance(p, dict) and p.get("action")][:4]
        return data.get("thinking") or "", probes

    def _chat(self, body: dict, schema: dict = SCHEMA) -> dict:
        if self.keep_alive:
            return self._ollama_chat(body, schema)
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
        r.raise_for_status()
        return _parse_json(r.json()["choices"][0]["message"]["content"])

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
        return sorted(m["id"] for m in usable)
