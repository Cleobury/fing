"""Hands-free listening: the wake word, and answering Jev's questions without holding Right Ctrl.

The Listener reads the same always-open mic stream as push-to-talk (see Recorder.add_listener), so nothing
else opens the mic. It works on 30 ms frames:

  spot     While the wake word is on and Jev is idle, each stretch of speech (up to SPOT_WINDOW_S of it) is
           transcribed by the already-loaded Whisper model and checked for the phrase (wakephrase.match).
           On a hit the rest of that stretch, and anything said right after it, becomes the command.
  capture  Record one utterance hands-free and hand it to the app: after a wake word, or when a question is
           asked and "Listen for my answer automatically" is on. It ends on END_SILENCE_S of quiet, or gives
           up if nobody speaks within the timeout.
  test     Settings' Test button: spot like above, but only report what was heard; never trigger anything.

Speech is found by loudness against a running noise floor (SpeechGate). Whisper's own VAD still filters
the audio it's given, so a loud non-speech noise costs one quick transcription and nothing else.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np

from . import wakephrase

if TYPE_CHECKING:
    from .stt import Transcriber

log = logging.getLogger(__name__)

RATE = 16000
FRAME = 480  # 30 ms
FRAME_S = FRAME / RATE
START_FRAMES = 3  # speech frames in a row that start an utterance (~90 ms)
PREROLL_S = 0.3  # kept from before speech starts, so the first syllable isn't lost
END_SILENCE_S = 0.8  # quiet that ends an utterance
SPOT_WINDOW_S = 2.5  # how much of a stretch of speech is checked for the wake phrase
SPOT_MIN_S = 0.25  # shorter blips aren't worth transcribing
WAKE_FOLLOW_S = 6.0  # after just the phrase ("Hey Jev" … pause), how long to wait for the command
MAX_CAPTURE_S = 30.0
MIN_SPEECH_RMS = 0.005  # nothing quieter than this counts as speech, however quiet the room


class SpeechGate:
    """Is this frame speech? Louder than both MIN_SPEECH_RMS and three times the background noise, which is
    tracked while it's quiet (and drifts up very slowly during sound, so a fan switched on is learned)."""

    def __init__(self, noise: float = 0.002):
        self.noise = noise

    def __call__(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame * frame))) if len(frame) else 0.0
        speech = rms > max(MIN_SPEECH_RMS, self.noise * 3.0)
        rate = 0.002 if speech else 0.05
        self.noise = max(1e-4, self.noise + (rms - self.noise) * rate)
        return speech


class Utterance:
    """One stretch of speech: starts after START_FRAMES speech frames in a row (keeping PREROLL_S before
    them) and ends after `end_silence_s` of quiet."""

    def __init__(self, end_silence_s: float = END_SILENCE_S):
        self.end_frames = max(1, round(end_silence_s / FRAME_S))
        self._ring: collections.deque = collections.deque(maxlen=round(PREROLL_S / FRAME_S) + START_FRAMES)
        self.frames: list[np.ndarray] = []
        self.started = self.ended = False
        self._run = 0  # speech frames in a row before the start; quiet frames in a row after it

    def push(self, frame: np.ndarray, speech: bool) -> str:
        """Add a frame. Returns "started" on the frame speech starts, "ended" when it ends, else ""."""
        if not self.started:
            self._ring.append(frame)
            self._run = self._run + 1 if speech else 0
            if self._run >= START_FRAMES:
                self.started, self.frames, self._run = True, list(self._ring), 0
                return "started"
            return ""
        if self.ended:
            return ""
        self.frames.append(frame)
        self._run = 0 if speech else self._run + 1
        self.ended = self._run >= self.end_frames
        return "ended" if self.ended else ""

    @property
    def seconds(self) -> float:
        return len(self.frames) * FRAME_S

    def audio(self) -> np.ndarray:
        return np.concatenate(self.frames) if self.frames else np.zeros(0, np.float32)


@dataclass
class _Capture:
    token: int
    timeout_s: float  # give up if speech hasn't started this long after listening begins
    not_before: float  # ignore the mic until then (e.g. Jev's own question beep)
    prefix: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))  # the wake phrase, already said
    utt: Utterance = field(default_factory=Utterance)
    began: float | None = None


