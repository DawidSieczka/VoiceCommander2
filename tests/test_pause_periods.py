"""Whisper closes a segment with a period at every thinking pause. The
unambiguous case (period + lowercase word) is repaired deterministically before
the LLM; the ambiguous one (period + capitalised continuation) is left to the
model, which the prompt now instructs and shows by example."""
import requests

import corrector as corr_mod
from config import AppConfig
from corrector import Corrector, drop_pause_periods, strip_pause_marks


def test_period_before_lowercase_is_dropped():
    assert drop_pause_periods("Na razie skupmy się na stylistyce. i potem będziemy iteracyjnie przechodzić") \
        == "Na razie skupmy się na stylistyce i potem będziemy iteracyjnie przechodzić"
    assert drop_pause_periods("jest bardzo mocno zestarzały. moje ostatnie poprawki") \
        == "jest bardzo mocno zestarzały moje ostatnie poprawki"


def test_period_before_capitalised_continuation_word_is_dropped():
    assert drop_pause_periods("Zmiksujemy to do tego szablonu. Ani też animowanie postaci.") \
        == "Zmiksujemy to do tego szablonu ani też animowanie postaci."
    assert drop_pause_periods("Trzy strzałki do góry. Oraz jedną grubą. Która wskazuje zasięg.") \
        == "Trzy strzałki do góry oraz jedną grubą która wskazuje zasięg."
    assert drop_pause_periods("lets focus on styling first. And then the colours. Which are too saturated") \
        == "lets focus on styling first and then the colours which are too saturated"


def test_period_before_other_capital_is_left_to_the_model():
    for t in ("Na razie nas nie interesuje animacja. Zmiksujemy to do tego szablonu.",
              "Zrób to teraz. I to jest ważne.",
              "Zrób to teraz. Ale nie dzisiaj.",
              "Run the tests. But do not push."):
        assert drop_pause_periods(t) == t


def test_abbreviations_numbers_and_sentence_end_are_kept():
    assert drop_pause_periods("Będzie tych podziałów więcej i m.in. będą bossowie.") \
        == "Będzie tych podziałów więcej i m.in. będą bossowie."
    assert drop_pause_periods("Zrób to np. tak jak ostatnio.") == "Zrób to np. tak jak ostatnio."
    assert drop_pause_periods("Wersja 2.5 działa ok. 5 sekund.") == "Wersja 2.5 działa ok. 5 sekund."
    assert drop_pause_periods("Koniec zdania.") == "Koniec zdania."


def test_english_lowercase_continuation():
    assert drop_pause_periods("lets focus on the styling first. and then the colours") \
        == "lets focus on the styling first and then the colours"
    assert drop_pause_periods("see e.g. the injector") == "see e.g. the injector"


def test_pause_marks_combined_with_ellipsis():
    assert strip_pause_marks("Zamysł jest poprawny. natomiast potrzeba... małej poprawki") \
        == "Zamysł jest poprawny natomiast potrzeba małej poprawki"


def test_corrector_applies_pause_fix_to_raw_fallback(monkeypatch):
    c = Corrector(AppConfig())
    monkeypatch.setattr(corr_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert c.correct("skupmy się na stylistyce. i potem kolory", timeout_s=1) == "skupmy się na stylistyce i potem kolory"


def test_pause_fix_can_be_disabled(monkeypatch):
    c = Corrector(AppConfig(fix_pause_marks=False))
    monkeypatch.setattr(corr_mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError()))
    assert c.correct("a. b... c", timeout_s=1) == "a. b... c"


def test_prompt_teaches_pause_period_judgment():
    assert "Przykład 4" in corr_mod._SYSTEM_PL and "stylistyce. I potem" in corr_mod._SYSTEM_PL
    assert "Example 4" in corr_mod._SYSTEM_EN and "first. And then" in corr_mod._SYSTEM_EN
