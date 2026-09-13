"""T016/T019: eager on_release — assembly, ordering, single injection,
no-speech, snapshot latching (FR-003, FR-005, FR-006)."""
import logging

import numpy as np

from tests.conftest import (FakeCorrector, FakeTranscriber, base_cfg, wait_until)

AUDIO = np.zeros(16000, dtype=np.int16)


def eager_cfg(**over):
    return base_cfg(mode="on_release", perf_eager_stt=True, **over)


def dictation_lines(caplog):
    return [r.message for r in caplog.records
            if r.name == "timing" and r.message.startswith("DICTATION")]


def test_segments_join_in_order_single_injection(make_pipeline):
    p, stt, _, inj = make_pipeline(eager_cfg(), FakeTranscriber(["ala", "ma", "kota"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    assert wait_until(lambda: len(stt.calls) == 2)
    assert inj.calls == []                       # nothing injected during the hold
    p._segmenter.flush_emits = [AUDIO]           # tail arrives at release
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    text, target, _ = inj.calls[0]
    assert text == "ala ma kota"
    assert target == 777                         # focus captured at release
    assert len(stt.calls) == 3


def test_tail_only_no_segment_before_release(make_pipeline):
    p, stt, _, inj = make_pipeline(eager_cfg(), FakeTranscriber(["krótka fraza"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.flush_emits = [AUDIO]
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    assert inj.calls[0][0] == "krótka fraza"


def test_empty_tail_still_injects_collected_parts(make_pipeline):
    p, stt, _, inj = make_pipeline(eager_cfg(), FakeTranscriber(["tylko to"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    assert wait_until(lambda: len(stt.calls) == 1)
    p.ptt_released()                             # no tail from flush
    assert wait_until(lambda: len(inj.calls) == 1)
    assert inj.calls[0][0] == "tylko to"


def test_all_silence_concludes_no_speech(make_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="timing")
    p, stt, _, inj = make_pipeline(eager_cfg(), FakeTranscriber([]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p.ptt_released()
    assert wait_until(lambda: len(dictation_lines(caplog)) == 1)
    assert "outcome=no_speech" in dictation_lines(caplog)[0]
    assert inj.calls == []


def test_stale_segment_after_conclude_never_injects_again(make_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="timing")
    p, stt, _, inj = make_pipeline(eager_cfg(), FakeTranscriber(["a", "b"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    p._segmenter.emit(AUDIO)                     # stale: after utterance concluded
    assert wait_until(lambda: len(stt.calls) == 2)
    assert len(inj.calls) == 1                   # invariant: exactly one injection
    assert len(dictation_lines(caplog)) == 1     # and exactly one summary line


def test_correction_failure_falls_back_to_raw_join(make_pipeline):
    corr = FakeCorrector(available=True, raise_on={0})
    cfg = eager_cfg(ai_correction=True)
    p, stt, _, inj = make_pipeline(cfg, FakeTranscriber(["surowy tekst"]), corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    assert wait_until(lambda: len(stt.calls) == 1)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    assert inj.calls[0][0] == "surowy tekst"     # raw survives the crash


def test_snapshot_latches_mid_flight_config_change(make_pipeline):
    cfg = eager_cfg()
    p, stt, _, inj = make_pipeline(cfg, FakeTranscriber(["jeden", "dwa"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    cfg.perf_eager_stt = False                   # tray toggle mid-hold
    cfg.mode = "per_sentence"
    p._segmenter.emit(AUDIO)
    assert wait_until(lambda: len(stt.calls) == 1)
    assert inj.calls == []                       # still collecting (snapshot rules)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    assert inj.calls[0][0] == "jeden"


def test_legacy_off_path_single_pass(make_pipeline, monkeypatch):
    import pipeline as pl
    monkeypatch.setattr(pl, "has_speech", lambda audio, ms: True)
    p, stt, _, inj = make_pipeline(base_cfg(mode="on_release"), FakeTranscriber(["legacy"]))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    for _ in range(10):
        p._on_frame(np.zeros(512, dtype=np.int16))
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 1)
    assert inj.calls[0][0] == "legacy"
    assert len(stt.calls) == 1                   # exactly one post-release pass
    assert stt.calls[0] == 5120                  # the full concatenated buffer
