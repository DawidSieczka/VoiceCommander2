"""Whisper hesitation ellipses are removed before correction; the tray lists
the models installed in this machine's Ollama and a model switch re-probes."""
import requests

import corrector as corr_mod
from config import AppConfig
from corrector import Corrector, strip_ellipses


# --- ellipses ---

def test_mid_sentence_ellipsis_becomes_space():
    assert strip_ellipses("Tutaj jest pokazane, że... pojawił się błąd") == "Tutaj jest pokazane, że pojawił się błąd"
    assert strip_ellipses("do... okna, w którym") == "do okna, w którym"


def test_ellipsis_before_capital_becomes_sentence_break():
    assert strip_ellipses("Repozytorium leży na GitHubie... Sprawdź MCP") == "Repozytorium leży na GitHubie. Sprawdź MCP"


def test_trailing_and_unicode_ellipsis_are_dropped():
    assert strip_ellipses("w którym będę mógł...") == "w którym będę mógł"
    assert strip_ellipses("Długoterminowo oczekuję, że… moja gra") == "Długoterminowo oczekuję, że moja gra"
    assert strip_ellipses("a miecz tak jakby miał dwie....") == "a miecz tak jakby miał dwie"


def test_text_without_ellipsis_is_untouched():
    t = "Wersja 2.5 działa. Sprawdź git push."
    assert strip_ellipses(t) == t


def test_raw_fallback_is_also_stripped(monkeypatch):
    c = Corrector(AppConfig())
    monkeypatch.setattr(corr_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert c.correct("Chciałbym... żeby to działało", timeout_s=1) == "Chciałbym żeby to działało"


def test_llm_receives_stripped_text(monkeypatch):
    c = Corrector(AppConfig())
    seen = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Chciałbym, żeby to działało."}, "done_reason": "stop"}

    def fake_post(url, json, timeout):
        seen["user"] = json["messages"][1]["content"]
        return R()

    monkeypatch.setattr(corr_mod.requests, "post", fake_post)
    assert c.correct("Chciałbym... żeby to działało", timeout_s=1) == "Chciałbym, żeby to działało."
    assert "..." not in seen["user"]


def test_strip_can_be_disabled(monkeypatch):
    c = Corrector(AppConfig(fix_pause_marks=False))
    monkeypatch.setattr(corr_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert c.correct("a... b", timeout_s=1) == "a... b"


# --- model switch re-probes ---

def test_refresh_wakes_keepalive_only_when_model_changed(monkeypatch):
    cfg = AppConfig(ollama_model="qwen3.5:2b")
    c = Corrector(cfg)

    class R:
        status_code = 200

    monkeypatch.setattr(corr_mod.requests, "post", lambda *a, **k: R())
    assert c._warm_up() == (True, "ok")
    c.available, c.status = True, "ok"
    c.refresh()
    assert not c._wake.is_set() and c.status == "ok"       # same model: nothing to do
    cfg.ollama_model = "gemma4:e2b-it-qat"
    c.refresh()
    assert c._wake.is_set() and c.status == "starting"     # tray grays out until probed


# --- tray: installed models list ---

class _FakeTagsResponse:
    def __init__(self, names):
        self._names = names

    def raise_for_status(self):
        pass

    def json(self):
        return {"models": [{"name": n} for n in self._names]}


def _tray(cfg):
    import tray as tray_mod
    t = tray_mod.Tray.__new__(tray_mod.Tray)
    t.cfg = cfg
    return t


def test_tray_lists_installed_models_sorted_with_current_checked(monkeypatch):
    import requests as req
    monkeypatch.setattr(req, "get", lambda url, timeout: _FakeTagsResponse(["qwen3.5:2b", "gemma4:e2b-it-qat"]))
    t = _tray(AppConfig(ollama_model="qwen3.5:2b"))
    items = list(t._ollama_model_items())
    assert [i.text for i in items] == ["gemma4:e2b-it-qat", "qwen3.5:2b"]
    assert [i.checked for i in items] == [False, True]


def test_tray_marks_configured_model_missing_on_this_machine(monkeypatch):
    import requests as req
    monkeypatch.setattr(req, "get", lambda url, timeout: _FakeTagsResponse(["llama3:8b"]))
    t = _tray(AppConfig(ollama_model="qwen3.5:2b"))
    items = list(t._ollama_model_items())
    assert items[0].text.startswith("qwen3.5:2b (not installed here")
    assert items[0].checked and not items[0].enabled
    assert items[1].text == "llama3:8b" and items[1].enabled


def test_tray_shows_offline_when_ollama_down(monkeypatch):
    import requests as req

    def boom(url, timeout):
        raise req.ConnectionError()

    monkeypatch.setattr(req, "get", boom)
    t = _tray(AppConfig(ollama_model="qwen3.5:2b"))
    items = list(t._ollama_model_items())
    assert items[0].text == "Ollama not running" and not items[0].enabled
    assert items[1].text == "qwen3.5:2b (configured)"
