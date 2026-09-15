# Data Model: Spoken Read-Back of Claude Code Answers

All entities are in-process Python dataclasses (no database). Persistent state is limited
to `config.json` keys, the pronunciation file, generated hook files and downloaded voice
files (see contracts/config-and-tray.md).

## 1. TtsSnapshot (frozen)

Captured in `SpeakServer` when a request is accepted — the request is then immune to tray
changes (same rule as `PerfSnapshot`).

| Field | Type | Source |
|---|---|---|
| enabled | bool | `tts_enabled` |
| voice_pl, voice_en | str | config |
| speed | float (clamped 0.5–2.0) | `tts_speed` |
| output_device | str | config |
| queue_policy | `"latest"`/`"append"` | config |
| strip_code, read_inline_code | bool | config |
| summarize | bool | config AND corrector.available |
| summary_threshold | int | config |
| summary_timeout_s | float | config |
| speak_subagents | bool | config |

## 2. SpeakRequest

| Field | Type | Notes |
|---|---|---|
| id | int | Monotonic per process; the "current id" gate for stale results. |
| source | `"hook"`/`"manual"` | `hook` when `hook_event_name` present. |
| event | str | `Stop`, `SubagentStop`, `manual`. |
| session_id, cwd | str | For the log only; may be empty. |
| lang | `"pl"`/`"en"`/`None` | Explicit from body, else heuristic in text_prep. |
| raw_text | str | `text` or `last_assistant_message`. |
| received_at | float (monotonic) | `first_audio_ms` is measured from here. |
| snapshot | TtsSnapshot | |

State machine: `accepted → (superseded | empty | disabled | ignored_event)` or
`accepted → preparing → [summarising] → synthesising/playing → (spoken | stopped | error)`.
Exactly one `SPEAK` summary line is emitted per request at the terminal state
(`RequestOutcome.conclude()` is idempotent, like `DictationTiming.conclude()`).

## 3. PreparedText

| Field | Type | Notes |
|---|---|---|
| sentences | list[Sentence] | In reading order. |
| stripped_code_blocks, stripped_tables, stripped_urls | int | Logged. |
| dictionary_hits | int | Logged. |
| detected_lang | `"pl"`/`"en"` | Heuristic: share of Polish diacritics/stop-words. |

`Sentence` = list[Span]; `Span(text: str, lang: "pl"|"en")`. Consecutive spans of the same
language are merged before synthesis to minimise voice switches. A sentence longer than
`MAX_SENTENCE_CHARS = 350` is split at the nearest comma/semicolon/colon, else at a
word boundary.

## 4. Voice

| Field | Type | Notes |
|---|---|---|
| id | str | File stem, e.g. `pl_PL-jarvis_wg_glos-medium`. |
| lang | `"pl"`/`"en"` | From id prefix. |
| model_path, config_path | Path | Under `MODELS_DIR / "tts"`. |
| sample_rate | int | From `.onnx.json` (22050 for all medium voices). |
| source_url | str | Hugging Face `resolve/main` URL used for the one-time download. |
| state | `missing`/`downloading`/`loading`/`ready`/`error` | Drives tray label. |
| reason | str | Human-readable cause when `error`/`missing`. |

Known voices table (id → source):

- `pl_PL-jarvis_wg_glos-medium`, `pl_PL-justyna_wg_glos-medium`,
  `pl_PL-meski_wg_glos-medium`, `pl_PL-zenski_wg_glos-medium` →
  `https://huggingface.co/WitoldG/polish_piper_models/resolve/main/<id>.onnx[.json]` (MIT).
- `pl_PL-darkman-medium`, `pl_PL-gosia-medium`, `pl_PL-mc_speech-medium` →
  `https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL/<name>/medium/<id>.onnx[.json]`.
- `en_US-lessac-medium` → `…/en/en_US/lessac/medium/…`.

## 5. Engine (PiperBackend)

| Field | Type | Notes |
|---|---|---|
| voices | dict[lang, LoadedVoice] | `PiperVoice` instances; load ≈ 8–10 s each on this laptop → background thread `tts-loader`. |
| loaded_key | tuple(voice_pl, voice_en) | Reload only when changed (`reload_if_changed` idiom from `stt.py`). |
| state, reason | as Voice | Aggregated for the tray. |

Method contract: `synthesize(span_text, lang, speed) -> Iterator[np.ndarray(int16)]`
yields per-sentence chunks (Piper already chunks by sentence); raises only
`EngineUnavailable` (handled → `outcome=error`).

## 6. PlaybackSession

| Field | Type | Notes |
|---|---|---|
| request_id | int | |
| stream | sd.OutputStream | samplerate = voice sample rate, dtype int16, mono, `device` resolved by name prefix. |
| started_at | float | `first_audio_ms = started_at - received_at`. |
| audio_seconds | float | Sum of chunks written. |
| stop_event | threading.Event | Set by `stop()`; the writer loop checks it between ≤ 100 ms blocks (2205 samples) and calls `stream.abort()`. |

## 7. PronunciationDictionary

Ordered list of `(pattern, spoken, lang)` from `pronunciation.txt`; `lang` optional third
field (`pl` default) so the spoken form is synthesised with the right voice, e.g.
`GitHub = git hab | pl`, `JSON = jason | en`. Matching is whole-token, case-insensitive;
longest pattern first. Re-read on every request (file is tiny; mirrors `user_dictionary()`).

## 8. HookMaterial

Generated files and their inputs: port, `sys.executable`. Regenerated when port changes;
never touched otherwise (user may edit README notes).
