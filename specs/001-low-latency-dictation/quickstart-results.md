# Quickstart Validation Results

**Date**: 2026-09-13 | **Feature**: 001-low-latency-dictation

## Automated tests

`.venv\Scripts\python.exe -m pytest tests -q` → **50 passed** (0 failed) in ~2.7 s.

Coverage delivered: eager assembly/ordering/single-injection (8 tests), ordered
injection under reversed correction delays + failure isolation + one-summary-line
per dictation incl. realtime and refusal outcomes (7), frame-queue hygiene and
thread affinity (3), timing-line contract format incl. all outcomes and
hold/post-release STT split (12 incl. parametrized), config back-compat and
snapshot clamping (5), segmenter state-machine regression with scripted VAD +
persistent has_speech detector reuse (9), injector routing/guards (7).

Environment note: `.venv` recreated from `requirements.txt` + `pytest`
(this clone shipped without it). `pysilero_vad.SileroVoiceActivityDetector`
confirmed to expose `reset()` (T003) — persistent-VAD strategy uses it.

## Manual A/B scenarios (A–G)

**PENDING — require the user** (live voice, microphone, Ollama, and the one-time
Whisper `small` model download; no model is cached on this machine). Follow
[quickstart.md](quickstart.md): scenario A (eager ≥40% lower total_ms),
B (fast injection, ≥200 ms saved on short texts), C (overlap ≥25% on 4 sentences,
order intact), D (toggle latching), E (all-OFF legacy equivalence), F (10-min
robustness, zero overflow warnings), G (silent dictation → `outcome=no_speech`).

Record per-scenario `DICTATION` log lines here after running them.

## Constitution review of the diff (T033)

- **I Local-First**: no new network paths; timing lines carry durations only,
  never dictated text; delayed-render clipboard still sets both Win+V
  history-exclusion formats. ✓
- **II Never Lose Words**: correction wrapped in a raw-text fallback even
  against corrector bugs; refused injection still logs the text; frame queue
  unbounded (no audio drops); eager accumulator injects exactly once. ✓
- **III Responsiveness**: PortAudio callback reduced to `put_nowait`; adaptive
  clipboard wait capped at the legacy 300 ms (never slower); all latency paths
  measured via the DICTATION line. ✓
- **IV Fail Soft**: all toggles default OFF; audio-worker/inject-worker loop
  bodies catch and log; delayed-render setup failure falls back to the legacy
  clipboard path with a one-time warning. ✓
- **V Windows Integration Safety**: focus-drift/password/modifier guards run
  upstream of every new path; clipboard restored (or promise materialized) in
  every outcome. ✓
- **VI Simplicity**: one new module (`timing.py`) plus `tests/`; no new runtime
  dependencies (pytest dev-only). ✓
- **VII Observability**: persistent-VAD init, adaptive-wait outcome
  (`render_ms`/capped), fallbacks, and per-stage timings all logged. ✓
