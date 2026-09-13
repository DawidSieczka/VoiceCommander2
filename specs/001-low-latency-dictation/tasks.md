# Tasks: Low-Latency Dictation with Runtime A/B Performance Toggles

**Input**: Design documents from `/specs/001-low-latency-dictation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the constitution's testing gate mandates unit tests for state
machines and concurrency-sensitive code, runnable without microphone/GPU/Ollama.

**Organization**: Grouped by user story (US1–US5 from spec.md) after shared
Setup/Foundational phases.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: Restore the missing runtime environment and create the test scaffold.

- [X] T001 Create `.venv` with Python 3.12 (`py -3.12 -m venv .venv`), install `requirements.txt` plus dev-only `pytest`; verify `python -c "import faster_whisper, pysilero_vad, sounddevice, keyboard, pystray, win32clipboard"` succeeds
- [X] T002 [P] Create `tests/__init__.py` and `tests/conftest.py` with shared fakes: `FakeTranscriber` (returns queued texts), `FakeCorrector` (configurable delay/failure), `FakeInjector` (records calls+order), frame factory for synthetic int16 audio; no hardware imports at module level
- [X] T003 [P] Verify `pysilero_vad.SileroVoiceActivityDetector` exposes a state-reset method in the installed version (research R3); record the finding and chosen reset strategy as a comment block to be used in T028

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config surface, settings snapshot, timing record, and the audio-callback
refactor that every story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Add config fields to `config.py` `AppConfig` exactly per data-model.md §2: `perf_eager_stt: bool = False`, `perf_fast_injection: bool = False`, `perf_pipelined_correction: bool = False`, `short_text_chars: int = 120` (constraint: "> 0"), `clipboard_wait_max_ms: int = 300` (constraint: "50–1000; never exceeds legacy fixed wait by default")
- [X] T005 Add frozen `PerfSnapshot` dataclass to `config.py` per data-model.md §1 (fields: eager_stt, fast_injection, pipelined_correction, short_text_chars, clipboard_wait_max_ms, mode, language) with `PerfSnapshot.from_config(cfg)` factory; "constructed only in ptt_pressed; never mutated"
- [X] T006 [P] Create `timing.py`: `DictationTiming` record per data-model.md §3 (released_at monotonic, mode, toggles, eager_stt_ms, stt_ms, correction_ms, injection_ms, outcome ∈ `injected|no_speech|refused_focus|refused_password|error`, total_ms) with `conclude(outcome)` emitting exactly one INFO line on logger `timing` in the exact format of contracts/log-summary-format.md; repeated conclude = debug-logged no-op
- [X] T007 [P] Unit test `tests/test_timing_line.py`: line format matches contract regex; absent stages report 0; double-conclude emits once; all five outcomes render
- [X] T008 [P] Unit test `tests/test_config_compat.py`: config JSON without new keys loads with toggles False and thresholds 120/300 (FR-015); unknown keys still ignored; round-trip save/load preserves new fields
- [X] T009 Refactor `pipeline.py` audio path per research R2/data-model.md §7: PortAudio callback `_on_frame` only does `put_nowait` on unbounded `queue.Queue`; new `audio-worker` thread drains it and performs the current dispatch (recording check, raw-buffer append, segmenter feed, streaming feed); `None` sentinel for shutdown; worker body wrapped in try/except with `log.exception`; wire shutdown in `Pipeline.shutdown()` and `main.py` `on_exit`
- [X] T010 Unit test `tests/test_frame_queue.py`: frames dispatched in order; callback function contains no VAD/segmenter calls (assert via monkeypatched segmenter recording call thread); shutdown drains cleanly

**Checkpoint**: Foundation ready — config, snapshot, timing record, clean audio path.

---

## Phase 3: User Story 1 — Text appears sooner after releasing the key (Priority: P1) 🎯 MVP

**Goal**: With `perf_eager_stt` ON in on_release mode, segments are transcribed during
the hold; after release only the tail is transcribed, then ONE correction + ONE
injection (FR-005/FR-006).

**Independent Test**: quickstart.md scenario A — same passage, toggle OFF vs ON, ≥40%
lower `total_ms`, identical text, nothing typed during the hold.

- [X] T011 [US1] Extend pipeline queue protocol in `pipeline.py` per data-model.md §5: replace `(Segment, target)` tuples with `SegmentItem(segment, target_hwnd, utterance_id, snapshot)` and `UtteranceEnd(utterance_id, target_hwnd, timing, snapshot)` dataclasses (module-level, next to Pipeline); `None` remains shutdown
- [X] T012 [US1] Add utterance accumulator per data-model.md §4 to `pipeline.py`: `utterance_id` incremented at each PTT press; `parts: list[str]` appended only by the pipeline thread; stale-utterance segments discarded on arrival with a debug log
- [X] T013 [US1] Route eager mode in `pipeline.py`: in `ptt_pressed` capture `PerfSnapshot`, create timing shell, and reset the `Segmenter` (guards against stale state from an aborted prior utterance — analysis F5); audio-worker feeds the existing `Segmenter` when `snapshot.eager_stt and snapshot.mode == "on_release"` (collect-mode), raw buffer otherwise unchanged; `_on_segment` enqueues `SegmentItem` WITHOUT injecting when collect-mode (accumulate in `_process`)
- [X] T014 [US1] Implement release path in `pipeline.py` `ptt_released` for eager: flush segmenter (tail segment), enqueue `UtteranceEnd` carrying release timestamp, focus target, timing; in `_process` on `UtteranceEnd`: join parts in arrival order, single correction with `correction_timeout_release_s` (existing availability/fallback rules), single injection to captured target, `timing.conclude(...)`; empty join → `no_speech` conclude, nothing injected
- [X] T015 [US1] Preserve legacy path byte-for-byte when toggle OFF in `pipeline.py` (raw-buffer concat → has_speech → single SegmentItem) and record `eager_stt_ms` (hold-time STT) vs `stt_ms` (post-release STT) in the eager path per contracts/log-summary-format.md
- [X] T016 [P] [US1] Unit test `tests/test_eager_accumulator.py` with conftest fakes: (a) 3 segments + end-marker → one injection of joined text in order; (b) no segment before release → tail-only works; (c) empty tail + prior parts → still injects parts; (d) all-silence → `no_speech`, no injection; (e) stale segments from previous utterance discarded; (f) nothing injected before end-marker; (g) correction failure → raw joined text (FR-006, FR-010 analog)

**Checkpoint**: US1 fully functional — A/B testable via log `total_ms` with the toggle.

---

## Phase 4: User Story 2 — Compare old vs new behavior in seconds (Priority: P2)

**Goal**: Tray "Performance (A/B)" submenu, instant no-restart latching (FR-002/FR-003),
and the summary line emitted for ALL modes and outcomes (FR-011/FR-012).

**Independent Test**: quickstart.md scenario D + G — flip toggle, next dictation reflects
it in its `DICTATION` line; mid-hold flip reports old state; silent hold yields
`outcome=no_speech`.

- [X] T017 [US2] Add "Performance (A/B)" submenu to `tray.py` `_menu()` after "Recognition model" per contracts/config-and-tray.md: three checkable items ("Eager transcription (on-release mode)", "Fast injection", "Overlapped correction (per-sentence)") using the existing `toggle()`/`checked()` helpers; persists via existing `_save()`
- [X] T018 [US2] Wire timing into non-eager modes in `pipeline.py`: legacy on_release (timing created at release, staged times recorded, concluded after injection/refusal), per_sentence (per-sentence stage times summed across the whole utterance — sums MAY exceed `total_ms` because hold-time work counts in stages but `total_ms` spans release→last injection, per updated contract), realtime (release→last-word total via `streaming_stt.py` end path; `stt_ms` = final pass, `eager_stt_ms` = hold-time passes). Change `injector.inject` here to return `(ok, refusal_reason)` so outcomes map to `refused_focus`/`refused_password` (moved from T023 — analysis F2)
- [X] T019 [US2] Snapshot latching audit across `pipeline.py`/`streaming_stt.py`: every per-dictation decision reads `snapshot`, never live `cfg` (mode, toggles, thresholds, language); tray changes apply from next press (FR-003); add targeted assertions in `tests/test_eager_accumulator.py` (mid-flight cfg mutation does not change behavior)
- [X] T020 [P] [US2] Extend `tests/test_timing_line.py`: per_sentence multi-segment sums stages into one line; realtime path emits one line; refusal outcomes carried through from fake injector

**Checkpoint**: US1+US2 — full A/B loop (flip → dictate → grep) works.

---

## Phase 5: User Story 3 — Faster text insertion (Priority: P3)

**Goal**: With `perf_fast_injection` ON: short texts typed directly (no clipboard);
long texts use delayed-render clipboard with WM_RENDERFORMAT-signaled wait capped at
`clipboard_wait_max_ms` (FR-007/FR-008).

**Independent Test**: quickstart.md scenario B — `inject_ms` drops ≥200 ms for short
texts; clipboard untouched (short) / restored (long).

- [X] T021 [US3] Add short-text routing to `injector.py` `inject()`: when `fast_injection` and `len(text) <= short_text_chars` (values passed via snapshot parameters, not global cfg) → `_inject_unicode`; guards (focus/password/modifiers) unchanged upstream; log chosen path at debug
- [X] T022 [US3] Implement delayed-render adaptive clipboard in `injector.py` per research R5: lazy hidden message-only window (ctypes `CreateWindowExW` with `HWND_MESSAGE`) created **per injecting thread (thread-local)** — a Win32 window is bound to its creator's message queue, and injection may run on the pipeline thread or the inject-worker thread depending on the overlap toggle (analysis F1); `SetClipboardData(CF_UNICODETEXT, NULL)` via ctypes with history-exclusion formats still set; after Ctrl+V, pump `PeekMessage`/`DispatchMessage` until `WM_RENDERFORMAT` handled (render real text, record timestamp) + 30 ms grace, else cap at `clipboard_wait_max_ms`; then restore clipboard via existing retry path; on any delayed-render setup failure fall back to the legacy fixed-sleep clipboard write with a one-time warning (research R5 contingency); log wait outcome (`render_ms=N` or `capped`)
- [X] T023 [US3] Thread injection timing through `injector.py`: caller records `injection_ms` around `inject()` (the `(ok, refusal_reason)` signature lands earlier in T018); legacy fixed 300 ms sleep path kept verbatim for toggle OFF (FR-004)
- [X] T024 [P] [US3] Unit test `tests/test_injector_routing.py` (pure-logic parts only, Win32 calls monkeypatched): threshold routing (len == threshold → typed; len > → clipboard), toggle OFF → always legacy clipboard, refusal reasons propagate; adaptive-wait cap arithmetic honors "50–1000" bound

**Checkpoint**: US3 independently testable in Notepad with the toggle.

---

## Phase 6: User Story 4 — Sentence-by-sentence keeps up (Priority: P4)

**Goal**: With `perf_pipelined_correction` ON in per_sentence mode, STT of sentence N+1
overlaps correction of sentence N; injection strictly in spoken order (FR-009/FR-010).

**Independent Test**: quickstart.md scenario C — ≥25% lower `total_ms` for 4 sentences,
order always correct, Ollama failure affects only its own sentence.

- [X] T025 [US4] Implement `InjectItem` + single `inject-worker` thread in `pipeline.py` per data-model.md §6 and research R6: FIFO `queue.Queue`, worker performs correction (existing timeout/backlog/fallback semantics; `max_correction_backlog` checks inject-queue depth) then injection; `_last_sentence` context moves to the worker; per-item try/except; started lazily when first needed, joined in `shutdown()`
- [X] T026 [US4] Route per_sentence hand-off in `pipeline.py` `_process`: overlap ON → enqueue `InjectItem(raw_text, final, target, snapshot, timing)` and return (STT thread free for next segment); overlap OFF → inline correction+injection exactly as today; timing concluded by whichever component finishes the dictation's last item
- [X] T027 [P] [US4] Unit test `tests/test_inject_order.py`: with FakeCorrector delays reversed (sentence 1 slow, sentence 2 fast) injection order is still 1→2; correction exception → raw text injected, next items unaffected; backlog skip rule fires on queue depth; shutdown drains without loss

**Checkpoint**: All four flagged behaviors complete and individually toggleable.

---

## Phase 7: User Story 5 — Recognition quality protected under load (Priority: P5)

**Goal**: Persistent release-time VAD (FR-014) — callback hygiene (FR-013) already
landed in Phase 2; this phase completes and validates the story.

**Independent Test**: quickstart.md scenario F — 10-min session, zero overflow
warnings, one persistent-VAD init log line.

- [X] T028 [US5] Make `has_speech()` in `vad.py` use a lazily created module-level `SileroVoiceActivityDetector` guarded by a lock, with the T003-verified reset strategy before each scan; INFO log once at first init ("persistent VAD ready"); never shared with `Segmenter`'s instance (data-model.md §8)
- [X] T029 [P] [US5] Unit test `tests/test_segmenter.py`: existing `Segmenter` thresholds regression on synthetic audio (speech/silence patterns → expected segment boundaries, min-speech drop, forced cut at max length, flush-final) and `has_speech` correctness + instance reuse across calls (constructor called once — assert via monkeypatched class)

**Checkpoint**: All user stories independently functional.

---

## Phase 8: Polish & Cross-Cutting

- [X] T030 [P] Fix `audio.py` module docstring drift (claims stream opens only during PTT; `mic_always_open` defaults True) and document the new frame-queue contract in `pipeline.py` module docstring
- [X] T031 [P] Update `README.md` (Performance (A/B) submenu, `DICTATION` log line for A/B comparison) and `DOKUMENTACJA.md` (new config keys with defaults/constraints, toggle semantics, adaptive clipboard behavior)
- [ ] T032 Run full suite `.venv\Scripts\python.exe -m pytest tests -q` and fix any failures; then run quickstart.md scenario E (all toggles OFF regression) plus A–D, F, G manually with the real app and record results in `specs/001-low-latency-dictation/quickstart-results.md`
- [X] T033 Final constitution review of the diff (privacy: no text in timing lines; word preservation on every failure path; guards unchanged) and commit per-phase history if not already committed

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 → user stories**: T001 blocks everything; T004–T010 block all
  stories (config/snapshot/timing/audio-worker are shared substrate).
- **US1 (Phase 3)**: after Phase 2. No dependency on other stories.
- **US2 (Phase 4)**: after Phase 2; T018 touches the same `pipeline.py` regions as
  US1 — run after Phase 3 in single-developer flow (T017/T020 independent of US1).
- **US3 (Phase 5)**: after Phase 2 only (`injector.py` is disjoint from US1/US2 work);
  can run in parallel with Phases 3–4 if staffed.
- **US4 (Phase 6)**: after US2's timing wiring (T018) to conclude timing correctly.
- **US5 (Phase 7)**: T028/T029 only depend on Phase 1 (T003) — parallelizable anytime
  after Setup.
- **Polish (Phase 8)**: after all stories.

### Parallel Opportunities

- Phase 1: T002 ∥ T003 (after T001).
- Phase 2: T006, T007, T008 ∥ each other; T009 sequential (pipeline.py), then T010.
- Across stories (if parallel agents): US3 (injector.py) ∥ US1 (pipeline.py) ∥ US5
  (vad.py) — disjoint files.
- Test tasks marked [P] within each story parallel to each other.

## Implementation Strategy

Single-developer incremental order (recommended): Phases 1→2→3 (**MVP: eager A/B
demonstrable**) → validate scenario A → 4 → 5 → 6 → 7 → 8. Commit at each phase
checkpoint so every increment is revertable and A/B-comparable on its own.
