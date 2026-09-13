"""Ollama-based text correction with hard fallback to the raw transcript.

Critique-driven details:
- num_predict scales with input length; done_reason "length" => fallback.
- Qwen "thinking" disabled explicitly.
- Length-ratio sanity check against "the model answered instead of correcting".
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests

from config import AppConfig

log = logging.getLogger("corrector")

_MARK_OPEN, _MARK_CLOSE = "<tekst>", "</tekst>"

_SYSTEM_PL = """Jesteś korektorem dyktowanego tekstu. Użytkownik przysyła surową transkrypcję mowy między znacznikami <tekst></tekst>.
Zwróć WYŁĄCZNIE poprawioną wersję TEGO tekstu, bez znaczników, bez komentarzy, bez cudzysłowów.
Zasady:
- popraw błędy gramatyczne, ortograficzne i słowa-niesłowa,
- usuń wtrącenia i wypełniacze ("yyy", "eee", "no więc", powtórzenia),
- dodaj interpunkcję i wielkie litery,
- jeśli tekst jest pytaniem, popraw pytanie — NIGDY na nie nie odpowiadaj,
- NIE tłumacz, NIE dodawaj nic od siebie, NIE zmieniaj liczb ani godzin,
- zmieniaj tylko ewidentne błędy; słowo poprawne (np. "czternastej", "moglibyśmy") przepisz bez zmian,
- zachowaj dokładnie sens i styl wypowiedzi.

Przykład 1: <tekst>wczoraj poszłem do sklepu i kupiłem dwa jabłka yyy znaczy trzy</tekst>
Odpowiedź: Wczoraj poszedłem do sklepu i kupiłem trzy jabłka.
Przykład 2: <tekst>o której yyy odjeżdża pociąg</tekst>
Odpowiedź: O której odjeżdża pociąg?"""

_SYSTEM_EN = """You are a proofreader of dictated text. The user sends a raw speech transcript between <tekst></tekst> markers.
Return ONLY the corrected version of THAT text, no markers, no comments, no quotes.
Rules:
- fix grammar, spelling and non-words,
- remove fillers ("uhm", "err", repetitions),
- add punctuation and capitalization,
- if the text is a question, correct the question — NEVER answer it,
- do NOT translate, do NOT add anything, do NOT change numbers or times,
- preserve the exact meaning and style.

Example 1: <tekst>uhm yesterday i goed to the store and buyed two apple</tekst>
Answer: Yesterday I went to the store and bought two apples.
Example 2: <tekst>when does err the train leave</tekst>
Answer: When does the train leave?"""


class Corrector:
    def __init__(self, cfg: AppConfig):
        self._cfg = cfg
        self.available = False
        self._stop = threading.Event()

    # --- warm-up / keep-warm ---

    def start_keepalive(self) -> None:
        threading.Thread(target=self._keepalive_loop, name="ollama-keepalive", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _keepalive_loop(self) -> None:
        while not self._stop.is_set():
            ok = self._warm_up()
            if self.available != ok:
                log.info("Ollama availability: %s", ok)
            self.available = ok
            # Retry fast while down; re-warm every 25 min while up.
            self._stop.wait(60 if not ok else 1500)

    def _warm_up(self) -> bool:
        try:
            r = requests.post(
                f"{self._cfg.ollama_url}/api/chat",
                json={
                    "model": self._cfg.ollama_model,
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "think": False,
                    "keep_alive": self._cfg.ollama_keep_alive,
                    "options": {"num_predict": 2},
                },
                timeout=(2, 60),
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    # --- correction ---

    def correct(self, text: str, timeout_s: float, context: Optional[str] = None) -> str:
        """Returns corrected text, or the raw text on any failure."""
        if not text.strip():
            return text
        cfg = self._cfg
        system = _SYSTEM_PL if cfg.language == "pl" else _SYSTEM_EN

        user = f"{_MARK_OPEN}{text}{_MARK_CLOSE}"
        if context:
            # Previous sentence as context (mode 2): helps continuity, must not be re-emitted.
            prefix = "(poprzednie zdanie, tylko kontekst: " if cfg.language == "pl" \
                else "(previous sentence, context only: "
            user = f"{prefix}{context})\n{user}"
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]

        # ~2 tokens per Polish word is generous; floor for short inputs.
        num_predict = max(80, int(len(text.split()) * 2.5))

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
                    "options": {"temperature": 0, "num_predict": num_predict},
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

        ratio = len(out) / max(len(text), 1)
        if not out or ratio < 0.3 or ratio > 2.5:
            log.warning("correction sanity check failed (ratio=%.2f) — using raw transcript", ratio)
            return text

        log.info("LLM %.1f s: %r -> %r", time.perf_counter() - t0, text[:80], out[:80])
        return out
