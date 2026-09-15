# Contract: configuration keys and tray surface

## New `AppConfig` fields (flat primitives, all with defaults; constitution VI)

| Key | Default | Notes |
|---|---|---|
| `tts_enabled` | `false` | Off until the user turns it on; the server still starts (so the hook probe works) but requests log `outcome=disabled`. |
| `tts_backend` | `"piper"` | `"piper"` only in this feature; the enum is reserved for a later HTTP backend. |
| `tts_voice_pl` | `"pl_PL-jarvis_wg_glos-medium"` | Voice id = file stem in the tts models dir. |
| `tts_voice_en` | `"en_US-lessac-medium"` | Used for English spans and `lang: en` requests. |
| `tts_speed` | `1.0` | Mapped to Piper `length_scale = 1 / speed`; clamped to 0.5–2.0. |
| `tts_output_device` | `""` | Output device name prefix; `""` = system default. Same matching rules as `input_device`. |
| `tts_queue_policy` | `"latest"` | `latest` or `append`. |
| `tts_strip_code` | `true` | Replace fenced blocks / tables by a spoken placeholder; `false` = omit silently. |
| `tts_read_inline_code` | `true` | Keep inline-code content (file names, flags). |
| `tts_codeswitch` | `"inject"` | `inject` = English phonemes injected into the Polish voice (one timbre); `splice` = two voices concatenated. Added so the listening spike can flip without code changes. |
| `tts_summarize` | `false` | Off by default: it costs an Ollama round-trip and the user has not asked for it to be default-on. |
| `tts_summary_threshold` | `600` | Characters of cleaned text. |
| `tts_summary_timeout_s` | `8.0` | `(connect 2 s, read timeout)`. |
| `tts_speak_subagents` | `false` | Speak `SubagentStop` payloads. |
| `tts_server_port` | `47321` | Loopback only. Changing it regenerates the hook snippets. |
| `tts_server_enabled` | `true` | Allows turning the listener off entirely. |

Removed from the handoff: `chatterbox_url` (no second backend is built in this feature;
adding the key now would be YAGNI — it is re-added by the feature that ships that backend).

Snapshot semantics: a `TtsSnapshot` (frozen dataclass like `PerfSnapshot`) is taken when a
request is accepted, so a tray change mid-reading applies from the next request.

## Files

| Path | Purpose |
|---|---|
| `%LOCALAPPDATA%\VoiceCommander2\models\tts\<voice>.onnx(.json)` | Voice files (one-time download from Hugging Face; same policy as STT). |
| `%APPDATA%\VoiceCommander2\pronunciation.txt` | `term = spoken form` per line, `#` comments, UTF-8, case-insensitive match on whole tokens; hot-reloaded per request like `dictionary.txt`. Seeded with ~20 entries (npm, JSON, YAML, GitHub, Claude, pytest, CUDA, Ollama, Whisper, Piper, PowerShell, …). |
| `%APPDATA%\VoiceCommander2\hooks\README-hooks.txt`, `hook-http.json`, `hook-command.json`, `vc2_speak.py` | Generated hook material (see hook-install.md). |

## Tray: submenu "Read Claude answers"

```
Read Claude answers
├─ Enabled                       (checkbox, tts_enabled)
├─ Stop reading                  (action → tts.stop())
├─ Voice                         (dynamic radio list of downloaded pl_PL voices + "Download default voices…")
├─ Speed                         (radio: 0.8× / 1.0× / 1.2× / 1.5×)
├─ Output device                 (dynamic radio: System default + list_output_devices())
├─ Summarise long answers        (checkbox, tts_summarize; grayed with reason when Ollama unusable — reuse corrector status)
├─ Queue: latest wins            (checkbox; unchecked = append)
└─ Open hook instructions        (opens %APPDATA%\VoiceCommander2\hooks\README-hooks.txt)
```

The top-level item label is dynamic like `_ai_correction_label()`:
`Read Claude answers` when ready, `Read Claude answers (piper not installed)`,
`… (voice downloading…)`, `… (voice missing)`, `… (port 47321 busy)`. Sub-items other
than "Open hook instructions" are disabled while the engine state is not `ready`.

Also: "Open pronunciation dictionary" next to the existing "Open dictionary (vocabulary
hints)" in the bottom section.

## Tray state

New `_COLORS["speaking"]` (e.g. green) and tooltip `speaking…` via `main.on_status`.
Precedence: `recording` > `processing` > `speaking` > `idle`. Speaking state is set by the
playback worker when the output stream starts and cleared when it ends or is stopped.
