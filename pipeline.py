"""Mode dispatch and the sequential processing pipeline.

One pipeline thread consumes segments in order -> STT -> correction -> injection,
which guarantees output ordering. Status changes are reported to the tray via
a callback ("idle" | "recording" | "processing").
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

import numpy as np

from audio import MicCapture
from config import AppConfig
from corrector import Corrector
from injector import Injector, focused_window
from stt import Transcriber
from streaming_stt import StreamingWorker
from vad import Segment, Segmenter, has_speech

log = logging.getLogger("pipeline")


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

        self._recording = False
        self._rec_lock = threading.Lock()
        self._raw_buf: list[np.ndarray] = []          # mode on_release buffer
        self._segment_q: "queue.Queue[Optional[tuple[Segment, Optional[int]]]]" = queue.Queue()
        self._last_sentence: Optional[str] = None      # mode 2 context

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
        if self.cfg.mic_always_open or self._recording:
            try:
                self._mic.start()
            except Exception:
                log.exception("failed to reopen microphone")

    # --- PTT events (called from hotkey worker threads) ---

    def ptt_pressed(self) -> None:
        if self.cfg.paused or not self._stt.ready:
            log.info("PTT ignored (paused=%s, model ready=%s)", self.cfg.paused, self._stt.ready)
            return
        with self._rec_lock:
            if self._recording:
                return
            self._recording = True
            self._raw_buf = []
            self._last_sentence = None
        try:
            self._mic.start()
        except Exception:
            with self._rec_lock:
                self._recording = False
            return
        if self.cfg.mode == "realtime":
            self._streaming.start_utterance()
        self._status("recording")
        log.info("recording started (mode=%s)", self.cfg.mode)

    def ptt_released(self) -> None:
        with self._rec_lock:
            if not self._recording:
                return
            self._recording = False
        if not self.cfg.mic_always_open:
            self._mic.stop()
        target = focused_window()  # capture BEFORE long processing (focus-drift guard)
        mode = self.cfg.mode
        log.info("recording stopped")

        if mode == "realtime":
            self._status("processing")
            self._streaming.end_utterance()
            self._status("idle")
            return

        if mode == "per_sentence":
            self._segmenter.flush()
            self._status("processing" if not self._segment_q.empty() else "idle")
            return

        # on_release
        audio = np.concatenate(self._raw_buf) if self._raw_buf else np.zeros(0, dtype=np.int16)
        self._raw_buf = []
        peak = int(np.abs(audio).max()) if len(audio) else 0
        log.info("captured %.1f s, peak amplitude %d/32768 (device=%r)",
                 len(audio) / 16000, peak, self.cfg.input_device or "system default")
        if peak < 500 and len(audio) > 0:
            log.warning("audio is near-silent — wrong microphone selected? (tray > Microphone)")
        if len(audio) == 0 or not has_speech(audio, self.cfg.vad_min_speech_ms):
            log.info("no speech detected — nothing to do")
            self._status("idle")
            return
        self._segment_q.put((Segment(audio=audio, final=True), target))
        self._status("processing")

    def shutdown(self) -> None:
        self._mic.stop()
        self._segment_q.put(None)

    # --- audio path (PortAudio callback thread) ---

    def _on_frame(self, frame: np.ndarray) -> None:
        with self._rec_lock:
            if not self._recording:
                return
            mode = self.cfg.mode
            if mode == "on_release":
                self._raw_buf.append(frame)
        if mode == "per_sentence":
            self._segmenter.feed(frame)
        elif mode == "realtime":
            self._streaming.feed(frame)

    def _on_segment(self, seg: Segment) -> None:
        # Mode 2: sentence boundary hit; capture focus now (injection is soon).
        self._segment_q.put((seg, focused_window()))
        self._status("processing")

    # --- sequential worker ---

    def _process_loop(self) -> None:
        while True:
            item = self._segment_q.get()
            if item is None:
                return
            seg, target = item
            try:
                self._process(seg, target)
            except Exception:
                log.exception("segment processing failed")
            if self._segment_q.empty():
                self._status("recording" if self._recording else "idle")

    def _process(self, seg: Segment, target: Optional[int]) -> None:
        text = self._stt.transcribe(seg.audio)
        if not text:
            return

        if self.cfg.ai_correction and self._corr.available:
            backlog = self._segment_q.qsize()
            if self.cfg.mode == "per_sentence" and backlog > self.cfg.max_correction_backlog:
                log.warning("backlog %d — skipping correction for this sentence", backlog)
            else:
                timeout = (self.cfg.correction_timeout_release_s if seg.final and self.cfg.mode == "on_release"
                           else self.cfg.correction_timeout_sentence_s)
                context = self._last_sentence if self.cfg.mode == "per_sentence" else None
                text = self._corr.correct(text, timeout_s=timeout, context=context)

        self._last_sentence = text
        suffix = " " if self.cfg.mode == "per_sentence" and not seg.final else ""
        ok = self._inj.inject(text + suffix, target_hwnd=target)
        if not ok:
            log.warning("injection refused; text kept in log only: %r", text)
