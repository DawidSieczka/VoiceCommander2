"""T013: speak queue policy, stop semantics, stale requests, one SPEAK line per request
(FR-003, FR-008, FR-009, FR-013)."""
import logging
import time

import pytest

import tts as ttsmod
from tests.conftest import FakeEngine, FakePlayer, base_cfg, wait_until


@pytest.fixture
def make_speaker(monkeypatch, tmp_path):
    monkeypatch.setattr(ttsmod.text_prep, "PRONUNCIATION_PATH", tmp_path / "pron.txt")
    monkeypatch.setattr(ttsmod.text_prep, "seed_pronunciation_file", lambda path=None: None)
    monkeypatch.setattr(ttsmod.text_prep, "load_pronunciation", lambda path=None: [])
    built = []

    def build(engine=None, player=None, summarize=None, corrector_available=False, **cfg_over):
        cfg_over.setdefault("tts_enabled", True)
        cfg = base_cfg(**cfg_over)
        engine = engine or FakeEngine()
        player = player or FakePlayer()
        states = []
        sp = ttsmod.Speaker(cfg, engine, player, summarize=summarize,
                            corrector_available=lambda: corrector_available,
                            on_speaking=states.append)
        built.append(sp)
        return sp, engine, player, states

    yield build
    for sp in built:
        sp.shutdown()


def speak_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("SPEAK ")]


