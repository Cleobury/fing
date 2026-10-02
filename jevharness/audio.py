"""Microphone capture for push-to-talk.

The input stream stays open so recording starts instantly on key-down, and a
short pre-roll buffer keeps the first syllable that is spoken as the key goes down.

An always-open stream doesn't survive the PC sleeping (or the mic being unplugged):
Windows tears down the audio endpoint, and PortAudio's callback either stops without
an error or keeps delivering digital silence. A watchdog thread reopens the stream
when the callback goes quiet, after a resume, or when a recording comes back as pure
zeros, re-scanning the devices so a mic that came back under a new handle is picked up.

The mic is chosen by name, not index: PortAudio renumbers the devices every time it
re-scans, and a name still finds the same mic after a resume or a replug.
"""

import collections
import logging
import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects
_BLOCK = 1024
_STALL_S = 1.0  # no callback for this long means the stream is dead (blocks arrive every ~64 ms)
_WATCH_S = 0.5  # how often the watchdog checks
_RESUME_GAP_S = 5.0  # the wall clock jumping this far between checks means the PC was asleep

log = logging.getLogger(__name__)

# PortAudio is restarted on reconnect; listing devices from another thread meanwhile could crash.
_pa_lock = threading.Lock()


def _input_names() -> list[str]:
    """Input device names on the default host API (MME on Windows), which the system default also uses;
    the other APIs list the same mics again under slightly different names. MME's Sound Mapper is left
    out: it just follows the Windows default, which "System default" already covers."""
    hostapi = sd.default.hostapi
    names: list[str] = []
    for d in sd.query_devices():
        if (d["max_input_channels"] > 0 and d["hostapi"] == hostapi and d["name"] not in names
                and not d["name"].startswith("Microsoft Sound Mapper")):
            names.append(d["name"])
    return names


def input_devices() -> list[str]:
    """Names of the microphones the user can pick in Settings."""
    with _pa_lock:
        return _input_names()


class Recorder:
    def __init__(self, device: str = "", preroll_s: float = 0.3):
        self._device = device  # input device name; blank = the system default
        self._missing_logged = False
        self._preroll_s = preroll_s
        self._lock = threading.Lock()
        self._recording = False
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._last_audio_t = 0.0
        self._closed = threading.Event()
        self._reopen_now = threading.Event()
        self._force_reopen = False
        self._connect()
        threading.Thread(target=self._watch, daemon=True, name="mic-watchdog").start()

    def set_device(self, device: str) -> None:
        """Switch to another input device by name (blank = the system default)."""
        if device == self._device:
            return
        self._device = device
        self._missing_logged = False
        self._force_reopen = True
        self._reopen_now.set()

    def _find_device(self) -> int | None:
        """Index of the chosen mic, or None for the system default (also when the chosen one isn't connected)."""
        name = self._device
        if not name:
            return None
        hostapi = sd.default.hostapi
        matches = [i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and d["name"] == name]
        same_api = [i for i in matches if sd.query_devices(i)["hostapi"] == hostapi]
        if same_api or matches:
            self._missing_logged = False
            return (same_api or matches)[0]
        if not self._missing_logged:
            log.warning("Microphone %r isn't connected; using the system default", name)
            self._missing_logged = True
        return None

    def _open(self, device: int | None, rate: int) -> sd.InputStream:
        return sd.InputStream(device=device, samplerate=rate, channels=1, dtype="float32", blocksize=_BLOCK,
                              callback=self._on_audio)

    def _connect(self) -> None:
        """Open and start a stream on the chosen input device, or the system default."""
        with _pa_lock:
            device = self._find_device()
        try:
            stream = self._open(device, SAMPLE_RATE)
        except sd.PortAudioError:
            # Some devices refuse 16 kHz; record at their native rate and resample on stop().
            rate = int(sd.query_devices(device, "input")["default_samplerate"])
            log.info("Mic does not support 16 kHz; recording at %d Hz", rate)
            stream = self._open(device, rate)
        rate = int(stream.samplerate)
        with self._lock:
            self._stream = stream
            self._rate = rate
            self._preroll = collections.deque(maxlen=max(1, round(self._preroll_s * rate / _BLOCK)))
            # Audio already recorded at another rate can't be mixed with this stream's.
            self._chunks = []
            self._last_audio_t = time.monotonic()
        stream.start()

    def _reconnect(self) -> None:
        old, self._stream = self._stream, None
        if old is not None:
            try:
                old.abort()
                old.close()
            except Exception:
                log.debug("Closing the dead mic stream failed", exc_info=True)
        # Restart PortAudio so it re-enumerates devices; the old device handles are stale after resume.
        with _pa_lock:
            sd._terminate()
            sd._initialize()
        self._connect()

    def _healthy(self) -> bool:
        stream = self._stream
        return (stream is not None and stream.active
                and time.monotonic() - self._last_audio_t < _STALL_S)

    def _watch(self) -> None:
        failing = False
        last_tick = time.time()
        while not self._closed.is_set():
            self._reopen_now.wait(_WATCH_S)
            self._reopen_now.clear()
            now = time.time()
            if now - last_tick > _RESUME_GAP_S:
                log.info("Woke from sleep; reopening the microphone")
                self._force_reopen = True
            last_tick = now
            if self._closed.is_set() or (self._healthy() and not self._force_reopen):
                continue
            try:
                self._force_reopen = False
                self._reconnect()
                log.info("Microphone stream reopened")
                failing = False
            except Exception:
                # No mic yet (still waking up, or unplugged): keep trying, but log it once.
                if not failing:
                    log.exception("Could not reopen the microphone; retrying")
                failing = True
                time.sleep(2)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        chunk = indata[:, 0].copy()
        with self._lock:
            self._last_audio_t = time.monotonic()
            (self._chunks if self._recording else self._preroll).append(chunk)

    def start(self) -> None:
        healthy = self._healthy()
        with self._lock:
            # A dead stream's pre-roll is from before it died, not from just now.
            self._chunks = list(self._preroll) if healthy else []
            self._recording = True
        if not healthy:
            # Don't wait for the next watchdog tick: reopen now so the rest of this press is captured.
            self._reopen_now.set()

    def stop(self) -> np.ndarray:
        """Stop recording and return mono float32 audio at 16 kHz."""
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
            self._preroll.clear()
            rate = self._rate
        if not chunks:
            return np.zeros(0, np.float32)
        audio = np.concatenate(chunks)
        if not audio.any():
            # A live mic is never exactly zero; a stream left over from before sleep can be.
            log.info("Recording was pure digital silence; reopening the microphone")
            self._force_reopen = True
            self._reopen_now.set()
        if rate != SAMPLE_RATE:
            n = round(len(audio) * SAMPLE_RATE / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio

    def close(self) -> None:
        self._closed.set()
        self._reopen_now.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.debug("Closing the mic stream failed", exc_info=True)
