"""Wires the pieces together.

Threads:
  main       Tk event loop: overlay, highlight, settings dialog. Other threads post work via `ui()`.
  keyboard   global hook for Right Ctrl push-to-talk (must return quickly).
  ocr        screen capture + OCR, started the moment Right Ctrl goes down.
  work       transcription -> Jev -> action, one command at a time.
  tray       pystray message loop.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
import winsound
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

import keyboard
import numpy as np
from typesafe_sdk import TypeSafeAPIError, TypeSafeAuthenticationError, TypeSafeError

from . import executor
from .apps import App as InstalledApp
from .apps import load_start_apps
from .decide import (
    NOT_UNDERSTOOD,
    Decider,
    Plan,
    Question,
    clean_span,
    navigation,
    plan,
)
from .desktop import (
    find_app_window,
    foreground_is,
    foreground_window,
    wait_until_settled,
)
from .llm import PROVIDERS, Planner
from .overlay import Highlight, Overlay
from .settings import LOG_DIR, Settings, get_api_key
from .settings_dialog import SettingsDialog
from .stt import Transcriber
from .tray import Tray

if TYPE_CHECKING:
    from .audio import Recorder
    from .perception import Perception, Screen

# `.audio` (PortAudio) and `.perception` (WinRT) initialise COM when imported, so they are
# imported only after Whisper has loaded; see _load_model.
SAMPLE_RATE = 16000

log = logging.getLogger(__name__)

HOTKEY = "right ctrl"
ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.ico")
MIN_AUDIO_S = 0.35  # shorter presses are treated as taps and ignored
MIN_RMS = 0.002  # below this the clip is silence
HINT_WORDS = 40
RECENT_S = 120  # how long earlier actions count as context for a new command
MAX_NAV_HOPS = 3  # clicks to find where a step can be done (e.g. Library → Store) before giving up
ANSWER_TIMEOUT_S = 30  # how long a clarifying question waits for a spoken answer
STUCK_ROUNDS = 3  # rounds in a row without progress before asking the user what to do next
CHECK_IN_EVERY = 20  # actions beyond what was said between "keep going?" check-ins
DONE_THRESHOLD = 0.5  # after the last step, keep going (Jev, else the AI planner) while Jev's "request is done" is below this
LOADING = 0.5  # Jev's "screen is still loading" probability that makes a failing step wait
JUST_ACTED_S = 3  # a step failing this soon after an action also waits, in case the screen hasn't caught up
LOAD_WAIT_S = 5  # the longest a step waits for the screen to change before looking elsewhere
CONTINUE = object()  # pending-step marker: let Jev choose the next action toward the whole request
PLANNER_ERROR = object()  # _replan result when the AI call itself failed


@dataclass
class StepOutcome:
    plan: Plan | None  # runnable plan, or None if the step can't go ahead
    problem: str | None  # why not, when the user hasn't already been told
    screen: Screen  # the screen the step was judged on (after any navigation)


def make_planner(s: Settings) -> Planner | None:
    """The AI planner configured in settings, or None if it's off or missing its key/model."""
    info = PROVIDERS.get(s.llm_provider)
    if info is None or not s.llm_model:
        return None
    key = get_api_key(s.llm_provider) if info["needs_key"] else None
    if info["needs_key"] and not key:
        return None
    return Planner(s.llm_provider, s.llm_model, key, s.llm_base_url or None, s.llm_screenshot, keep_alive=s.llm_keep_alive)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        # Every window (Settings, dialogs) gets the app's icon instead of Python's.
        self.root.iconbitmap(default=ICON_PATH)
        self._ui_queue: queue.Queue = queue.Queue()
        self._last_status = "Loading speech model…"
        self.settings = Settings.load()
        self.settings_dialog: SettingsDialog | None = None

        self.tray: Tray | None = None
        self.perception: Perception | None = None
        self.recorder: Recorder | None = None
        self.model_ready = False
        self.recording = False
        self.busy = False
        self._ocr_future: Future | None = None
        self._cancel = threading.Event()
        self._executing = False  # our own synthetic key presses mustn't count as "cancel"
        self._recent: collections.deque = collections.deque(maxlen=6)
        self._question: Question | None = None  # set while waiting for the user to answer
        self._answers: queue.Queue = queue.Queue()
        self._last_action_t = 0.0
        self._marked_done = False  # the user clicked ✓: stop, and report success rather than "stopped"

        self.overlay = Overlay(self.root)
        self.overlay.on_check = self._user_marked_done
        self.apply_overlay_style()
        self.highlight = Highlight(self.root)
        self.root.update()  # draw "Loading speech model…" before blocking on the load

        # Whisper must load on this thread before anything else touches COM (Windows OCR,
        # PortAudio, the tray icon): otherwise CTranslate2 crashes with an access violation.
        self.transcriber = Transcriber(self.settings.whisper_model, self.settings.language)
        self._load_model()

        self.installed_apps: list[InstalledApp] = []
        threading.Thread(target=self._load_apps, daemon=True, name="apps").start()
        self.tray = Tray(self)
        self.decider: Decider | None = None
        self.planner: Planner | None = None
        self._rebuild_decider()
        self._ocr_pool = ThreadPoolExecutor(1, thread_name_prefix="ocr")
        self._work_pool = ThreadPoolExecutor(1, thread_name_prefix="work")
        self._llm_pool = ThreadPoolExecutor(2, thread_name_prefix="llm")
        keyboard.hook(self._on_key)
        self._refresh_idle()
        if self.model_ready and self.transcriber.device != "cuda":
            self.status("warn", "CUDA unavailable: transcribing on CPU (slow)", 5000)
        elif self.model_ready and self.decider:
            # Say how to use it once, then shrink to the idle dot.
            self.overlay.show(self.overlay.idle_state, "Jev ready · hold Right Ctrl to speak" + self._dry_run_note(), 5000)
        self._pump()

    # ---- thread plumbing -------------------------------------------------

    def ui(self, fn, *args) -> None:
        """Run fn(*args) on the Tk thread."""
        self._ui_queue.put((fn, args))

    def _pump(self) -> None:
        while True:
            try:
                fn, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                log.exception("UI callback failed")
        self.root.after(25, self._pump)

    def status(self, state: str, text: str, hold_ms: int | None = None) -> None:
        """Thread-safe: update the floating pill and tray icon."""
        self._last_status = text
        # While a command is running, offer ✓ "it's done" so the user can stop it once it has succeeded.
        check = self.busy and not self._cancel.is_set() and state in ("thinking", "done", "question")
        self.ui(self.overlay.show, state, text, hold_ms, check)
        if self.tray:
            self.tray.set_state({"listening": "listening", "thinking": "thinking", "error": "error"}.get(state, "idle"))

    def status_line(self) -> str:
        return self._last_status[:60]

    # ---- lifecycle -------------------------------------------------------

    def _idle_text(self) -> str:
        """Text for the idle indicator: empty (just a dot) unless something needs the user's attention."""
        if not self.model_ready:
            return "Speech model failed to load (see logs)"
        if self.decider is None:
            return "Add TypeSafe API key (right-click tray icon)"
        return ""

    def _dry_run_note(self) -> str:
        return " · dry run" if self.settings.dry_run else ""

    def _refresh_idle(self) -> None:
        self.overlay.idle_text = self._idle_text()
        # Amber dot while it can't act for real (setup missing, or dry run).
        ready = self.model_ready and self.decider
        self.overlay.idle_state = "idle" if ready and not self.settings.dry_run else "warn"
        if self.overlay.state in ("idle", "loading", "warn") and not self.recording and not self.busy:
            self.overlay.show(self.overlay.idle_state, self.overlay.idle_text)
        self._last_status = self.overlay.idle_text or "Ready · hold Right Ctrl to speak" + self._dry_run_note()
        self.tray.refresh()

    def _load_model(self) -> None:
        try:
            self.transcriber.load()
            from .audio import Recorder
            from .perception import Perception

            self.perception = Perception()
            self.recorder = Recorder()
            self.model_ready = True
            log.info("Ready (whisper on %s)", self.transcriber.device)
        except Exception as e:
            log.exception("Startup failed")
            self.status("error", f"Startup failed: {e}")

    def _load_apps(self) -> None:
        try:
            self.installed_apps = load_start_apps()
        except Exception:
            log.exception("Could not list installed apps; 'open <app>' will be unavailable")

    def _rebuild_decider(self) -> None:
        key = get_api_key()
        self.decider = Decider(key, self.settings.model) if key else None
        self.planner = make_planner(self.settings)
        if self.planner and self.planner.keep_alive:
            threading.Thread(target=self._preload_planner, args=(self.planner,), daemon=True, name="llm-preload").start()

    def _preload_planner(self, planner: Planner) -> None:
        try:
            t = time.perf_counter()
            planner.preload()
            log.info("Loaded %s and pinned it in memory (%.1f s)", planner.model, time.perf_counter() - t)
        except Exception:
            log.exception("Couldn't preload %s", planner.model)

    def apply_overlay_style(self, s: Settings | None = None) -> None:
        """Colour and opacity of the indicator, from `s` (e.g. a live preview) or the saved settings."""
        s = s or self.settings
        self.overlay.apply_style(s.overlay_bg, s.overlay_fg, s.overlay_opacity, s.overlay_dots)

    def on_settings_changed(self) -> None:
        self.apply_overlay_style()
        self._rebuild_decider()
        self._refresh_idle()

    def open_settings(self) -> None:
        if self.settings_dialog and self.settings_dialog.win.winfo_exists():
            self.settings_dialog.win.lift()
            self.settings_dialog.win.focus_force()
        else:
            self.settings_dialog = SettingsDialog(self)

    def toggle_dry_run(self) -> None:
        self.settings.dry_run = not self.settings.dry_run
        self.settings.save()
        self._refresh_idle()
        self.overlay.show(self.overlay.idle_state, f"Dry run {'on' if self.settings.dry_run else 'off'}", 2000)

    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        keyboard.unhook_all()
        if self.recorder:
            self.recorder.close()
        self.tray.stop()
        self.root.quit()

    # ---- push-to-talk ----------------------------------------------------

    def _on_key(self, e: keyboard.KeyboardEvent) -> None:
        if self.busy and not self._executing and e.event_type == keyboard.KEY_DOWN and not self.recording:
            # Esc stops a running command; Right Ctrl does too, unless we're waiting for an answer to a question.
            if e.name == "esc" or (e.name == HOTKEY and self._question is None):
                self._cancel.set()
                return
        if e.name == HOTKEY:
            if e.event_type == keyboard.KEY_DOWN and not self.recording:
                self._start_recording()
            elif e.event_type == keyboard.KEY_UP and self.recording:
                self._stop_recording()
        elif self.recording and e.event_type == keyboard.KEY_DOWN:
            # Right Ctrl was used as part of a shortcut (e.g. RCtrl+C), not push-to-talk.
            self.recording = False
            self.recorder.stop()
            self._show_waiting()

    def _show_waiting(self) -> None:
        """Back to the question being asked, or to idle."""
        if self._question is not None:
            self.status("question", self._question_prompt())
        else:
            self.status(self.overlay.idle_state, self.overlay.idle_text)

    def _start_recording(self) -> None:
        if self.busy and self._question is None:
            return
        if not self.model_ready:
            self.status("warn", "Still loading the speech model…", 2500)
            return
        if self.decider is None:
            self.status("error", "No TypeSafe API key: right-click the tray icon → Settings", 4000)
            return
        self.recording = True
        self.recorder.start()
        if self._question is None:
            # OCR runs while the user is still speaking, so it's ready by the time they let go.
            self._ocr_future = self._ocr_pool.submit(self.perception.capture, [self.overlay.rect])
        self.status("listening", "Listening…")

    def _stop_recording(self) -> None:
        self.recording = False
        audio = self.recorder.stop()
        if len(audio) < MIN_AUDIO_S * SAMPLE_RATE:
            self._show_waiting()
            return
        if self._question is not None:
            self._answers.put(audio)  # the worker thread is blocked in _ask_user waiting for this
            self.status("thinking", "Got it…")
            return
        self.busy = True
        self._cancel.clear()
        self._marked_done = False
        self._work_pool.submit(self._handle_command, audio, self._ocr_future, time.perf_counter())

    # ---- command pipeline ------------------------------------------------

    def _handle_command(self, audio: np.ndarray, ocr_future: Future, released_t: float) -> None:
        entry: dict = {"time": datetime.now().isoformat(timespec="seconds"), "audio_s": round(len(audio) / SAMPLE_RATE, 2)}
        try:
            self._run_command(audio, ocr_future, released_t, entry)
        except TypeSafeAuthenticationError:
            entry["error"] = "auth"
            self.status("error", "TypeSafe rejected the API key: check Settings", 5000)
        except TypeSafeAPIError as e:
            entry["error"] = f"api {e.status}"
            self.status("error", f"TypeSafe error {e.status}", 4000)
        except TypeSafeError as e:
            entry["error"] = f"connection: {e}"
            self.status("error", "Couldn't reach TypeSafe", 4000)
        except Exception as e:
            log.exception("Command failed")
            entry["error"] = repr(e)
            self.status("error", f"Error: {e}", 5000)
        finally:
            self.busy = False
            self._write_log(entry)

    def _run_command(self, audio: np.ndarray, ocr_future: Future, released_t: float, entry: dict) -> None:
        if float(np.sqrt(np.mean(audio**2))) < MIN_RMS:
            entry["skipped"] = "silence"
            self.status("warn", "Didn't hear anything", 2000)
            return
        self.status("thinking", "Transcribing…")
        screen: Screen = ocr_future.result()
        entry.update(ocr_ms=round(screen.ocr_ms), window=screen.window_title, elements=len(screen.elements))

        t = time.perf_counter()
        hints = list(dict.fromkeys(e.text for e in screen.elements if len(e.text.split()) <= 3))[:HINT_WORDS]
        command = self.transcriber.transcribe(audio, hints)
        entry.update(stt_ms=round((time.perf_counter() - t) * 1000), command=command)
        if not command:
            self.status("warn", "Didn't catch that", 2500)
            return

        self.status("thinking", f"“{command}”")
        recent = self._recent_actions()
        entry.update(dry_run=self.settings.dry_run, results=[], replans=[])
        if self.planner and self.settings.llm_mode == "always":
            steps = self._replan(command, [], screen, None, entry)
            if steps is None:
                return
            if steps is PLANNER_ERROR or not steps:
                steps = [command]  # fall back to Jev on the command as said
        else:
            t = time.perf_counter()
            steps, seg_log = self.decider.segment(command, recent)
            entry.update(split_ms=round((time.perf_counter() - t) * 1000), split=seg_log)
        entry["steps"] = list(steps)

        pending = list(steps)
        done: list[str] = []
        guidance: list[tuple[str, str]] = []  # the user's answers when asked how to get unstuck
        stuck = 0  # rounds in a row without progress: failed steps, repeated actions, or an unchanged screen
        last_seen = None
        fresh = True  # `screen` is current for the next step
        # Keep going until Jev judges the request done, or the user presses Esc / Right Ctrl.
        while not self._cancel.is_set():
            if not pending:
                if not done:
                    break
                # Out of steps. Requests often imply more than was said ("open YouTube in Brave" is also "go to
                # YouTube"), so check the result against the screen, and work out what's still needed if it isn't done.
                screen, fresh = self.perception.capture([self.overlay.rect], follow="foreground"), True
                p_done = self.decider.is_done(command, done, screen)
                entry.setdefault("done_checks", []).append(round(p_done, 2))
                if p_done >= DONE_THRESHOLD:
                    break
                seen = (screen.window_title, frozenset(e.text for e in screen.elements))
                stuck = stuck + 1 if seen == last_seen else 0
                last_seen = seen
                extra = len(done) - len(steps)
                if extra > 0 and extra % CHECK_IN_EVERY == 0 and not self._keep_going(command, entry):
                    break
                problem = "The steps so far haven't finished the request. Give only what is still needed."
            else:
                step = pending.pop(0)
                continuing = step is CONTINUE
                label = f"[{len(done) + 1}/{len(done) + 1 + len(pending)}] " if not continuing else f"[{len(done) + 1}] "
                if len(steps) == 1 and not done and not pending:
                    label = ""
                if not fresh:
                    screen = self.perception.capture([self.overlay.rect], follow="foreground")
                fresh = False
                context = {"full_request": command, "steps_done": done, "recent_actions": recent}
                if continuing:
                    # No planned step left but the request isn't done: Jev picks the next action itself.
                    step = command
                    context["task"] = ("Choose only the next single action still needed to finish `command`, given "
                                       "`steps_done` and the current screen. Do not repeat anything in `steps_done`.")
                out = self._resolve_step(step, label, screen, context, entry)
                if continuing and out.plan is not None and out.plan.description in done[-3:]:
                    out = StepOutcome(None, f'the next action would repeat "{out.plan.description}"', out.screen)

                if out.plan is not None:
                    p = self._prefer_open_window(out.plan)
                    if self.settings.dry_run:
                        # Later steps depend on the screen the earlier ones produce, so a dry run previews one step.
                        if p.target:
                            self.ui(self.highlight.flash, p.target.rect, 2000, "#fbbc04")
                        more = f", then {len(pending)} more step{'s' * (len(pending) > 1)}" if pending else ""
                        self.status("done", f"Would: {p.description}{more}", 4000)
                        break
                    before = foreground_window()
                    self._execute(p)
                    done.append(p.description)
                    self._recent.append((time.monotonic(), p.description))
                    self.status("done", f"{label}{p.description}", 3000)
                    self._let_screen_catch_up(p, before)
                    continue

                if not out.problem or self._cancel.is_set():
                    break  # the user has already been told (cancelled or unanswered question)
                if self.settings.dry_run:
                    self._fail(f"{label}“{step}”: {out.problem}")
                    break
                # Retrying won't help if nothing has happened yet and Jev didn't understand what was said: ask now.
                stuck = STUCK_ROUNDS if not done and out.problem.startswith(NOT_UNDERSTOOD) else stuck + 1
                screen, fresh = out.screen, True
                problem = f'The step "{"finish the request" if continuing else step}" failed: {out.problem}'

            # Not done yet: work out the further steps (the AI planner's list, else Jev's next action).
            if stuck >= STUCK_ROUNDS:
                answer = self._ask_how_to_continue(command, problem, entry)
                if answer is None:
                    break
                guidance.append(("I'm stuck. What should I do next?", answer))
                stuck = 0
                if self.planner is None:
                    pending = [answer]  # Jev takes the user's instruction as the next step
                    continue
            pending = self._further_steps(command, done, screen, problem, guidance, entry)
            if pending is None:
                break

        if self._cancel.is_set() and self._marked_done:
            entry["marked_done_by_user_after"] = len(done)
            self.status("done", "Done ✓", 2500)  # in case a late step message replaced it
        elif self._cancel.is_set():
            self.status("warn", f"Stopped after {len(done)} step{'s' * (len(done) != 1)}", 3000)
            entry["cancelled_after"] = len(done)
        entry["total_ms_after_release"] = round((time.perf_counter() - released_t) * 1000)
        log.info("%s -> %s (%s ms after release)", command, done, entry["total_ms_after_release"])

    def _further_steps(self, command: str, done: list[str], screen: Screen, problem: str,
                       guidance: list[tuple[str, str]], entry: dict) -> list | None:
        """What to do next toward an unfinished request: the AI planner's list of steps if one is set up,
        otherwise let Jev choose the next action. None to stop (cancelled, or a question went unanswered)."""
        if self.planner is None:
            return [CONTINUE]
        steps = self._replan(command, done, screen, problem, entry, guidance)
        if steps is PLANNER_ERROR or steps == []:
            return [CONTINUE]  # the AI failed, or thinks it's done when Jev doesn't: Jev tries the next action
        return steps

    def _ask_how_to_continue(self, command: str, problem: str, entry: dict) -> str | None:
        """Stuck for several rounds: ask the user what to do next rather than loop forever or quit silently."""
        entry.setdefault("stuck", []).append(problem)
        short = command if len(command) <= 50 else command[:49] + "…"
        prompt = (f"I didn't understand “{short}”. What should I do?" if NOT_UNDERSTOOD in problem
                  else f"I'm stuck on “{short}”. What should I do next?")
        return self._ask_user(Question(prompt, "text", [], lambda s: s, []), "", entry)

    def _keep_going(self, command: str, entry: dict) -> bool:
        short = command if len(command) <= 50 else command[:49] + "…"
        q = Question(f"Still working on “{short}”. Keep going?", "choose", [("Keep going", True), ("Stop", False)], lambda v: v, [])
        return bool(self._ask_user(q, "", entry))

    def _replan(self, command: str, done: list[str], screen: Screen, problem: str | None, entry: dict,
                guidance: list[tuple[str, str]] = ()):
        """Have the AI planner rewrite what's left of the request as simple commands for Jev.

        Returns the steps ([] if it thinks nothing more is needed), PLANNER_ERROR if the call failed, or
        None if cancelled or its question to the user went unanswered. `guidance` is the user's earlier answers.
        """
        answers: list[tuple[str, str]] = list(guidance)
        for _ in range(3):
            self.status("thinking", f"Thinking it through ({self.planner.name})…  Esc to cancel")
            t = time.perf_counter()
            job = self._llm_pool.submit(self.planner.plan, command, done, screen, problem, answers)
            while not job.done():
                if self._cancel.is_set():
                    entry["replans"].append({"problem": problem, "cancelled": True})
                    return None  # the request finishes in the background and is ignored
                time.sleep(0.05)
            try:
                lp = job.result()
            except Exception as e:
                log.exception("AI planner failed")
                entry["replans"].append({"problem": problem, "error": repr(e)})
                self.status("warn", f"AI planner failed ({str(e)[:60]}): carrying on with Jev", 3000)
                return PLANNER_ERROR
            entry["replans"].append({"problem": problem, "ms": round((time.perf_counter() - t) * 1000),
                                     "understanding": lp.understanding, "steps": lp.steps, "question": lp.question})
            if lp.question and not lp.steps:
                answer = self._ask_user(Question(lp.question, "text", [], lambda s: s, []), "", entry)
                if answer is None:
                    return None
                answers.append((lp.question, answer))
                continue
            if lp.steps:
                self.status("thinking", "Plan: " + " → ".join(lp.steps))
            return lp.steps
        return PLANNER_ERROR

    def _resolve_step(self, step: str, label: str, screen: Screen, context: dict, entry: dict) -> StepOutcome:
        """Ask Jev how to do `step`. If it can't be done on this screen, click where Jev says it can be
        found (a tab, Home, Store…), re-read the screen and ask again, up to MAX_NAV_HOPS times.

        Failures come back as `problem` for the caller to report or hand to the AI planner; a problem of
        None means the user has already been told (dry run, cancelled or unanswered question).
        """
        tried: list[str] = []
        hop = 0
        wait_until: float | None = None  # None: haven't waited this step; 0: already waited
        while True:
            self.status("thinking", f"{label}“{step}”" + (f" (looking: {' → '.join(tried)})" if tried else ""))
            t = time.perf_counter()
            answers, targets, texts, apps = self.decider.ask(step, screen, self.installed_apps, {**context, "navigation_tried": tried})
            p = plan(answers, targets, texts, apps, self.settings.min_action_prob, self.settings.min_target_prob)
            nav = None if p.ok else navigation(answers, targets, self.settings.min_target_prob, set(tried))
            entry["results"].append({"step": step, "hop": hop, "jev_ms": round((time.perf_counter() - t) * 1000),
                                     "ocr_ms": round(screen.ocr_ms), "window": screen.window_title, "elements": len(screen.elements),
                                     "plan": p.description, "ok": p.ok, "navigate_to": nav and nav.text, "answers": p.log})
            if p.ok:
                return StepOutcome(p, None, screen)
            if p.question is not None:
                if self.settings.dry_run:
                    self.ui(self.highlight.mark, p.question.rects, 3000)
                    self.status("question", f"{label}Would ask: {p.question.prompt}", 5000)
                    return StepOutcome(None, None, screen)
                return StepOutcome(self._ask_user(p.question, label, entry), None, screen)

            # Maybe the app or page just hasn't finished loading: give it a few seconds before looking elsewhere.
            if wait_until is None and (answers["loading"].noul > LOADING or len(screen.elements) <= 2
                                       or time.monotonic() - self._last_action_t < JUST_ACTED_S):
                wait_until = time.monotonic() + LOAD_WAIT_S
            if wait_until and not self._cancel.is_set():
                changed = self._wait_for_change(screen, wait_until, label)
                if changed is not None:
                    screen = changed
                    continue
                wait_until = 0

            if nav is None or hop == MAX_NAV_HOPS or self._cancel.is_set():
                if p.target:
                    self.ui(self.highlight.flash, p.target.rect, 1500, "#fbbc04")
                where = f" (looked in {', '.join(tried)})" if tried else ""
                return StepOutcome(None, p.description + where, screen)
            if self.settings.dry_run:
                self.ui(self.highlight.flash, nav.rect, 2000, "#fbbc04")
                self.status("done", f'Would click "{nav.text[:40]}" to look for it', 4000)
                return StepOutcome(None, None, screen)
            tried.append(nav.text)
            hop += 1
            self._execute(Plan(True, f'Click "{nav.text[:50]}"', kind="click", target=nav))
            self.status("thinking", f'{label}Not here: trying "{nav.text[:40]}"')
            wait_until_settled(6, self._cancel.is_set)
            screen = self.perception.capture([self.overlay.rect], follow="foreground")

    def _wait_for_change(self, screen: Screen, until: float, label: str) -> Screen | None:
        """Re-read the screen until its text changes (then return it, once settled), or None at `until`."""
        self.status("thinking", f"{label}Waiting for it to load…")
        before = (screen.window_title, {e.text for e in screen.elements})
        while time.monotonic() < until and not self._cancel.is_set():
            time.sleep(0.5)
            now = self.perception.capture([self.overlay.rect], follow="foreground")
            if (now.window_title, {e.text for e in now.elements}) != before:
                wait_until_settled(max(0.5, min(2.0, until - time.monotonic())), self._cancel.is_set)
                return self.perception.capture([self.overlay.rect], follow="foreground")
        return None

    def _let_screen_catch_up(self, p: Plan, before: int) -> None:
        """After an action, wait for its effect: the app's window for "open app", then the screen to settle."""
        if p.kind in ("open_app", "switch_app"):
            self._wait_for_app_window(p.app.name, before, 10 if p.kind == "open_app" else 2)
        wait_until_settled(6 if p.kind in ("open_app", "click", "double_click", "search_pc") else 2, self._cancel.is_set)

    def _fail(self, text: str) -> None:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        self.status("warn", text, 6000)

    def _question_prompt(self) -> str:
        q = self._question
        opts = "  ".join(f"{i}) {label}" for i, (label, _) in enumerate(q.options, 1))
        return f"{q.prompt}  {opts}  ·  hold Right Ctrl to answer, Esc to cancel" if opts else \
               f"{q.prompt}  ·  hold Right Ctrl and say it, Esc to cancel"

    def _ask_user(self, q: Question, label: str, entry: dict):
        """Ask a clarifying question and wait for a spoken answer.

        Returns q.complete(answer): a Plan for Jev's questions, the answer text for the AI planner's.
        None if cancelled, unanswered or unclear (the user has been told).
        """
        while not self._answers.empty():
            self._answers.get_nowait()
        self._question = q
        record = {"question": q.prompt, "options": [o for o, _ in q.options]}
        entry.setdefault("questions", []).append(record)
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        self.ui(self.highlight.mark, q.rects, None)
        self.status("question", label + self._question_prompt())
        try:
            deadline = time.monotonic() + ANSWER_TIMEOUT_S
            audio = None
            while audio is None:
                if self._cancel.is_set() or (time.monotonic() > deadline and not self.recording):
                    record["answer"] = None
                    self.status("warn", "Cancelled" if self._cancel.is_set() else "No answer, so I stopped", 3000)
                    return None
                try:
                    audio = self._answers.get(timeout=0.1)
                except queue.Empty:
                    pass
        finally:
            self._question = None
            self.ui(self.highlight.hide)

        hints = [o for o, _ in q.options] if q.options else []
        answer = self.transcriber.transcribe(audio, [clean_span(h) for h in hints])
        record["answer"] = answer
        if not answer:
            self._fail("Didn't catch the answer, so I stopped")
            return None
        if q.kind == "text":
            text = clean_span(answer)
            return q.complete(text) if text else None
        idx = self.decider.pick(q.prompt, answer, [o for o, _ in q.options])
        record["picked"] = idx
        if idx is None:
            self._fail(f"“{answer}” didn't match an option, so I stopped")
            return None
        return q.complete(q.options[idx][1])

    def _execute(self, p: Plan) -> None:
        self._last_action_t = time.monotonic()
        self._executing = True
        try:
            executor.execute(p, self.settings.search_hotkey if self.settings.powertoys_search else None)
        finally:
            self._executing = False
        if p.target:
            self.ui(self.highlight.flash, p.target.rect, 500)

    def _user_marked_done(self) -> None:
        """✓ clicked on the indicator: the task has succeeded, so stop working on it (like Esc, but a success)."""
        if not self.busy or self._cancel.is_set():
            return
        self._marked_done = True
        self._cancel.set()
        winsound.MessageBeep(winsound.MB_OK)
        self.overlay.show("done", "Done ✓", 2500)

    def _recent_actions(self) -> list[str]:
        """Actions from the last couple of minutes, so follow-ups like "now search for dogs" have context."""
        now = time.monotonic()
        return [d for t, d in self._recent if now - t < RECENT_S]

    def _wait_for_app_window(self, name: str, before: int, timeout_s: float) -> None:
        """Wait until the app's window is in front (e.g. "TickTick - Inbox"). If some other window takes
        focus instead, give the app's own window a few more seconds, then carry on."""
        deadline = time.monotonic() + timeout_s
        changed_at = None
        while time.monotonic() < deadline and not self._cancel.is_set():
            if foreground_is(name):
                return
            if foreground_window() != before:
                changed_at = changed_at or time.monotonic()
                if time.monotonic() - changed_at > 3:
                    return
            time.sleep(0.1)

    @staticmethod
    def _prefer_open_window(p: Plan) -> Plan:
        """ "Open <app>" switches to the app if it's already running, unless a new window was asked for."""
        if p.kind != "open_app" or p.new_instance:
            return p
        hwnd = find_app_window(p.app.name)
        if hwnd is None:
            return p
        return replace(p, kind="switch_app", window=hwnd, description=f"Switch to {p.app.name}")

    def _write_log(self, entry: dict) -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"commands-{datetime.now():%Y-%m-%d}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
