"""Speech to text with faster-whisper, running locally on CPU inside the container.
Nothing spoken leaves the machine. The model is downloaded once into /data/whisper."""
from __future__ import annotations

import importlib.util
import io
import threading
from pathlib import Path


class Transcriber:
    def __init__(self, model_name: str, cache_dir: Path):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def transcribe(self, audio: bytes) -> str:
        """Blocking; call from a worker thread. Accepts whatever the browser recorded (webm/ogg/mp4)."""
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                           download_root=str(self.cache_dir))
            segments, _info = self._model.transcribe(io.BytesIO(audio), vad_filter=True)
            return " ".join(s.text.strip() for s in segments).strip()
