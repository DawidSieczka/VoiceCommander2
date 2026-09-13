"""T027: overlapped correction — strict injection order, failure isolation,
backlog skip, and one summary line for per_sentence (FR-009, FR-010)."""
import logging

import numpy as np

from tests.conftest import (FakeCorrector, FakeStreaming, FakeTranscriber,
                            base_cfg, wait_until)

AUDIO = np.zeros(16000, dtype=np.int16)


def ps_cfg(**over):
    defaults = dict(mode="per_sentence", ai_correction=True,
                    perf_pipelined_correction=True)
    defaults.update(over)
    return base_cfg(**defaults)


def dictation_lines(caplog):
    return [r.message for r in caplog.records
            if r.name == "timing" and r.message.startswith("DICTATION")]


def test_order_preserved_with_reversed_correction_delays(make_pipeline):
    corr = FakeCorrector(available=True, delays=[0.3, 0.0])
    p, stt, _, inj = make_pipeline(ps_cfg(), FakeTranscriber(["pierwsze", "drugie"]),
                                   corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 2)
    assert [c[0].strip() for c in inj.calls] == ["PIERWSZE", "DRUGIE"]


def test_correction_crash_is_item_local(make_pipeline):
    corr = FakeCorrector(available=True, raise_on={0})
    p, stt, _, inj = make_pipeline(ps_cfg(), FakeTranscriber(["padło", "działa"]),
                                   corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 2)
    assert [c[0].strip() for c in inj.calls] == ["padło", "DZIAŁA"]  # raw, then corrected


def test_overlap_frees_stt_while_correcting(make_pipeline):
    corr = FakeCorrector(available=True, delays=[0.4, 0.0, 0.0])
    stt = FakeTranscriber(["a", "b", "c"])
    p, _, _, inj = make_pipeline(ps_cfg(), stt, corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    # All three STT passes finish while the first correction still sleeps.
    assert wait_until(lambda: len(stt.calls) == 3, timeout=0.35)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 3)


def test_inline_when_toggle_off(make_pipeline):
    corr = FakeCorrector(available=True)
    p, stt, _, inj = make_pipeline(
        ps_cfg(perf_pipelined_correction=False),
        FakeTranscriber(["jeden", "dwa"]), corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 2)
    assert p._inject_worker is None              # worker never started


def test_per_sentence_emits_exactly_one_summary(make_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="timing")
    corr = FakeCorrector(available=True)
    p, stt, _, inj = make_pipeline(ps_cfg(), FakeTranscriber(["raz", "dwa"]),
                                   corrector=corr)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(inj.calls) == 2)
    assert wait_until(lambda: len(dictation_lines(caplog)) == 1)
    line = dictation_lines(caplog)[0]
    assert "mode=per_sentence" in line and "outcome=injected" in line and "overlap=1" in line


def test_realtime_emits_one_summary(make_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="timing")
    p, *_ = make_pipeline(base_cfg(mode="realtime"))
    p._streaming = FakeStreaming(words=5)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p.ptt_released()
    assert wait_until(lambda: len(dictation_lines(caplog)) == 1)
    assert "mode=realtime" in dictation_lines(caplog)[0]
    assert "outcome=injected" in dictation_lines(caplog)[0]


def test_refusal_outcome_reported(make_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="timing")
    from tests.conftest import FakeInjector
    inj = FakeInjector(refuse="refused_focus")
    p, stt, _, _ = make_pipeline(ps_cfg(), FakeTranscriber(["tekst"]),
                                 corrector=FakeCorrector(available=True), injector=inj)
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._segmenter.emit(AUDIO)
    p.ptt_released()
    assert wait_until(lambda: len(dictation_lines(caplog)) == 1)
    assert "outcome=refused_focus" in dictation_lines(caplog)[0]
