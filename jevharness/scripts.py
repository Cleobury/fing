"""Saved scripts: a named list of tasks in plain language, run the same way every time (app testing, routines).

The AI planner breaks a script into simple steps once; the result is cached with the script (keyed on its text
and the model) so every run follows exactly the same steps until the script is edited. Without an AI planner each
line becomes a step. Two kinds of step are special: "check: <condition>" (verified against the screen by Jev and
reported as passed/failed) and "wait: <seconds>".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field, fields

from .settings import DATA_DIR, LOG_DIR

log = logging.getLogger(__name__)

SCRIPTS_PATH = os.path.join(DATA_DIR, "scripts.json")
REPORTS_DIR = os.path.join(LOG_DIR, "scripts")
CHECK = "check: "
WAIT = "wait: "


@dataclass
class Script:
    name: str
    text: str = ""
    unattended: bool = True  # decide everything itself while running (like YOLO mode), so a run isn't blocked
    stop_on_failure: bool = True  # stop at the first failed step or check (else record it and carry on)
    steps: list[str] = field(default_factory=list)  # the AI's breakdown, cached
    steps_key: str = ""  # what the cached breakdown was made from (text + model)


def load_scripts() -> list[Script]:
    try:
        with open(SCRIPTS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.exception("Could not read scripts")
        return []
    known = {f.name for f in fields(Script)}
    return [Script(**{k: v for k, v in s.items() if k in known}) for s in raw if isinstance(s, dict) and s.get("name")]


def save_scripts(scripts: list[Script]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCRIPTS_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(s) for s in scripts], f, indent=2, ensure_ascii=False)


def breakdown_key(text: str, model: str) -> str:
    return hashlib.sha1(f"{model}\n{text.strip()}".encode()).hexdigest()[:16]


_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|step\s+\d+[:.)]?)\s*", re.I)
_CHECK_WORDS = re.compile(r"^(?:check|verify|assert|expect|confirm|make sure)(?:\s+that)?[:\s]+", re.I)
_WAIT_WORDS = re.compile(r"^wait(?:\s+for)?[:\s]*(\d+(?:\.\d+)?)?\s*(?:s|sec|secs|seconds?)?\.?$", re.I)


def split_lines(text: str) -> list[str]:
    """A script as steps without an AI: one per line, numbering and bullets removed, checks and waits normalised."""
    steps = []
    for line in text.splitlines():
        line = _BULLET.sub("", line).strip()
        if not line or line.startswith("#"):
            continue
        if m := _WAIT_WORDS.match(line):
            steps.append(f"{WAIT}{m.group(1) or 2}")
        elif m := _CHECK_WORDS.match(line):
            steps.append(CHECK + line[m.end():].strip())
        else:
            steps.append(line)
    return steps


def check_of(step) -> str | None:
    """The condition, if `step` is a "check: …" step."""
    return step[len(CHECK):].strip() if isinstance(step, str) and step.lower().startswith(CHECK) else None


def wait_of(step) -> float | None:
    """Seconds to wait, if `step` is a "wait: …" step (capped at a minute)."""
    if not (isinstance(step, str) and step.lower().startswith(WAIT)):
        return None
    try:
        return max(0.0, min(60.0, float(step[len(WAIT):].strip() or 2)))
    except ValueError:
        return 2.0


def write_report(name: str, report: dict) -> str:
    """Save a run's report as JSON plus a readable text summary; returns the text file's path."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    safe = re.sub(r"[^\w\- ]", "_", name).strip() or "script"
    base = os.path.join(REPORTS_DIR, f"{safe} {report['started'].replace(':', '-')}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    lines = [f"Script: {name}", f"Started: {report['started']}   Took: {report['seconds']} s",
             f"Result: {report['summary']}", ""]
    for r in report["steps"]:
        mark = {"ok": "✓", "passed": "✓", "failed": "✗", "skipped": "–"}.get(r["status"], "?")
        lines.append(f"{mark} {r['n']}. {r['step']}")
        for a in r.get("actions", []):
            lines.append(f"      {a}")
        if r.get("note"):
            lines.append(f"      → {r['note']}")
    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return base + ".txt"
