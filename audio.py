"""Microphone capture.

The stream is kept open by default (cfg.mic_always_open=True, no first-words
clipping; the Win11 mic indicator stays lit). With mic_always_open=False the
stream opens on PTT press and ~150-200 ms of leading speech may be lost.

Emits 32 ms int16 mono frames at 16 kHz to a callback. The callback consumer
(pipeline) must only enqueue — all analysis happens on its audio-worker thread.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

log = logging.getLogger("audio")

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # 32 ms @ 16 kHz — exactly what Silero VAD wants


def list_input_devices() -> list[str]:
    """Unique input-device names (default host API), for the tray menu."""
    names: list[str] = []
    try:
        hostapi = sd.query_hostapis(sd.default.hostapi)
        for idx in hostapi["devices"]:
            dev = sd.query_devices(idx)
            if dev["max_input_channels"] > 0 and dev["name"] not in names:
                names.append(dev["name"])
    except Exception:
        log.exception("device enumeration failed")
    return names


def _resolve_device(name: str) -> Optional[int]:
    """Device index for a saved name; None = system default (also on stale names)."""
    if not name:
        return None
    try:
        hostapi = sd.query_hostapis(sd.default.hostapi)
        for idx in hostapi["devices"]:
            dev = sd.query_devices(idx)
            if dev["max_input_channels"] > 0 and dev["name"] == name:
                return idx
    except Exception:
        pass
    log.warning("saved input device %r not found — using system default", name)
    return None


def list_output_devices() -> list[str]:
    """Unique output-device names (default host API), for the tray menu."""
    names: list[str] = []
    try:
        hostapi = sd.query_hostapis(sd.default.hostapi)
        for idx in hostapi["devices"]:
            dev = sd.query_devices(idx)
            if dev["max_output_channels"] > 0 and dev["name"] not in names:
                names.append(dev["name"])
    except Exception:
        log.exception("output device enumeration failed")
    return names


def resolve_output_device(name: str) -> Optional[int]:
    """Device index for a saved output name; None = system default (also on stale names)."""
    if not name:
        return None
    try:
        hostapi = sd.query_hostapis(sd.default.hostapi)
        for idx in hostapi["devices"]:
            dev = sd.query_devices(idx)
            if dev["max_output_channels"] > 0 and dev["name"] == name:
                return idx
    except Exception:
        pass
    log.warning("saved output device %r not found — using system default", name)
    return None


class MicCapture:
    def __init__(self, on_frame: Callable[[np.ndarray], None], get_device_name: Callable[[], str] = lambda: ""):
        self._on_frame = on_frame
        self._get_device_name = get_device_name
        self._stream: Optional[sd.InputStream] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            try:
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=FRAME_SAMPLES,
                    device=_resolve_device(self._get_device_name()),
                    callback=self._callback,
                )
                self._stream.start()
                log.debug("mic stream opened")
            except Exception:
                self._stream = None
                log.exception("failed to open microphone stream")
                raise

    def stop(self) -> None:
        with self._lock:
            s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
                log.debug("mic stream closed")
            except Exception:
                log.exception("failed to close microphone stream")

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.warning("audio status: %s", status)
        try:
            self._on_frame(indata[:, 0].copy())
        except Exception:
            log.exception("audio frame handler failed")
