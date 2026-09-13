# Feature Specification: Low-Latency Dictation with Runtime A/B Performance Toggles

**Feature Branch**: `001-low-latency-dictation`

**Created**: 2026-09-13

**Status**: Draft

**Input**: User description: "Low-latency dictation pipeline with runtime A/B performance toggles — reduce perceived latency between releasing the PTT key and text appearing; every behavioral improvement sits behind an individual runtime toggle (default OFF) so the user can compare old vs new behavior mid-session within seconds, with objective per-dictation timing measurements in the log."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Text appears sooner after releasing the key (Priority: P1)

As a user dictating in the default mode (text appears after releasing the push-to-talk
key), I want the application to do its speech-recognition work *while I am still
speaking*, so that when I release the key I wait only for the last few words to be
processed — not for the entire recording.

**Why this priority**: Release-to-text latency is the single most noticeable quality of
the application (constitution: "Responsiveness Is the Product"). Today a 15-second
dictation makes the user wait for recognition of all 15 seconds after release; doing that
work during the hold is the largest possible perceived-speed win.

**Independent Test**: Dictate a ~15-second passage with the eager toggle ON and OFF and
compare the logged release-to-text times. The dictated output text must be the same in
both settings; only the waiting time differs.

**Acceptance Scenarios**:

1. **Given** the eager-transcription toggle is ON and the user holds the PTT key speaking
   several sentences with natural pauses, **When** the user releases the key, **Then**
   the final corrected text appears at the caret noticeably faster than with the toggle
   OFF, and the logged release-to-text time is at least 40% lower for the same passage.
2. **Given** the eager-transcription toggle is ON, **When** the user speaks one short
   phrase with no internal pauses and releases the key, **Then** the text appears
   correctly (the tail-only path handles the "no segment closed before release" case).
3. **Given** the eager-transcription toggle is OFF, **When** the user dictates, **Then**
   behavior is exactly as before the feature existed (single post-release recognition
   pass).
4. **Given** eager transcription produced partial results during the hold, **When** the
   user releases the key, **Then** exactly one block of text is injected, once, in spoken
   order — no partial text ever appears while the key is still held.

---

### User Story 2 - Compare old vs new behavior in seconds (Priority: P2)

As the user evaluating these improvements, I want each behavioral change exposed as an
individual ON/OFF switch in the tray menu, applied immediately without restarting the
application, and I want every dictation to end with one readable log line stating total
release-to-text time, the per-stage breakdown, and which switches were active — so I can
objectively verify whether a change actually helps on my machine.

**Why this priority**: Without instant switching and objective numbers, the other stories
cannot be honestly evaluated (constitution: latency claims require measurements). This
story makes the whole feature verifiable.

**Independent Test**: Open the tray menu, flip a performance toggle, dictate, flip it
back, dictate again — no restart in between — and find two summary lines in the log with
the toggle states and timings clearly distinguishable.

**Acceptance Scenarios**:

1. **Given** the application is running, **When** the user opens the tray menu, **Then**
   a "Performance (A/B)" submenu lists each behavioral toggle with its current state, and
   all toggles start OFF (legacy behavior) on first run after the update.
2. **Given** the user flips a toggle, **When** they start the next dictation, **Then**
   the new setting is already in effect — no restart, no reload wait.
3. **Given** any completed dictation (any mode), **When** the user opens the log,
   **Then** exactly one summary line for that dictation states: total release-to-text
   time, recognition time, correction time, injection time, and the set of active
   performance toggles.
4. **Given** a dictation is mid-processing, **When** the user flips a toggle, **Then**
   the in-flight dictation completes under the settings it started with and only the next
   dictation uses the new setting (no corruption, no mixed behavior).

---

### User Story 3 - Faster text insertion (Priority: P3)

As a user, I want the moment between "processing finished" and "text visible in my
application" to be as short as possible: short dictations should be typed directly, and
clipboard-based insertion should wait only as long as the target application actually
needs, instead of a fixed worst-case pause.

**Why this priority**: A fixed ~300 ms pause is added to every single dictation today; in
sentence-by-sentence mode it also delays the processing of the following sentence.
Removing it is a small, safe win that benefits every mode.

**Independent Test**: With the fast-injection toggle ON, dictate a short phrase and a
long passage; the logged injection time drops for both compared to toggle OFF, and the
clipboard content is still restored afterwards.

**Acceptance Scenarios**:

1. **Given** the fast-injection toggle is ON and the dictated text is short, **When** it
   is inserted, **Then** it is typed directly without touching the clipboard at all, and
   the user's clipboard content is untouched.
2. **Given** the fast-injection toggle is ON and the dictated text is long, **When** it
   is inserted via the clipboard, **Then** the wait before restoring the user's clipboard
   adapts to the target application's actual paste handling, with a bounded maximum, and
   the logged injection time for typical applications is materially below the legacy
   fixed pause.
