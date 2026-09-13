"""Shared fakes for pipeline tests — no microphone, GPU, Ollama, or real
keyboard/clipboard access (constitution testing gate)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import AppConfig  # noqa: E402


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def frame(value: int = 0, samples: int = 512) -> np.ndarray:
    return np.full(samples, value, dtype=np.int16)


class FakeTranscriber:
    """Returns queued texts in order; '' when exhausted."""

    def __init__(self, texts: Optional[list[str]] = None, delay: float = 0.0):
        self.texts = list(texts or [])
        self.delay = delay
        self.calls: list[int] = []          # audio lengths seen
        self.call_threads: list[str] = []
        self.ready = True

    def transcribe(self, audio) -> str:
        self.calls.append(len(audio))
        self.call_threads.append(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        return self.texts.pop(0) if self.texts else ""


class FakeCorrector:
    """Mimics the real Corrector contract: correct() never raises by default."""

    def __init__(self, available: bool = True, delays: Optional[list[float]] = None,
                 transform=lambda t: t.upper(), raise_on: Optional[set[int]] = None):
        self.available = available
        self.delays = list(delays or [])
        self.transform = transform
        self.raise_on = raise_on or set()
        self.calls: list[str] = []

    def correct(self, text: str, timeout_s: float, context=None) -> str:
        n = len(self.calls)
        self.calls.append(text)
        if self.delays:
            time.sleep(self.delays.pop(0))
        if n in self.raise_on:
            raise RuntimeError("simulated corrector crash")
        return self.transform(text)


class FakeInjector:
    def __init__(self, refuse: Optional[str] = None):
        self.refuse = refuse                 # None | "refused_focus" | "refused_password"
        self.calls: list[tuple] = []         # (text, target, kwargs)
        self.typed: list[str] = []

    def inject(self, text, target_hwnd=None, **kwargs):
        self.calls.append((text, target_hwnd, kwargs))
        if self.refuse:
            return False, self.refuse
        return True, None

    def type_text(self, text):
        self.typed.append(text)


class FakeMic:
    """Stands in for MicCapture: never touches PortAudio."""

    def __init__(self, on_frame, get_device_name=lambda: ""):
        self.on_frame = on_frame
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class FakeSegmenter:
    """Programmable segment source implementing the Segmenter surface."""

    def __init__(self, cfg, on_segment):
        self.on_segment = on_segment
        self.fed: list[tuple[np.ndarray, str]] = []   # (frame, thread name)
        self.flush_emits: list[np.ndarray] = []       # audio emitted on flush
        self.resets = 0

    def feed(self, fr):
        self.fed.append((fr, threading.current_thread().name))

    def flush(self):
        from vad import Segment
        for audio in self.flush_emits:
            self.on_segment(Segment(audio=audio, final=True))
        self.flush_emits = []

    def reset(self):
        self.resets += 1

    def emit(self, audio, final=False, forced=False):
        from vad import Segment
        self.on_segment(Segment(audio=audio, final=final, forced_cut=forced))


class FakeStreaming:
    def __init__(self, words: int = 3):
        self.words = words
        self.started = 0
        self.ended = 0
        self.fed = 0
        self.timing = None

    def start_utterance(self, timing=None):
        self.started += 1
        self.timing = timing

    def feed(self, fr):
        self.fed += 1

    def end_utterance(self):
        self.ended += 1
        return self.words


def base_cfg(**overrides) -> AppConfig:
    cfg = AppConfig(mic_always_open=False, ai_correction=False)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def make_pipeline(monkeypatch):
    """Builds a Pipeline with all hardware surfaces faked; yields helpers."""
    import pipeline as pl

    built = []

    def build(cfg=None, transcriber=None, corrector=None, injector=None):
        cfg = cfg or base_cfg()
        monkeypatch.setattr(pl, "MicCapture", FakeMic)
        monkeypatch.setattr(pl, "Segmenter", FakeSegmenter)
        monkeypatch.setattr(pl, "focused_window", lambda: 777)
        transcriber = transcriber or FakeTranscriber()
        corrector = corrector or FakeCorrector(available=False)
        injector = injector or FakeInjector()
        p = pl.Pipeline(cfg, transcriber, corrector, injector, lambda s: None)
        p._streaming = FakeStreaming()
        built.append(p)
        return p, transcriber, corrector, injector

    yield build
    for p in built:
        p.shutdown()
        time.sleep(0.05)
