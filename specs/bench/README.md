# Benchmark modeli korekty — gemma4 vs qwen3.5:2b (16.09.2026)

Pytanie: czy któryś z modeli `gemma4` z biblioteki Ollamy poprawia dyktowane zdania PL/EN lepiej niż obecny `qwen3.5:2b`, i czy warto go uruchomić na GPU.

## Sprzęt i budżet VRAM

RTX 3080 Laptop 8 GB, i7-12700H (14 rdzeni), 32 GB RAM, Ollama 0.32.13 (główny przebieg) i 0.34.1 (powtórka). Przy działającej aplikacji (Whisper `medium` na CUDA) i przeglądarce zajęte jest ~4,5 GB VRAM — **dla LLM zostaje ~3,5 GB**. Sklep modeli Ollamy przeniesiony na `D:\ollama\models` (C: miał 5 GB wolnego).

## Metoda

`tools/model_bench.py` — 35 surowych transkryptów PL (15 z `correction_eval.py` + 20 pełnych linii z logów aplikacji) i 12 EN, przez `Corrector.correct()` (prompt, sanity-check i fallback z aplikacji), każdy model raz z domyślnym umieszczeniem i raz z `num_gpu=0`. Pełne wyjścia: `bench_20260916-115601.md` (qwen), `bench_20260916-115839.md` (e2b), `bench_20260916-120232.md` (e4b). Przebieg 12b przerwany przez użytkownika po części GPU (za wolny).

## Wyniki (mediana / max latencji, 35 zdań PL; EN w nawiasie)

| model | rozmiar | VRAM po załadowaniu | GPU | CPU | fallbacki | fragmenty zachowane |
|---|---|---|---|---|---|---|
| qwen3.5:2b (Q8) | 2,7 GB | ~2,4 GB, 100% GPU | **0,58 s** / 0,77 s (0,53 s) | 6,3 s / 8,4 s (5,7 s) | 1/35 | 31/35 |
| gemma4:e2b-it-qat | 4,3 GB | ~2,8 GB, 100% GPU | 0,65 s / **3,7 s** (0,69 s) | **1,5 s** / 5,8 s (4,1 s) | 1/35 | 28/35 |
| gemma4:e4b-it-qat | 6,1 GB | ~3,3 GB, 100% GPU, **7,9/8,2 GB zajęte** | 0,75 s / 1,1 s (0,81 s) | 2,2 s / 11,1 s (7,6 s) | 0/35 | 30/35 |
| gemma4:12b-it-qat | 7,2 GB | 27% CPU / 72% GPU | 6,0 s / 13,2 s (5,3 s) | (przerwano) | 1/35 | 34/35 |

"Fragmenty zachowane" to twardy test z `correction_eval.py` (liczby, słowa z apostrofem, wielokropki, żargon); kilka "strat" to nieszkodliwe normalizacje (`low poly`→`low-poly`, `refactoru`→`refaktoru`).

## Jakość — przegląd ręczny (GPU)

Wszystkie trzy małe modele **wycinają wielokropki "..."** ze środka zdania mimo zakazu w promptcie (qwen 2/3, gemma 3/3 przypadków) i wszystkie zamieniają zniekształcone "oknogi" na "okno".

