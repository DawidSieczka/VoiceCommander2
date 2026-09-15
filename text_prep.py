"""Markdown answer -> speakable sentences of (text, lang) spans. Pure functions.

Pipeline (feature 002, research R4/R5):
  strip_markdown()  remove/replace markdown so only prose remains
  split_sentences() sentence boundaries with abbreviation guard + long-sentence split
  build_spans()     pronunciation dictionary, then English-token detection per sentence
  prepare()         the whole thing, returning PreparedText for the speaker

No third-party dependency (constitution VI). All decisions are counted in
StripStats / PreparedText so the speaker can log them (constitution VII).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import APPDATA_DIR

log = logging.getLogger("tts.prep")

PRONUNCIATION_PATH = APPDATA_DIR / "pronunciation.txt"

MAX_SENTENCE_CHARS = 350

PLACEHOLDERS = {
    "pl": {"code": "fragment kodu", "table": "tabela"},
    "en": {"code": "code block", "table": "table"},
}

# Seeded on first run; the user edits the file afterwards. `term = spoken [| lang]`.
_SEED_PRONUNCIATION = """# VoiceCommander2 — pronunciation dictionary for spoken read-back.
# One rule per line:  term = spoken form [| lang]
#   term   : matched as a whole token, case-insensitive
#   spoken : Polish respelling, or raw espeak phonemes inside [[ ... ]]
#   lang   : pl (default) or en — which voice speaks the replacement
# Lines starting with # are comments.
npm = en pe em
JSON = dżejson
YAML = jamel
YML = jamel
HTML = ha te em el
CSS = ce es es
SQL = eskuel
API = a pe i
URL = u er el
CLI = si el aj
GPU = dżi pi ju
CPU = ce pe u
CUDA = kuda
GitHub = githab
git = git
pytest = pajtest
Python = pajton
Claude = klod
Ollama = olama
Whisper = łisper
Piper = pajper
PowerShell = pałer szel
Windows = łindows
Linux = linuks
README = ridmi
config = konfig
main = mejn
pipeline = pajplajn
tray = trej
hook = huk
hooks = huki
commit = komit
branch = brancz
merge = merdż
push = pusz
pull = pul
"""


@dataclass
class StripStats:
    code_blocks: int = 0
    tables: int = 0
    urls: int = 0
    inline_code: int = 0
    headings: int = 0
    links: int = 0


@dataclass(frozen=True)
class Span:
    text: str
    lang: str          # "pl" | "en"


@dataclass
class PreparedText:
    sentences: list[list[Span]] = field(default_factory=list)
    stats: StripStats = field(default_factory=StripStats)
    dictionary_hits: int = 0
    en_spans: int = 0
    detected_lang: str = "pl"
    clean_text: str = ""

    @property
    def clean_chars(self) -> int:
        return len(self.clean_text)

    def is_empty(self) -> bool:
        return not any(sp.text.strip() for s in self.sentences for sp in s)


# ---------------------------------------------------------------- markdown

_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.S | re.M)
_FENCE_OPEN_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*\Z", re.S | re.M)  # unterminated fence
_TABLE_RE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?){2,}", re.M)
_HTML_RE = re.compile(r"<[^>\n]{1,80}>")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)\]>]+", re.I)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.M)
_HR_RE = re.compile(r"^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$", re.M)
_BULLET_RE = re.compile(r"^[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t]+", re.M)
_QUOTE_RE = re.compile(r"^[ \t]*>+[ \t]?", re.M)
_INLINE_CODE_RE = re.compile(r"`{1,2}([^`\n]+)`{1,2}")
_EMPH_STAR_RE = re.compile(r"(\*\*\*|\*\*|\*)(?=\S)(.+?)(?<=\S)\1")
# Underscore emphasis only at word boundaries, so snake_case identifiers survive.
_EMPH_UNDER_RE = re.compile(r"(?<!\w)(___|__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️]")
_CHECKBOX_RE = re.compile(r"\[[ xX]\][ \t]+")
_ARROW_RE = re.compile(r"\s*(?:->|→|=>|⇒)\s*")


def strip_markdown(text: str, *, strip_code: bool = True, read_inline_code: bool = True,
                   lang: str = "pl") -> tuple[str, StripStats]:
    """Return prose-only text and what was removed. Placeholders are spoken
    words ("fragment kodu"), or nothing when strip_code=False means 'omit'."""
    st = StripStats()
    ph = PLACEHOLDERS.get(lang, PLACEHOLDERS["pl"])
    t = text.replace("\r\n", "\n")

    def _code_sub(_m):
        st.code_blocks += 1
        return f"\n{ph['code']}.\n" if strip_code else "\n"

    t = _FENCE_RE.sub(_code_sub, t)
    t = _FENCE_OPEN_RE.sub(_code_sub, t)

    def _table_sub(_m):
        st.tables += 1
        return f"\n{ph['table']}.\n" if strip_code else "\n"

    t = _TABLE_RE.sub(_table_sub, t)
    t = _IMAGE_RE.sub("", t)
    t, st.links = _LINK_RE.subn(r"\1", t)
    t, st.urls = _URL_RE.subn("", t)
    t = _HTML_RE.sub("", t)
    t, st.headings = _HEADING_RE.subn("", t)
    t = _HR_RE.sub("", t)
    t = _CHECKBOX_RE.sub("", t)
    t = _BULLET_RE.sub("\n\n", t)   # each list item becomes its own sentence
    t = _QUOTE_RE.sub("", t)

    def _inline_sub(m):
        st.inline_code += 1
        return m.group(1) if read_inline_code else ""

    t = _INLINE_CODE_RE.sub(_inline_sub, t)
    for _ in range(2):  # nested ***bold italic***
        t = _EMPH_STAR_RE.sub(r"\2", t)
        t = _EMPH_UNDER_RE.sub(r"\2", t)
    t = _STRIKE_RE.sub(r"\1", t)
    t = _EMOJI_RE.sub("", t)
    t = _ARROW_RE.sub(", ", t)
    # Line breaks inside a paragraph are spaces; blank lines end a sentence.
    paras = [" ".join(p.split()) for p in re.split(r"\n[ \t]*\n+", t)]
    out = []
    for p in paras:
        p = " ".join(line.strip() for line in p.split("\n")).strip()
        if not p:
            continue
        if p[-1] not in ".!?…:;":
            p += "."
        out.append(p)
    return " ".join(out), st


# ---------------------------------------------------------------- sentences

_ABBREV = {"np", "tzn", "tj", "itd", "itp", "m.in", "ok", "por", "zob", "ww", "ul", "nr", "godz",
           "e.g", "i.e", "etc", "vs", "cf", "approx", "min", "max", "dr", "mr", "mrs", "ms", "prof", "st"}
_SENT_END_RE = re.compile(r"([.!?…]+)(\s+|$)")


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        end = m.end(1)
        before = text[start:end].rstrip(".!?…")
        last = before.split()[-1].lower() if before.split() else ""
        last = last.strip("(\"'„")
        # Abbreviation or a number like "2.1" — not a boundary. (Single-letter
        # initials are deliberately NOT guarded: "Dodałem A. Poprawiłem B." is a
        # far more common shape in these answers than "J. Kowalski".)
        if m.group(1) == "." and last in _ABBREV:
            continue
        nxt = text[m.end(1):m.end(1) + 1]
        if m.group(1) == "." and nxt.isdigit():
            continue
        sent = text[start:end].strip()
        if sent:
            out.append(sent)
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for chunk in out for s in _split_long(chunk)]


def _split_long(s: str) -> list[str]:
    if len(s) <= MAX_SENTENCE_CHARS:
        return [s]
    # Prefer a clause boundary in the middle third; else a word boundary.
    lo, hi = len(s) // 3, 2 * len(s) // 3
    cut = -1
    for sep in (";", ":", ",", " "):
        cut = s.rfind(sep, lo, hi)
        if cut != -1:
            cut += 1
            break
    if cut <= 0:
        cut = MAX_SENTENCE_CHARS
    return _split_long(s[:cut].strip()) + _split_long(s[cut:].strip())


# ---------------------------------------------------------------- language

_PL_CHARS = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
_PL_WORDS = {"i", "w", "z", "na", "do", "nie", "się", "to", "jest", "że", "oraz", "dla", "przez", "jak", "są", "być", "po", "od"}
_EN_WORDS = {"the", "and", "is", "are", "to", "of", "in", "that", "this", "with", "for", "it", "was", "be", "on", "as", "you", "have"}


def detect_lang(text: str) -> str:
    if any(c in _PL_CHARS for c in text):
        return "pl"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    pl = sum(1 for w in words if w in _PL_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    return "en" if en > pl else "pl"


# ---------------------------------------------------------------- dictionary

@dataclass(frozen=True)
class Rule:
    term: str
    spoken: str
    lang: str


def seed_pronunciation_file(path: Path = PRONUNCIATION_PATH) -> None:
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_SEED_PRONUNCIATION, encoding="utf-8")
            log.info("pronunciation dictionary seeded at %s", path)
    except Exception:
        log.exception("could not seed pronunciation dictionary")


def load_pronunciation(path: Path = PRONUNCIATION_PATH) -> list[Rule]:
    """`term = spoken [| lang]`; longest term first so 'git push' beats 'git'."""
    rules: list[Rule] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rules
    except Exception:
        log.exception("pronunciation dictionary unreadable")
        return rules
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        term, _, rest = line.partition("=")
        spoken, _, lang = rest.partition("|")
        term, spoken, lang = term.strip(), spoken.strip(), (lang.strip().lower() or "pl")
        if term and spoken:
            rules.append(Rule(term, spoken, "en" if lang == "en" else "pl"))
    rules.sort(key=lambda r: -len(r.term))
    return rules


# ---------------------------------------------------------------- spans

_TOKEN_RE = re.compile(r"\S+")
_WORD_CHARS_RE = re.compile(r"^[\W_]*(.*?)[\W_]*$", re.S)
_EXT_RE = re.compile(r"^[\w./\\-]+\.([a-z0-9]{1,5})$", re.I)
_CAMEL_RE = re.compile(r"[a-z][A-Z]")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_COMMON_EN = {"file", "files", "test", "tests", "branch", "commit", "merge", "pull", "push", "request",
              "default", "config", "build", "deploy", "release", "debug", "server", "client", "cache",
              "token", "tokens", "thread", "queue", "worker", "hook", "hooks", "feature", "bug", "fix",
              "issue", "update", "upgrade", "install", "package", "module", "import", "export", "class",
              "function", "method", "string", "array", "object", "null", "true", "false", "none"}


def _core(tok: str) -> tuple[str, str, str]:
    """Split leading/trailing punctuation from a token."""
    m = re.match(r"^([\W_]*)(.*?)([\W_]*)$", tok, re.S)
    lead, core, trail = m.group(1), m.group(2), m.group(3)
    # keep a dot-extension or an inner slash as part of the core
    return lead, core, trail


def is_english_token(core: str) -> bool:
    if not core or not core.isascii() or not any(c.isalpha() for c in core):
        return False   # numbers, versions (2.1.272), punctuation stay Polish
    low = core.lower()
    if _EXT_RE.match(core):
        return True
    if core.startswith("--") or core.startswith("-") and len(core) > 2:
        return True
    if "_" in core.strip("_") or "/" in core or "\\" in core:
        return True
    if _CAMEL_RE.search(core):
        return True
    if core.isupper() and 2 <= len(core) <= 6 and core.isalpha():
        return True
    if low in _COMMON_EN:
        return True
    return False


def _reduce_path(core: str) -> str:
    """English-voice form of an identifier: `specs/002/plan.md` -> `plan dot md`,
    `list_output_devices` -> `list output devices`, `TtsSnapshot` -> `Tts Snapshot`,
    `--fast` -> `fast`. The English voice (or its phonemes) then reads it."""
    core = core.strip("`'\"")
    if "/" in core or "\\" in core:
        core = re.split(r"[/\\]", core.rstrip("/\\"))[-1] or core
    core = core.lstrip("-")
    m = _EXT_RE.match(core)
    if m:
        ext = m.group(1)
        core = f"{core[: -(len(ext) + 1)]} dot {ext}"
    core = core.replace("_", " ").replace(".", " dot ")
    core = _CAMEL_SPLIT_RE.sub(" ", core)
    return " ".join(core.split())


def _apply_dictionary(sentence: str, rules: list[Rule]) -> tuple[list[Span], int]:
    """Replace whole-token dictionary terms; returns spans with the rule's lang."""
    if not rules:
        return [Span(sentence, "pl")], 0
    hits = 0
    spans: list[Span] = []
    buf: list[str] = []
    buf_lang = "pl"
    for tok in sentence.split(" "):
        lead, core, trail = _core(tok)
        rule = next((r for r in rules if r.term.lower() == core.lower()), None) if core else None
        if rule is None:
            buf.append(tok)
            continue
        hits += 1
        if rule.lang == "pl":
            buf.append(f"{lead}{rule.spoken}{trail}")
        else:
            if buf:
                spans.append(Span(" ".join(buf), buf_lang))
                buf = []
            spans.append(Span(f"{lead}{rule.spoken}{trail}", "en"))
    if buf:
        spans.append(Span(" ".join(buf), buf_lang))
    return spans, hits


