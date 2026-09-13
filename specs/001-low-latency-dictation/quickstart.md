# Quickstart Validation: Low-Latency Dictation with Runtime A/B Toggles

**Feature**: 001-low-latency-dictation | maps to Success Criteria SC-001…SC-008 in
[spec.md](spec.md).

## Prerequisites

- Windows 11, working microphone; Ollama running locally with the configured model
  (correction scenarios only).
- Environment (this clone ships without `.venv`):

  ```powershell
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe -m pip install pytest
  ```

## Automated tests (no mic/GPU/Ollama needed)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Expected: all pass. Covers eager assembly/ordering, ordered injection under slow or
failing correction, frame-queue hygiene, timing-line format
([contracts/log-summary-format.md](contracts/log-summary-format.md)), config
back-compat, segmenter regression.

## Manual A/B scenarios

Start the app (`.\.venv\Scripts\python.exe main.py` for console logs, or `pythonw` +
tray → Open logs). All comparisons read `DICTATION` lines — see the log contract.

### A. Eager transcription (SC-001, SC-008)

1. Mode = On release, Performance (A/B) → all OFF. Dictate a fixed ~15 s passage with
   natural pauses into Notepad. Note `total_ms` (this is "A").
2. Toggle **Eager transcription** ON (no restart). Dictate the same passage ("B").
3. Expect: B `total_ms` ≥ 40% lower; identical text; `eager_stt_ms` > 0 only in B;
   whole comparison takes under a minute.
4. Short-phrase check: one 2-second phrase with eager ON → correct text (tail-only
   path), one line, `outcome=injected`.

### B. Fast injection (SC-004)

1. All OFF, dictate a short phrase (<120 chars) into Notepad → note `inject_ms` (~300+).
2. **Fast injection** ON, same phrase → text is typed (clipboard untouched — verify by
   copying something first and pasting it manually afterwards), `inject_ms` materially
   lower (≥200 ms saved).
3. Long passage (>120 chars) with fast ON → clipboard path, `inject_ms` well below 300
   in Notepad; clipboard content restored.

### C. Overlapped correction (SC-005)

1. Mode = Per sentence, AI correction ON, Ollama up. All perf toggles OFF. Dictate 4+
   sentences in one hold; note `total_ms`.
2. **Overlapped correction** ON, repeat the same sentences.
3. Expect: `total_ms` ≥ 25% lower; sentence order in Notepad identical to spoken order.
4. Failure isolation: stop Ollama mid-dictation → affected sentences appear raw, order
   preserved, later sentences unaffected.

### D. Toggle latching (SC-002; FR-003)

1. Flip any toggle, dictate within ~2 s → the new state shows in that line's flags.
2. Start a long dictation, flip a toggle mid-hold → the line reports the *old* state;
   the next dictation reports the new one.

### E. Legacy equivalence & regression (SC-007)

All toggles OFF: dictate a fixed passage in each mode; text output identical to
pre-feature build; only additions in the log are `DICTATION` lines.

### F. Audio-path robustness (SC-006)

Mode = Per sentence; dictate continuously ~10 min (podcast playback near the mic works).
Expect: zero `audio status` overflow warnings; startup log shows one persistent-VAD
init line; no per-dictation VAD construction entries.

### G. Empty dictation (FR-012)

Hold PTT in silence 3 s, release → one `DICTATION … outcome=no_speech` line, all stage
fields 0, nothing injected.
