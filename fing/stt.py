"""Speech-to-text with faster-whisper on the NVIDIA GPU."""

import glob
import logging
import os
import sys

import numpy as np

log = logging.getLogger(__name__)


def add_cuda_dll_dirs() -> None:
    """Expose the pip-installed cuBLAS/cuDNN DLLs (nvidia-*-cu12 wheels) to CTranslate2.

    Must run before faster_whisper / ctranslate2 is imported.
    """
    for d in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    def __init__(self, model_name: str, language: str):
        self.model_name = model_name
        self.language = language
        self.model = None
        self.device = None

    def load(self) -> None:
        from faster_whisper import WhisperModel

        try:
            self.model = WhisperModel(self.model_name, device="cuda", compute_type="float16")
            self.device = "cuda"
        except Exception:
            log.exception("CUDA load failed; falling back to CPU (much slower)")
            self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self.device = "cpu"
        # Warm-up so the first real command doesn't pay for kernel initialisation.
        self.transcribe(np.zeros(16000, np.float32))
        log.info("Whisper %s loaded on %s", self.model_name, self.device)

    def transcribe(self, audio: np.ndarray, hint_words: list[str] = ()) -> str:
        # Seeding the prompt with on-screen words biases recognition towards names
        # the user is likely to say ("click Downloads", "open Spotify").
        prompt = None
        if hint_words:
            prompt = "Voice command to control a computer. On screen: " + ", ".join(hint_words) + "."
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def heard(self, audio: np.ndarray) -> str:
        """A quick transcription of a short stretch of speech, for spotting the wake phrase. No prompt: one
        naming the phrase would make Whisper "hear" it in any noise."""
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
