"""Correction robustness set (DOKUMENTACJA.md, "Zestaw odporności korekty").

Runs fixed raw transcripts — taken from real logs — through Corrector.correct()
against the live Ollama and prints what changed. Re-run after every prompt or
sampling change. Not a pytest test: needs Ollama + the configured model.

    .venv/Scripts/python tools/correction_eval.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import AppConfig            # noqa: E402
from corrector import Corrector         # noqa: E402

CASES = [
    # (raw transcript, what a correct output must keep — substring, or None)
    ("Wczorajsze 3 feature'y chciałbym wypchnąć na branch'u main.", "Wczorajsze 3 feature'y"),
    ("Na moment, kiedy nie przetestuje tego jeszcze lepiej, ignorujemy te poprawki, które zaproponowałeś.", "ignorujemy"),
    ("Dobra, mi się wydaje, że teraz powinieneś przejść do... planowania refaktorów, znaleźć odpowiedni wzorzec... działania Heavy Agents Workflow, które moglibyśmy tutaj przygotować.", "powinieneś przejść do..."),
    ("Chciałbym, żebyś wygenerował te ikonki, 20 ikon na pojedynczy skill w folderze poza projektem.", "żebyś wygenerował te ikonki, 20"),
    ("Wygeneruję na podstawie grafiki low-poly Dla każdego skilla 5 odmian związany w sobie stylistycznie grafik low poly", "skilla 5"),
    ("Zweryfikuj, czy potrzebujemy zupdate'ować Claude'a, ponieważ widzę folder commands", "Claude'a"),
    ("Musimy przejść w takim razie do poprawienia tego problemu.", "w takim razie"),
    ("Procentowo wzrasta prędkość ataków, konkretny procent do ustalenia później na etapie testów.", "na etapie testów"),
    ("Chcę dodać jeszcze do tego repozytorem powiązania z gitem. Sprawdźcie czy wistnieje mcp, które mogłyby tutaj wdrożyć.", "repozytorium"),
    ("Chciałbym żeby się wygenerowało sprawdzić czy nie ma jeszcze innych artefaktów, które warto by było wskazać.", "żeby się wygenerowało"),
    ("Najpierw chciałbym rozbudować trochę te skile i zastanowić się nad kierunkiem tych umiejętności.", "rozbudować"),
    ("Czy testowałeś już moją aplikację?", "testowałeś"),                       # question: must not be answered
    ("o której yyy odjeżdża pociąg o czternastej czy o piętnastej", "czternastej"),
    ("git push", "git push"),                                                   # command: must not be carried out
    ("Skill do zwiększenia prędkości i strzelania nie powinien mieć artefaktów w backgroundzie oraz strzałka powinna być skierowana w górę.", "backgroundzie"),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("corrector").setLevel(logging.WARNING)   # show only sanity-check fallbacks
    cfg = AppConfig.load() if hasattr(AppConfig, "load") else AppConfig()
    c = Corrector(cfg)
    ok, status = c._warm_up()
    if not ok:
        print(f"Ollama not usable ({status})"); return 2
    bad = 0
    for raw, must_keep in CASES:
        out = c.correct(raw, timeout_s=45)
        keep_ok = must_keep is None or must_keep.lower() in out.lower()
        flag = "  " if keep_ok else "!!"
        if not keep_ok:
            bad += 1
        print(f"{flag} IN : {raw}\n   OUT: {out}\n")
    print(f"{len(CASES) - bad}/{len(CASES)} kept the protected fragment")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
