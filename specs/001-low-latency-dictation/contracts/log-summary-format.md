# Contract: Dictation Timing Summary Log Line

**Feature**: 001-low-latency-dictation

Exactly one INFO line per completed dictation, logger name `timing`, fixed format:

```
DICTATION mode=<mode> outcome=<outcome> total_ms=<int> stt_ms=<int> eager_stt_ms=<int> corr_ms=<int> inject_ms=<int> eager=<0|1> fastinj=<0|1> overlap=<0|1>
```

Field semantics:

| Field | Meaning |
|-------|---------|
| mode | `on_release` \| `per_sentence` \| `realtime` (snapshot at press) |
| outcome | `injected` \| `no_speech` \| `refused_focus` \| `refused_password` \| `error` |
| total_ms | PTT release → dictation concluded (last text visible or refusal decided) |
| stt_ms | STT wall time spent after release |
| eager_stt_ms | STT wall time spent during the hold (eager only; else 0) |
| corr_ms | AI-correction wall time (0 = skipped/disabled/unavailable) |
| inject_ms | injection wall time incl. clipboard wait (0 = nothing injected) |
| eager / fastinj / overlap | toggle snapshot taken at PTT press |

Guarantees:
- Emitted for all three modes, including empty dictations (`no_speech`, all stage
  fields 0) — never a crash on absent stages (FR-012).
- Never emitted twice for one dictation (record concludes once).
- In `per_sentence`, per-sentence stage times are summed across the whole utterance
  (including work done while the key was still held); total_ms spans release → last
  sentence injected, so stage sums MAY exceed total_ms in this mode.
- In `realtime`, stt_ms is the final post-release pass, eager_stt_ms is the sum of
  hold-time streaming passes; corr_ms is always 0 (no correction in realtime).
- Greppable A/B: `Select-String "DICTATION" *.log` yields directly comparable rows;
  toggle fields alone distinguish the A and B halves (SC-003).

Example:

```
2026-09-13 14:02:11 INFO    timing: DICTATION mode=on_release outcome=injected total_ms=1840 stt_ms=1210 eager_stt_ms=6480 corr_ms=520 inject_ms=95 eager=1 fastinj=1 overlap=0
```