class Listener:
    """Runs on its own thread. The app supplies:
      wake_allowed() -> bool          may a wake word start a command right now?
      on_wake(token) -> bool          the wake phrase was heard: start a hands-free recording (False = can't)
      on_heard(token, audio | None)   a capture finished (None = nobody spoke, or it was cancelled)
    """

    def __init__(self, transcriber: Transcriber, wake_allowed: Callable[[], bool],
                 on_wake: Callable[[int], bool], on_heard: Callable[[int, np.ndarray | None], None]):
        self.transcriber = transcriber
        self.wake_allowed, self.on_wake, self.on_heard = wake_allowed, on_wake, on_heard
        self.phrase = wakephrase.DEFAULT_PHRASE
        self.sensitivity = 0.5
        self.wake_enabled = False
        self.on_test: Callable[[str, float], None] | None = None  # set while Settings is testing a phrase
        self.test_phrase = ""
        self._q: queue.Queue = queue.Queue(maxsize=400)  # ~25 s of mic blocks
        self._lock = threading.Lock()
        self._token = 0
        self._capture: _Capture | None = None
        self._mute_until = 0.0  # Jev's own chime is playing: don't take it for speech
        self._pending = np.zeros(0, np.float32)
        self._gate = SpeechGate()
        self._spot = Utterance(end_silence_s=0.5)
        self._spot_checked = False
        threading.Thread(target=self._run, daemon=True, name="listener").start()

    # ---- called from other threads ----

    def feed(self, chunk: np.ndarray, rate: int) -> None:
        """A block from the mic (Recorder's audio callback: must return at once)."""
        if not (self.wake_enabled or self.on_test or self._capture):
            return
        try:
            self._q.put_nowait((chunk, rate))
        except queue.Full:
            pass  # the listener is stuck transcribing; dropping audio beats growing without bound

    def capture(self, timeout_s: float, delay_s: float = 0.0) -> int:
        """Start recording one utterance hands-free; on_heard(token, …) gets the result. Returns the token."""
        with self._lock:
            self._token += 1
            self._capture = _Capture(self._token, timeout_s, time.monotonic() + delay_s)
            return self._token

    def cancel(self) -> None:
        """Drop any capture in progress (on_heard isn't called for it)."""
        with self._lock:
            self._token += 1
            self._capture = None

    def mute(self, seconds: float) -> None:
        """Ignore the mic for a moment (one of Jev's chimes is playing). Speech already under way carries on."""
        self._mute_until = time.monotonic() + seconds

    @property
    def capturing(self) -> bool:
        return self._capture is not None

    # ---- the listener thread ----

    def _run(self) -> None:
        while True:
            chunk, rate = self._q.get()
            if rate != RATE and len(chunk):
                n = max(1, round(len(chunk) * RATE / rate))
                chunk = np.interp(np.linspace(0, len(chunk) - 1, n), np.arange(len(chunk)), chunk).astype(np.float32)
            audio = np.concatenate([self._pending, chunk])
            whole = len(audio) // FRAME * FRAME
            self._pending = audio[whole:]
            for i in range(0, whole, FRAME):
                try:
                    self._frame(audio[i:i + FRAME])
                except Exception:
                    log.exception("Hands-free listening failed on a frame")

    def _frame(self, frame: np.ndarray) -> None:
        speech = self._gate(frame)
        with self._lock:
            cap = self._capture
        if time.monotonic() < self._mute_until:
            if cap is not None and cap.utt.started:
                cap.utt.push(frame, False)  # keep the words said over the chime, but it can't end the utterance
            return
        if cap is not None:
            self._capture_frame(cap, frame, speech)
            return
        testing = self.on_test is not None
        if not (testing or (self.wake_enabled and self.wake_allowed())):
            self._spot, self._spot_checked = Utterance(end_silence_s=0.5), False
            return
        event = self._spot.push(frame, speech)
        if self._spot.started and not self._spot_checked and (event == "ended" or self._spot.seconds >= SPOT_WINDOW_S):
            self._spot_checked = True
            if self._spot.seconds >= SPOT_MIN_S:
                self._check(self._spot, ended=event == "ended", testing=testing)
                if self._capture is not None:
                    return  # the phrase was heard: this stretch of speech now belongs to the capture
        if event == "ended" or self._spot.seconds > MAX_CAPTURE_S:  # a long talk (a podcast…) isn't kept
            self._spot, self._spot_checked = Utterance(end_silence_s=0.5), False

    def _check(self, utt: Utterance, ended: bool, testing: bool) -> None:
        audio = utt.audio()[: round(SPOT_WINDOW_S * RATE)]
        heard = self.transcriber.heard(audio)
        phrase = self.test_phrase if testing else self.phrase
        m = wakephrase.match(phrase, heard)
        hit = m.score >= wakephrase.threshold(self.sensitivity)
        if heard:
            log.debug("Heard %r (wake score %.2f%s)", heard, m.score, ", hit" if hit else "")
        if testing:
            if heard and self.on_test:
                self.on_test(heard, m.score)  # Settings judges the score against its own slider
            return
        if not hit or not self.wake_allowed():
            return
        log.info("Wake phrase heard: %r (score %.2f)", heard, m.score)
        with self._lock:
            self._token += 1
            token = self._token
        if ended and not m.rest:
            # Just the phrase, then a pause: wait for the command, keeping the phrase so Whisper hears it all.
            cap = _Capture(token, WAKE_FOLLOW_S, 0.0, prefix=utt.audio())
        else:
            # Still talking, or the command came in the same breath: carry on with this stretch of speech.
            cap = _Capture(token, WAKE_FOLLOW_S, 0.0, utt=self._continue(utt))
            cap.began = time.monotonic()
        with self._lock:
            self._capture = cap
        if not self.on_wake(token):
            self.cancel()
            return
        if ended and m.rest:
            self._finish(cap, cap.utt.audio())  # "Hey Jev, open Steam" then quiet: that's the whole command
        self._spot, self._spot_checked = Utterance(end_silence_s=0.5), False

    @staticmethod
    def _continue(spot: Utterance) -> Utterance:
        """A capture-length utterance that carries on from a spotting one already under way."""
        utt = Utterance()
        utt.started, utt.frames = True, list(spot.frames)
        return utt

    def _capture_frame(self, cap: _Capture, frame: np.ndarray, speech: bool) -> None:
        now = time.monotonic()
        if now < cap.not_before:
            return
        if cap.began is None:
            cap.began = now
        event = cap.utt.push(frame, speech)
        if not cap.utt.started:
            if now - cap.began > cap.timeout_s:
                self._finish(cap, None)
            return
        if event == "ended" or cap.utt.seconds >= MAX_CAPTURE_S:
            audio = cap.utt.audio()
            if len(cap.prefix):
                audio = np.concatenate([cap.prefix, np.zeros(round(0.2 * RATE), np.float32), audio])
            self._finish(cap, audio)

    def _finish(self, cap: _Capture, audio: np.ndarray | None) -> None:
        with self._lock:
            if self._capture is not cap:
                return  # cancelled meanwhile
            self._capture = None
        self.on_heard(cap.token, audio)
