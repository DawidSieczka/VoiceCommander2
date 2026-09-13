"""T024: injector routing logic — thresholds, toggle-off legacy path,
refusal reasons. Win32 surfaces monkeypatched; no real clipboard/SendInput."""
import pytest

import injector as inj_mod
from injector import Injector


@pytest.fixture
def injector(monkeypatch):
    inj = Injector(method="clipboard")
    calls = {"unicode": [], "clipboard": [], "fast": []}
    monkeypatch.setattr(inj, "_inject_unicode", lambda t: calls["unicode"].append(t) or True)
    monkeypatch.setattr(inj, "_inject_clipboard", lambda t: calls["clipboard"].append(t) or True)
    monkeypatch.setattr(inj, "_inject_clipboard_fast",
                        lambda t, cap: calls["fast"].append((t, cap)) or True)
    monkeypatch.setattr(inj_mod, "_is_password_field", lambda: False)
    monkeypatch.setattr(inj_mod, "focused_window", lambda: 111)
    return inj, calls


def test_short_text_typed_directly(injector):
    inj, calls = injector
    ok, reason = inj.inject("x" * 120, fast=True, short_chars=120)
    assert ok and reason is None
    assert calls["unicode"] and not calls["clipboard"] and not calls["fast"]


def test_long_text_uses_adaptive_clipboard(injector):
    inj, calls = injector
    ok, _ = inj.inject("x" * 121, fast=True, short_chars=120, wait_max_ms=250)
    assert ok
    assert calls["fast"] == [("x" * 121, 250)]
    assert not calls["unicode"] and not calls["clipboard"]


def test_toggle_off_always_legacy_clipboard(injector):
    inj, calls = injector
    ok, _ = inj.inject("x" * 10, fast=False)
    assert ok
    assert calls["clipboard"] and not calls["unicode"] and not calls["fast"]


def test_sendinput_method_unaffected_by_fast(injector):
    inj, calls = injector
    inj.method = "sendinput"
    ok, _ = inj.inject("x" * 500, fast=True, short_chars=120)
    assert ok and calls["unicode"] and not calls["fast"]


def test_focus_drift_refused(injector, monkeypatch):
    inj, calls = injector
    monkeypatch.setattr(inj_mod, "focused_window", lambda: 222)
    ok, reason = inj.inject("abc", target_hwnd=111, fast=True)
    assert not ok and reason == "refused_focus"
    assert not calls["unicode"] and not calls["clipboard"] and not calls["fast"]


def test_password_field_refused(injector, monkeypatch):
    inj, calls = injector
    monkeypatch.setattr(inj_mod, "_is_password_field", lambda: True)
    ok, reason = inj.inject("abc")
    assert not ok and reason == "refused_password"
    assert not calls["unicode"] and not calls["clipboard"]


def test_empty_text_trivially_ok(injector):
    inj, calls = injector
    ok, reason = inj.inject("")
    assert ok and reason is None
    assert not calls["unicode"] and not calls["clipboard"]
