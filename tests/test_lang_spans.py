"""T033: pronunciation dictionary precedence and English-token spans (FR-005)."""
import text_prep as tp
from text_prep import Rule, Span


def spans(sentence, rules=(), lang="pl"):
    out, hits = tp.build_spans(sentence, list(rules), lang)
    return [(s.lang, s.text) for s in out], hits


def test_identifier_heuristic():
    yes = ["config.py", "plan.md", "TtsSnapshot", "list_output_devices", "JSON", "API", "--fast",
           "specs/002/plan.md", "commit", "requirements.txt"]
    no = ["Zaktualizowałem", "plik", "2.1.272", "57", "i", "A", "ABCDEFGH", "—", "(nowa"]
    assert all(tp.is_english_token(t) for t in yes), [t for t in yes if not tp.is_english_token(t)]
    assert not any(tp.is_english_token(t) for t in no), [t for t in no if tp.is_english_token(t)]


def test_path_reduction_and_word_splitting():
    assert tp._reduce_path("specs/002/plan.md") == "plan dot md"
    assert tp._reduce_path("C:\\Users\\x\\config.py") == "config dot py"
    assert tp._reduce_path("list_output_devices") == "list output devices"
    assert tp._reduce_path("TtsSnapshot") == "Tts Snapshot"
    assert tp._reduce_path("--fast") == "fast"


def test_polish_sentence_with_identifiers_yields_merged_spans():
    got, hits = spans("Zaktualizowałem plik config.py i uruchomiłem testy.")
    assert got == [("pl", "Zaktualizowałem plik"), ("en", "config dot py"), ("pl", "i uruchomiłem testy.")]
    assert hits == 0


def test_flags_and_punctuation_preserved():
    got, _ = spans("Użyj flagi --fast, potem koniec.")
    assert got == [("pl", "Użyj flagi"), ("en", "fast,"), ("pl", "potem koniec.")]


def test_dictionary_polish_respelling_stays_in_polish_span():
    rules = [Rule("pytest", "pajtest", "pl"), Rule("JSON", "dżejson", "pl")]
    got, hits = spans("Uruchom pytest i zwróć JSON.", rules)
    assert got == [("pl", "Uruchom pajtest i zwróć dżejson.")]
    assert hits == 2


def test_dictionary_english_rule_and_phonemes_make_en_span():
    rules = [Rule("GitHub", "git hub", "en"), Rule("npm", "[[ ˌɛnpiːˈɛm ]]", "en")]
    got, hits = spans("Wrzuć na GitHub przez npm.", rules)
    assert got == [("pl", "Wrzuć na"), ("en", "git hub"), ("pl", "przez"), ("en", "[[ ˌɛnpiːˈɛm ]].")]
    assert hits == 2


def test_dictionary_is_case_insensitive_and_whole_token():
    rules = [Rule("git", "git", "pl")]
    got, hits = spans("GIT i digital.", rules)
    assert hits == 1
    assert got[0][1].startswith("git i")


def test_english_sentence_is_one_english_span():
    got, _ = spans("I updated config.py and ran pytest.", lang="en")
    assert got == [("en", "I updated config.py and ran pytest.")]


def test_load_pronunciation_parses_and_sorts_longest_first(tmp_path):
    p = tmp_path / "pron.txt"
    p.write_text("# c\ngit = git\ngit push = git pusz\nnpm = en pe em | en\nbroken line\n", encoding="utf-8")
    rules = tp.load_pronunciation(p)
    assert [r.term for r in rules] == ["git push", "git", "npm"]
    assert rules[2].lang == "en"
    assert tp.load_pronunciation(tmp_path / "missing.txt") == []


def test_seed_file_created_once(tmp_path):
    p = tmp_path / "pron.txt"
    tp.seed_pronunciation_file(p)
    first = p.read_text(encoding="utf-8")
    p.write_text(first + "extra = ekstra\n", encoding="utf-8")
    tp.seed_pronunciation_file(p)
    assert "extra = ekstra" in p.read_text(encoding="utf-8")
    assert len(tp.load_pronunciation(p)) >= 20


def test_prepare_on_sample_answers_finds_english_spans():
    md = ("Gotowe. Zaktualizowałem `config.py` i `tts.py`.\n\n- `TtsSnapshot` dodany\n"
          "- test `tests/test_speak_queue.py` przechodzi\n\nWersja 2.1.272 bez zmian.")
    pt = tp.prepare(md, rules=[])
    assert pt.en_spans >= 4
    flat = [(s.lang, s.text) for sent in pt.sentences for s in sent]
    assert ("en", "Tts Snapshot") in flat
    assert not any(l == "en" and "2.1.272" in t for l, t in flat)
