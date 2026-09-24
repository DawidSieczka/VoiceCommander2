"""Ollama-based text correction with hard fallback to the raw transcript.

Critique-driven details:
- num_predict scales with input length; done_reason "length" => fallback.
- Qwen "thinking" disabled explicitly.
- Sampling penalties zeroed: a copy-editing task must be allowed to copy.
- Word-level sanity check (dropped/added/rewritten words, lost numbers and
  apostrophe tokens) against "the model answered or paraphrased instead of
  correcting".
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

import requests

from config import AppConfig

log = logging.getLogger("corrector")


def _load_options(cfg: AppConfig) -> dict:
    """Load-time options shared by every request: `num_gpu` pins the model to
    CPU (0) or to N GPU layers; -1 leaves the placement to Ollama."""
    n = int(getattr(cfg, "ollama_num_gpu", -1))
    return {"num_gpu": n} if n >= 0 else {}

_MARK_OPEN, _MARK_CLOSE = "<tekst>", "</tekst>"

# Whisper marks hesitations/pauses with "..." (or "…"). They are not dictated
# punctuation, and a 2B model does not follow prompt rules about them in either
# direction (kept when told to drop, dropped when told to keep — bench
# 2026-09-16), so they are removed deterministically before correction.
_ELLIPSIS_RE = re.compile(r"\s*(?:\.{2,}|…)+\s*")


def strip_ellipses(text: str) -> str:
    """"że... pojawił się" -> "że pojawił się"; "GitHubie... Sprawdź" -> "GitHubie. Sprawdź";
    a trailing "..." is dropped (the model adds the final punctuation)."""
    def repl(m: re.Match) -> str:
        nxt = text[m.end():m.end() + 1]
        if not nxt:
            return ""
        if nxt.isupper():
            return ". "
        return " "
    out = _ELLIPSIS_RE.sub(repl, text)
    return " ".join(out.split())


# A period followed by a lowercase word is a Whisper segment boundary at a
# thinking pause ("na stylistyce. i potem"), never dictated punctuation —
# unless the "period" belongs to an abbreviation.
_ABBREVIATIONS = {
    "np", "m.in", "tzn", "tj", "itd", "itp", "ok", "ew", "dr", "ul", "godz", "min", "max",
    "ang", "pol", "zob", "por", "str", "nr", "tel", "ok", "ca", "vs", "e.g", "i.e", "etc",
    "mr", "mrs", "ms", "st", "no", "approx", "incl", "excl", "cf", "al", "vol", "fig",
}
_PAUSE_PERIOD_RE = re.compile(r"(\S+)\.\s+(?=[a-ząćęłńóśźż])")
# Capitalised words that do not open a dictated sentence but do continue one
# after a pause ("szablonu. Ani też", "first. And then"). Closed list on
# purpose: "I", "Ale", "Bo", "But", "So" legitimately start sentences, so those
# stay with the model (prompt rule + example 4).
_CONTINUATION_WORDS = {
    "oraz", "ani", "albo", "lub", "ponieważ", "gdyż",
    "który", "która", "które", "którego", "której", "którym", "których", "którymi", "którą",
    "and", "or", "nor", "which", "whereas",
}
_PAUSE_PERIOD_CAP_RE = re.compile(r"(\S+)\.\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\b")


def drop_pause_periods(text: str) -> str:
    """"skupmy się na stylistyce. i potem" -> "skupmy się na stylistyce i potem";
    keeps "m.in. będzie", "np. tak", "2.5" and any period before a capital (the
    model decides those, see the prompt)."""
    def repl(m: re.Match) -> str:
        word = m.group(1)
        core = word.lstrip("(\"'„").lower()
        if core in _ABBREVIATIONS or any(c.isdigit() for c in core) or len(core) < 2:
            return m.group(0)
        return word + " "

    def repl_cap(m: re.Match) -> str:
        word, nxt = m.group(1), m.group(2)
        core = word.lstrip("(\"'„").lower()
        if nxt.lower() not in _CONTINUATION_WORDS or core in _ABBREVIATIONS or len(core) < 2:
            return m.group(0)
        return f"{word} {nxt.lower()}"
    return _PAUSE_PERIOD_CAP_RE.sub(repl_cap, _PAUSE_PERIOD_RE.sub(repl, text))


def strip_pause_marks(text: str) -> str:
    return drop_pause_periods(strip_ellipses(text))

_SYSTEM_PL = """Jesteś korektorem dyktowanego tekstu. Użytkownik przysyła surową transkrypcję mowy między znacznikami <tekst></tekst>. Zwykle jest to polecenie lub pytanie do asystenta programisty albo notatka o projekcie.
Zwróć WYŁĄCZNIE poprawioną wersję TEGO tekstu, bez znaczników, bez komentarzy, bez cudzysłowów.

