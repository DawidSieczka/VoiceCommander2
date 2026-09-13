# Phase 0 Research: Low-Latency Dictation with Runtime A/B Performance Toggles

**Date**: 2026-09-13 | **Spec**: [spec.md](spec.md)

All Technical Context unknowns resolved below. Format per item: Decision / Rationale /
Alternatives considered.

## R1. Eager STT architecture for on_release mode

**Decision**: When `perf_eager_stt` is ON and mode is `on_release`, feed frames to the
existing `Segmenter` (as `per_sentence` already does) instead of only appending to the
raw buffer. Emitted segments are queued to the pipeline thread tagged with the utterance
id and `collect=True`: the pipeline transcribes them as they arrive (during the hold) and
appends the text to an utterance accumulator instead of injecting. On PTT release the
segmenter is flushed (tail segment) and an *utterance-end marker* is queued carrying the
release timestamp, the focus target captured at release, and the settings snapshot. When
the pipeline reaches the marker it joins accumulated texts in arrival order, runs ONE
correction pass over the joined text (existing `correction_timeout_release_s`), and
injects ONCE.

**Rationale**: Reuses two proven components (Segmenter, sequential pipeline thread) with
no new concurrency: ordering is inherited from the single FIFO queue, satisfying FR-005/
FR-006 structurally. The tail-only wait after release is exactly the stated goal. The
raw-buffer path remains untouched for the OFF case (FR-004).

**Alternatives considered**:
- Separate eager worker thread with its own queue — rejected: duplicates the pipeline
  loop and creates a second ordering domain to reconcile.
- Chunked fixed-interval transcription (no VAD boundaries) — rejected: re-transcribes
  audio repeatedly (streaming-mode cost) where VAD boundaries give clean one-pass cuts.
- Correcting each eager segment individually — rejected: changes output semantics vs
  legacy (correction quality benefits from full-utterance context; spec requires
  "correction and injection still happen once").

## R2. Audio-callback hygiene (unconditional)

**Decision**: `Pipeline._on_frame` (PortAudio callback) only does
`self._frame_q.put_nowait(frame)` on an unbounded `queue.Queue`. A dedicated
`audio-worker` thread drains the queue and performs everything the callback does today:
recording-state check, raw-buffer append, segmenter feed, streaming feed. Silero VAD
inference thus moves off the callback thread entirely.

**Rationale**: PortAudio callbacks must be allocation-light and never block (constitution
III; the current code runs ONNX inference there). `queue.Queue.put_nowait` is safe from
the callback and the worker preserves frame order trivially. Frame rate is ~31/s; queue
depth stays near zero except under load, which is precisely when the buffer protects us.

**Alternatives considered**:
- `collections.deque` + polling — rejected: `queue.Queue` gives blocking get with
  timeout for clean shutdown, no busy-wait.
- Bounded queue with drop policy — rejected: dropping frames is word loss (constitution
  II); memory for even 60 s of backlog is ~2 MB.

## R3. Persistent Silero VAD for the release-time speech check

**Decision**: Replace per-call `SileroVoiceActivityDetector()` construction in
`has_speech()` with a lazily created module-level instance guarded by a lock, calling its
state-reset method before each scan. **Implementation-time verification required**: check
whether the installed `pysilero_vad` version exposes `reset()` (recent versions do); if
absent, reset by re-running the first ~5 frames (LSTM state settles) or pin a version
that has it. Log the one-time init at startup.

**Rationale**: ONNX session construction per dictation is pure per-use overhead
(FR-014). LSTM state carryover between scans is the only correctness concern; an explicit
reset removes it.

**Alternatives considered**:
- Reuse the Segmenter's own VAD instance — rejected: it holds live streaming state during
  eager/per_sentence use; sharing would corrupt both users.
- Keep per-call construction but cache the ONNX bytes — rejected: session build is the
  cost, not file IO.

## R4. Fast injection — short-text direct typing

**Decision**: With `perf_fast_injection` ON, text whose length ≤ `short_text_chars`
(new config, default **120**) is injected via the existing `_inject_unicode()`
(SendInput `KEYEVENTF_UNICODE`), skipping the clipboard entirely. All existing guards
(focus, password field, modifier release) run first, as they are in `inject()` upstream
of the method choice.

**Rationale**: SendInput of ~120 chars completes in well under 50 ms and touches nothing
the user owns; the clipboard path costs ≥300 ms today (fixed sleep) plus
save/set/restore. Most per_sentence segments and short commands fit under the threshold,
so this alone removes the fixed pause from the most frequent case. 120 chars is where
clipboard round-trip overhead and typing cost are roughly at parity with a wide safety
margin, and it is configurable.

**Alternatives considered**:
- Always SendInput — rejected: v1 experience and general Windows practice show some
  targets (games, some frameworks/IME situations) mishandle long synthetic Unicode
  streams; the clipboard is the robust bulk path.

## R5. Fast injection — adaptive clipboard wait

**Decision**: With `perf_fast_injection` ON and text above the threshold, use
**delayed clipboard rendering** as the paste-consumption signal: create (lazily, on the
pipeline thread) a hidden message-only window; open the clipboard with that window as
owner and call `SetClipboardData(CF_UNICODETEXT, NULL)` (ctypes — pywin32 does not pass
NULL). When the target application pastes, Windows sends `WM_RENDERFORMAT`; the handler
renders the actual text and records the timestamp. After sending Ctrl+V, the injector
pumps messages (`PeekMessage`/`DispatchMessage`) for that window until render occurred
plus a 30 ms grace, or a bounded cap of **300 ms** (equal to today's fixed sleep, so
never slower), then restores the user's clipboard via the existing retry-guarded path.
The Win+V history-exclusion formats are still set alongside the delayed-render handle.

