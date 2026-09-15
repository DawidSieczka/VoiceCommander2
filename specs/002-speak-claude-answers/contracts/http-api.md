# Contract: Local speak server (HTTP, loopback only)

Bind: `127.0.0.1:<tts_server_port>` (default `47321`). Never `0.0.0.0`. Implementation:
stdlib `http.server.ThreadingHTTPServer` on a daemon thread named `speak-server`.
No authentication (loopback only); requests larger than 1 MiB are rejected with 413.

## POST /speak

Accepted bodies (Content-Type `application/json`; anything else → 415):

1. **Claude Code hook payload** (verified on 2.1.272, identical for `type: command` stdin
   and `type: http` POST):

   ```json
   {
     "session_id": "b7bb2494-…",
     "transcript_path": "C:\\Users\\…\\<session>.jsonl",
     "cwd": "D:\\Projects\\AI\\VoiceCommander2",
     "prompt_id": "48f14120-…",
     "permission_mode": "default",
     "hook_event_name": "Stop",
     "stop_hook_active": false,
     "last_assistant_message": "Plik `config.py` to **centralny** punkt …",
     "background_tasks": [],
     "session_crons": []
   }
   ```

   Field used: `last_assistant_message` (markdown, UTF-8). `transcript_path` is never
   opened (it lags one turn; anthropics/claude-code#74340). `hook_event_name` other than
   `Stop` is spoken only if `tts_speak_subagents=true` (for `SubagentStop`); otherwise
   logged `outcome=ignored_event`.

2. **Minimal body**: `{"text": "...", "lang": "pl" | "en" (optional), "source": "..."
   (optional)}`.

Precedence: `text` if present, else `last_assistant_message`. Empty/whitespace → 202 with
`outcome=empty` in the log.

Response: **always `202 Accepted`, body `{}`**, within 50 ms; processing continues after
the response. Exceptions: 400 (invalid JSON), 413, 415. Never 5xx for a well-formed body,
even when read-back is disabled or the engine is unavailable (those are logged outcomes),
so the hook never surfaces an error in Claude Code.

## POST /stop

Stops playback, clears the queue, marks in-flight synthesis stale. Body ignored.
Response `202 {}`.

## GET /health

`200` with

```json
{
  "enabled": true,
  "engine": "piper",
  "engine_state": "ready" | "loading" | "missing" | "error",
  "engine_reason": "",
  "voice_pl": "pl_PL-jarvis_wg_glos-medium",
  "voice_en": "en_US-lessac-medium",
  "speaking": false,
  "queue": 0
}
```

Used by the quickstart and by the tray "Open hook instructions" page text.

## Log lines (logger `tts`)

One summary line per request, mirroring the `DICTATION` line convention:

```
SPEAK id=<n> src=<hook|manual> event=<Stop|…> session=<8 chars> outcome=<spoken|stopped|empty|disabled|ignored_event|error> raw_chars=<int> clean_chars=<int> summarised=<0|1> summary_ms=<int> first_audio_ms=<int> audio_s=<float> sentences=<int> en_spans=<int> voice=<id> speed=<float> reason=<text|->
```

Plus cause lines for every non-obvious decision (constitution VII): stripped block counts,
dictionary hits, language spans, why summarisation was skipped, why a request was dropped
by the `latest` policy (`superseded by id=<n>`), engine load/unload timing.

## Concurrency

- Requests are serialised through `_speak_q` (worker thread `tts-worker`, `None`
  sentinel, same idiom as `pipeline._inject_q`).
- `latest` policy: enqueueing a new request calls `stop()` first.
- Each request carries a monotonically increasing id; the worker discards any
  synthesised chunk or summary result whose id is not the current one.
