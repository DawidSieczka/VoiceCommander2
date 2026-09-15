"""Loopback HTTP server that receives Claude Code answers (feature 002).

POST /speak   hook JSON (last_assistant_message) or {"text": ...}  -> 202 {} at once
POST /stop    -> 202 {}
GET  /health  -> engine/voice/queue state

Bound to 127.0.0.1 only (constitution I). Never returns a hook "decision", never
5xx for a well-formed body, so Claude Code is never delayed or looped
(contracts/http-api.md). Also writes the hook snippets the user pastes into
~/.claude/settings.json (contracts/hook-install.md) — the app never edits that file.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from config import APPDATA_DIR, AppConfig

log = logging.getLogger("tts.server")

MAX_BODY = 1 << 20
HOOKS_DIR = APPDATA_DIR / "hooks"


class SpeakServer:
    def __init__(self, cfg: AppConfig, speaker, on_state_change=lambda: None):
        self.cfg = cfg
        self.speaker = speaker
        self._on_state_change = on_state_change
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: Optional[int] = None

    # --- lifecycle ---

    def start(self) -> bool:
        port = int(self.cfg.tts_server_port)
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_a):      # keep stdlib access log out of our log
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.split("?")[0] == "/health":
                    self._send(200, server.health())
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                path = self.path.split("?")[0]
                if path not in ("/speak", "/stop"):
                    self._send(404, {"error": "not found"})
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n > MAX_BODY:
                    self._send(413, {"error": "body too large"})
                    return
                raw = self.rfile.read(n) if n else b""
                if path == "/stop":
                    server.speaker.stop("POST /stop")
                    self._send(202, {})
                    return
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype and ctype != "application/json":
                    self._send(415, {"error": "expected application/json"})
                    return
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("not an object")
                except Exception:
                    self._send(400, {"error": "invalid JSON"})
                    return
                # Respond first, then hand off: the hook must never wait on synthesis.
                self._send(202, {})
                try:
                    server.handle_speak(payload)
                except Exception:
                    log.exception("speak request handling failed")

        class QuietServer(ThreadingHTTPServer):
            allow_reuse_address = False
            daemon_threads = True

            def handle_error(self, request, client_address):
                # axios keeps the connection alive and resets it later; that is
                # not an error worth a traceback in the app log.
                exc = sys.exc_info()[1]
                if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
                    return
                log.exception("speak server request failed")

        try:
            self._httpd = QuietServer(("127.0.0.1", port), Handler)
        except OSError as e:
            self.speaker.server_reason = f"port {port} busy ({e.strerror or e})"
            log.error("speak server cannot bind 127.0.0.1:%d: %s — read-back endpoint disabled", port, e)
            self._on_state_change()
            return False
        self.port = self._httpd.server_address[1]
        self.speaker.server_reason = ""
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="speak-server", daemon=True)
        self._thread.start()
        log.info("speak server listening on http://127.0.0.1:%d (POST /speak, /stop; GET /health)", self.port)
        return True

    def shutdown(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                log.exception("speak server shutdown failed")
        if self._thread is not None:
            self._thread.join(timeout=3)
        log.info("speak server stopped")

    # --- requests ---

    def handle_speak(self, payload: dict) -> None:
        event = str(payload.get("hook_event_name") or ("manual" if "text" in payload else "Stop"))
        text = payload.get("text")
        if text is None:
            text = payload.get("last_assistant_message") or ""
        source = "hook" if "hook_event_name" in payload else "manual"
        lang = payload.get("lang")
        if lang not in ("pl", "en"):
            lang = None
        if payload.get("stop_hook_active"):
            log.debug("stop_hook_active=true on session %s (informational)", str(payload.get("session_id"))[:8])
        self.speaker.submit(str(text), source=source, event=event,
                            session_id=str(payload.get("session_id") or ""),
                            cwd=str(payload.get("cwd") or ""), lang=lang)

    def health(self) -> dict:
        sp = self.speaker
        return {
            "enabled": bool(self.cfg.tts_enabled),
            "engine": self.cfg.tts_backend,
            "engine_state": sp.state,
            "engine_reason": sp.reason,
            "voice_pl": self.cfg.tts_voice_pl,
            "voice_en": self.cfg.tts_voice_en,
            "speaking": sp.speaking,
            "queue": sp._q.qsize(),
        }


# ============================================================ hook material

_VC2_SPEAK_PY = '''"""VoiceCommander2 Claude Code hook (command variant) — forwards the Stop payload.

Reads the hook JSON from stdin, POSTs it unchanged to the local speak server and
ALWAYS exits 0 without printing anything, so Claude Code is never blocked or
sent back to work. Never reads transcript_path.
"""
import json
import sys
import urllib.request

PORT = {port}

def main():
    try:
        raw = sys.stdin.read()
        json.loads(raw)                       # validate only; forward as-is
        req = urllib.request.Request(
            "http://127.0.0.1:%d/speak" % PORT, data=raw.encode("utf-8"),
            headers={{"Content-Type": "application/json"}}, method="POST")
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
'''

_README = """VoiceCommander2 — reading Claude Code answers aloud: hook installation
=====================================================================

The application listens on http://127.0.0.1:{port}/speak. Claude Code must be told to
POST its final answer there when a turn ends. Paste ONE of the two variants below into
%USERPROFILE%\\.claude\\settings.json, inside the existing "hooks" object (keep the
entries that are already there — add a "Stop" key next to them). Claude Code picks the
change up automatically; /hooks in Claude Code only shows the result.

Variant A — HTTP hook (Claude Code >= 2.1.63; verified on 2.1.272). File: hook-http.json
Variant B — command hook (any version). File: hook-command.json (uses vc2_speak.py here).

Test without Claude Code:
  curl -X POST http://127.0.0.1:{port}/speak -H "Content-Type: application/json" ^
       -d "{{\\"last_assistant_message\\":\\"Gotowe. Zaktualizowalem plik config.py.\\"}}"
  curl http://127.0.0.1:{port}/health

Notes
- Only the final answer of the main session is read (event Stop). To also read subagent
  results, add the same hook under "SubagentStop" and enable tts_speak_subagents in
  config.json.
- If VoiceCommander2 is not running, Claude Code shows no error and is not delayed.
- Pressing the push-to-talk key or tray > Read Claude answers > Stop reading interrupts.
- Pronunciation fixes: edit pronunciation.txt next to config.json (term = spoken form).
"""


def write_hook_material(port: int, hooks_dir: Path = HOOKS_DIR, python: Optional[str] = None) -> Path:
    """Generate snippets + helper script; safe to call repeatedly (overwrites)."""
    hooks_dir.mkdir(parents=True, exist_ok=True)
    py = (python or sys.executable).replace("\\", "/")
    script = hooks_dir / "vc2_speak.py"
    script.write_text(_VC2_SPEAK_PY.format(port=port), encoding="utf-8")
    http_snippet = {"hooks": {"Stop": [{"hooks": [
        {"type": "http", "url": f"http://127.0.0.1:{port}/speak", "timeout": 5}]}]}}
    cmd_snippet = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": f'"{py}" "{str(script).replace(chr(92), "/")}"',
         "timeout": 5, "async": True}]}]}}
    (hooks_dir / "hook-http.json").write_text(json.dumps(http_snippet, indent=2), encoding="utf-8")
    (hooks_dir / "hook-command.json").write_text(json.dumps(cmd_snippet, indent=2), encoding="utf-8")
    readme = hooks_dir / "README-hooks.txt"
    readme.write_text(_README.format(port=port), encoding="utf-8")
    log.info("hook material written to %s", hooks_dir)
    return readme