- **qwen3.5:2b** — najbardziej zachowawczy (18/35 zdań bez zmian). Poprawił: repozytorium, istnieje MCP, miejsca, redesignu. Popsuł: "wykonał"→"wykonać" (błąd gramatyczny), "skile"→"skill" (zamiast "skille"), "git push"→"Git push", wyciął "ileś". Nie poprawił "gołęzi", "przetestuje", "Comit". `zupdate'ować` → fallback.
- **gemma4:e2b-it-qat** — poprawia więcej realnych błędów (przetestuję, skille, gałąź: Commit, GitHubie, mogłoby; "wykonał" i "git push" zachowane). Ale: **"ikonki"→"ikony"** (synonim wprost zakazany w promptcie), wyciął "trochę", "3"→"trzy" (złapane przez sanity-check), przepisał bełkotliwe zdanie ("przyczności ileś"→"przyczółku jest"). Na GPU 2/35 zdań trwało ~3,5 s: llama-server przeliczał 18 tokenów promptu po 160 ms/token — przebudowa punktu kontrolnego sliding-window attention w Ollamie 0.32.13 (0.33.0 przebudowało "prefill restore points"; aktualizacja do 0.34.1 jest już pobrana przez aplikację Ollama).
- **gemma4:e4b-it-qat** — najlepsze poprawki (gałęzi, redesignu, przetestuję, skille, Commit, zachowane "ikonki", "3", "trochę"), 0 fallbacków. Ale: **"backgroundzie"→"w tle"** (tłumaczenie anglicyzmu), przesunięty przecinek zmieniający sens ("nie przetestuję tego, jeszcze lepiej ignorujemy"), wycięte "dwukropek", "sendinput"→"sendInput". Operacyjnie: VRAM zapełniony do 7,9/8,2 GB — jedna karta w przeglądarce więcej i Ollama zrzuci część na CPU (5–8 s na zdanie); ładowanie 8–11 s.
- **gemma4:12b-it-qat** — nie mieści się w VRAM; 6 s na zdanie. Odrzucony.

EN: wszystkie modele równorzędne; gemma nieco lepiej (usuwa "um", stawia przecinki), wszystkie gubią `feature'y` (fallback).

## Powtórka po aktualizacji Ollamy do 0.34.1 (GPU, ten sam zestaw; `bench_20260916-123955.md`)

| model | mediana | p90 | max | zdania > 2 s |
|---|---|---|---|---|
| qwen3.5:2b | **0,25 s** (EN 0,22 s) | 0,34 s | 0,43 s | 0/35 |
| gemma4:e2b-it-qat | 0,19 s (EN 0,24 s) | 2,22 s | 6,11 s | 5/35 |

Aktualizacja sama w sobie **skróciła korektę qwen ponad 2× (0,58 → 0,25 s)**. Skoki gemma4 e2b nie zniknęły, a wręcz są częstsze: 5/35 zdań PL trwało 2–6 s, w każdym przypadku llama-server liczył 10–19 tokenów promptu po 150–200 ms/token (przebudowa punktu kontrolnego SWA). W EN skoków nie było. Ładowanie e2b: 56 s (pierwszy raz po instalacji), potem 14 s.

## Decyzja

**Domyślny model pozostaje `qwen3.5:2b`** (potwierdzone również na Ollamie 0.34.1). Żaden wariant gemma4 nie jest jednoznacznie lepszy w tym, na czym zależy aplikacji (przepisz wiernie, nie parafrazuj): e2b ma podobną liczbę naruszeń wierności co qwen, tylko innych (synonimy zamiast błędów gramatycznych), do tego sporadyczne 3,5-sekundowe skoki; e4b jest najlepszy językowo, ale tłumaczy żargon i wyczerpuje VRAM. Na GPU wszystkie są równie szybkie (0,6–0,8 s), więc nie ma zysku latencji, który uzasadniałby ryzyko.

Co zostaje z tego przebiegu:
- `ollama_num_gpu` w configu — jawne wymuszenie CPU/GPU dla korekty,
- `tools/model_bench.py` — powtarzalny benchmark; gemma4 e2b warto sprawdzić ponownie dopiero, gdy kolejna wersja Ollamy/llama.cpp naprawi przeliczanie promptu dla SWA (objaw: `prompt eval time` > 100 ms/token dla kilkunastu tokenów w `server.log`); przełączenie to jeden wpis `"ollama_model": "gemma4:e2b-it-qat"`,
- `gemma4:e2b-it-qat` i `gemma4:e4b-it-qat` pozostają pobrane (`ollama rm <model>` zwalnia 4,3 / 6,1 GB na D:), `gemma4:12b-it-qat` usunięty.
