# Implementation Plan: Low-Latency Dictation with Runtime A/B Performance Toggles

**Branch**: `001-low-latency-dictation` | **Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-low-latency-dictation/spec.md`

## Summary

Cut perceived release-to-text latency in the push-to-talk dictation app and make every
improvement user-verifiable at runtime. Three flagged behaviors (default OFF = legacy):
eager STT during the key hold for on_release mode (reusing the existing VAD segmenter
and pipeline thread with an utterance accumulator + end-marker), fast injection
(SendInput for short texts; delayed-render clipboard with WM_RENDERFORMAT-signaled
adaptive wait, capped at the legacy 300 ms), and pipelined correction for per_sentence
mode (STT thread hands off to a single ordered inject-worker). Two unconditional
robustness fixes: the PortAudio callback only enqueues frames (all VAD/buffer work moves
to an audio-worker thread) and the release-time speech check reuses a persistent Silero
instance. One `DICTATION` summary log line per dictation carries total + per-stage
timings + the toggle snapshot, making A/B comparison a grep. Toggles live in a new tray
"Performance (A/B)" submenu and latch per dictation via an immutable snapshot taken at
PTT press.

## Technical Context

**Language/Version**: Python 3.12 (Windows, `pythonw.exe` tray app)

**Primary Dependencies**: faster-whisper (CTranslate2, CPU int8), pysilero-vad,
sounddevice/PortAudio, keyboard, pystray, pywin32 + ctypes (Win32: SendInput,
clipboard, message-only window), requests → local Ollama. No new runtime dependencies;
`pytest` added as dev-only.

**Storage**: existing `%APPDATA%\VoiceCommander2\config.json` (5 new keys, defaults =
legacy; see contracts/config-and-tray.md). No other persistence.

**Testing**: pytest unit tests with fakes (no mic/GPU/Ollama) + manual quickstart
scenarios (quickstart.md). Note: this clone has no `.venv`; implementation starts by
creating it from `requirements.txt`.

**Target Platform**: Windows 11 desktop, consumer laptop (2-core-class CPU envelope,
2 GB GPU owned by Ollama).

**Project Type**: single flat Python desktop/tray application.

**Performance Goals**: release→text ≥40% lower with eager ON on a ~15 s paused passage
(SC-001); ≥200 ms injection saving for short texts (SC-004); ≥25% total reduction for
4-sentence per_sentence dictations with overlap ON (SC-005); toggle switch effective
within 2 s, no restart (SC-002).

**Constraints**: injection strictly in spoken order in all outcomes; no text loss on any
failure path (fallback to raw transcript); audio callback does no analysis; all toggles
OFF ⇒ byte-identical behavior to current release; all decisions diagnosable from the
rotating log.

**Scale/Scope**: single user, one dictation at a time; ~15 source files today; feature
touches config, tray, pipeline, injector, vad, stt call-sites, adds `timing.py` and
`tests/`.

## Constitution Check

*GATE evaluated against constitution v1.0.0 — pre-research: PASS; re-checked post-design
(Phase 1): PASS.*

| Principle | Compliance |
|-----------|-----------|
| I. Local-First & Private | No new network paths; delayed-render clipboard still sets Win+V history-exclusion formats; timing log contains durations only, no dictated text. PASS |
| II. Never Lose Words | Eager accumulator injects exactly once with full fallback to raw text; inject-worker failures are item-local with raw-text fallback; refused injection still logs text; frame queue is unbounded specifically to avoid dropping audio. PASS |
| III. Responsiveness Is the Product | The feature's purpose. Callback reduced to `put_nowait`; fixed 300 ms sleep replaced by signaled wait capped at the legacy value (never slower); latency-affecting changes measured via the mandated summary line. PASS |
| IV. Fail Soft | All toggles default OFF; new threads (audio-worker, inject-worker) wrap loop bodies in try/except; delayed-render failure falls back to legacy clipboard write; VAD reset-API absence has a documented fallback (research R3). PASS |
| V. Windows Integration Safety | All guards (focus-drift, password field, modifier release, clipboard restore) run unchanged upstream of method choice; adaptive wait cannot exceed legacy wait; clipboard restored in bounded time in every outcome. PASS |
| VI. Simplicity & Small Surface | No new runtime deps; flat modules kept (one new `timing.py`); flags reuse existing config/tray patterns. Two justified additions in Complexity Tracking. PASS |
| VII. Observability | One greppable `DICTATION` line per dictation (contract); persistent-VAD init, adaptive-wait outcomes, and fallback events all logged with reasons. PASS |

## Project Structure

### Documentation (this feature)

```text
specs/001-low-latency-dictation/
├── plan.md              # This file
├── research.md          # Phase 0 (10 resolved decisions R1–R10)
├── data-model.md        # Phase 1 (runtime entities + config additions)
├── quickstart.md        # Phase 1 (validation guide, maps to SC-001…008)
├── contracts/
│   ├── config-and-tray.md
│   └── log-summary-format.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
.                        # flat layout, unchanged by policy (constitution VI)
├── audio.py             # unchanged API; callback consumers move
├── config.py            # +5 fields (perf toggles, thresholds)
├── corrector.py         # unchanged (used from inject-worker when overlap ON)
├── hotkey.py            # unchanged
├── injector.py          # +short-text routing, +delayed-render adaptive wait
├── main.py              # wiring only (persistent VAD init log, shutdown of new workers)
├── pipeline.py          # frame queue + audio-worker; eager accumulator + end-marker;
│                        #   optional inject-worker stage; timing integration
├── stt.py               # unchanged API (dictionary-read caching NOT in scope)
├── streaming_stt.py     # feed() now called from audio-worker (no code change expected)
├── timing.py            # NEW: DictationTiming record + summary-line emission
├── tray.py              # +"Performance (A/B)" submenu
├── vad.py               # persistent has_speech detector (+reset), Segmenter unchanged
└── tests/               # NEW (pytest, fakes; no hardware/network)
    ├── test_eager_accumulator.py
    ├── test_inject_order.py
    ├── test_frame_queue.py
    ├── test_timing_line.py
    ├── test_config_compat.py
    └── test_segmenter.py
```

**Structure Decision**: keep the existing flat module-per-concern layout; the only new
source module is `timing.py` plus a `tests/` package. No `src/` reorganization — that
would be unrelated churn contradicting constitution VI.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Hidden message-only window + delayed clipboard rendering (injector) | Only Windows mechanism that positively signals the paste was read, enabling an adaptive wait that is never slower than the legacy cap (FR-007) | Polling clipboard sequence number cannot detect reads; shorter fixed sleep is a guess that either wastes time or breaks slow targets (kept only as documented contingency, research R5) |
| Second worker thread (inject-worker) when overlap toggle ON | Overlaps STT (CPU) with correction (GPU/HTTP) while a single FIFO consumer structurally guarantees injection order (FR-009) | Futures with re-sequencing buffer allows out-of-order completion and needs more machinery for the same invariant |