def _split_english(span: Span) -> list[Span]:
    """Within a Polish span, mark identifier tokens as English spans."""
    if span.lang != "pl":
        return [span]
    out: list[Span] = []
    buf: list[str] = []
    cur = "pl"
    for tok in span.text.split(" "):
        lead, core, trail = _core(tok)
        is_flag = lead.endswith("-") and core.isascii() and core.isalpha()   # --fast, -v
        lang = "en" if (is_flag or is_english_token(core)) else "pl"
        if lang == "en":
            text = f"{lead.rstrip('-')}{_reduce_path(core)}{trail}"
        else:
            text = tok
        if lang != cur and buf:
            out.append(Span(" ".join(buf), cur))
            buf = []
        cur = lang
        buf.append(text)
    if buf:
        out.append(Span(" ".join(buf), cur))
    return out


def _merge(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for s in spans:
        if not s.text.strip():
            continue
        if out and out[-1].lang == s.lang:
            out[-1] = Span(out[-1].text + " " + s.text, s.lang)
        else:
            out.append(s)
    return out


def build_spans(sentence: str, rules: list[Rule], lang: str) -> tuple[list[Span], int]:
    if lang == "en":
        spans, hits = _apply_dictionary(sentence, [r for r in rules if r.lang == "en"])
        return _merge([Span(s.text, "en") for s in spans]), hits
    spans, hits = _apply_dictionary(sentence, rules)
    spans = [s2 for s in spans for s2 in _split_english(s)]
    return _merge(spans), hits


# ---------------------------------------------------------------- entry point

def prepare(text: str, *, strip_code: bool = True, read_inline_code: bool = True,
            lang: Optional[str] = None, rules: Optional[list[Rule]] = None) -> PreparedText:
    clean, stats = strip_markdown(text, strip_code=strip_code, read_inline_code=read_inline_code,
                                  lang=lang or "pl")
    detected = lang or detect_lang(clean)
    if lang is None and detected != "pl":
        # placeholders were inserted in Polish; redo with English ones
        clean, stats = strip_markdown(text, strip_code=strip_code, read_inline_code=read_inline_code,
                                      lang=detected)
    rules = load_pronunciation() if rules is None else rules
    prepared = PreparedText(stats=stats, detected_lang=detected, clean_text=clean)
    for sent in split_sentences(clean):
        spans, hits = build_spans(sent, rules, detected)
        prepared.dictionary_hits += hits
        prepared.en_spans += sum(1 for s in spans if s.lang == "en")
        if spans:
            prepared.sentences.append(spans)
    return prepared
