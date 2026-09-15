# Implementation Plan: Spoken Read-Back of Claude Code Answers (local TTS)

**Branch**: `002-speak-claude-answers` | **Date**: 2026-09-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-speak-claude-answers/spec.md`

## Summary

When a Claude Code turn ends, its `Stop` hook POSTs the hook JSON to a loopback HTTP
server inside the tray app. The app strips markdown, fixes pronunciation of English
identifiers, optionally summarises long answers with local Ollama, synthesises Polish
speech with Piper (running in a supervised worker subprocess) and plays it through a
dedicated output stream. Pressing push-to-talk or a tray item stops playback within
100 ms. Everything runs offline after a one-time voice download. Design decisions and
their evidence are in [research.md](research.md); every non-trivial claim there was
verified on this machine or against the official docs.

## Technical Context

**Language/Version**: Python 3.12.10 (Microsoft Store build; `.venv` in repo). Note the
Store build virtualises `%APPDATA%`/`%LOCALAPPDATA%` into
`…\Packages\PythonSoftwareFoundation.Python.3.12_…\LocalCache\` — the code keeps using
the environment variables; only documentation of paths needs the caveat.

**Primary Dependencies**: existing — `sounddevice`, `numpy`, `requests`, `pystray`,
`keyboard`, `onnxruntime` (transitively present). New, optional — `piper-tts==1.8.0`
(GPL-3.0, installed from `requirements-tts.txt`, used only by the worker subprocess).
No web framework (stdlib `http.server`), no markdown library (regex), no resampler
(output stream runs at the voice's 22 050 Hz).

**Storage**: `config.json` (15 new flat keys), `pronunciation.txt`, generated hook files
under `%APPDATA%\VoiceCommander2\hooks\`, voice files under
`%LOCALAPPDATA%\VoiceCommander2\models\tts\`.

**Testing**: pytest, fakes in `tests/conftest.py`; no audio, network, Ollama or Claude
Code needed for the unit suite; manual smoke per quickstart.md.

**Target Platform**: Windows 11, single user, tray app; Claude Code ≥ 2.1.63 for HTTP
hooks (2.1.272 verified), command-hook fallback for anything older.

**Project Type**: desktop tray utility (flat module layout).

**Performance Goals**: first audio ≤ 1.5 s after the hook fires (measured 0.34 s
synthesis for sentence 1 + ≈ 50 ms plumbing on CPU); stop ≤ 100 ms; hook response ≤ 50 ms;
zero measurable impact on dictation latency (separate threads/process, no shared locks
with the pipeline).

**Constraints**: offline (constitution I); no GPU use (shared, nearly full, and Piper CUDA
is broken with current wheels); dictation states win over speaking in the tray; the app
never edits Claude Code settings; worker subprocess must die with the app.

**Scale/Scope**: one user, several concurrent Claude Code sessions posting to one
endpoint; answers 0–20 k characters; two voices resident (~350 MB RSS in the worker).

## Constitution Check

*GATE: passed pre-research; re-checked after design (this document).*

| Principle | Compliance |
|---|---|
| I. Local-first & private | PASS with one explicit extension: a one-time download of TTS voice files from Hugging Face, same policy and location as STT models. Inbound server binds 127.0.0.1 only. Answer text stays on the machine; the only outbound calls are to local Ollama. Spoken text is logged truncated to 120 chars like `STT`/`LLM` lines — this is Claude's output, not the user's dictation. |
| II. Never lose the user's words | PASS — the feature never touches dictation paths. Its own analogue: summarisation, engine or device failures fall back to the full text or to a logged outcome; a failed request never blocks Claude Code. |
| III. Responsiveness | PASS — hook request answered before processing; synthesis in a separate process; playback callback trivial (queue pop); PTT press calls `tts.stop()` (non-blocking flag + `abort()`) before existing handling; the keyboard hook thread is untouched. `first_audio_ms` logged per request. |
| IV. Fail soft | PASS — piper missing → feature grayed with reason; worker crash → respawn with back-off, request logged `error`; port busy → server disabled with reason; device vanished → stream error logged, next request retries default device. Single-instance mutex unchanged. |
| V. (Testing gate) | PASS — text_prep, span segmentation, queue policy, stale-id discard, stop semantics, server parsing and config compatibility are pure/fakeable and unit-tested; tray, real audio and the hook path are covered by the documented manual smoke. |
| VI. Simplicity & small surface | PASS with justification: 4 new modules (`text_prep.py`, `tts.py`, `tts_worker.py`, `speak_server.py`) each one concern; one optional dependency (`piper-tts`) justified in research R2/R3; flat config keys with defaults; no framework. The worker subprocess is the one piece of added machinery — justified by GPL isolation, crash isolation and hard-stop (R3). |
| VII. Observability | PASS — one `SPEAK` summary line per request with outcome and timings; cause lines for stripping, dictionary hits, language spans, summary skip reasons, supersession, engine load/respawn. |
| Threads named/daemonised, clean shutdown | PASS — `speak-server`, `tts-worker`, `tts-loader`, `tts-player`; `on_exit` stops server, player, worker (terminate → kill after 2 s). |

## Project Structure

### Documentation (this feature)

```text
specs/002-speak-claude-answers/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R10 decisions with evidence
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── hook-install.md
│   └── config-and-tray.md
└── tasks.md
```

### Source Code (repository root)

```text
text_prep.py        # markdown → sentences of (text, lang) spans; dictionary; heuristics (pure)
tts.py              # TTSBackend protocol, PiperWorkerBackend (supervisor), Player, Speaker (queue+policy+stop)
tts_worker.py       # subprocess: imports piper, stdio frame protocol; the only file that imports piper
speak_server.py     # ThreadingHTTPServer, request parsing, hook-material generation
corrector.py        # + summarize() (same Ollama plumbing, new prompt pair)
audio.py            # + list_output_devices(), _resolve_output_device()
config.py           # + 15 tts_* fields, TtsSnapshot
main.py             # wiring, PTT wrapper (stop before ptt_pressed), shutdown order, status map
tray.py             # "Read Claude answers" submenu, speaking colour, labels with reasons
requirements-tts.txt
tests/test_text_prep.py, test_lang_spans.py, test_speak_queue.py,
tests/test_speak_server.py, test_tts_summary.py, test_config_compat.py (extended)
```

## Architecture

```
Claude Code ──Stop hook (http or command→vc2_speak.py)──▶ POST 127.0.0.1:47321/speak
                                                               │ 202 {} immediately
                                                     speak_server.py (thread speak-server)
                                                               │ SpeakRequest(id, text, snapshot)
                                                     tts.Speaker._speak_q  (thread tts-worker)
                                                               │  policy latest → stop() first
                                                     text_prep.prepare()  ──▶ [corrector.summarize()]
                                                               │ sentences of spans
                                                     PiperWorkerBackend ⇄ tts_worker.py (subprocess, pipes)
                                                               │ int16 chunks per sentence
                                                     Player (sd.OutputStream 22050, thread tts-player)
                                                               │
                                              PTT press / tray "Stop reading" / POST /stop ──▶ Speaker.stop()
