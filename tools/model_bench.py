"""Correction model benchmark: quality + latency, GPU vs CPU, per Ollama model.

Runs the PL robustness set (tools/correction_eval.py) and an EN set through
Corrector.correct() — the app's real code path, incl. sanity checks and
fallbacks — for every model given on the command line, once with Ollama's
default placement (GPU when it fits) and once pinned to CPU (num_gpu=0).

    .venv/Scripts/python tools/model_bench.py qwen3.5:2b gemma4:e2b-it-qat [--cpu-only|--gpu-only] [--out DIR]

Per model x device it reports: median / p90 / max latency, sanity-check
fallbacks (model paraphrased or answered -> raw transcript used), protected
fragments kept, outputs left unchanged, and Ollama's own placement string
("100% GPU", "45%/55% CPU/GPU", ...). Full outputs go to <out>/bench_<ts>.md
so the quality of the actual edits can be reviewed by eye.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import AppConfig                       # noqa: E402
from corrector import Corrector                    # noqa: E402
from tools.correction_eval import CASES as CASES_PL   # noqa: E402

# Raw EN transcripts in the same style (fillers, STT typos, jargon, a question
# and a command that must be corrected, never answered / carried out).
CASES_EN = [
    ("um so i think we should move the auth logic into a seperate module and add unit tests for it", "separate module"),
    ("can you check wether the docker compose file still builds after the last comit", "docker compose"),
    ("the api returns a 404 for the users endpoint when the id has more then 10 digits", "404"),
    ("lets rename the feature'y branch to something more descriptive like tts-readback", "tts-readback"),
    ("we're gonna need the MCP server for git and probably the one for the file system as well", "MCP"),
    ("i want you to run the tests first... then if they pass commit and push to main", "commit and push to main"),
    ("what time is the standup tomorrow nine or nine thirty", "nine thirty"),           # question: must not be answered
    ("git status", "git status"),                                                     # command: must not be carried out
    ("the the whisper model on cpu is about 3 times slower than realtime so realtime mode is is not usable", "3 times"),
    ("please refactor the injector so that clipboard and sendinput share the same retry loop", "sendinput"),
    ("this skill shouldn't have any artefacts in the background and the arrow should point up", "point up"),
    ("ok so basically eee the low-poly icons look good but the colours are to saturated", "low-poly"),
]


# Real raw PL transcripts from the 2026-09-13..16 app logs (complete lines only).
CASES_PL_LOG = [
    ("Chciałbym żebyś to wykonał na osobnym branczu.", "branczu"),
    ("Comit może zostać na tej gołęzi.", "gałęzi"),
    ("Czy plan reddesignu agentów został gdzieś spisany w pliku Markdownu?", "agentów"),   # question
    ("Ignoruję około pięciu grafik, które będą bazowały na grafice low-poly.", "Ignoruję"),
    ("Jak zrobić, żeby oknogi to otwierało się... na dole zamiast pokrawej.", "na dole"),
    ("Repozytorem leży na githubie.", "Repozytorium"),
    ("Skomituj zbarżyć z głównym branczem i wypchnij.", "wypchnij"),                        # command
    ("Wszystkie nieaktualne miejsce powinniśmy zaktualizować.", "powinniśmy"),
    ("Zwiększa się określona ilość procentowa obrażeń krytycznych.", "krytycznych"),
    ("Ten plan refactoru dotyczy agentów.", "refactoru"),
    ("Możesz przejść do implementacji.", "Możesz"),
    ("Ty jesteś dzisiaj w piórze?", "piórze"),
    ("Wciąż zostajemy w stylu low poly.", "low poly"),
    ("Tarcza z wieloma okrągami, a w środku napis CRIT", "CRIT"),
    ("Przygotuj mi stachet ML-a, w którym będę mógł...", "ML-a"),
    ("Dziś mi w punktach odpowiedzialność agent-art-direktora.", "w punktach"),
    ("z przyczności ileś 4 kątów Bohater dwukropek 25 tysięcy Wrogowie 2.5-6000", "2.5-6000"),
    ("Cienie wyglądają troszkę jak mazy A miecz tak jakby miał dwie...", "troszkę"),
    ("procentowo zwiększa się prędkość chodzenia.", "chodzenia"),
    ("Synergia z każdym atakiem, który polega na... obszarze zadawania obrażeń", "obszarze"),
]


class _WarnCounter(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.reasons: list[str] = []

    def emit(self, record):
        self.reasons.append(record.getMessage().replace("correction sanity check failed ", ""))


def _ollama_ps(url: str) -> dict:
    try:
        r = requests.get(f"{url}/api/ps", timeout=5).json()
        return {m["name"]: m for m in r.get("models", [])}
    except Exception:
        return {}


def _unload(url: str, model: str) -> None:
    try:
        requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass
    time.sleep(1.5)


def _placement(ps: dict, model: str) -> str:
    m = ps.get(model) or next((v for k, v in ps.items() if k.startswith(model)), None)
    if not m:
        return "?"
    size, vram = m.get("size", 0), m.get("size_vram", 0)
    if not size:
        return "?"
    if vram >= size:
        return "100% GPU"
    if vram == 0:
        return "100% CPU"
    return f"{100 * (size - vram) // size}%/{100 * vram // size}% CPU/GPU"


def _nvidia_mem() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=5).strip()
        used, total = out.split(",")
        return f"{int(used)}/{int(total)} MiB"
    except Exception:
        return "n/a"


def run_one(model: str, num_gpu: int, cases: list[tuple[str, str]], lang: str, timeout_s: float) -> dict:
    cfg = AppConfig(ollama_model=model, ollama_num_gpu=num_gpu, ollama_keep_alive="10m", language=lang)
    c = Corrector(cfg)
    counter = _WarnCounter()
    logging.getLogger("corrector").addHandler(counter)
    t0 = time.perf_counter()
    ok, status = c._warm_up()
    load_s = time.perf_counter() - t0
    if not ok:
        logging.getLogger("corrector").removeHandler(counter)
        return {"model": model, "num_gpu": num_gpu, "lang": lang, "error": status}
    placement = _placement(_ollama_ps(cfg.ollama_url), model)
    vram = _nvidia_mem()
    rows, lat = [], []
    for raw, keep in cases:
        n_before = len(counter.reasons)
        t = time.perf_counter()
        out = c.correct(raw, timeout_s=timeout_s)
        dt = time.perf_counter() - t
        lat.append(dt)
        fell_back = len(counter.reasons) > n_before
        rows.append({
            "raw": raw, "out": out, "s": round(dt, 2),
            "kept": keep.lower() in out.lower(),
            "fallback": counter.reasons[n_before] if fell_back else None,
            "unchanged": out == raw,
        })
    logging.getLogger("corrector").removeHandler(counter)
    lat_sorted = sorted(lat)
    return {
        "model": model, "num_gpu": num_gpu, "lang": lang, "placement": placement, "gpu_mem": vram,
        "load_s": round(load_s, 1),
        "median_s": round(statistics.median(lat), 2),
        "p90_s": round(lat_sorted[max(0, int(len(lat) * 0.9) - 1)], 2),
        "max_s": round(max(lat), 2),
        "fallbacks": sum(1 for r in rows if r["fallback"]),
        "kept": sum(1 for r in rows if r["kept"]),
        "unchanged": sum(1 for r in rows if r["unchanged"]),
        "n": len(rows), "rows": rows,
    }


def _dev(r: dict) -> str:
    return "cpu" if r["num_gpu"] == 0 else "gpu"


def main() -> int:
    # Polish output on a cp1252 console (git bash / pipes) must not crash the tool.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--cpu-only", action="store_true")
    ap.add_argument("--gpu-only", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "specs" / "bench"))
    a = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("corrector").setLevel(logging.WARNING)
    logging.getLogger("corrector").propagate = False

    devices = [(-1, "gpu"), (0, "cpu")]
    if a.cpu_only:
        devices = [(0, "cpu")]
    if a.gpu_only:
        devices = [(-1, "gpu")]
    url = AppConfig().ollama_url
    results = []
    for model in a.models:
        for num_gpu, dev in devices:
            for lang, cases in (("pl", CASES_PL + CASES_PL_LOG), ("en", CASES_EN)):
                print(f"== {model} [{dev}] {lang} ...", flush=True)
                res = run_one(model, num_gpu, cases, lang, a.timeout)
                results.append(res)
                if "error" in res:
                    print(f"   ERROR: {res['error']}", flush=True)
                else:
                    print(f"   {res['placement']:>20}  median {res['median_s']:5.2f}s  p90 {res['p90_s']:5.2f}s  "
                          f"max {res['max_s']:5.2f}s  fallbacks {res['fallbacks']}/{res['n']}  "
                          f"kept {res['kept']}/{res['n']}  unchanged {res['unchanged']}/{res['n']}  "
                          f"load {res['load_s']}s  gpu {res['gpu_mem']}", flush=True)
            _unload(url, model)

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"bench_{ts}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [f"# Correction model benchmark {ts}\n",
          "| model | device | lang | placement | load s | median s | p90 s | max s | fallbacks | kept | unchanged |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            md.append(f"| {r['model']} | {_dev(r)} | {r['lang']} | ERROR {r['error']} |||||||")
            continue
        md.append(f"| {r['model']} | {_dev(r)} | {r['lang']} | {r['placement']} | {r['load_s']} | "
                  f"{r['median_s']} | {r['p90_s']} | {r['max_s']} | {r['fallbacks']}/{r['n']} | "
                  f"{r['kept']}/{r['n']} | {r['unchanged']}/{r['n']} |")
    for r in results:
        if "error" in r:
            continue
        md.append(f"\n## {r['model']} [{_dev(r)}] {r['lang']} ({r['placement']})\n")
        for row in r["rows"]:
            flag = "FALLBACK " + row["fallback"] if row["fallback"] else ("unchanged" if row["unchanged"] else "")
            keep = "" if row["kept"] else " !!LOST"
            md.append(f"- ({row['s']}s{keep}) {flag}\n  - IN : {row['raw']}\n  - OUT: {row['out']}")
    (out_dir / f"bench_{ts}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nwritten: {out_dir / f'bench_{ts}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