Wolno zmienić tylko:
- literówki i ewidentnie przekręcone słowa (np. "repozytorem" -> "repozytorium"),
- interpunkcję i wielkie litery,
- wypełniacze z tej listy: "yyy", "eee", "mmm", "hmm" oraz bezpośrednie powtórzenie tego samego słowa ("że że"),
- kropki, które rozpoznawanie mowy wstawiło w miejscu pauzy w środku zdania: jeśli tekst po kropce jest dalszym ciągiem tej samej myśli (zaczyna się od spójnika lub zaimka: "oraz", "i", "ale", "który", "żeby", "ani", "czyli", albo urwane zdanie nie ma bez niego sensu), usuń kropkę lub zamień ją na przecinek i popraw wielką literę; kropkę między dwoma pełnymi zdaniami zostaw.

NIE wolno zmieniać:
- osoby, liczby ani czasu czasowników ("powinieneś" zostaje "powinieneś", "ignorujemy" zostaje "ignorujemy"),
- słów na synonimy ("żebyś" nie zamieniaj na "abyś", "ikonki" nie zamieniaj na "ikony"),
- nazw własnych, terminów IT, anglicyzmów i słów z apostrofem (skill, skille, branch, commit, feature'y, Claude, low-poly, git push, MCP, refaktor) — przepisz je dosłownie, nawet jeśli wyglądają dziwnie,
- liczb, godzin ani procentów,
- liczby i kolejności zdań: nie skracaj, nie streszczaj, nie pomijaj fragmentów, nie dopisuj nic od siebie.
Jeśli tekst jest pytaniem lub poleceniem, popraw je — NIGDY na nie nie odpowiadaj i nie wykonuj go. W razie wątpliwości przepisz słowo bez zmian.

Przykład 1: <tekst>yyy myślę że powinieneś to wykonać na osobnym branczu i te ikonki zakomitować</tekst>
Odpowiedź: Myślę, że powinieneś to wykonać na osobnym branczu i te ikonki zakomitować.
Przykład 2: <tekst>o której odjeżdża pociąg o czternastej czy o piętnastej</tekst>
Odpowiedź: O której odjeżdża pociąg, o czternastej czy o piętnastej?
Przykład 3: <tekst>repozytorem leży na githubie sprawdź czy wistnieje mcp do gita</tekst>
Odpowiedź: Repozytorium leży na GitHubie. Sprawdź, czy istnieje MCP do Gita.
Przykład 4: <tekst>na razie skupmy się na stylistyce. I potem przejdziemy do kolorów. Ikonki zostają bez zmian</tekst>
Odpowiedź: Na razie skupmy się na stylistyce i potem przejdziemy do kolorów. Ikonki zostają bez zmian."""

_SYSTEM_EN = """You are a proofreader of dictated text. The user sends a raw speech transcript between <tekst></tekst> markers. It is usually a command or question for a coding assistant, or a project note.
Return ONLY the corrected version of THAT text, no markers, no comments, no quotes.

You may change only:
- typos and clearly garbled words,
- punctuation and capitalization,
- fillers from this list: "uhm", "err", "hmm" and an immediate repetition of the same word ("the the"),
- periods the speech recognizer inserted at a thinking pause mid-sentence: if the text after the period continues the same thought (starts with a conjunction or relative pronoun: "and", "or", "but", "which", "that", "so", or the cut sentence makes no sense on its own), drop the period or turn it into a comma and fix the capital; keep a period between two complete sentences.

You must NOT change:
- the person, number or tense of verbs,
- words into synonyms,
- proper nouns, IT terms, jargon and words with apostrophes (skill, branch, commit, Claude, low-poly, git push, MCP) — copy them verbatim even if they look odd,
- numbers, times or percentages,
- the number or order of sentences: do not shorten, summarize, drop parts or add anything.
If the text is a question or a command, correct it — NEVER answer it or carry it out. When in doubt, copy the word unchanged.

Example 1: <tekst>uhm i think you should do this on a separate branch and comit those little icons</tekst>
Answer: I think you should do this on a separate branch and commit those little icons.
Example 2: <tekst>when does the train leave at two or at three</tekst>
Answer: When does the train leave, at two or at three?
Example 3: <tekst>the repo is on github check if there exsists an mcp for git</tekst>
Answer: The repo is on GitHub. Check if there exists an MCP for Git.
Example 4: <tekst>lets focus on the styling first. And then we move on to the colours. The icons stay as they are</tekst>
Answer: Let's focus on the styling first and then we move on to the colours. The icons stay as they are."""


_WORD_RE = re.compile(r"[\w'’]+", re.UNICODE)


_FILLERS = {"yyy", "eee", "mmm", "hmm", "uhm", "um", "uh", "err", "ehm"}


def _words(text: str) -> list[str]:
    """Lower-cased word tokens minus fillers and immediate repetitions — the
    only removals the prompt allows, so they must not count as dropped words."""
    out: list[str] = []
    for w in _WORD_RE.findall(text):
        w = w.lower().replace("’", "'")
        if w in _FILLERS or (out and out[-1] == w):
            continue
        out.append(w)
    return out


def sanity_check(text: str, out: str) -> Optional[str]:
    """Reject a correction that did more than copy-editing. Returns the reason
    or None when `out` is acceptable. Tuned on the 2026-09-13..15 logs where a
    plain length-ratio window (0.3-2.5) let through half-dropped sentences,
    person changes and jargon rewrites while firing once in 134 corrections."""
    if not out:
        return "empty"
    src, dst = _words(text), _words(out)
    if not src:
        return None
    ratio = len(dst) / len(src)
    if ratio < 0.75:
        return f"dropped words (ratio={ratio:.2f})"
    if ratio > 1.5:
        return f"added words (ratio={ratio:.2f})"
    dst_set = set(dst)
    # Numbers and apostrophe words (feature'y, Claude'a, git'a) are the
    # tokens a 2B model most often "fixes" into something else.
    for w in src:
        if (any(c.isdigit() for c in w) or "'" in w) and w not in dst_set:
            return f"lost token {w!r}"
    # Legit typo fixes change a few words; paraphrase changes many.
    changed = sum(1 for w in src if w not in dst_set)
    allowed = max(2, int(len(src) * 0.25))
    if changed > allowed:
        return f"rewrote {changed}/{len(src)} words"
    return None


_SUMMARY_MARK_OPEN, _SUMMARY_MARK_CLOSE = "<streszczenie>", "</streszczenie>"

_SUMMARY_PL = """Jesteś asystentem, który streszcza odpowiedź asystenta programisty tak, aby można ją było odczytać na głos. Użytkownik przysyła tekst między znacznikami <streszczenie></streszczenie>.
Zwróć WYŁĄCZNIE streszczenie po polsku: 2 do 3 krótkie zdania, bez znaczników, bez list, bez kodu, bez nagłówków.
Powiedz: co zostało zrobione, co się nie udało (jeśli coś) i czy użytkownik musi podjąć jakąś decyzję lub coś zrobić.
Nazwy plików, poleceń i technologii zostaw w oryginale."""

_SUMMARY_EN = """You summarise a coding assistant's answer so it can be read aloud. The user sends the text between <streszczenie></streszczenie> markers.
Return ONLY the summary in English: 2 to 3 short sentences, no markers, no lists, no code, no headings.
Say what was done, what failed (if anything) and whether the user must decide or do something.
Keep file, command and technology names as they are."""


def summary_sanity_check(text: str, out: str) -> Optional[str]:
    """A spoken summary must be much shorter than the source but not empty."""
    if not out:
        return "empty"
    ratio = len(out) / max(len(text), 1)
    if ratio > 0.6:
        return f"not shorter (ratio={ratio:.2f})"
    if len(out) < 15:
        return "too short"
    if out.count(".") + out.count("!") + out.count("?") > 5:
        return "too many sentences"
    if "```" in out or re.search(r"^\s*(?:[-*•]|\d+[.)])\s", out, re.M) or re.search(r"^\s*#", out, re.M):
        return "contains markdown"
    return None


class Corrector:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self.available = False
        self.status = "starting"   # "ok" | "offline" | "no_model" | "error"
        self.on_change: Optional[callable] = None  # tray hook: refresh menu on state flip
        self._stop = threading.Event()
        self._wake = threading.Event()      # refresh(): re-probe now instead of in 25 min
        self._probed_model: Optional[str] = None

    # --- warm-up / keep-warm ---

    def start_keepalive(self) -> None:
        threading.Thread(target=self._keepalive_loop, name="ollama-keepalive", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def refresh(self) -> None:
        """Tray changed cfg.ollama_model: probe the new model right away (the
        tray grays out / shows 'ollama pull ...' until it answers)."""
        if self._cfg.ollama_model == self._probed_model:
            return
        self.available, self.status = False, "starting"
        self._wake.set()

    def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            ok, status = self._warm_up()
            if (self.available, self.status) != (ok, status):
                log.info("Ollama availability: %s (%s)", ok, status)
                self.available, self.status = ok, status
                if self.on_change:
                    try:
                        self.on_change()
                    except Exception:
                        log.exception("availability callback failed")
            # Retry fast while down (so the tray un-grays soon after the user
            # starts Ollama / pulls the model); re-warm every 25 min while up.
            self._wake.wait(15 if not ok else 1500)
            self._wake.clear()

    def _warm_up(self) -> tuple[bool, str]:
        """Probe the correction endpoint; distinguish 'server down' from
        'server up but the configured model is not installed'."""
        self._probed_model = self._cfg.ollama_model
        try:
            r = requests.post(
                f"{self._cfg.ollama_url}/api/chat",
                json={
                    "model": self._cfg.ollama_model,
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "think": False,
                    "keep_alive": self._cfg.ollama_keep_alive,
                    "options": {"num_predict": 2, **_load_options(self._cfg)},
                },
                timeout=(2, 120),
            )
        except requests.RequestException:
            return False, "offline"
        if r.status_code == 200:
            return True, "ok"
        if r.status_code == 404:
            return False, "no_model"   # e.g. model never pulled: ollama pull <model>
        return False, "error"

    # --- correction ---

    def correct(self, text: str, timeout_s: float, context: Optional[str] = None) -> str:
        """Returns corrected text, or the raw text on any failure."""
        if not text.strip():
            return text
        cfg = self._cfg
        if getattr(cfg, "fix_pause_marks", True):
            text = strip_pause_marks(text)
            if not text:
                return text
        system = _SYSTEM_PL if cfg.language == "pl" else _SYSTEM_EN

        user = f"{_MARK_OPEN}{text}{_MARK_CLOSE}"
        if context:
            # Previous sentence as context (mode 2): helps continuity, must not be re-emitted.
            prefix = "(poprzednie zdanie, tylko kontekst: " if cfg.language == "pl" \
                else "(previous sentence, context only: "
            user = f"{prefix}{context})\n{user}"
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]

        # ~3 tokens per Polish word (Qwen tokenizer); floor for short inputs.
        # A "length" stop falls back to the raw transcript, so err generous.
        num_predict = max(80, int(len(text.split()) * 3))
        # Copy-editing means reproducing the input. The Ollama model card for
        # qwen3.5 ships presence_penalty=1.5 and Ollama adds repeat_penalty=1.1
        # by default — both punish tokens already present in the context, i.e.
        # the very words the model should copy. Left in place they turned
        # "żebyś" into "abyś", "ignorujemy" into "ignoruję", "skilla" into
        # "skali" and dropped whole clauses (log analysis 2026-09-15).
        options = {
            "temperature": 0,
            "presence_penalty": 0,
            "frequency_penalty": 0,
            "repeat_penalty": 1.0,
            "num_predict": num_predict,
            **_load_options(cfg),
        }

        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{cfg.ollama_url}/api/chat",
                json={
                    "model": cfg.ollama_model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "keep_alive": cfg.ollama_keep_alive,
                    "options": options,
                },
                timeout=(2, timeout_s),
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            log.warning("correction failed (%s) — using raw transcript", type(e).__name__)
            return text

        if data.get("done_reason") == "length":
            log.warning("correction truncated (num_predict=%d) — using raw transcript", num_predict)
            return text

        out = (data.get("message") or {}).get("content", "").strip()
        # Strip markers/label the model sometimes echoes.
        for junk in (_MARK_OPEN, _MARK_CLOSE, "Odpowiedź:", "Answer:"):
            out = out.replace(junk, " ")
        out = " ".join(out.split())
        # Strip enclosing quotes the model sometimes adds.
        if len(out) >= 2 and out[0] in "\"'„«" and out[-1] in "\"'”»":
            out = out[1:-1].strip()

        reason = sanity_check(text, out)
        if reason:
            log.warning("correction sanity check failed (%s) — using raw transcript: %r -> %r",
                        reason, text[:80], out[:80])
            return text

        log.info("LLM %.1f s: %r -> %r", time.perf_counter() - t0, text[:80], out[:80])
        return out

    # --- spoken summary (feature 002) ---

    def summarize(self, text: str, timeout_s: float, lang: str = "pl") -> Optional[str]:
        """2-3 sentence spoken summary of a long answer, or None on any failure
        (the caller then reads the full text — never nothing)."""
        if not text.strip() or not self.available:
            return None
        cfg = self._cfg
        system = _SUMMARY_PL if lang == "pl" else _SUMMARY_EN
        # Long answers are truncated for the 2B model's context; the head carries
        # the "what was done" part and the tail the "next steps" part.
        if len(text) > 6000:
            text = text[:4000] + " … " + text[-1500:]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"{_SUMMARY_MARK_OPEN}{text}{_SUMMARY_MARK_CLOSE}"}]
        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{cfg.ollama_url}/api/chat",
                json={"model": cfg.ollama_model, "messages": messages, "stream": False,
                      "think": False, "keep_alive": cfg.ollama_keep_alive,
                      "options": {"temperature": 0, "num_predict": 220, **_load_options(cfg)}},
                timeout=(2, timeout_s),
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            log.warning("summary failed (%s) — reading full text", type(e).__name__)
            return None
        except ValueError:
            log.warning("summary failed (bad JSON) — reading full text")
            return None
        raw = (data.get("message") or {}).get("content", "").strip()
        for junk in (_SUMMARY_MARK_OPEN, _SUMMARY_MARK_CLOSE, "Streszczenie:", "Summary:"):
            raw = raw.replace(junk, " ")
        reason = summary_sanity_check(text, raw.strip())   # before collapsing newlines (markdown check)
        out = " ".join(raw.split())
        if reason:
            log.warning("summary sanity check failed (%s) — reading full text: %r", reason, out[:80])
            return None
        log.info("LLM summary %.1f s: %d -> %d chars", time.perf_counter() - t0, len(text), len(out))
        return out
