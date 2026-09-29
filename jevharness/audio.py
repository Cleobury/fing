"""Microphone capture for push-to-talk.

The input stream stays open so recording starts instantly on key-down, and a
short pre-roll buffer keeps the first syllable that is spoken as the key goes down.
"""

import collections
import logging
import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # what Whisper expects
_BLOCK = 1024

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, preroll_s: float = 0.3):
        self._lock = threading.Lock()
        self._recording = False
        self._chunks: list[np.ndarray] = []
        try:
            self._stream = self._open(SAMPLE_RATE)
        except sd.PortAudioError:
            # Some devices refuse 16 kHz; record at their native rate and resample on stop().
            rate = int(sd.query_devices(kind="input")["default_samplerate"])
            log.info("Mic does not support 16 kHz; recording at %d Hz", rate)
            self._stream = self._open(rate)
        self._rate = int(self._stream.samplerate)
        self._preroll = collections.deque(maxlen=max(1, round(preroll_s * self._rate / _BLOCK)))
        self._stream.start()

    def _open(self, rate: int) -> sd.InputStream:
        return sd.InputStream(samplerate=rate, channels=1, dtype="float32", blocksize=_BLOCK, callback=self._on_audio)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        chunk = indata[:, 0].copy()
        with self._lock:
            (self._chunks if self._recording else self._preroll).append(chunk)

    def start(self) -> None:
        with self._lock:
            self._chunks = list(self._preroll)
            self._recording = True

    def stop(self) -> np.ndarray:
        """Stop recording and return mono float32 audio at 16 kHz."""
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
            self._preroll.clear()
        if not chunks:
            return np.zeros(0, np.float32)
        audio = np.concatenate(chunks)
        if self._rate != SAMPLE_RATE:
            n = round(len(audio) * SAMPLE_RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
