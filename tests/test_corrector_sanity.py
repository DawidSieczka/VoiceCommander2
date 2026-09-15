"""Word-level sanity check on LLM corrections (corrector.sanity_check).

Cases are taken from the 2026-09-13..15 logs: the old length-ratio window let
every one of the regressions through."""
from corrector import sanity_check


def test_plain_copy_edit_passes():
    assert sanity_check("repozytorem leży na githubie.",
                        "Repozytorium leży na GitHubie.") is None


def test_punctuation_and_case_only_passes():
    assert sanity_check("chciałbym żebyś to wykonał na osobnym branczu",
                        "Chciałbym, żebyś to wykonał na osobnym branczu.") is None


def test_filler_removal_passes():
    assert sanity_check("yyy o której eee odjeżdża pociąg",
                        "O której odjeżdża pociąg?") is None


def test_half_sentence_dropped_is_rejected():
    r = sanity_check("Procentowo wzrasta prędkość ataków, konkretny procent do ustalenia później na etapie testów.",
                     "Procentowo wzrasta prędkość ataków.")
    assert r and r.startswith("dropped words")


def test_answer_instead_of_correction_is_rejected():
    r = sanity_check("o której odjeżdża pociąg",
                     "Pociąg odjeżdża o godzinie czternastej trzydzieści z peronu drugiego, proszę się pospieszyć.")
    assert r and r.startswith("added words")


def test_lost_apostrophe_token_is_rejected():
    r = sanity_check("Zweryfikuj, czy potrzebujemy zupdate'ować Claude'a, ponieważ widzę folder commands",
                     "Zweryfikuj, czy potrzebujemy zaktualizować Cloda, ponieważ widzę folder commands.")
    assert r and "lost token" in r


def test_lost_number_is_rejected():
    r = sanity_check("Wczorajsze 3 feature'y chciałbym wypchnąć.",
                     "Wczorajsze feature'y chciałbym wypchnąć.")
    assert r and "lost token '3'" in r


def test_paraphrase_is_rejected():
    r = sanity_check("Chciałbym żeby się wygenerowało sprawdzić czy nie ma jeszcze innych artefaktów",
                     "Chciałbym, aby się wygenerować sprawdzenie, czy nie ma jeszcze innych artefaktów")
    # 3 of 11 words replaced -> above the 25 % budget
    assert r and r.startswith("rewrote")


def test_repeated_word_removal_passes():
    assert sanity_check("myślę że że to to jest dobre", "Myślę, że to jest dobre.") is None


def test_short_input_allows_two_changed_words():
    assert sanity_check("3 kontów.", "3 konta.") is None


def test_empty_output_is_rejected():
    assert sanity_check("cokolwiek", "") == "empty"