3. **Given** the fast-injection toggle is OFF, **Then** insertion behaves exactly as
   before (clipboard paste with the fixed pause).
4. **Given** the target application never consumes the paste (edge case), **When** the
   bounded maximum wait elapses, **Then** the clipboard is restored anyway and the event
   is logged — the application never hangs waiting.

---

### User Story 4 - Sentence-by-sentence mode keeps up with speech (Priority: P4)

As a user of the sentence-by-sentence mode with AI correction enabled, I want the
recognition of my next sentence to proceed while the previous sentence is still being
corrected, so that a long dictation finishes sooner — while my sentences still appear in
the exact order I spoke them.

**Why this priority**: Recognition and correction run on different resources and are
naturally overlappable; serializing them makes long dictations roughly the sum of both.
Valuable, but only affects one mode with correction enabled, hence lower priority.

**Independent Test**: Dictate 4+ sentences in sentence-by-sentence mode with the overlap
toggle ON and OFF; total time from release to last sentence inserted is materially lower
with ON, and sentence order in the target application is identical.

**Acceptance Scenarios**:

1. **Given** the overlap toggle is ON and several sentences are queued, **When** they are
   processed, **Then** sentences are inserted strictly in spoken order, every time.
2. **Given** the overlap toggle is ON, **When** correction of one sentence fails or times
   out, **Then** that sentence falls back to its raw transcript (no word loss) and later
   sentences are unaffected.
3. **Given** the overlap toggle is OFF, **Then** processing is fully sequential as
   before.

---

### User Story 5 - Recognition quality is protected under load (Priority: P5)

As a user, I want the audio capture path to remain rock-solid even while the machine is
busy recognizing speech, so that no fragments of my speech are ever dropped — and I want
the log to prove it.

**Why this priority**: Heavy work currently performed on the audio-capture path can cause
dropped audio under load, which surfaces as mysteriously missing or wrong words. This is
an unconditional robustness fix (no toggle): there is no scenario where the old behavior
is preferable.

**Independent Test**: Dictate continuously for several minutes in sentence-by-sentence
mode while recognition is running; the log shows no audio-overflow warnings, and the
speech-presence check no longer adds a per-dictation setup delay.

**Acceptance Scenarios**:

1. **Given** the machine is under recognition load, **When** audio frames arrive, **Then**
   the capture path only hands them off for later processing (all analysis happens off
   the capture path), and no audio-status warnings appear in the log during a 10-minute
   continuous session.
2. **Given** any dictation ends, **When** the silence/speech check runs, **Then** it
   reuses a persistent detector (its one-time setup cost is paid at startup, not per
   dictation).

---

### Edge Cases

- **No closed segment before release (eager ON)**: user speaks one continuous phrase —
  the tail path must recognize the entire audio; output identical to legacy.
- **Release during a word**: the final audio tail is recognized as part of the final
  pass; no truncated duplicate words from the eager partials.
- **Toggle flipped mid-dictation**: the in-flight dictation completes with the settings
  captured at its start; the new value applies from the next press.
- **Correction service down (eager ON)**: raw transcript is inserted, as today — eager
  mode changes when recognition happens, never whether text is preserved.
- **Focus moves to another window during the hold (eager ON)**: guard behavior identical
  to legacy — the insertion target is what has focus at release; drift after release
  still refuses injection.
- **Very long hold hitting the maximum segment length**: forced segment cuts feed the
  eager path the same way natural pauses do.
- **Silent dictation**: nothing recognized during hold or tail → nothing inserted, log
  states why; no summary-line crash on empty stages.
- **Clipboard occupied by another application (fast injection ON)**: falls back to direct
  typing, as the legacy path already does.
- **Recognition model being swapped (from tray) while eager dictation is in progress**:
  the dictation completes on the old model; the new model applies afterwards.
- **Config file from a previous version (no new settings present)**: application starts
  normally with all toggles OFF.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each behavioral change (eager transcription, fast injection, overlapped
  correction) MUST be controlled by its own independent setting, persisted in the
  existing configuration file, defaulting to OFF (legacy behavior).
- **FR-002**: All three toggles MUST be switchable from a dedicated "Performance (A/B)"
  tray submenu and MUST take effect for the next dictation without restarting the
  application.
- **FR-003**: A dictation already in progress when a toggle changes MUST complete
  entirely under the settings active when it started.
- **FR-004**: With all toggles OFF, observable behavior (text output, timing profile,
  insertion method) MUST be indistinguishable from the current release.
- **FR-005** (eager): With eager transcription ON in on-release mode, speech MUST be
  segmented and recognized incrementally while the key is held; after release, only
  not-yet-recognized audio is recognized; correction (when enabled) and insertion MUST
  still happen exactly once, after release, over the assembled full text.
