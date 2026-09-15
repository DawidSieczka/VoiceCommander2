"""T041: Ollama spoken summary — success, failures and sanity fallbacks (FR-010)."""
import requests

import corrector as cmod
from config import AppConfig


class FakeResp:
    def __init__(self, status=200, content="", done_reason="stop", bad_json=False):
        self.status_code = status
        self._content = content
        self._done = done_reason
        self._bad = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return {"message": {"content": self._content}, "done_reason": self._done}


LONG = ("Zaktualizowałem plik config.py, dodałem klasę TtsSnapshot i uruchomiłem testy. " * 12)


def make(monkeypatch, resp=None, exc=None, available=True):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if exc:
            raise exc
        return resp

    monkeypatch.setattr(cmod.requests, "post", fake_post)
    c = cmod.Corrector(AppConfig())
    c.available = available
    return c, calls


def test_success_returns_summary_and_uses_chat_endpoint(monkeypatch):
    c, calls = make(monkeypatch, FakeResp(content="<streszczenie>Zaktualizowano config.py i dodano TtsSnapshot. Testy przechodzą.</streszczenie>"))
    out = c.summarize(LONG, 8.0, "pl")
    assert out == "Zaktualizowano config.py i dodano TtsSnapshot. Testy przechodzą."
    req = calls[0]
    assert req["url"].endswith("/api/chat") and req["timeout"] == (2, 8.0)
    assert req["json"]["think"] is False and req["json"]["options"]["temperature"] == 0
    assert req["json"]["messages"][0]["content"].startswith("Jesteś asystentem")
    assert "<streszczenie>" in req["json"]["messages"][1]["content"]


def test_english_prompt(monkeypatch):
    c, calls = make(monkeypatch, FakeResp(content="Updated config.py and added TtsSnapshot. Tests pass."))
    assert c.summarize(LONG, 8.0, "en")
    assert calls[0]["json"]["messages"][0]["content"].startswith("You summarise")


def test_network_error_and_http_error_return_none(monkeypatch):
    c, _ = make(monkeypatch, exc=requests.ConnectionError("down"))
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, exc=requests.Timeout("slow"))
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, FakeResp(status=404))
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, FakeResp(bad_json=True))
    assert c.summarize(LONG, 8.0) is None


def test_sanity_rejects_not_shorter_empty_or_markdown(monkeypatch):
    c, _ = make(monkeypatch, FakeResp(content=LONG))              # echoed input
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, FakeResp(content=""))
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, FakeResp(content="Ok."))
    assert c.summarize(LONG, 8.0) is None
    c, _ = make(monkeypatch, FakeResp(content="Zrobione:\n- punkt jeden\n- punkt dwa i coś jeszcze"))
    assert c.summarize(LONG, 8.0) is None
    assert cmod.summary_sanity_check(LONG, "Krótko i na temat, dwa zdania. Drugie zdanie.") is None


def test_unavailable_or_empty_input_skips_call(monkeypatch):
    c, calls = make(monkeypatch, FakeResp(content="x"), available=False)
    assert c.summarize(LONG, 8.0) is None and calls == []
    c, calls = make(monkeypatch, FakeResp(content="x"))
    assert c.summarize("   ", 8.0) is None and calls == []


def test_very_long_input_is_truncated_head_and_tail(monkeypatch):
    c, calls = make(monkeypatch, FakeResp(content="Streszczenie w dwóch zdaniach. Koniec."))
    text = "A" * 5000 + "B" * 5000
    c.summarize(text, 8.0)
    sent = calls[0]["json"]["messages"][1]["content"]
    assert len(sent) < 6000 and "A" * 100 in sent and "B" * 100 in sent and " … " in sent
