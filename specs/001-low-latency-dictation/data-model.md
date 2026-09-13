# Phase 1 Data Model: Low-Latency Dictation with Runtime A/B Performance Toggles

**Date**: 2026-09-13 | **Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

No persistent storage beyond the existing `config.json`. All entities are in-memory
runtime structures plus config fields.

## 1. PerfSnapshot (immutable, per dictation)

Captured at PTT press (research R8); attached to the utterance; read-only downstream.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| eager_stt | bool | cfg.perf_eager_stt | governs segmenter routing for this utterance |
| fast_injection | bool | cfg.perf_fast_injection | governs injection method choice |
| pipelined_correction | bool | cfg.perf_pipelined_correction | governs stage hand-off |
| short_text_chars | int | cfg.short_text_chars | direct-typing threshold |
| clipboard_wait_max_ms | int | cfg.clipboard_wait_max_ms | adaptive wait cap |
| mode | str | cfg.mode | frozen so a mid-dictation mode change cannot mix paths |
| language | str | cfg.language | for correction prompt choice |

**Validation**: constructed only in `ptt_pressed`; never mutated (dataclass frozen).

## 2. AppConfig additions (persisted)

| Field | Type | Default | Constraint |
|-------|------|---------|-----------|
| perf_eager_stt | bool | False | legacy behavior when False (FR-001/FR-004) |
| perf_fast_injection | bool | False | " |
| perf_pipelined_correction | bool | False | " |
| short_text_chars | int | 120 | > 0; config-file only (no tray item) |
| clipboard_wait_max_ms | int | 300 | 50–1000; never exceeds legacy fixed wait by default |

**Back-compat**: `config.load()` already drops unknown keys and fills missing ones from
defaults → old files load with toggles OFF (FR-015).

## 3. DictationTiming (per dictation, transient)

Created at PTT release; concluded exactly once (FR-011/FR-012).

| Field | Type | Notes |
|-------|------|-------|
| released_at | float (monotonic) | t0 for release→text |
| mode | str | from snapshot |
| toggles | str | compact e.g. `eager=1 fastinj=0 overlap=0` |
| eager_stt_ms | int | STT work done during hold (eager only; else 0) |
| stt_ms | int | STT wall time after release |
| correction_ms | int | 0 when skipped/disabled/unavailable |
| injection_ms | int | 0 when nothing injected |
| outcome | enum str | `injected` \| `no_speech` \| `refused_focus` \| `refused_password` \| `error` |
| total_ms | int | conclude_time − released_at |

**State transitions**: `open` → (stages accumulate) → `concluded` (emits the summary
line; further conclude calls are no-ops with a debug warning — guarantees "exactly one
line").

## 4. Utterance accumulator (eager path, transient)

| Field | Type | Notes |
|-------|------|-------|
| utterance_id | int | incremented at each PTT press; stale segments from a previous utterance are discarded on arrival |
| parts | list[str] | transcribed segment texts in arrival order (single-producer: pipeline thread) |
| snapshot | PerfSnapshot | governs the whole utterance |
| timing | DictationTiming | attached at release via the end-marker |

**Invariants** (FR-005/FR-006): parts only appended by the pipeline thread; joined and
injected exactly once when the end-marker for the *same utterance_id* is processed;
nothing injected before the marker.

## 5. Pipeline queue items (extended)

Existing queue carried `(Segment, target_hwnd) | None`. Extended tagged union:

| Item | Fields | Producer | Consumer action |
|------|--------|----------|-----------------|
| SegmentItem | segment, target_hwnd, utterance_id, snapshot | audio-worker / release handler | transcribe; inject (legacy/per_sentence) or accumulate (eager collect) |
| UtteranceEnd | utterance_id, target_hwnd, timing, snapshot | release handler | join accumulator → correct once → inject once → conclude timing |
| Shutdown (None) | — | shutdown() | exit loop |

## 6. InjectItem (pipelined-correction hand-off)

FIFO queue consumed by the single `inject-worker` thread (research R6).

| Field | Type | Notes |
|-------|------|-------|
| raw_text | str | STT output (pre-correction) |
| final | bool | segment.final passthrough (suffix logic) |
| target_hwnd | Optional[int] | focus captured at segment emit |
| snapshot | PerfSnapshot | correction timeout / language / injection choices |
| timing | Optional[DictationTiming] | concluded here when this item ends the dictation |

**Invariant** (FR-009/FR-010): single consumer + FIFO ⇒ injection order = enqueue order
= spoken order; per-item try/except keeps failures item-local; fallback to raw_text on
any correction failure.

## 7. Frame queue (audio hygiene)

`queue.Queue[np.ndarray | None]` — callback `put_nowait`s 32 ms frames; `audio-worker`
drains and dispatches (raw buffer / segmenter / streaming) per current recording state;
`None` = worker shutdown. Unbounded by design (drop = word loss; ~62 KB/s worst case).

## 8. Persistent VAD handle

Module-level lazy singleton in `vad.py` + lock; state reset before each
`has_speech()` scan (research R3). Not shared with `Segmenter`'s instance.
