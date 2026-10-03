"""Short chimes for the mic opening and closing, and for a question.

They're synthesised once (soft bell-like notes: a sine with a little of its overtones, a quick attack and a
gentle decay) and saved as WAV files next to the settings, because winsound can only play a sound in the
background from a file.
"""

from __future__ import annotations

import io
import logging
import os
import wave

import numpy as np

log = logging.getLogger(__name__)

RATE = 44100
LENGTH_S = 0.4  # the longest chime; the hands-free listener ignores the mic this long after one plays
_VERSION = 1  # bump to regenerate the files after changing the notes

# name -> [(start s, frequency Hz, loudness)]
CHIMES = {
    "open": [(0.0, 1046.5, 0.8), (0.07, 1568.0, 1.0)],  # C6 → G6, rising: listening
    "close": [(0.0, 1568.0, 0.7), (0.07, 1046.5, 0.6)],  # G6 → C6, falling: done listening
    "question": [(0.0, 880.0, 0.8), (0.09, 1318.5, 0.9)],  # A5 → E6: Jev has a question
}


def _note(freq: float, seconds: float) -> np.ndarray:
    t = np.arange(round(seconds * RATE)) / RATE
    tone = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t) + 0.08 * np.sin(6 * np.pi * freq * t)
    attack = np.minimum(1.0, t / 0.006)
    return tone * attack * np.exp(-t / 0.075)


def render(name: str) -> np.ndarray:
    """The chime as float samples in -1..1."""
    out = np.zeros(round(LENGTH_S * RATE))
    for start, freq, loud in CHIMES[name]:
        note = _note(freq, LENGTH_S - start) * loud
        i = round(start * RATE)
        out[i:i + len(note)] += note[: len(out) - i]
    return out / np.abs(out).max() * 0.3


def wav_bytes(name: str) -> bytes:
    samples = (render(name) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def _path(name: str) -> str:
    from .settings import DATA_DIR

    folder = os.path.join(DATA_DIR, "sounds")
    path = os.path.join(folder, f"{name}-v{_VERSION}.wav")
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as f:
            f.write(wav_bytes(name))
    return path


def play(name: str) -> None:
    """Play a chime in the background (a new one cuts off the one playing)."""
    try:
        import winsound

        winsound.PlaySound(_path(name), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        log.debug("Couldn't play the %s chime", name, exc_info=True)