def test_plain_request_is_spoken_with_one_summary_line(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    sp, eng, pl, states = make_speaker()
    req = sp.submit("Pierwsze zdanie. Drugie zdanie.")
    assert wait_until(lambda: req._concluded)
    lines = speak_lines(caplog)
    assert len(lines) == 1 and "outcome=spoken" in lines[0] and f"id={req.id}" in lines[0]
    assert "sentences=2" in lines[0]
    assert len(eng.synth_calls) == 2 and len(pl.written) == 2
    assert pl.opened == [22050] and pl.closed >= 1
    assert states[:1] == [True] and states[-1] is False
    assert req.first_audio_ms >= 0 and req.audio_s > 0


def test_disabled_and_empty_and_ignored_events(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    sp, eng, pl, _ = make_speaker(tts_enabled=False)
    r1 = sp.submit("tekst")
    sp.cfg.tts_enabled = True
    r2 = sp.submit("   ")
    r3 = sp.submit("tekst", event="SubagentStop")
    sp.cfg.tts_speak_subagents = True
    r4 = sp.submit("tekst subagenta", event="SubagentStop")
    assert wait_until(lambda: r4._concluded)
    lines = speak_lines(caplog)
    assert any(f"id={r1.id}" in l and "outcome=disabled" in l for l in lines)
    assert any(f"id={r2.id}" in l and "outcome=empty" in l for l in lines)
    assert any(f"id={r3.id}" in l and "outcome=ignored_event" in l for l in lines)
    assert any(f"id={r4.id}" in l and "outcome=spoken" in l for l in lines)
    assert len(eng.synth_calls) == 1


def test_latest_policy_supersedes_current_and_queued(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    eng = FakeEngine(delay=0.15)
    sp, _, pl, _ = make_speaker(engine=eng)
    r1 = sp.submit("Jeden. Dwa. Trzy. Cztery. Pięć.")
    assert wait_until(lambda: len(pl.written) >= 1)
    r2 = sp.submit("Nowa odpowiedź.")
    assert wait_until(lambda: r1._concluded and r2._concluded, timeout=5)
    lines = speak_lines(caplog)
    l1 = next(l for l in lines if l.startswith(f"SPEAK id={r1.id} "))
    l2 = next(l for l in lines if l.startswith(f"SPEAK id={r2.id} "))
    assert "outcome=stopped" in l1 or "outcome=superseded" in l1
    assert "outcome=spoken" in l2
    # r1 never finished all five sentences
    assert sum(1 for t, _ in eng.synth_calls if t in ("Jeden.", "Dwa.", "Trzy.", "Cztery.", "Pięć.")) < 5


def test_append_policy_keeps_order(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    eng = FakeEngine(delay=0.05)
    sp, _, pl, _ = make_speaker(engine=eng, tts_queue_policy="append")
    r1 = sp.submit("A jeden.")
    r2 = sp.submit("B dwa.")
    r3 = sp.submit("C trzy.")
    assert wait_until(lambda: r3._concluded)
    assert [t for t, _ in eng.synth_calls] == ["A jeden.", "B dwa.", "C trzy."]
    assert all("outcome=spoken" in l for l in speak_lines(caplog))


def test_stop_during_synthesis_drops_remaining_sentences(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    eng = FakeEngine(delay=0.2)
    sp, _, pl, states = make_speaker(engine=eng)
    req = sp.submit("Raz. Dwa. Trzy. Cztery.")
    assert wait_until(lambda: len(pl.written) == 1)
    t0 = time.perf_counter()
    sp.stop()
    assert wait_until(lambda: req._concluded)
    assert time.perf_counter() - t0 < 1.0
    assert len(pl.written) <= 2
    assert "outcome=stopped" in speak_lines(caplog)[-1]
    assert states[-1] is False


def test_stop_before_start_marks_superseded_and_drains_queue(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    eng = FakeEngine(delay=0.3)
    sp, _, pl, _ = make_speaker(engine=eng, tts_queue_policy="append")
    r1 = sp.submit("Pierwszy długi.")
    r2 = sp.submit("Drugi.")
    r3 = sp.submit("Trzeci.")
    assert wait_until(lambda: len(eng.synth_calls) == 1)
    sp.stop()
    assert wait_until(lambda: r1._concluded and r2._concluded and r3._concluded)
    lines = speak_lines(caplog)
    assert sum("outcome=stopped" in l for l in lines) == 3   # user stop: current + queued
    assert sum("outcome=superseded" in l for l in lines) == 0
    assert [t for t, _ in eng.synth_calls] == ["Pierwszy długi."]


def test_engine_missing_yields_error_outcome(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    sp, eng, pl, _ = make_speaker(engine=FakeEngine(state="missing"))
    req = sp.submit("Cześć.")
    assert wait_until(lambda: req._concluded)
    line = speak_lines(caplog)[-1]
    assert "outcome=error" in line and "fake_missing" in line
    assert pl.opened == []


def test_synth_failure_is_error_not_crash(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    sp, eng, pl, _ = make_speaker(engine=FakeEngine(fail_synth=True))
    req = sp.submit("Cześć.")
    assert wait_until(lambda: req._concluded)
    assert "outcome=error" in speak_lines(caplog)[-1]
    # speaker thread survives
    eng.fail_synth = False
    req2 = sp.submit("Znowu.")
    assert wait_until(lambda: req2._concluded)
    assert "outcome=spoken" in speak_lines(caplog)[-1]


def test_snapshot_is_taken_at_submit(make_speaker):
    eng = FakeEngine(delay=0.1)
    sp, _, pl, _ = make_speaker(engine=eng, tts_speed=1.0)
    req = sp.submit("Jeden. Dwa.")
    sp.cfg.tts_speed = 1.5
    assert wait_until(lambda: req._concluded)
    assert req.snapshot.speed == 1.0


def test_inject_mode_phonemizes_english_spans_into_one_polish_call(make_speaker):
    sp, eng, pl, _ = make_speaker(tts_codeswitch="inject")
    req = sp.submit("Zaktualizowałem plik config.py i testy.")
    assert wait_until(lambda: req._concluded)
    assert eng.phonemize_calls == ["config dot py"]
    assert len(eng.synth_calls) == 1
    text, lang = eng.synth_calls[0]
    assert lang == "pl" and "[[ fˈeɪk ]]" in text and "Zaktualizowałem plik" in text


def test_splice_mode_uses_both_voices(make_speaker):
    sp, eng, pl, _ = make_speaker(tts_codeswitch="splice")
    req = sp.submit("Zaktualizowałem plik config.py i testy.")
    assert wait_until(lambda: req._concluded)
    assert [l for _, l in eng.synth_calls] == ["pl", "en", "pl"]
    assert eng.phonemize_calls == []


def test_summary_used_when_long_and_available(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    calls = []

    def summarize(text, timeout_s, lang):
        calls.append((len(text), timeout_s, lang))
        return "Krótkie streszczenie."

    sp, eng, pl, _ = make_speaker(summarize=summarize, corrector_available=True,
                                  tts_summarize=True, tts_summary_threshold=100)
    long_text = "Zdanie numer jeden. " * 20
    req = sp.submit(long_text)
    assert wait_until(lambda: req._concluded)
    assert calls and calls[0][2] == "pl"
    assert eng.synth_calls == [("Krótkie streszczenie.", "pl")]
    assert "summarised=1" in speak_lines(caplog)[-1]


def test_summary_skipped_below_threshold_or_when_unavailable(make_speaker, caplog):
    caplog.set_level(logging.INFO, logger="tts")
    calls = []
    sp, eng, pl, _ = make_speaker(summarize=lambda t, s, l: calls.append(1) or None,
                                  corrector_available=True, tts_summarize=True,
                                  tts_summary_threshold=100)
    short = sp.submit("Krótko.")
    assert wait_until(lambda: short._concluded)
    assert calls == []
    long_req = sp.submit("Zdanie numer jeden. " * 20)   # summarize returns None -> full text
    assert wait_until(lambda: long_req._concluded)
    assert calls == [1]
    assert len(eng.synth_calls) == 21 and "summarised=0" in speak_lines(caplog)[-1]


def test_shutdown_stops_and_closes(make_speaker):
    eng = FakeEngine(delay=0.2)
    sp, _, pl, _ = make_speaker(engine=eng)
    req = sp.submit("Raz. Dwa. Trzy.")
    assert wait_until(lambda: len(pl.written) >= 1)
    sp.shutdown()
    assert req._concluded and not sp._thread.is_alive()
    assert eng.shutdowns == 1 and pl.closed >= 1
