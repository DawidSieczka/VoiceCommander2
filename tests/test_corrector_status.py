"""Ollama availability detection: server-down vs model-missing vs OK, and the
tray-facing status/label behavior."""
import requests

import corrector as corr_mod
from config import AppConfig
from corrector import Corrector


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def make(monkeypatch, result):
    c = Corrector(AppConfig())

    def fake_post(*a, **k):
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)

    monkeypatch.setattr(corr_mod.requests, "post", fake_post)
    return c


def test_server_down_reports_offline(monkeypatch):
    c = make(monkeypatch, requests.ConnectionError())
    assert c._warm_up() == (False, "offline")


def test_missing_model_reports_no_model(monkeypatch):
    c = make(monkeypatch, 404)
    assert c._warm_up() == (False, "no_model")


def test_http_error_reports_error(monkeypatch):
    c = make(monkeypatch, 500)
    assert c._warm_up() == (False, "error")


def test_ok(monkeypatch):
    c = make(monkeypatch, 200)
    assert c._warm_up() == (True, "ok")


def test_state_flip_fires_callback_once(monkeypatch):
    c = make(monkeypatch, 200)
    fired = []
    c.on_change = lambda: fired.append(1)
    # Simulate two keepalive iterations with the same result: callback once.
    for _ in range(2):
        ok, status = c._warm_up()
        if (c.available, c.status) != (ok, status):
            c.available, c.status = ok, status
            c.on_change()
    assert fired == [1]
    assert (c.available, c.status) == (True, "ok")


def test_tray_label_and_enabled_state():
    import tray as tray_mod
    cfg = AppConfig(ollama_model="qwen3.5:2b")
    status = {"value": "ok"}
    t = tray_mod.Tray.__new__(tray_mod.Tray)
    t.cfg = cfg
    t._corrector_status = lambda: status["value"]
    assert t._ai_correction_label() == "AI correction"
    status["value"] = "offline"
    assert t._ai_correction_label() == "AI correction — Ollama not running"
    status["value"] = "no_model"
    assert t._ai_correction_label() == "AI correction — run: ollama pull qwen3.5:2b"
    status["value"] = "error"
    assert t._ai_correction_label() == "AI correction — Ollama error"