- **FR-006** (eager): No text MUST ever be inserted while the key is still held, and the
  assembled text MUST contain every recognized word exactly once, in spoken order.
- **FR-007** (fast injection): With fast injection ON, text at or below a short-text
  threshold MUST be inserted by direct typing without using the clipboard; longer text
  MUST use clipboard insertion whose post-paste wait adapts to actual paste consumption
  with a bounded maximum no larger than the legacy fixed wait.
- **FR-008** (fast injection): The user's prior clipboard content MUST still be restored
  in all clipboard-path outcomes, including the bounded-maximum timeout.
- **FR-009** (overlap): With overlapped correction ON in sentence-by-sentence mode,
  recognition of a queued sentence MAY proceed while an earlier sentence is being
  corrected, but insertion MUST occur strictly in spoken order in every outcome,
  including correction failures, timeouts, and skips.
- **FR-010** (overlap): Correction failure or timeout for any sentence MUST fall back to
  that sentence's raw transcript; it MUST NOT affect the processing or ordering of other
  sentences.
- **FR-011** (instrumentation): Every completed dictation MUST produce exactly one
  summary log line containing: total release-to-text duration, recognition duration,
  correction duration, insertion duration, and the states of the three toggles at
  dictation start. Stages that did not run report zero/absent explicitly.
- **FR-012** (instrumentation): The summary line MUST be emitted for all three modes and
  also when the dictation ends with nothing inserted (stating the reason category).
- **FR-013** (audio path, unconditional): The audio-capture callback MUST perform no
  analysis work — only hand frames off; all voice-activity analysis and buffer assembly
  MUST run outside the capture path.
- **FR-014** (audio path, unconditional): The speech-presence check performed at release
  MUST reuse a persistent detector initialized at startup; no per-dictation detector
  construction.
- **FR-015**: Configuration files from previous versions MUST load without error, with
  all new settings taking their OFF defaults.
- **FR-016**: All existing safety guards (focus-drift refusal, password-field refusal,
  modifier release, clipboard-history exclusion, word-preservation fallbacks) MUST apply
  unchanged in every new path.

### Key Entities

- **Performance toggle set**: the three persisted user-facing switches (eager
  transcription, fast injection, overlapped correction), each independent, default OFF;
  snapshot taken at dictation start governs that dictation.
- **Dictation timing record**: the per-dictation measurement — release timestamp,
  per-stage durations, active toggle snapshot, completion reason — surfaced as the single
  summary log line.
- **Speech segment**: a contiguous stretch of recognized speech produced during or after
  the hold; ordered; carries its recognition result until assembly/insertion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a ~15-second dictation containing natural sentence pauses, the logged
  release-to-text time with eager transcription ON is at least 40% lower than with it
  OFF on the same machine and passage (typical expectation: 50–70% lower).
- **SC-002**: Switching any performance toggle requires at most 2 user actions
  (open tray → click), completes without application restart, and is in effect for a
  dictation started 2 seconds later.
- **SC-003**: 100% of completed dictations produce exactly one timing summary line, and
  the two halves of an A/B comparison are distinguishable in the log by toggle state
  alone.
- **SC-004**: With fast injection ON, logged insertion time for short texts into a
  standard editor decreases by at least 200 ms compared to OFF.
- **SC-005**: With overlapped correction ON, total time from release to last sentence
  inserted for a 4-sentence dictation is at least 25% lower than with it OFF, with
  insertion order correct in 100% of runs.
- **SC-006**: A 10-minute continuous sentence-by-sentence session under recognition load
  produces zero audio-overflow warnings in the log.
- **SC-007**: With all toggles OFF, a fixed regression passage dictated before and after
  the update yields identical inserted text.
- **SC-008**: The user can run a complete old-vs-new comparison of any single toggle
  (dictate, flip, dictate, read two log lines) in under one minute.

## Assumptions

- The three behavioral toggles are independent; any combination is valid. Interactions
  (e.g., eager + fast injection) compose without special cases.
- "Short text" for direct typing is defined by a configurable character threshold with a
  sensible default (on the order of a clipboard paste's fixed overhead being larger than
  typing that many characters); the exact default is a planning-level decision.
- The eager path reuses the existing sentence segmentation with its current thresholds;
  no new tuning parameters are user-facing in this feature beyond the toggles themselves.
- The timing summary is a log line only; no new UI surface for metrics is in scope
  (the tray already links to the log file).
- Realtime mode improvements (tick cadence, first-word delay) are OUT of scope for this
  feature and may be specified separately; realtime dictations still emit the timing
  summary line (FR-012).
- Per-user rollback remains available via the toggles themselves (OFF = legacy), so no
  separate migration/rollback tooling is needed.
- Single-user desktop application; no concurrency beyond one dictation at a time is in
  scope (press-while-processing continues to behave as today).
