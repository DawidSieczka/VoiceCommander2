# Quickstart: Spoken Read-Back of Claude Code Answers

## Prerequisites

- `.venv` with `pip install -r requirements.txt` (adds `piper-tts`; `onnxruntime` is already
  present through `pysilero-vad`).
- First enable downloads two voices (~63 MB each) into
  `%LOCALAPPDATA%\VoiceCommander2\models\tts\`. On this machine (Store Python) the real
  path is `%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Local\VoiceCommander2\models\tts\`;
  logs are under the matching `LocalCache\Roaming\VoiceCommander2\logs`.
- Claude Code ≥ 2.1.63 (HTTP hooks). Verified on 2.1.272.

## Automated tests (no audio hardware, network or Ollama)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

New files: `tests/test_text_prep.py`, `tests/test_lang_spans.py`,
`tests/test_speak_queue.py`, `tests/test_speak_server.py`, `tests/test_tts_summary.py`,
`tests/test_config_compat.py` (extended). Fakes: `FakeEngine` (returns silence chunks with
a controllable delay), `FakePlayer` (records chunks and honours `stop()`), reuse
`FakeCorrector`/`requests.post` monkeypatch for the summary.

## Manual smoke scenarios

### A. Endpoint without Claude Code

```powershell
curl -X POST http://127.0.0.1:47321/speak -H "Content-Type: application/json" `
  -d '{"last_assistant_message":"Gotowe. Zaktualizowałem plik config.py i uruchomiłem pytest."}'
curl http://127.0.0.1:47321/health
```

Expect: `202 {}` immediately; Polish voice; "config.py" and "pytest" sound English; log has
one `SPEAK … outcome=spoken first_audio_ms=<≤1500>` line.

### B. Hook end-to-end

1. Tray → Read Claude answers → Open hook instructions; paste Variant A into
   `~/.claude/settings.json` under `"hooks"` next to the existing entries.
2. In any Claude Code session ask a short question. Reading starts as the answer completes.
3. Quit VoiceCommander2, ask again: no error, no delay in Claude Code.

### C. Barge-in

Trigger a long answer (or POST a 10-sentence text). Press the PTT key mid-sentence: silence
within 100 ms, recording starts. Repeat with the app paused from the tray. Repeat with the
tray "Stop reading".

### D. Markdown and code

POST an answer containing a heading, bullets, a table, a fenced block and a link. Only prose
and bullets are read; "fragment kodu" and "tabela" placeholders are heard once each.

### E. Summary (optional)

Set `tts_summarize=true`, POST a 1500-character answer: a 2–3 sentence Polish summary is
read; log shows `summarised=1 summary_ms=<…>`. Stop Ollama, repeat: full text is read,
log shows the fallback reason.

### F. Device and voice switches

Change the output device and voice in the tray; the next request uses them; the log shows
`voice … loaded in … s` from thread `tts-loader`. Turn a Bluetooth headset off during
playback: playback stops with a logged stream error; the next request plays on the new
default device.

### G. Shutdown

Exit from the tray while speaking: audio stops, port 47321 is free
(`netstat -ano | findstr 47321` shows nothing), no python process remains.
