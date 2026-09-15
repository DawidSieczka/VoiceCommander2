"""T017: loopback speak server — body shapes, status codes, instant 202, health,
hook material, shutdown frees the port (FR-001, FR-002, FR-015, FR-016)."""
import http.client
import json
import socket
import time

import pytest

import speak_server as ss
from config import AppConfig


class RecordingSpeaker:
    """Minimal Speaker surface used by the server."""

    def __init__(self):
        self.submits = []
        self.stops = []
        self.state = "ready"
        self.reason = ""
        self.server_reason = ""
        self.speaking = False

        class Q:
            def qsize(self_inner):
                return 0
        self._q = Q()
        self.delay = 0.0

    def submit(self, text, **kw):
        if self.delay:
            time.sleep(self.delay)
        self.submits.append((text, kw))

    def stop(self, reason="", outcome="stopped"):
        self.stops.append(reason)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    cfg = AppConfig(tts_enabled=True, tts_server_port=_free_port())
    sp = RecordingSpeaker()
    srv = ss.SpeakServer(cfg, sp)
    assert srv.start()
    yield srv, sp, cfg
    srv.shutdown()


def _post(port, path, body, ctype="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": ctype} if ctype else {}
    t0 = time.perf_counter()
    conn.request("POST", path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw or b"{}"), (time.perf_counter() - t0) * 1000


def test_hook_payload_is_accepted_and_forwarded(server):
    srv, sp, cfg = server
    payload = {"session_id": "abc12345-x", "cwd": "D:\\proj", "hook_event_name": "Stop",
               "stop_hook_active": False, "last_assistant_message": "Gotowe. Plik `a.py`.",
               "transcript_path": "C:\\nope.jsonl"}
    status, body, _ = _post(srv.port, "/speak", payload)
    assert status == 202 and body == {}
    time.sleep(0.05)
    assert len(sp.submits) == 1
    text, kw = sp.submits[0]
    assert text == "Gotowe. Plik `a.py`."
    assert kw["source"] == "hook" and kw["event"] == "Stop"
    assert kw["session_id"] == "abc12345-x" and kw["cwd"] == "D:\\proj" and kw["lang"] is None


def test_minimal_body_and_lang(server):
    srv, sp, _ = server
    status, _, _ = _post(srv.port, "/speak", {"text": "Hello there.", "lang": "en"})
    assert status == 202
    time.sleep(0.05)
    text, kw = sp.submits[0]
    assert text == "Hello there." and kw["source"] == "manual" and kw["event"] == "manual" and kw["lang"] == "en"


def test_subagent_event_is_forwarded_with_its_name(server):
    srv, sp, _ = server
    _post(srv.port, "/speak", {"hook_event_name": "SubagentStop", "last_assistant_message": "x"})
    time.sleep(0.05)
    assert sp.submits[0][1]["event"] == "SubagentStop"   # the speaker decides ignored_event


def test_error_codes(server):
    srv, sp, _ = server
    assert _post(srv.port, "/speak", b"{not json", )[0] == 400
    assert _post(srv.port, "/speak", b"[1,2]")[0] == 400
    assert _post(srv.port, "/speak", b"text", ctype="text/plain")[0] == 415
    assert _post(srv.port, "/nope", {"text": "x"})[0] == 404
    big = json.dumps({"text": "x" * (ss.MAX_BODY + 10)}).encode()
    assert _post(srv.port, "/speak", big)[0] == 413
    assert sp.submits == []


def test_empty_and_missing_text_still_202(server):
    srv, sp, _ = server
    assert _post(srv.port, "/speak", {})[0] == 202
    assert _post(srv.port, "/speak", {"hook_event_name": "Stop"})[0] == 202
    time.sleep(0.05)
    assert [t for t, _ in sp.submits] == ["", ""]


def test_response_is_sent_before_processing(server):
    srv, sp, _ = server
    sp.delay = 0.6
    _, _, ms = _post(srv.port, "/speak", {"text": "slow"})
    assert ms < 300


def test_stop_and_health(server):
    srv, sp, cfg = server
    assert _post(srv.port, "/stop", b"")[0] == 202
    assert sp.stops == ["POST /stop"]
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.request("GET", "/health")
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    assert resp.status == 200
    assert body["enabled"] is True and body["engine_state"] == "ready"
    assert body["voice_pl"] == cfg.tts_voice_pl and body["queue"] == 0


def test_port_busy_sets_reason_and_does_not_raise():
    port = _free_port()
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        sp = RecordingSpeaker()
        srv = ss.SpeakServer(AppConfig(tts_server_port=port), sp)
        assert srv.start() is False
        assert f"port {port} busy" in sp.server_reason
        srv.shutdown()
    finally:
        blocker.close()


def test_shutdown_frees_port(server):
    srv, _, cfg = server
    srv.shutdown()
    s = socket.socket()
    s.bind(("127.0.0.1", cfg.tts_server_port))
    s.close()


def test_hook_material_written(tmp_path):
    readme = ss.write_hook_material(47399, hooks_dir=tmp_path, python="C:/py/python.exe")
    assert readme.exists()
    http_snip = json.loads((tmp_path / "hook-http.json").read_text(encoding="utf-8"))
    cmd_snip = json.loads((tmp_path / "hook-command.json").read_text(encoding="utf-8"))
    assert http_snip["hooks"]["Stop"][0]["hooks"][0] == {
        "type": "http", "url": "http://127.0.0.1:47399/speak", "timeout": 5}
    cmd = cmd_snip["hooks"]["Stop"][0]["hooks"][0]
    assert cmd["type"] == "command" and cmd["async"] is True
    assert "C:/py/python.exe" in cmd["command"] and "vc2_speak.py" in cmd["command"]
    script = (tmp_path / "vc2_speak.py").read_text(encoding="utf-8")
    assert "PORT = 47399" in script and "transcript_path" not in script.split('"""', 2)[2]
    compile(script, "vc2_speak.py", "exec")   # generated script is valid Python