```

Worker protocol (stdio, binary): request `{"id", "op": "load"|"synth"|"phonemize"|"quit",
…}` as one JSON line; responses as frames `<u32 len><u8 type><payload>` where type
`A` = PCM chunk, `J` = JSON (done/error/phonemes). The supervisor reads on a dedicated
thread, routes by id, discards frames for stale ids, restarts the worker on EOF/timeout
(back-off 1 s, 5 s, 30 s) and reports state to the tray.

## Phases

**Phase 0 — spike (½ day)**: `tts_worker.py` + a throwaway script: synthesise 10 real
answers from the logs with (a) dictionary + English phoneme injection and (b) two-voice
splicing; the user listens and picks (research R4). Also confirm worker RSS and respawn
timing. Outcome recorded in research.md before Phase 2.

**Phase 1 — foundation**: config keys + snapshot, `text_prep.py` with tests, `audio.py`
output-device helpers, `Speaker`/`Player` with `FakeEngine` tests, worker supervisor.

**Phase 2 — MVP (US1 + US2)**: `speak_server.py`, wiring in `main.py`, PTT stop wrapper,
tray on/off + Stop reading, hook material generation, quickstart A–C pass.

**Phase 3 — quality (US3 + US4)**: pronunciation dictionary + injection/splicing per
spike result, placeholders, path handling, sample-based tests, quickstart D.

**Phase 4 — options (US5 + US6)**: `summarize()`, voice/speed/device/queue menus, speaking
tray state, README/DOKUMENTACJA sections, quickstart E–G.

## Decisions that need the user's confirmation (defaults applied in the plan)

| # | Decision | Default in plan | Alternative |
|---|---|---|---|
| 1 | Piper process model | Worker subprocess (GPL isolation, crash isolation, hard stop) | In-process import: ~120 fewer lines; the combined program is GPL-licensed under the FSF reading; fine for private use |
| 2 | Summarise long answers by default | Off | On with threshold 600 (handoff) |
| 3 | Read `SubagentStop` answers | Off | On (noisy with parallel agents) |
| 4 | Inline code | Read its content | Skip |
| 5 | Code-switching technique | Dictionary + phoneme injection, splicing fallback, chosen by the Phase 0 listening test | Splicing only (handoff) |
| 6 | Chatterbox backend | Not in this feature (VRAM already ~7.1/8 GB used; torch stack) | Separate later feature behind `tts_backend` |

## Complexity Tracking

| Item | Why it is needed | Simpler alternative rejected because |
|---|---|---|
| Worker subprocess + frame protocol | GPL boundary, crash isolation, hard stop, keeps 4.5 s import out of the main process | In-process import couples the MIT app to a GPL module and a hung ONNX call cannot be interrupted |
| Two voices resident (pl + en) | English phonemisation for identifiers | Polish-only voice mispronounces every identifier (verified) |
| Request ids with stale-result discard | `latest` policy and PTT stop must drop in-flight synthesis/summary | Locks around long calls would block stop |
| Generated hook files instead of editing settings | User's settings already hold unrelated hooks; a merge bug would break them | Auto-install is convenient but risky and irreversible without user review |

## Risks

1. English phonemes in a Polish model may sound off → Phase 0 spike decides; splicing is
   ready as fallback.
2. Piper project health (alpha, seeking maintainers) → pin `piper-tts==1.8.0`,
   `onnxruntime<2`; voices are vendored locally; backend interface allows swapping.
3. Port 47321 conflicts or firewall prompts → loopback binding does not trigger the
   Windows Firewall prompt for inbound loopback [I]; port configurable; tray shows reason.
4. Many concurrent sessions posting at once → `latest` policy; log shows session/cwd.
5. Worker RSS (~350 MB) on a memory-constrained laptop → unload voices after 30 min idle
   (configurable later if needed; not in v1).
