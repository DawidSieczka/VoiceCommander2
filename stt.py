"""faster-whisper wrapper with v1's hallucination guards."""
from __future__ import annotations

import logging
import os
import time
import unicodedata
from pathlib import Path
from typing import Optional

import numpy as np

from config import APPDATA_DIR, AppConfig, MODELS_DIR

log = logging.getLogger("stt")

_BLOCKLIST_DIR = Path(__file__).parent / "assets"
DICTIONARY_PATH = APPDATA_DIR / "dictionary.txt"


def user_dictionary() -> list[str]:
    """User's vocabulary hints (proper nouns, jargon), one per line."""
    try:
        lines = DICTIONARY_PATH.read_text(encoding="utf-8").splitlines()
        return [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        return []


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.replace("ł", "l").split())


class Transcriber:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self._model = None
        self._blocklists: dict[str, list[str]] = {}
        self.loaded_model_name: Optional[str] = None
        self._loading = False

    def reload_if_changed(self) -> None:
        """Swap the model in the background when cfg.stt_model changed (tray switch).
        The old model keeps serving until the new one is ready."""
        import threading

        if self._loading or self.loaded_model_name == self._cfg.stt_model:
            return
        self._loading = True

        def _do():
            try:
                self.load()
            except Exception:
                log.exception("model reload failed; keeping %s", self.loaded_model_name)
            finally:
                self._loading = False

        threading.Thread(target=_do, name="model-reload", daemon=True).start()

    @staticmethod
    def _add_cuda_dll_dirs() -> None:
        """Make pip-installed cuBLAS/cuDNN DLLs visible to ctranslate2.

        add_dll_directory alone is not always honored for transitively loaded
        CUDA libs, so the directories are also prepended to PATH.
        """
        import sys
        nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        added = []
        for sub in ("cublas", "cudnn", "cuda_nvrtc"):
            p = nvidia_root / sub / "bin"
            if p.is_dir():
                os.add_dll_directory(str(p))
                added.append(str(p))
        if added:
            os.environ["PATH"] = os.pathsep.join(added + [os.environ.get("PATH", "")])
            log.info("CUDA DLL dirs added: %s", added)
        else:
            log.warning("no NVIDIA DLL dirs found under %s", nvidia_root)

    def _build(self, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        cpu_threads = max(4, (os.cpu_count() or 4) - 2)
        kwargs = dict(device=device, compute_type=compute_type,
                      cpu_threads=cpu_threads, download_root=str(MODELS_DIR))
        try:
            # Fully offline when the model is already cached (nothing leaves the laptop).
            return WhisperModel(self._cfg.stt_model, local_files_only=True, **kwargs)
        except Exception:
            log.info("model not cached yet — downloading (one-time network use)")
            return WhisperModel(self._cfg.stt_model, **kwargs)

    def load(self) -> None:
        import numpy as _np

        # Guards reload_if_changed(): a tray click during the initial ~40 s load
        # must not spawn a second concurrent load of the same model.
        self._loading = True
        try:
            t0 = time.perf_counter()
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            device, compute = self._cfg.stt_device, self._cfg.stt_compute_type
            if device == "cuda":
                self._add_cuda_dll_dirs()
            try:
                model = self._build(device, compute)
                # Warm-up inference: catches CUDA OOM/driver failures at load time
                # instead of losing the user's first utterance (v1 lesson: never
                # trust a GPU model until it actually ran).
                list(model.transcribe(_np.zeros(8000, dtype=_np.float32),
                                      language=self._cfg.language, beam_size=1)[0])
            except Exception:
                if device == "cuda":
                    log.exception("CUDA load/warm-up failed — falling back to CPU int8")
                    device, compute = "cpu", "int8"
                    model = self._build(device, compute)
                else:
                    raise
            self._model = model
            self.active_device = device
            self.loaded_model_name = self._cfg.stt_model
            log.info("model %s (%s/%s) loaded+warmed in %.1f s",
                     self._cfg.stt_model, device, compute, time.perf_counter() - t0)
        finally:
            self._loading = False

    @property
    def ready(self) -> bool:
        return self._model is not None

    def _blocklist(self, lang: str) -> list[str]:
        if lang not in self._blocklists:
            path = _BLOCKLIST_DIR / f"blocklist_{lang}.txt"
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
                self._blocklists[lang] = [_normalize(l) for l in lines if l.strip() and not l.startswith("#")]
            except FileNotFoundError:
                self._blocklists[lang] = []
        return self._blocklists[lang]

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: int16 mono 16 kHz. Returns '' when rejected as hallucination/silence."""
        if self._model is None:
            raise RuntimeError("model not loaded")
        cfg = self._cfg
        samples = audio.astype(np.float32) / 32768.0
        t0 = time.perf_counter()
        # Bias recognition toward the user's vocabulary (anglicisms, jargon,
        # proper nouns) — mitigates e.g. "speech-to-text" -> "spić tu tekst".
        vocab = user_dictionary()
        hotwords = ", ".join(vocab) if vocab else None
        segments, info = self._model.transcribe(
            samples,
            language=cfg.language,           # explicit, never auto (short utterances flap)
            beam_size=cfg.beam_size,
            condition_on_previous_text=False,
            vad_filter=False,                # our own VAD runs upstream
            hotwords=hotwords,
        )
        parts: list[str] = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            if seg.no_speech_prob > 0.6:
                log.info("rejected (no_speech_prob=%.2f): %r", seg.no_speech_prob, text)
                continue
            if seg.avg_logprob < -1.2:
                log.info("rejected (avg_logprob=%.2f): %r", seg.avg_logprob, text)
                continue
            norm = _normalize(text)
            if any(b in norm for b in self._blocklist(cfg.language)):
                log.info("rejected (blocklist): %r", text)
                continue
            parts.append(text)
        result = " ".join(parts).strip()
        dt = time.perf_counter() - t0
        log.info("STT %.1f s audio -> %.1f s (RTF %.2f): %r",
                 len(audio) / 16000, dt, dt / max(len(audio) / 16000, 0.01), result[:120])
        return result
