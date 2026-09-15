# Quickstart results — 2026-09-15 (implementation day)

Machine: i7-12700H, RTX 3080 Laptop (GPU not used by TTS), Windows 11 26200, Store Python
3.12.10, Claude Code 2.1.272, output device "Headphones (AirPods Pro)" via MME.

## Automated

`.venv\Scripts\python.exe -m pytest tests -q` → **129 passed** (53 pre-existing + 76 new:
text_prep 14, lang_spans 11, speak_queue 14, speak_server 10, tts_summary 6, config 2).

## A. Endpoint without Claude Code — PASS

Standalone `SpeakServer` + real `PiperWorkerBackend` on 127.0.0.1:47321.

| Check | Result |
|---|---|
| `GET /health` | `engine_state=ready`, voices jarvis / lessac |
| `POST /speak` HTTP status / latency | `202` in 1.3 ms |
| Cold engine (worker spawn + 2 voices) | 3.8–6.0 s load, first request `first_audio_ms=4156` |
| Warm engine, 3-sentence answer with 2 identifiers | `first_audio_ms=125`, `audio_s=8.4` |
| Spoken text | Polish, "config.py" via dictionary, "TtsSnapshot" via English phonemes |

## B. Hook end-to-end — PASS

`claude -p` in an isolated directory with `{"type":"http","url":"http://127.0.0.1:47321/speak"}`
under `Stop`. Log line:

```
SPEAK id=2 src=hook event=Stop session=d47d9572 outcome=spoken raw_chars=199 clean_chars=197
summarised=0 summary_ms=0 first_audio_ms=156 audio_s=10.3 sentences=3 en_spans=1
```

Claude Code printed no error and was not delayed (202 returned before processing). Earlier
probe (2026-09-15 morning): with the server down, the turn completed silently.

## C. Barge-in — PARTIAL (programmatic PASS, physical PTT pending)

`Speaker.stop()` during playback of a 4-sentence answer: stop → request concluded in
**18 ms**, `audio_s=1.3` of ~10 s played, `outcome=stopped`. The PTT wrapper in `main.py`
calls `speaker.stop()` before `pipeline.ptt_pressed()`; pressing the physical key is to
be confirmed by the user after restarting the app (the running instance predates this code).

## D–G — pending

D (markdown/code placeholders) is covered by unit tests and by sample 03 in the spike WAVs;
E (summary) unit-tested against a fake Ollama; F (device/voice switch) and G (shutdown in
the real tray app) require the user's running instance to be restarted with the new code.
Verified in the standalone run: `SpeakServer.shutdown()` + `Speaker.shutdown()` leave no
`tts_worker.py` process and only TIME_WAIT sockets on 47321.

## Phase 0 listening spike (T004) — awaiting the user's ear

WAVs in `%APPDATA%\VoiceCommander2\tts-spike\` (three answers × inject/splice, plus
`CZYTAJ.txt`). Measured: inject synthesises 2–3× faster than splice (0.5 s vs 1.6 s for a
13 s answer) and produces shorter audio (no timbre switch pauses). Default stays `inject`
until the user prefers `splice`.
