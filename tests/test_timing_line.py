"""T007/T020: DICTATION summary line matches the contract
(specs/001-low-latency-dictation/contracts/log-summary-format.md)."""
import logging
import re
import time

import pytest

from config import AppConfig, PerfSnapshot
from timing import DictationTiming, OUTCOMES

LINE_RE = re.compile(
    r"^DICTATION mode=(on_release|per_sentence|realtime) "
    r"outcome=(injected|no_speech|refused_focus|refused_password|error) "
    r"total_ms=\d+ stt_ms=\d+ eager_stt_ms=\d+ corr_ms=\d+ inject_ms=\d+ "
    r"eager=[01] fastinj=[01] overlap=[01]$"
)


def snap(**over):
    cfg = AppConfig()
    for k, v in over.items():
        setattr(cfg, k, v)
    return PerfSnapshot.from_config(cfg)


def lines(caplog):
    return [r.message for r in caplog.records
            if r.name == "timing" and r.message.startswith("DICTATION")]


def test_line_format_and_stage_zeroes(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap())
    t.mark_released()
    t.conclude("no_speech")
    out = lines(caplog)
    assert len(out) == 1
    assert LINE_RE.match(out[0]), out[0]
    assert "stt_ms=0" in out[0] and "corr_ms=0" in out[0] and "inject_ms=0" in out[0]


def test_double_conclude_emits_once(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap())
    t.mark_released()
    t.conclude("injected")
    t.conclude("error")
    assert len(lines(caplog)) == 1
    assert "outcome=injected" in lines(caplog)[0]


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_all_outcomes_render(caplog, outcome):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap())
    t.mark_released()
    t.conclude(outcome)
    assert f"outcome={outcome}" in lines(caplog)[0]


def test_unknown_outcome_maps_to_error(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap())
    t.mark_released()
    t.conclude("nonsense")
    assert "outcome=error" in lines(caplog)[0]


def test_stt_split_hold_vs_post_release(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap(perf_eager_stt=True))
    t.add_stt(0.5)            # during the hold
    t.mark_released()
    t.add_stt(0.25)           # after release
    t.conclude("injected")
    line = lines(caplog)[0]
    assert "eager_stt_ms=500" in line and "stt_ms=250" in line
    assert "eager=1" in line


def test_stt_classified_by_start_not_finish(caplog):
    # A pass that STARTED during the hold but finished after release counts as
    # hold-time work — callers capture `held` before transcribing.
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap(perf_eager_stt=True))
    held = t.released_at is None      # captured at pass start (True)
    t.mark_released()                 # release lands mid-pass
    t.add_stt(0.7, held=held)         # completion after release
    t.conclude("injected")
    line = lines(caplog)[0]
    assert "eager_stt_ms=700" in line and "stt_ms=0" in line


def test_toggle_flags_reflect_snapshot(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap(perf_fast_injection=True, perf_pipelined_correction=True,
                             mode="per_sentence"))
    t.mark_released()
    t.conclude("injected")
    line = lines(caplog)[0]
    assert "mode=per_sentence" in line
    assert "eager=0 fastinj=1 overlap=1" in line


def test_total_measures_release_to_conclude(caplog):
    caplog.set_level(logging.INFO, logger="timing")
    t = DictationTiming(snap())
    t.mark_released()
    time.sleep(0.05)
    t.conclude("injected")
    total = int(re.search(r"total_ms=(\d+)", lines(caplog)[0]).group(1))
    assert 30 <= total < 2000
