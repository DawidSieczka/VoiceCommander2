"""Mode 1 (realtime): LocalAgreement-2 streaming over the shared faster-whisper model.

Every `realtime_interval_s` the uncommitted audio window is re-transcribed;
the longest common word-prefix of the last two hypotheses is committed and
newly committed words are typed via SendInput Unicode.

Critique note: with `small` int8 on this CPU a pass over a long window may
exceed the tick — words then arrive in bursts (graceful degradation). RTF is
logged per pass; if it is consistently > ~1, switch stt_model to "base".
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from audio import SAMPLE_RATE
from config import AppConfig
from injector import Injector
from stt import Transcriber
from timing import DictationTiming

log = logging.getLogger("streaming")


class StreamingWorker:
    def __init__(self, cfg: AppConfig, transcriber: Transcriber, injector: Injector):
        self._cfg = cfg
        self._stt = transcriber
        self._inj = injector
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()
        self._active = False
        self._committed: list[str] = []
        self._prev_hyp: list[str] = []
        self._thread: threading.Thread | None = None
        self._timing: Optional[DictationTiming] = None

    def start_utterance(self, timing: Optional[DictationTiming] = None) -> None:
        with self._lock:
            self._buf = np.zeros(0, dtype=np.int16)
            self._committed = []
            self._prev_hyp = []
            self._active = True
            self._timing = timing
        self._thread = threading.Thread(target=self._loop, name="streaming", daemon=True)
        self._thread.start()

    def feed(self, frame: np.ndarray) -> None:
        with self._lock:
            if self._active:
                self._buf = np.concatenate([self._buf, frame])

    def end_utterance(self) -> int:
        """Blocks until the final pass typed the tail; returns committed words."""
        with self._lock:
            self._active = False
        if self._thread:
            self._thread.join(timeout=30)
            self._thread = None
        return len(self._committed)

    def _type(self, words: list[str]) -> None:
        t0 = time.perf_counter()
        self._inj.type_text(" ".join(words) + " ")
        if self._timing:
            self._timing.add_injection(time.perf_counter() - t0)

    def _loop(self) -> None:
        interval = self._cfg.realtime_interval_s
        while True:
            time.sleep(interval)
            with self._lock:
                active = self._active
                audio = self._buf.copy()
            if len(audio) < SAMPLE_RATE // 2:  # <0.5 s — nothing to do yet
                if not active:
                    break
                continue

            held = self._timing.released_at is None if self._timing else False
            t0 = time.perf_counter()
            words = self._stt.transcribe(audio).split()
            dt = time.perf_counter() - t0
            if self._timing:
                self._timing.add_stt(dt, held=held)
            rtf = dt / (len(audio) / SAMPLE_RATE)
            if rtf > 1.0:
                log.warning("streaming pass RTF %.2f > 1 — consider stt_model=base for realtime", rtf)

            if not active:
                # Final pass: type everything not yet committed, then stop.
                tail = words[len(self._committed):]
                if tail:
                    self._type(tail)
                    self._committed.extend(tail)
                break

            # LocalAgreement-2: commit the common prefix of the two last hypotheses.
            agree = 0
            for a, b in zip(words, self._prev_hyp):
                if a == b:
                    agree += 1
                else:
                    break
            self._prev_hyp = words
            new = words[len(self._committed):agree]
            if new:
                self._type(new)
                self._committed.extend(new)

            # Trim the window when it grows past the cap: drop already-committed
            # leading audio proportionally (approximation without word timestamps).
            max_samples = int(self._cfg.realtime_window_max_s * SAMPLE_RATE)
            with self._lock:
                if len(self._buf) > max_samples and self._committed:
                    drop = len(self._buf) - max_samples
                    self._buf = self._buf[drop:]
                    # Committed words for dropped audio are approximated as all of them
                    # minus what the remaining window can still contain; safest is to
                    # reset agreement so we do not re-type old words.
                    self._prev_hyp = []
                    frac = len(self._committed) * drop // max(len(audio), 1)
                    self._committed = self._committed[frac:] if frac < len(self._committed) else []
        log.info("streaming utterance done: %d words", len(self._committed))
