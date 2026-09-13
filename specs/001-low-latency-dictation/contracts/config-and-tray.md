# Contract: Configuration & Tray Surface

**Feature**: 001-low-latency-dictation

## config.json additions

New keys in `%APPDATA%\VoiceCommander2\config.json` (all optional; absent ⇒ default):

```json
{
  "perf_eager_stt": false,
  "perf_fast_injection": false,
  "perf_pipelined_correction": false,
  "short_text_chars": 120,
  "clipboard_wait_max_ms": 300
}
```

Guarantees:
- A config file written by any previous version loads without error; the three toggles
  default to `false` (legacy behavior).
- A config file written by this version loads in a previous version without error
  (previous `load()` also ignores unknown keys).
- Toggle changes persist immediately on tray click (existing save path) and apply from
  the next PTT press; the in-flight dictation is unaffected.

## Tray menu contract

New submenu inserted after "Recognition model", before the "Paused" separator block:

```
Performance (A/B)
├── Eager transcription (on-release mode)   [checkable]
├── Fast injection                           [checkable]
└── Overlapped correction (per-sentence)     [checkable]
```

- Check state always reflects the persisted config value.
- Menu text is English (existing requirement).
- No restart, dialog, or confirmation involved.
- Thresholds (`short_text_chars`, `clipboard_wait_max_ms`) are config-file only.
