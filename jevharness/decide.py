"""Turn a spoken command + OCR'd screen into one input action, using Jev.

Code builds every candidate (on-screen elements, keys, spans of the transcript to
type) and Jev only selects among them, so it never has to generate coordinates or text.
All questions go in one request and run in parallel; the policy in `plan()` decides
which answers apply and whether they are confident enough to act on.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from typesafe_sdk import Choice, Noul, TypeSafeClient

from .apps import App, shortlist

if TYPE_CHECKING:  # importing .perception loads WinRT, which must wait until Whisper is loaded
    from .perception import Element, Screen

MAX_TARGETS = 200  # Choice allows 255 options; leave headroom for long element text
MAX_TEXT_OPTIONS = 120

ACTIONS = {
    "click": "Left-click one on-screen element: press a button, open a link, pick a tab or menu item, or focus a field",
    "double_click": "Double-click an on-screen element, e.g. open a file, folder or desktop icon",
    "right_click": "Right-click an on-screen element to open its context menu",
    "type_text": "Type words the user dictated, optionally into an on-screen field they name",
    "press_key": "Press a keyboard key or shortcut (Enter, Escape, Tab, arrows, copy, paste, undo, save, find, new tab...) without clicking anything",
    "scroll": "Scroll the current view up or down",
    "open_app": "Open, launch, start or switch to an installed application by name (it does not need to be visible on screen)",
    "search_pc": (
        "Search the computer with the system search box to find or open a file, folder, setting or program that is "
        "not visible on screen and is not an installed application listed for opening"
    ),
    "none": "Not a computer command, unclear, or misheard",
}

# Option ids are `keyboard` library combos. Deliberately excludes destructive shortcuts like alt+f4.
KEYS = {
    "enter": "Enter / Return: confirm, submit or send",
    "escape": "Escape: cancel, close a dialog or menu",
    "tab": "Tab: move to the next field",
    "shift+tab": "Shift+Tab: move to the previous field",
    "space": "Space bar: toggle, play/pause",
    "backspace": "Backspace: delete the character before the cursor",
    "delete": "Delete key: delete the selection or character after the cursor",
    "up": "Up arrow", "down": "Down arrow", "left": "Left arrow", "right": "Right arrow",
    "page up": "Page Up", "page down": "Page Down", "home": "Home: start of line or page", "end": "End: end of line or page",
    "ctrl+a": "Select all", "ctrl+c": "Copy", "ctrl+x": "Cut", "ctrl+v": "Paste",
    "ctrl+z": "Undo", "ctrl+y": "Redo", "ctrl+s": "Save", "ctrl+f": "Find / search in page",
    "ctrl+t": "New browser tab", "ctrl+w": "Close the current tab", "ctrl+n": "New window or document",
    "ctrl+shift+t": "Reopen closed tab", "ctrl+tab": "Next tab", "ctrl+shift+tab": "Previous tab",
    "ctrl+l": "Focus the browser address bar", "f5": "Refresh / reload",
    "alt+left": "Go back", "alt+right": "Go forward",
    "alt+tab": "Switch to the previous window", "windows": "Open the Start menu",
    "windows+d": "Show the desktop", "windows+e": "Open File Explorer",
    "volume up": "Volume up", "volume down": "Volume down", "volume mute": "Mute / unmute",
    "play/pause media": "Play or pause media",
    "none": "No key press is asked for",
}

SCROLL = {"up": "Scroll up, towards the top", "down": "Scroll down, towards the bottom"}

# Words after which the text to type or search for may start ("search for X", "find X", "open X" when X isn't an app).
_TYPE_TRIGGERS = {"type", "write", "enter", "input", "search", "say", "put", "insert", "fill", "dictate", "for", "with",
                  "named", "called", "find", "locate", "open", "show"}
_FILLER = {"my", "the", "a", "an", "our", "this", "that", "some"}
NOT_UNDERSTOOD = "Didn't understand that. Try rephrasing"
_CLICK_KINDS = {"click", "double_click", "right_click"}


@dataclass
class Question:
    """Something to ask the user before a step can go ahead."""

    prompt: str
    kind: str  # "choose": pick one of `options`; "text": dictate free text
    options: list[tuple[str, object]]  # (label shown to the user, payload passed to `complete`)
    complete: Callable[[object], Plan]  # builds the runnable Plan from the answer
    rects: list[tuple[int, int, int, int]]  # on-screen boxes to number, same order as options


@dataclass
class Plan:
    ok: bool
    description: str
    kind: str | None = None
    target: Element | None = None
    key: str | None = None
    text: str | None = None
    submit: bool = False
    scroll: str | None = None
    app: App | None = None
    new_instance: bool = False  # open_app: launch another copy even if the app is already open
    window: int | None = None  # switch_app: the existing window to bring forward
    question: Question | None = None
    log: dict = field(default_factory=dict)


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def clean_span(s: str) -> str:
    return s.strip().strip("\"'“”‘’").rstrip(".,!?;:").strip()


def text_candidates(command: str) -> list[str]:
    """Spans of the transcript that could be the text to type, starting after a trigger word."""
    words = command.split()
    starts = {i + 1 for i, w in enumerate(words) if clean_span(w).lower() in _TYPE_TRIGGERS}
    if not starts:
        starts = set(range(1, len(words)))
    for i in list(starts):
        # Also offer the span without leading filler ("my budget spreadsheet" → "budget spreadsheet").
        while i < len(words) and clean_span(words[i]).lower() in _FILLER:
            i += 1
            starts.add(i)
    seen: dict[str, None] = {}
    for i in sorted(starts):
        for j in range(i + 1, len(words) + 1):
            if span := clean_span(" ".join(words[i:j])):
                seen.setdefault(span, None)
    return list(seen)[:MAX_TEXT_OPTIONS]


_CONNECTORS = {"and", "then", "also", "next", "afterwards", "after", "that"}
MAX_BOUNDARIES = 5  # 2^5 = 32 ways to split


def split_candidates(command: str) -> list[list[str]]:
    """Every way to cut the command at connector words ("and", "then") or sentence punctuation.

    Code proposes the splits and Jev picks one, so "open Brave and search for cats" can become
    two steps while "type salt and pepper" stays one.
    """
    words = command.split()
    # A boundary is a run of connector words (dropped from the steps) or a gap after , . ; !
    boundaries: list[tuple[int, int]] = []  # (end of left step, start of right step)
    i = 1
    while i < len(words):
        j = i
        while j < len(words) - 1 and clean_span(words[j]).lower() in _CONNECTORS:
            j += 1
        if j > i:
            boundaries.append((i, j))
            i = j
        elif words[i - 1][-1:] in ",.;!":
            boundaries.append((i, i))
        i += 1
    boundaries = boundaries[:MAX_BOUNDARIES]
    options: dict[tuple[str, ...], None] = {}
    for mask in range(1 << len(boundaries)):
        steps, start = [], 0
        for b, (end, nxt) in enumerate(boundaries):
            if mask >> b & 1:
                steps.append(" ".join(words[start:end]))
                start = nxt
        steps.append(" ".join(words[start:]))
        steps = [s for s in (clean_span(s) for s in steps) if s]
        if steps:
            options.setdefault(tuple(steps), None)
    return [list(o) for o in options]


def _region(el: Element, mon: dict) -> str:
    cx, cy = el.center
    h = ("left", "centre", "right")[min(2, max(0, 3 * (cx - mon["left"]) // mon["width"]))]
    v = ("top", "middle", "bottom")[min(2, max(0, 3 * (cy - mon["top"]) // mon["height"]))]
    return "centre" if (h, v) == ("centre", "middle") else f"{v} {h}"


def _shortlist(command: str, elements: list[Element]) -> list[Element]:
    """If the screen has more text than a Choice can hold, keep the elements most like the command."""
    if len(elements) <= MAX_TARGETS:
        return elements
    words = set(_tokens(command))

    def relevance(e: Element) -> float:
        t = set(_tokens(e.text))
        return len(words & t) / max(1, len(t)) + 0.5 * difflib.SequenceMatcher(None, command.lower(), e.text.lower()).ratio()

    keep = set(id(e) for e in sorted(elements, key=relevance, reverse=True)[:MAX_TARGETS])
    return [e for e in elements if id(e) in keep]


def _top(answer, n: int = 5) -> dict[str, float]:
    return dict(sorted(answer.probabilities.items(), key=lambda kv: -kv[1])[:n])


class Decider:
    def __init__(self, api_key: str, model: str):
        self.client = TypeSafeClient(api_key=api_key, model=model, timeout=15)

    def ping(self) -> float:
        r = self.client.system_one("ping", {"ok": Noul(instructions="Is this text the word ping?")})
        return r.answers["ok"].noul

    def segment(self, command: str, recent: list[str]) -> tuple[list[str], dict]:
        """Split a spoken command into ordered steps. Returns the steps and the top options for logging."""
        options = split_candidates(command)
        if len(options) == 1:
            return options[0], {}
        criteria = {f"s{i}": " → ".join(f'"{s}"' for s in steps) for i, steps in enumerate(options)}
        r = self.client.system_one(
            {"command": command, "recent_actions": recent},
            {
                "steps": Choice(
                    instructions=(
                        "`command` was spoken to control a computer. Which option splits it into the separate actions to carry "
                        "out, in order? One action is a single click, typing one piece of text, one key press, one scroll, or "
                        "opening one app. Keep words together when they belong to the same action, e.g. text to type that "
                        "contains 'and', or a target described with 'and'."
                    ),
                    criteria=criteria,
                )
            },
        )
        ans = r.answers["steps"]
        return options[int(ans.choice[1:])], {criteria[k]: v for k, v in _top(ans, 3).items()}

    def is_done(self, request: str, done: list[str], screen: Screen) -> float:
        """Probability that `request` has been fully carried out, judging by the steps taken and the screen now."""
        r = self.client.system_one(
            {"request": request, "steps_done": done, "active_window": screen.window_title,
             "screen_elements": [e.text[:120] for e in screen.elements][:MAX_TARGETS]},
            {"done": Noul(instructions=(
                "The user asked for `request` and these actions were carried out: `steps_done`. Judging by those and by what "
                "is on screen now, has `request` been fully done, with nothing left to do? No if part of it (e.g. finding, "
                "typing or opening something it mentions) hasn't happened yet."
            ))},
        )
        return r.answers["done"].noul

    def pick(self, question: str, answer: str, labels: list[str]) -> int | None:
        """Which of `labels` the user chose in their spoken `answer` to `question`, or None."""
        criteria = {f"o{i}": f"Option {i + 1}: {label}" for i, label in enumerate(labels)}
        criteria["none"] = "They didn't pick any of these (cancelled, unclear, or said something else)"
        r = self.client.system_one(
            {"question": question, "options": [f"{i + 1}. {label}" for i, label in enumerate(labels)], "answer": answer},
            {"pick": Choice(
                instructions=(
                    "The user was asked `question` and replied out loud with `answer`. Which option did they choose? "
                    "They may give its number (one, the first, number two), its name, or describe it."
                ),
                criteria=criteria,
            )},
        )
        a = r.answers["pick"]
        return None if a.choice == "none" or a.probabilities[a.choice] < 0.5 else int(a.choice[1:])

    def ask(
        self, command: str, screen: Screen, installed: list[App], context: dict | None = None
    ) -> tuple[dict, list[Element], list[str], list[App]]:
        """`context` adds e.g. the full spoken request and the steps already done to the state."""
        targets = _shortlist(command, [e for e in screen.elements if e.text.strip()])
        texts = text_candidates(command)
        apps = shortlist(command, installed)
        state = {
            "command": command,
            **(context or {}),
            "active_window": screen.window_title,
            "screen_elements": [{"id": e.id, "text": e.text[:120], "where": _region(e, screen.monitor)} for e in targets],
        }
        target_options = {e.id: f'"{e.text[:120]}" ({_region(e, screen.monitor)} of the screen)' for e in targets}
        target_options["none"] = "No listed element: the command needs no on-screen target, or what it refers to is not visible"

        questions = {
            "action": Choice(
                instructions=(
                    "`command` is a voice instruction to control this computer (possibly one step of a longer `full_request`, "
                    "after `steps_done`). `active_window` is the focused window and `screen_elements` is the text currently "
                    "visible. What kind of input action carries out `command`?"
                ),
                criteria=ACTIONS,
            ),
            "target": Choice(
                instructions=(
                    "The user spoke `command` to control this computer; `screen_elements` lists the text visible on screen. "
                    "Which on-screen text element should the mouse click (or click into before typing) to carry out the command? "
                    "Choose none if the command needs no on-screen target (a key press, a scroll, or typing where the cursor already is) "
                    "or if nothing listed matches."
                ),
                criteria=target_options,
            ),
            "key": Choice(
                instructions="If `command` asks for a keyboard key or shortcut to be pressed, which one? Choose none if it does not.",
                criteria=KEYS,
            ),
            "scroll": Choice(instructions="If `command` asks to scroll, in which direction?", criteria=SCROLL),
            "submit": Noul(
                instructions="Does `command` ask for text to be typed and then submitted, sent or searched, so Enter should be pressed after typing?"
            ),
            "doable": Noul(
                instructions=(
                    "Can `command` be carried out right now on the current screen? Yes if what it needs is visible in "
                    "`screen_elements` (the button, tab, item, or the text box to type or search in), or if a text box is "
                    "clearly already focused for typing (e.g. a browser that just opened a new tab), or if it needs nothing on "
                    "screen (opening an app, a key press, a scroll). No if the needed control is not on this screen."
                ),
            ),
            "loading": Noul(
                instructions=(
                    "Does `active_window` look like it is still loading or not ready yet: a splash screen, 'Loading…' or "
                    "'Please wait' text, an almost empty window, or the app `command` needs not showing yet?"
                ),
            ),
            "ambiguous": Noul(
                instructions=(
                    "Does `command` name something that matches more than one element in `screen_elements` equally well, so "
                    "it is unclear which one the user means (e.g. 'click the Witcher' when several Witcher games are listed, "
                    "or 'press Save' when two Save buttons are visible)?"
                ),
            ),
            "navigate": Choice(
                instructions=(
                    "Suppose `command` cannot be done on the current screen yet because what it needs is on another page, tab "
                    "or view of `active_window`. Which visible element in `screen_elements` should be clicked first to get to "
                    "a screen where it can be done (e.g. a Store, Home or Search tab, or a menu)? Elements listed in "
                    "`navigation_tried` were already clicked for this step. Choose none if no visible element would help."
                ),
                criteria={**{k: v for k, v in target_options.items() if k != "none"}, "none": "No visible element would help"},
            ),
        }
        if texts:
            text_options = {f"t{i}": f'"{t}"' for i, t in enumerate(texts)}
            text_options["none"] = "None of these is the text to type"
            questions["text"] = Choice(
                instructions=(
                    "`command` asks to type some text. Which option is exactly the text the user wants typed, without the "
                    "instruction words around it (such as 'type', 'search for', 'into the search box')? Choose none if none fits."
                ),
                criteria=text_options,
            )
            questions["query"] = Choice(
                instructions=(
                    "Suppose `command` is carried out by searching the computer (the Start menu / system search). Which option "
                    "is the best search query for the file, folder, setting or program the user wants: the name of the thing "
                    "itself, without instruction or filler words like 'open', 'find', 'my', 'the', 'on my PC'? Choose none "
                    "if none fits."
                ),
                criteria={**text_options, "none": "None of these is a good search query"},
            )
        if apps:
            questions["new_instance"] = Noul(
                instructions=(
                    "Does `command` explicitly ask for a new window or another copy of the application (e.g. 'open a new "
                    "Brave window', 'another Notepad'), rather than just opening or switching to it?"
                ),
            )
            app_options = {f"a{i}": a.name for i, a in enumerate(apps)}
            app_options["none"] = "None of these applications"
            questions["app"] = Choice(
                instructions="If `command` asks to open, launch or switch to an application, which installed application does it mean? Choose none if it does not ask for an application or none of these match.",
                criteria=app_options,
            )
        response = self.client.system_one(state, questions)
        return response.answers, targets, texts, apps


def _tied(answer, items, min_target: float, ambiguous: bool = False, max_options: int = 4) -> list:
    """Options to ask the user to choose between, or [] if Jev's pick is clear enough to act on.

    `items` are Elements (matched by .id) or (option id, payload) pairs. Ask when "none" isn't
    dominant and either Jev flagged the command as `ambiguous`, or at least two options have real
    probability with the leader under the threshold or less than 0.2 ahead of the runner-up.
    """
    lookup = {(i.id if hasattr(i, "id") else i[0]): (i if hasattr(i, "id") else i[1]) for i in items}
    ranked = [(k, v) for k, v in sorted(answer.probabilities.items(), key=lambda kv: -kv[1]) if k in lookup]
    if answer.probabilities.get("none", 0) >= 0.5 or len(ranked) < 2:
        return []
    if ambiguous:
        # Jev's distribution leans hard on the closest text match, so take a low bar for the alternatives.
        picks = [lookup[k] for k, v in ranked[:max_options] if v >= 0.02]
        return picks if len(picks) >= 2 else []
    if ranked[1][1] < 0.15 or (ranked[0][1] >= min_target and ranked[0][1] - ranked[1][1] >= 0.2):
        return []
    return [lookup[k] for k, v in ranked[:max_options] if v >= 0.1]


def _label(e: Element) -> str:
    return f'"{e.text[:40]}"'


def navigation(answers: dict, targets: list[Element], min_target: float, tried: set[str]) -> Element | None:
    """The element Jev says leads towards a screen where the step can be done, if confident and not tried yet."""
    nav = answers["navigate"]
    if nav.choice == "none" or nav.probabilities[nav.choice] < min_target:
        return None
    el = next((e for e in targets if e.id == nav.choice), None)
    return el if el is not None and el.text not in tried else None


def plan(answers: dict, targets: list[Element], texts: list[str], apps: list[App], min_action: float, min_target: float) -> Plan:
    action = answers["action"]
    kind, p_action = action.choice, action.probabilities[action.choice]
    target_ans = answers["target"]
    p_target = target_ans.probabilities[target_ans.choice]
    target = next((e for e in targets if e.id == target_ans.choice), None)

    log = {
        "action": _top(action),
        "target": {(next((e.text for e in targets if e.id == k), k)): v for k, v in _top(target_ans).items()},
        "key": _top(answers["key"], 3),
        "scroll": _top(answers["scroll"], 2),
        "submit": answers["submit"].noul,
        "doable": answers["doable"].noul,
        "ambiguous": answers["ambiguous"].noul,
        "loading": answers["loading"].noul,
        "navigate": {(next((e.text for e in targets if e.id == k), k)): v for k, v in _top(answers["navigate"], 3).items()},
    }
    if "text" in answers:
        log["text"] = {(texts[int(k[1:])] if k != "none" else k): v for k, v in _top(answers["text"], 3).items()}
    if "query" in answers:
        log["query"] = {(texts[int(k[1:])] if k != "none" else k): v for k, v in _top(answers["query"], 3).items()}
    if "app" in answers:
        log["app"] = {(apps[int(k[1:])].name if k != "none" else k): v for k, v in _top(answers["app"], 3).items()}

    ambiguous = answers["ambiguous"].noul > 0.5

    def fail(msg: str) -> Plan:
        return Plan(False, msg, kind=kind, log=log)

    def ask(prompt: str, options: list[tuple[str, object]], complete, rects=None) -> Plan:
        return Plan(False, prompt, kind=kind, log=log, question=Question(prompt, "choose", options, complete, rects or []))

    if kind == "none" or p_action < min_action:
        return fail(NOT_UNDERSTOOD)

    if kind in _CLICK_KINDS:
        verb = {"click": "Click", "double_click": "Double-click", "right_click": "Right-click"}[kind]
        make = lambda el: Plan(True, f'{verb} "{el.text[:50]}"', kind=kind, target=el, log=log)
        if tied := _tied(target_ans, targets, min_target, ambiguous):
            return ask(f"{verb} which one?", [(_label(e), e) for e in tied], make, [e.rect for e in tied])
        if target is None or p_target < min_target:
            return fail(f"Couldn't find what to {verb.lower()} on this screen")
        return make(target)

    if kind == "type_text":
        submit = answers["submit"].noul > 0.5

        def typing(text: str, into: Element | None) -> Plan:
            desc = f'Type "{text[:40]}"' + (f' into "{into.text[:30]}"' if into else "") + (" + Enter" if submit else "")
            return Plan(True, desc, kind=kind, target=into, text=text, submit=submit, log=log)

        tied = _tied(target_ans, targets, min_target, ambiguous)
        into = target if target is not None and p_target >= min_target and not tied else None
        if not tied and into is None and answers["doable"].noul < 0.5:
            return fail("No text box to type into on this screen")
        text_ans = answers.get("text")
        if text_ans is None or text_ans.choice == "none" or text_ans.probabilities[text_ans.choice] < min_action:
            # Jev couldn't pick the words out of the command: have the user dictate them.
            return Plan(False, "What should I type?", kind=kind, log=log,
                        question=Question("What should I type?", "text", [], lambda s: typing(s, into), []))
        text = texts[int(text_ans.choice[1:])]
        if tied:
            return ask(f'Type "{text[:30]}" into which box?', [(_label(e), e) for e in tied],
                       lambda el: typing(text, el), [e.rect for e in tied])
        return typing(text, into)

    if kind == "press_key":
        key_ans = answers["key"]
        if key_ans.choice == "none" or key_ans.probabilities[key_ans.choice] < min_action:
            return fail("Couldn't tell which key to press")
        return Plan(True, f"Press {key_ans.choice}", kind=kind, key=key_ans.choice, log=log)

    if kind == "scroll":
        direction = answers["scroll"].choice
        return Plan(True, f"Scroll {direction}", kind=kind, scroll=direction, log=log)

    if kind == "search_pc":
        make = lambda q: Plan(True, f'Search the PC for "{q[:40]}"', kind=kind, text=q, log=log)
        text_ans = answers.get("query")
        if text_ans is None or text_ans.choice == "none" or text_ans.probabilities[text_ans.choice] < min_action:
            return Plan(False, "What should I search for?", kind=kind, log=log,
                        question=Question("What should I search for?", "text", [], make, []))
        return make(texts[int(text_ans.choice[1:])])

    if kind == "open_app":
        app_ans = answers.get("app")
        new = answers["new_instance"].noul > 0.5 if "new_instance" in answers else False
        make = lambda a: Plan(True, f"Open a new {a.name} window" if new else f"Open {a.name}", kind=kind, app=a,
                              new_instance=new, log=log)
        if app_ans is not None and (tied := _tied(app_ans, [(f"a{i}", a) for i, a in enumerate(apps)], min_target)):
            return ask("Open which app?", [(a.name, a) for a in tied], make)
        if app_ans is None or app_ans.choice == "none" or app_ans.probabilities[app_ans.choice] < min_target:
            return fail("Couldn't tell which app to open")
        return make(apps[int(app_ans.choice[1:])])

    return fail(f"Unsupported action {kind}")
