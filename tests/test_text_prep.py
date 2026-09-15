"""T009: markdown -> speech text (FR-004), sentence splitting, language detection."""
import text_prep as tp


def _clean(md, **kw):
    text, _ = tp.strip_markdown(md, **kw)
    return text


def test_fenced_code_becomes_placeholder_and_is_counted():
    md = "Zrobione.\n\n```python\nprint(1)\n```\n\nKoniec."
    text, st = tp.strip_markdown(md)
    assert "print" not in text
    assert "fragment kodu" in text
    assert st.code_blocks == 1
    assert text.startswith("Zrobione.") and text.endswith("Koniec.")


def test_fenced_code_omitted_when_strip_code_false():
    text, st = tp.strip_markdown("A.\n\n```\nx = 1\n```\n\nB.", strip_code=False)
    assert "fragment kodu" not in text and "x = 1" not in text
    assert st.code_blocks == 1


def test_unterminated_fence_is_stripped():
    text, st = tp.strip_markdown("Tekst.\n\n```\ndef f():\n    pass\n")
    assert "def f" not in text and st.code_blocks == 1


def test_table_becomes_placeholder():
    md = "Wyniki:\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nDalej."
    text, st = tp.strip_markdown(md)
    assert "|" not in text and "tabela" in text and st.tables == 1


def test_headings_emphasis_links_urls_stripped_text_kept():
    md = "## Co zrobiłem\n\nTo **ważne** i *bardzo* ___istotne___. Zobacz [docs](https://x.y/z) i https://a.b/c."
    text, st = tp.strip_markdown(md)
    assert "#" not in text and "*" not in text and "http" not in text
    assert "Co zrobiłem." in text and "ważne" in text and "istotne" in text and "docs" in text
    assert st.headings == 1 and st.links == 1 and st.urls == 1


def test_inline_code_kept_or_dropped():
    assert "config.py" in _clean("Plik `config.py` gotowy.")
    assert "config.py" not in _clean("Plik `config.py` gotowy.", read_inline_code=False)


def test_snake_case_survives_underscore_emphasis():
    assert "list_output_devices" in _clean("Funkcja list_output_devices oraz _kursywa_ tu.")
    assert "_kursywa_" not in _clean("Funkcja list_output_devices oraz _kursywa_ tu.")


def test_list_items_become_separate_sentences():
    md = "- Dodałem A\n- Poprawiłem B\n1. Trzeci punkt"
    sents = tp.split_sentences(_clean(md))
    assert sents == ["Dodałem A.", "Poprawiłem B.", "Trzeci punkt."]


def test_english_placeholders():
    text, _ = tp.strip_markdown("Done.\n\n```\nx\n```", lang="en")
    assert "code block" in text


def test_sentence_split_guards_abbreviations_and_versions():
    s = tp.split_sentences("Użyj np. modelu 2.1.272 tzn. nowego. Drugie zdanie! Trzecie?")
    assert s == ["Użyj np. modelu 2.1.272 tzn. nowego.", "Drugie zdanie!", "Trzecie?"]


def test_long_sentence_is_split_at_clause_boundary():
    long = ", ".join(["bardzo długa fraza numer %d" % i for i in range(30)]) + "."
    parts = tp.split_sentences(long)
    assert len(parts) >= 2
    assert all(len(p) <= tp.MAX_SENTENCE_CHARS for p in parts)
    assert " ".join(parts).replace("  ", " ").count("fraza") == 30


def test_detect_lang():
    assert tp.detect_lang("Zaktualizowałem plik i uruchomiłem testy") == "pl"
    assert tp.detect_lang("I updated the file and ran the tests") == "en"
    assert tp.detect_lang("config.py") == "pl"   # no evidence -> default Polish


def test_prepare_empty_after_stripping():
    assert tp.prepare("```\nonly code\n```", strip_code=False, rules=[]).is_empty()
    assert tp.prepare("   \n\n", rules=[]).is_empty()
    assert not tp.prepare("```\nonly code\n```", rules=[]).is_empty()   # placeholder spoken


def test_prepare_counts_and_clean_text():
    pt = tp.prepare("# T\n\nA `x` b.\n\n```\nc\n```", rules=[])
    assert pt.stats.code_blocks == 1 and pt.stats.headings == 1 and pt.stats.inline_code == 1
    assert pt.clean_chars == len(pt.clean_text) > 0
    assert len(pt.sentences) == 3   # "T.", "A x b.", "fragment kodu."
