"""Mode dispatch and the processing pipeline.

Thread layout (constitution III: the capture path stays trivial):
- PortAudio callback: ONLY enqueues frames (and press/release markers arrive on
  the same queue), so every audio-state transition is serialized in one place.
- audio-worker: drains the frame queue; owns recording state, the raw buffer,
  the segmenter and streaming feeds; handles release logic in-order after the
  last pre-release frame.
- pipeline: consumes SegmentItem/UtteranceEnd in FIFO order -> STT -> either
  inline correction+injection (legacy) or hand-off to the inject-worker.
- inject-worker (lazy, only when the overlap toggle is ON): correction +
  injection; single FIFO consumer => injection order == spoken order (FR-009).

Per-dictation settings are frozen in a PerfSnapshot at PTT press (FR-003) and
every stage reads the snapshot, never live cfg, for A/B-relevant decisions.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from audio import MicCapture
from config import AppConfig, PerfSnapshot
from corrector import Corrector
from injector import Injector, focused_window
from stt import Transcriber
from streaming_stt import StreamingWorker
from timing import DictationTiming
from vad import Segment, Segmenter, has_speech

log = logging.getLogger("pipeline")


class Utterance:
    """One PTT press..release cycle: settings snapshot, timing, eager parts,
    and the in-flight item bookkeeping that decides when to conclude."""

    def __init__(self, utt_id: int, snapshot: PerfSnapshot):
        self.id = utt_id
        self.snapshot = snapshot
        self.timing = DictationTiming(snapshot)
        self.parts: list[str] = []          # eager mode: transcribed segment texts
        self._lock = threading.Lock()
        self._pending = 0                   # queued-but-unfinished work items
        self._release_done = False          # release-side enqueueing finished
        self._outcomes: list[str] = []

    def item_started(self) -> None:
        with self._lock:
            self._pending += 1

    def note_outcome(self, outcome: str) -> None:
        with self._lock:
            self._outcomes.append(outcome)

    def item_done(self) -> None:
        with self._lock:
            self._pending -= 1
            ready = self._release_done and self._pending == 0
        if ready:
            self._conclude()

    def release_done(self) -> None:
        """Called once by the audio-worker after all release-side items are queued."""
        with self._lock:
            self._release_done = True
            ready = self._pending == 0
        if ready:
            self._conclude()

    def _conclude(self) -> None:
        with self._lock:
            outcomes = list(self._outcomes)
        if "injected" in outcomes:
            outcome = "injected"
        elif outcomes:
            outcome = outcomes[-1]
        else:
            outcome = "no_speech"
        self.timing.conclude(outcome)


@dataclass
class SegmentItem:
    segment: Segment
    target: Optional[int]
    utt: Utterance


@dataclass
class UtteranceEnd:
    target: Optional[int]
    utt: Utterance


@dataclass
class InjectItem:
    raw_text: str
    final: bool
    target: Optional[int]
    utt: Utterance


@dataclass
class _PressMarker:
    utt: Utterance


@dataclass
class _ReleaseMarker:
    utt: Utterance
    target: Optional[int]


class Pipeline:
    def __init__(self, cfg: AppConfig, transcriber: Transcriber, corrector: Corrector,
                 injector: Injector, on_status: Callable[[str], None]):
        self.cfg = cfg
        self._stt = transcriber
        self._corr = corrector
        self._inj = injector
        self._status = on_status

        self._mic = MicCapture(self._on_frame, get_device_name=lambda: self.cfg.input_device)
        self._segmenter = Segmenter(cfg, self._on_segment)
        self._streaming = StreamingWorker(cfg, transcriber, injector)

        # PTT edge state (hotkey threads); audio state itself lives on the worker.
        self._state_lock = threading.Lock()
        self._ptt_down = False
        self._utt_seq = 0
        self._utt: Optional[Utterance] = None   # utterance of the current/last press

        # audio-worker state (owned exclusively by that thread)
        self._recording = False
        self._cur: Optional[Utterance] = None
        self._raw_buf: list[np.ndarray] = []

        self._last_sentence: Optional[str] = None   # per_sentence correction context

        self._frame_q: "queue.Queue" = queue.Queue()
        self._segment_q: "queue.Queue" = queue.Queue()
        self._inject_q: "queue.Queue" = queue.Queue()
        self._inject_worker: Optional[threading.Thread] = None
        self._inject_lock = threading.Lock()

        threading.Thread(target=self._audio_loop, name="audio-worker", daemon=True).start()
        self._worker = threading.Thread(target=self._process_loop, name="pipeline", daemon=True)
        self._worker.start()
        if cfg.mic_always_open:
            try:
                self._mic.start()
            except Exception:
                log.warning("mic not available at startup; will retry on PTT press")

    def reopen_mic(self) -> None:
        """Apply an input-device change from the tray."""
        self._mic.stop()
        if self.cfg.mic_always_open or self._ptt_down:
            try:
                self._mic.start()
            except Exception:
                log.exception("failed to reopen microphone")

    # --- PTT events (called from hotkey worker threads) ---

    def ptt_pressed(self) -> None:
        if self.cfg.paused or not self._stt.ready:
            log.info("PTT ignored (paused=%s, model ready=%s)", self.cfg.paused, self._stt.ready)
            return
        with self._state_lock:
            if self._ptt_down:
                return
            self._ptt_down = True
            self._utt_seq += 1
            utt = Utterance(self._utt_seq, PerfSnapshot.from_config(self.cfg))
            self._utt = utt
        try:
            self._mic.start()
        except Exception:
            with self._state_lock:
                self._ptt_down = False
            return
        self._frame_q.put(_PressMarker(utt))
        self._status("recording")
        log.info("recording started (mode=%s eager=%s fastinj=%s overlap=%s)",
                 utt.snapshot.mode, utt.snapshot.eager_stt,
                 utt.snapshot.fast_injection, utt.snapshot.pipelined_correction)

    def ptt_released(self) -> None:
        with self._state_lock:
            if not self._ptt_down:
                return
            self._ptt_down = False
            utt = self._utt
        target = focused_window()  # capture BEFORE long processing (focus-drift guard)
        utt.timing.mark_released()
        if not self.cfg.mic_always_open:
            self._mic.stop()
        self._frame_q.put(_ReleaseMarker(utt, target))
        log.info("recording stopped")

    def shutdown(self) -> None:
        self._mic.stop()
        self._frame_q.put(None)
        self._segment_q.put(None)
        with self._inject_lock:
            if self._inject_worker is not None:
                self._inject_q.put(None)

    # --- audio path ---

    def _on_frame(self, frame: np.ndarray) -> None:
        # PortAudio callback thread: hand off and return (FR-013). Unbounded on
        # purpose — dropping frames is word loss (constitution II).
        self._frame_q.put_nowait(frame)

    def _audio_loop(self) -> None:
        while True:
            item = self._frame_q.get()
            if item is None:
                return
            try:
                self._dispatch_audio(item)
            except Exception:
                log.exception("audio dispatch failed")

    def _dispatch_audio(self, item) -> None:
        if isinstance(item, _PressMarker):
            self._cur = item.utt
            self._recording = True
            self._raw_buf = []
            self._last_sentence = None
            self._segmenter.reset()
            if item.utt.snapshot.mode == "realtime":
                self._streaming.start_utterance(item.utt.timing)
            return
        if isinstance(item, _ReleaseMarker):
            self._recording = False
            self._handle_release(item.utt, item.target)
            return
        if not self._recording or self._cur is None:
            return
        snap = self._cur.snapshot
        if snap.mode == "on_release" and not snap.eager_stt:
            self._raw_buf.append(item)
        elif snap.mode == "on_release" or snap.mode == "per_sentence":
            self._segmenter.feed(item)          # eager on_release or per_sentence
        elif snap.mode == "realtime":
            self._streaming.feed(item)

    def _handle_release(self, utt: Utterance, target: Optional[int]) -> None:
        snap = utt.snapshot

        if snap.mode == "realtime":
            self._status("processing")
            words = self._streaming.end_utterance()
            utt.note_outcome("injected" if words else "no_speech")
            utt.release_done()
            self._status("idle")
            return

        if snap.mode == "per_sentence":
            self._segmenter.flush()  # may emit a final tail segment via _on_segment
            utt.release_done()
            self._status("processing" if not self._segment_q.empty() else "idle")
            return

        if snap.eager_stt:  # on_release, eager path
            self._segmenter.flush()  # tail segment (if any) enqueued first (FIFO)
            utt.item_started()
            self._segment_q.put(UtteranceEnd(target, utt))
            utt.release_done()
            self._status("processing")
            return

        # on_release, legacy path — byte-for-byte pre-feature behavior (FR-004)
        audio = np.concatenate(self._raw_buf) if self._raw_buf else np.zeros(0, dtype=np.int16)
        self._raw_buf = []
        peak = int(np.abs(audio).max()) if len(audio) else 0
        log.info("captured %.1f s, peak amplitude %d/32768 (device=%r)",
                 len(audio) / 16000, peak, self.cfg.input_device or "system default")
        if peak < 500 and len(audio) > 0:
            log.warning("audio is near-silent — wrong microphone selected? (tray > Microphone)")
        if len(audio) == 0 or not has_speech(audio, self.cfg.vad_min_speech_ms):
            log.info("no speech detected — nothing to do")
            utt.release_done()   # concludes as no_speech
            self._status("idle")
            return
        utt.item_started()
        self._segment_q.put(SegmentItem(Segment(audio=audio, final=True), target, utt))
        utt.release_done()
        self._status("processing")

    def _on_segment(self, seg: Segment) -> None:
        # Called from the audio-worker (segmenter feed/flush).
        utt = self._cur
        if utt is None:
            return
        collect = utt.snapshot.mode == "on_release" and utt.snapshot.eager_stt
        # per_sentence: injection is soon — capture focus now. Eager collects only.
        target = None if collect else focused_window()
        utt.item_started()
        self._segment_q.put(SegmentItem(seg, target, utt))
        if not collect:
            self._status("processing")

    # --- sequential STT worker ---

    def _process_loop(self) -> None:
        while True:
            item = self._segment_q.get()
            if item is None:
                return
            try:
                self._process(item)
            except Exception:
                log.exception("segment processing failed")
                item.utt.note_outcome("error")
                item.utt.item_done()
            if self._segment_q.empty() and self._inject_q.empty():
                self._status("recording" if self._ptt_down else "idle")

    def _process(self, item) -> None:
        if isinstance(item, UtteranceEnd):
            self._finish_eager(item)
            return

        utt, seg, snap = item.utt, item.segment, item.utt.snapshot
        held = utt.timing.released_at is None   # classify by start, not finish
        t0 = time.perf_counter()
        text = self._stt.transcribe(seg.audio)
        utt.timing.add_stt(time.perf_counter() - t0, held=held)

        if snap.mode == "on_release" and snap.eager_stt:
            if text:
                utt.parts.append(text)
            utt.item_done()
            return

        if not text:
            utt.item_done()
            return

        if snap.pipelined_correction and snap.mode == "per_sentence":
            self._ensure_inject_worker()
            # pending stays with the utterance; the inject-worker finishes the item
            self._inject_q.put(InjectItem(text, seg.final, item.target, utt))
            return

        self._correct_and_inject(text, seg.final, item.target, utt)
        utt.item_done()

    def _finish_eager(self, item: UtteranceEnd) -> None:
        utt = item.utt
        text = " ".join(p for p in utt.parts if p).strip()
        if not text:
            log.info("no speech recognized during the hold — nothing to do")
            utt.item_done()   # concludes as no_speech
            return
        # One correction + one injection over the assembled utterance (FR-005/006).
        self._correct_and_inject(text, True, item.target, utt)
        utt.item_done()

    # --- correction + injection (pipeline thread, or inject-worker when overlap ON) ---

    def _ensure_inject_worker(self) -> None:
        with self._inject_lock:
            if self._inject_worker is None:
                self._inject_worker = threading.Thread(
                    target=self._inject_loop, name="inject-worker", daemon=True)
                self._inject_worker.start()

    def _inject_loop(self) -> None:
        while True:
            item = self._inject_q.get()
            if item is None:
                return
            try:
                self._correct_and_inject(item.raw_text, item.final, item.target, item.utt)
            except Exception:
                log.exception("inject-worker item failed")
                item.utt.note_outcome("error")
            item.utt.item_done()
            if self._inject_q.empty() and self._segment_q.empty():
                self._status("recording" if self._ptt_down else "idle")

    def _correct_and_inject(self, text: str, final: bool, target: Optional[int],
                            utt: Utterance) -> None:
        snap = utt.snapshot
        if self.cfg.ai_correction and self._corr.available:
            backlog = self._inject_q.qsize() if snap.pipelined_correction else self._segment_q.qsize()
            if snap.mode == "per_sentence" and backlog > self.cfg.max_correction_backlog:
                log.warning("backlog %d — skipping correction for this sentence", backlog)
            else:
                timeout = (self.cfg.correction_timeout_release_s
                           if final and snap.mode == "on_release"
                           else self.cfg.correction_timeout_sentence_s)
                context = self._last_sentence if snap.mode == "per_sentence" else None
                t0 = time.perf_counter()
                try:
                    text = self._corr.correct(text, timeout_s=timeout, context=context)
                except Exception:
                    # Corrector guarantees no-raise, but a bug there must never
                    # cost the user their words (constitution II).
                    log.exception("correction crashed — using raw transcript")
                utt.timing.add_correction(time.perf_counter() - t0)

        self._last_sentence = text
        suffix = " " if snap.mode == "per_sentence" and not final else ""
        t0 = time.perf_counter()
        ok, reason = self._inj.inject(text + suffix, target_hwnd=target,
                                      fast=snap.fast_injection,
                                      short_chars=snap.short_text_chars,
                                      wait_max_ms=snap.clipboard_wait_max_ms)
        utt.timing.add_injection(time.perf_counter() - t0)
        utt.note_outcome("injected" if ok else (reason or "error"))
        if not ok:
            log.warning("injection refused; text kept in log only: %r", text)
