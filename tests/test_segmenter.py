"""T029: Segmenter state-machine regression (scripted VAD probabilities) and
persistent has_speech detector reuse (FR-014)."""
import numpy as np
import pytest

import vad as vad_mod
from config import AppConfig
from vad import FRAME_MS, Segmenter


class ScriptedVAD:
    """Replaces Silero: returns scripted probabilities in order."""

    def __init__(self, probs):
        self.probs = list(probs)
        self.resets = 0

    def __call__(self, _audio_bytes):
        return self.probs.pop(0) if self.probs else 0.0

    def reset(self):
        self.resets += 1


def make_segmenter(probs, **cfg_over):
    cfg = AppConfig(vad_end_silence_ms=96, vad_min_speech_ms=64,
                    vad_max_segment_s=1, vad_padding_ms=64)
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    segments = []
    seg = Segmenter.__new__(Segmenter)
    seg._cfg = cfg
    seg._on_segment = segments.append
    seg._vad = ScriptedVAD(probs)
    seg._reset()
    return seg, segments


def frames(n):
    return [np.full(512, i + 1, dtype=np.int16) for i in range(n)]


def test_sentence_boundary_at_silence():
    # 5 speech frames (160 ms) then silence until 96 ms of it accumulates.
    seg, out = make_segmenter([0.9] * 5 + [0.1] * 10)
    for f in frames(9):
        seg.feed(f)
    assert len(out) == 1
    assert not out[0].forced_cut and not out[0].final


def test_min_speech_drop():
    seg, out = make_segmenter([0.9] * 1 + [0.1] * 10)  # 32 ms speech < 64 ms min
    for f in frames(6):
        seg.feed(f)
    assert out == []


def test_forced_cut_at_max_length():
    n = 1000 // FRAME_MS + 2                      # > vad_max_segment_s (1 s)
    seg, out = make_segmenter([0.9] * n)
    for f in frames(n):
        seg.feed(f)
    assert len(out) == 1
    assert out[0].forced_cut


def test_flush_emits_final_pending_speech():
    seg, out = make_segmenter([0.9] * 5)
    for f in frames(5):
        seg.feed(f)
    seg.flush()
    assert len(out) == 1
    assert out[0].final


def test_flush_on_silence_emits_nothing():
    seg, out = make_segmenter([0.1] * 5)
    for f in frames(5):
        seg.feed(f)
    seg.flush()
    assert out == []


def test_preroll_padding_included():
    seg, out = make_segmenter([0.1, 0.1, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1])
    fs = frames(9)
    for f in fs:
        seg.feed(f)
    assert len(out) == 1
    # 64 ms padding = up to 2 preroll frames; frame #2 (value 2) must be included.
    assert out[0].audio[0] == 2


def test_public_reset_clears_state_and_vad():
    seg, out = make_segmenter([0.9] * 3 + [0.9] * 5 + [0.1] * 10)
    for f in frames(3):
        seg.feed(f)
    seg.reset()
    assert seg._vad.resets == 1
    assert not seg._in_speech and seg._buffer == []


class CountingVAD:
    instances = 0

    def __init__(self):
        CountingVAD.instances += 1
        self.resets = 0

    def __call__(self, _b):
        return 0.9

    def reset(self):
        self.resets += 1


def test_has_speech_reuses_persistent_detector(monkeypatch):
    CountingVAD.instances = 0
    monkeypatch.setattr(vad_mod, "SileroVoiceActivityDetector", CountingVAD)
    monkeypatch.setattr(vad_mod, "_shared_vad", None)
    audio = np.zeros(512 * 20, dtype=np.int16)
    assert vad_mod.has_speech(audio, min_speech_ms=64)
    assert vad_mod.has_speech(audio, min_speech_ms=64)
    assert CountingVAD.instances == 1             # constructed once
    assert vad_mod._shared_vad.resets == 2        # state reset per scan


def test_has_speech_negative_on_silence(monkeypatch):
    class SilentVAD(CountingVAD):
        def __call__(self, _b):
            return 0.1
    monkeypatch.setattr(vad_mod, "SileroVoiceActivityDetector", SilentVAD)
    monkeypatch.setattr(vad_mod, "_shared_vad", None)
    assert not vad_mod.has_speech(np.zeros(512 * 20, dtype=np.int16), min_speech_ms=64)
