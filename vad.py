"""Silero VAD wrapper + hysteresis segmenter state machine (params proven in v1)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from pysilero_vad import SileroVoiceActivityDetector

from audio import FRAME_SAMPLES, SAMPLE_RATE
from config import AppConfig

log = logging.getLogger("vad")

FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE  # 32


@dataclass
class Segment:
    audio: np.ndarray          # int16 mono 16 kHz
    forced_cut: bool = False   # cut at max length, not at silence
    final: bool = False        # last segment of the utterance (PTT released)


class Segmenter:
    """Feeds 32 ms frames; emits Segment via callback at sentence boundaries.

    Used by mode "per_sentence". Also reusable as a pure "was there speech?"
    check for mode "on_release" via speech_ratio().
    """

    def __init__(self, cfg: AppConfig, on_segment: Callable[[Segment], None]):
        self._cfg = cfg
        self._on_segment = on_segment
        self._vad = SileroVoiceActivityDetector()
        self._reset()

    def _reset(self) -> None:
        self._in_speech = False
        self._silence_ms = 0
        self._speech_ms = 0
        self._buffer: list[np.ndarray] = []
        self._preroll: list[np.ndarray] = []  # padding before speech start

    def feed(self, frame: np.ndarray) -> None:
        cfg = self._cfg
        prob = self._vad(frame.tobytes())
        if not self._in_speech:
            self._preroll.append(frame)
            max_pre = max(1, cfg.vad_padding_ms // FRAME_MS)
            if len(self._preroll) > max_pre:
                self._preroll.pop(0)
            if prob >= cfg.vad_start_threshold:
                self._in_speech = True
                self._speech_ms = FRAME_MS
                self._silence_ms = 0
                self._buffer = list(self._preroll)
        else:
            self._buffer.append(frame)
            if prob >= cfg.vad_continue_threshold:
                self._speech_ms += FRAME_MS
                self._silence_ms = 0
            else:
                self._silence_ms += FRAME_MS
                if self._silence_ms >= cfg.vad_end_silence_ms:
                    self._emit(forced=False)
                    return
            if len(self._buffer) * FRAME_MS >= cfg.vad_max_segment_s * 1000:
                self._emit(forced=True)

    def flush(self) -> None:
        """PTT released: emit whatever speech is pending."""
        if self._in_speech and self._speech_ms >= self._cfg.vad_min_speech_ms:
            self._emit(forced=False, final=True)
        else:
            self._reset()

    def _emit(self, forced: bool, final: bool = False) -> None:
        if self._speech_ms < self._cfg.vad_min_speech_ms:
            log.debug("segment too short (%d ms) — dropped", self._speech_ms)
            self._reset()
            return
        audio = np.concatenate(self._buffer)
        log.info("segment: %.1f s (forced=%s final=%s)", len(audio) / SAMPLE_RATE, forced, final)
        self._reset()
        self._on_segment(Segment(audio=audio, forced_cut=forced, final=final))


def has_speech(audio: np.ndarray, min_speech_ms: int = 300) -> bool:
    """Standalone check for mode on_release: don't transcribe pure silence
    (Whisper hallucinates on it — v1 lesson)."""
    vad = SileroVoiceActivityDetector()
    speech_ms = 0
    for i in range(0, len(audio) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        if vad(audio[i:i + FRAME_SAMPLES].tobytes()) >= 0.5:
            speech_ms += FRAME_MS
            if speech_ms >= min_speech_ms:
                return True
    return False
