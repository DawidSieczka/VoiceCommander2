"""Per-dictation latency instrumentation.

Emits exactly one summary line per dictation on logger "timing", in the fixed
format defined by specs/001-low-latency-dictation/contracts/log-summary-format.md:

DICTATION mode=<mode> outcome=<outcome> total_ms=<int> stt_ms=<int>
eager_stt_ms=<int> corr_ms=<int> inject_ms=<int> eager=<0|1> fastinj=<0|1> overlap=<0|1>

STT time recorded before mark_released() counts as eager_stt_ms (work done while
the key was still held); after it, as stt_ms. total_ms spans release -> conclude,
so in per_sentence mode stage sums may exceed total_ms (hold-time work counts in
stages). Durations only — never dictated text (constitution I).
"""
from __future__ import annotations

import logging
import threading
import time

from config import PerfSnapshot

log = logging.getLogger("timing")

OUTCOMES = ("injected", "no_speech", "refused_focus", "refused_password", "error")


class DictationTiming:
    """Created at PTT press; concluded exactly once (FR-011/FR-012)."""

    def __init__(self, snapshot: PerfSnapshot):
        self.snapshot = snapshot
        self.released_at: float | None = None
        self.eager_stt_ms = 0
        self.stt_ms = 0
        self.correction_ms = 0
        self.injection_ms = 0
        self._concluded = False
        self._lock = threading.Lock()

    def mark_released(self) -> None:
        """Stamp t0 for release->text at the moment the PTT key goes up."""
        with self._lock:
            if self.released_at is None:
                self.released_at = time.monotonic()

    def add_stt(self, seconds: float) -> None:
        with self._lock:
            if self.released_at is None:
                self.eager_stt_ms += int(seconds * 1000)
            else:
                self.stt_ms += int(seconds * 1000)

    def add_correction(self, seconds: float) -> None:
        with self._lock:
            self.correction_ms += int(seconds * 1000)

    def add_injection(self, seconds: float) -> None:
        with self._lock:
            self.injection_ms += int(seconds * 1000)

    def conclude(self, outcome: str) -> None:
        """Emit the summary line; repeated calls are no-ops (exactly one line)."""
        if outcome not in OUTCOMES:
            outcome = "error"
        with self._lock:
            if self._concluded:
                log.debug("conclude(%s) after already concluded — ignored", outcome)
                return
            self._concluded = True
            released = self.released_at if self.released_at is not None else time.monotonic()
            total_ms = int((time.monotonic() - released) * 1000)
            snap = self.snapshot
        log.info(
            "DICTATION mode=%s outcome=%s total_ms=%d stt_ms=%d eager_stt_ms=%d "
            "corr_ms=%d inject_ms=%d eager=%d fastinj=%d overlap=%d",
            snap.mode, outcome, total_ms, self.stt_ms, self.eager_stt_ms,
            self.correction_ms, self.injection_ms,
            int(snap.eager_stt), int(snap.fast_injection), int(snap.pipelined_correction),
        )