**Rationale**: `WM_RENDERFORMAT` is the only Windows mechanism that positively signals
"the paste actually read our data" — clipboard sequence numbers change on writes, not
reads, and `WM_NULL` round-trips are not ordered against posted keystrokes. Typical
editors consume the paste in 10–60 ms, so the common case saves 200 ms+ per dictation,
and the cap guarantees the legacy worst case (FR-007, SC-004). If the render request
never comes (target ignored the paste), the cap fires, the clipboard is restored, and the
event is logged (edge case in spec).

**Alternatives considered**:
- Poll `GetClipboardSequenceNumber` — rejected: sequence number does not change when the
  clipboard is *read*; it cannot detect paste consumption.
- Poll `GetOpenClipboardWindow` — rejected: fast readers open/close between polls; racy.
- Shorter fixed sleep (e.g. 100 ms) — kept as documented contingency only: if delayed
  rendering proves incompatible with some target during implementation testing, the flag
  falls back to a configurable short fixed wait; decision recorded in code comment and
  log. Primary path remains delayed render.

## R6. Pipelined correction for per_sentence mode

**Decision**: With `perf_pipelined_correction` ON, split the pipeline into two stages:
the existing pipeline thread performs STT only, then enqueues
`(order_index, raw_text, segment_meta, target, settings)` onto a FIFO consumed by a new
single `inject-worker` thread that performs correction (with the existing
backlog-skip/timeout/fallback rules) and injection. Because the hand-off queue is FIFO
and the consumer is single-threaded, injection order is structurally guaranteed
(FR-009); a correction failure only affects its own item (FR-010). With the flag OFF the
pipeline thread calls correction+injection inline exactly as today. The
`_last_sentence` context for correction moves to the inject-worker (it sees final texts
in order). The `max_correction_backlog` check uses the inject-queue depth.

**Rationale**: STT (CPU) and Ollama (GPU/HTTP) are disjoint resources; overlapping them
hides the shorter of the two latencies per sentence. One added thread, one queue, no
reordering possible by construction — the cheapest design that satisfies the invariant.

**Alternatives considered**:
- Futures + ordered completion buffer — rejected: allows out-of-order completion that
  must be re-sequenced; more machinery for the same result.
- Parallel correction calls to Ollama — rejected: a 2 GB-GPU local model serves one
  request well; parallel requests would queue server-side anyway and complicate
  ordering.

## R7. Latency instrumentation

**Decision**: A small `DictationTiming` record (new module `timing.py`) created at PTT
release: release timestamp (monotonic), per-stage accumulators (stt_ms, correction_ms,
injection_ms), toggle snapshot, mode, outcome reason. The pipeline emits exactly one
`INFO` summary line per dictation from the code path that concludes it, in a fixed
greppable format (see contracts/log-summary-format.md). For eager dictations, STT time
spent *during the hold* is reported as a separate `eager_stt_ms` field so release→text
remains an honest wall-clock measure. Realtime mode reports release→last-word.

**Rationale**: FR-011/FR-012 and SC-003 require one comparable line per dictation; a
fixed format makes A/B comparison a grep. Monotonic clock; no new UI.

**Alternatives considered**: CSV metrics file — rejected: log already rotates and the
tray links to it; one more artifact to manage without added value for A/B reading.

## R8. Toggle snapshot semantics (FR-003)

**Decision**: At `ptt_pressed`, capture an immutable `PerfSnapshot` (the three toggles +
threshold + mode + language) and attach it to the utterance; every downstream decision
for that dictation reads the snapshot, never live `cfg`. Tray toggles mutate `cfg` and
save; next press picks them up.

**Rationale**: Guarantees "in-flight dictation completes under starting settings"
without locks around cfg, and makes the summary line's toggle report trustworthy.

**Alternatives considered**: Locking cfg during dictation — rejected: blocks the tray
thread and still races between stages.

## R9. Tray submenu & config surface

**Decision**: New config fields (all persisted, defaults = legacy):
`perf_eager_stt: bool = False`, `perf_fast_injection: bool = False`,
`perf_pipelined_correction: bool = False`, `short_text_chars: int = 120`,
`clipboard_wait_max_ms: int = 300`. Tray gains a "Performance (A/B)" submenu with three
checkable toggles (reusing the existing `toggle()` helper); thresholds are config-file
only. Unknown-key filtering in `config.load()` already makes old configs load clean
(FR-015 satisfied by existing code — verify with test).

**Rationale**: Matches existing config/tray patterns exactly (constitution VI); nothing
new to learn.

## R10. Testing approach

**Decision**: Introduce `tests/` with `pytest` (new dev-only dependency, justified by
constitution's own testing gate): unit tests with fakes for (a) eager accumulator
assembly/ordering incl. empty-tail and no-segment cases, (b) ordered injection under
simulated slow/failing correction (flag ON), (c) frame-queue worker preserves order and
callback does no VAD, (d) `DictationTiming` line format, (e) config back-compat load,
(f) segmenter regression (existing thresholds). STT/Ollama/injection replaced by fakes;
no microphone, GPU, or network. Manual smoke: quickstart.md scenarios map to SC-001–008.

**Environment note**: this clone has no `.venv` (README references one); implementation
phase must create it from `requirements.txt` + `pytest` before any run.
