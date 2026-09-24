# VoiceCommander2 — Dokumentacja projektowa

> Lokalna aplikacja dyktowania dla Windows: mówisz — aplikacja zamienia mowę na tekst, poprawia go lokalnym modelem AI i wpisuje w aktywne okno. Zero interfejsu, zero chmury, zero kosztów.

---

## 1. Wizja i zasady

- **100% lokalnie** — żadne dane (audio, tekst) nie opuszczają laptopa. Jedyny ruch sieciowy to `localhost` (Ollama) oraz jednorazowe pobranie modelu Whisper przy pierwszym uruchomieniu.
- **100% darmowo** — wyłącznie open-source'owe biblioteki i modele.
- **Zero UI** — aplikacja żyje w zasobniku systemowym (tray, prawy dolny róg), jak Ollama. Całe sterowanie przez menu kontekstowe (prawy przycisk myszy) po angielsku.
- **Polski domyślnie**, szybkie przełączenie na angielski z menu.

### Czego nauczyła nas wersja 1 (VoiceTypePL, C#)

Stary projekt (`..\VoiceCommander`) miał działający potok: przechwytywanie audio → Silero VAD → Whisper → wpisywanie tekstu. Najważniejsze wnioski przeniesione do v2:

1. **Niedopasowanie modelu do GPU było zabójcą wydajności nr 1** — duży model po cichym spadku na CPU działał kilkukrotnie wolniej niż właściwy model CPU. W v2 rozstrzygamy to projektowo (patrz §3.1).
2. **Wpisywanie w okno z fokusem wygrało** z klikaniem w miejsce kursora myszy — v1 porzuciło podejście "pod myszką" w praktyce.
3. **Whisper halucynuje po polsku na ciszy/szumie** ("napisy stworzone przez społeczność amara.org", "dziękuję za uwagę") — potrzebna czarna lista + progi pewności.
4. Okna z uprawnieniami administratora **odrzucają wstrzykiwany tekst** (UIPI).
5. Wklejanie przez schowek **koliduje z użyciem schowka przez użytkownika** — trzeba zapisywać i przywracać zawartość.

Nowość v2 względem v1: **korekta AI** (v1 nie miało żadnego LLM), architektura w Pythonie i trzy porównywalne tryby wypisywania.

---

## 2. Funkcjonalności

### 2.1 Push-to-talk (PTT)
Trzymasz klawisz — mówisz — puszczasz. Skrót globalny (działa niezależnie od aktywnej aplikacji).

- **Domyślny klawisz: prawy Ctrl** (konfigurowalny). Dlaczego nie Alt? Puszczenie Alt aktywuje paski menu aplikacji (Word, przeglądarka) i psuje Alt+Tab — Alt jest zdyskwalifikowany jako domyślny. CapsLock możliwy jako opcja zaawansowana (wymaga sprzątania stanu przełącznika). Bonus prawego Ctrl: wklejanie odbywa się przez Ctrl+V, więc fizycznie trzymany Ctrl nie przeszkadza.
- Klawisz jest tłumiony (suppressed) — nie trafia do aplikacji pod spodem.

### 2.2 Trzy tryby wypisywania (przełączane w menu tray)

| Tryb | Działanie | Korekta AI | Opóźnienie | Zastosowanie |
|---|---|---|---|---|
| **1. Realtime** | Słowa wpisywane na bieżąco podczas mówienia | ❌ brak (nie da się bezpiecznie cofać już wpisanego tekstu w cudzej aplikacji) | ~1–2 s za głosem | Szybkie notatki, podgląd na żywo |
| **2. Per-sentence** | Po każdej pauzie (~500 ms ciszy) zdanie jest transkrybowane, poprawiane i wpisywane; można mówić dalej w trakcie | ✅ | kilka sekund na zdanie | Dyktowanie dłuższych tekstów z widocznym postępem |
| **3. On-release** | Całość buforowana do puszczenia klawisza, potem transkrypcja + korekta + wpisanie w całości | ✅ (najlepsza jakość — model widzi cały kontekst) | najdłuższe, ale najlepszy wynik | Domyślny tryb; wiadomości, polecenia, akapity |

### 2.3 Korekta AI (Ollama + qwen3.5:2b)
Surowa transkrypcja przechodzi przez lokalny LLM, który:
- poprawia literówki, nieistniejące słowa i błędy gramatyczne ("Wczoraj **poszłem** na piknik który **odbędzie** się w południe" → "Wczoraj **poszedłem** na piknik, który **odbył** się w południe."),
- usuwa wypełniacze ("yyy", "eee", "no więc", powtórzenia),
- dodaje interpunkcję i wielkie litery,
- **nie** odpowiada na pytania, **nie** tłumaczy, **nie** dodaje treści.

Gdy Ollama nie działa albo odpowiada zbyt wolno → wpisywany jest **surowy transkrypt** (aplikacja nigdy nie "gubi" wypowiedzi). Korektę można wyłączyć w menu.

### 2.4 Języki
Polski (domyślnie) / angielski — przełącznik w menu tray. Język jest przekazywany jawnie do Whispera (bez auto-detekcji — auto-detekcja na krótkich wypowiedziach "skacze" między językami) i wybiera wersję promptu korekty oraz czarnej listy halucynacji.

### 2.5 Cel tekstu
Tekst trafia do okna, które **ma fokus klawiatury** (tam gdzie miga kursor) — tak jak dyktowanie Windows. To rozwiązanie sprawdzone w v1 (podejście "pod myszką" zostało tam porzucone). Zabezpieczenia:
- pola haseł są wykrywane i **nigdy** nie są wypełniane,
- okna z uprawnieniami administratora: wykrycie i sygnalizacja ikoną (tekst nie wejdzie bez uruchomienia aplikacji jako administrator — ograniczenie Windows).

### 2.6 Tray (zasobnik systemowy)
Ikona z trzema stanami: szara (bezczynna), czerwona (nagrywanie), pomarańczowa (przetwarzanie). Menu (po angielsku):

```
VoiceCommander2 — status
─────────────────────────
Language            ▸  (•) Polish   ( ) English
Mode                ▸  (•) On release  ( ) Per sentence  ( ) Realtime
AI correction          [x]
Push-to-talk key    ▸  (•) Right Ctrl  ( ) F9  ( ) Scroll Lock
─────────────────────────
Performance (A/B)   ▸  [ ] Eager transcription (on-release mode)
                       [ ] Fast injection
                       [ ] Overlapped correction (per-sentence)
─────────────────────────
Paused                 [ ]
Start with Windows     [ ]
Open config file
Open log
─────────────────────────
Exit
```

Model korekty wybiera się w tray z listy modeli zainstalowanych w **lokalnej** Ollamie (`GET /api/tags`, odświeżane co 5 s przy otwarciu menu) — na każdym komputerze widać jego własne modele; model z configu, którego nie ma na danej maszynie, jest pokazany jako "not installed here". Zmiana modelu budzi pętlę keep-alive korektora, więc status w menu (szary/aktywny) aktualizuje się w kilka sekund. Ustawienia zapisywane w `%APPDATA%\VoiceCommander2\config.json` (zapis atomowy). Autostart przez klucz rejestru `HKCU\...\Run`. Zabezpieczenie przed drugą instancją (mutex).

### 2.7 Przełączniki Performance (A/B)

Trzy niezależne usprawnienia latencji, domyślnie **wyłączone** (zachowanie legacy). Przełączenie działa od **następnego** dyktatu, bez restartu; dyktat w toku kończy się na ustawieniach z chwili wciśnięcia PTT (snapshot).

| Przełącznik | Klucz configu | Działanie |
|---|---|---|
| Eager transcription | `perf_eager_stt` | Tryb On release: segmentacja (VAD) i transkrypcja biegną **w trakcie trzymania** klawisza; po puszczeniu dogrywany jest tylko ogon, potem jedna korekta + jedno wstrzyknięcie. Tekst wynikowy identyczny, czekanie krótsze |
| Fast injection | `perf_fast_injection` | Teksty ≤ `short_text_chars` (domyślnie 120) wpisywane przez SendInput (schowek nietknięty); dłuższe — schowek z **adaptacyjnym** czekaniem: delayed rendering + `WM_RENDERFORMAT` sygnalizuje faktyczne odczytanie wklejki, limit `clipboard_wait_max_ms` (domyślnie 300, jak stały sleep legacy) |
| Overlapped correction | `perf_pipelined_correction` | Tryb Per sentence: STT zdania N+1 równolegle z korektą Ollama zdania N; wstrzykiwanie zawsze w kolejności wypowiedzi (pojedynczy worker FIFO) |

Każdy dyktat kończy się jedną linią w logu (logger `timing`):

```
DICTATION mode=on_release outcome=injected total_ms=1840 stt_ms=1210 eager_stt_ms=6480 corr_ms=520 inject_ms=95 eager=1 fastinj=1 overlap=0
```

`total_ms` = od puszczenia klawisza do zakończenia; `eager_stt_ms` = praca STT wykonana jeszcze w trakcie trzymania. Porównanie A/B: wykonaj dyktat, przełącz, powtórz, porównaj dwie linie (`Select-String DICTATION`). Progi `short_text_chars` i `clipboard_wait_max_ms` tylko w pliku config.

---

### 2.8 Czytanie odpowiedzi Claude Code (TTS, feature 002)

Claude Code po zakończeniu tury wywołuje hook `Stop` (HTTP POST na `127.0.0.1:47321/speak` albo skrypt `vc2_speak.py`), który przekazuje pole `last_assistant_message`. Serwer w tray (`speak_server.py`) odpowiada `202` natychmiast i przekazuje tekst do `tts.Speaker`:

1. `text_prep.py` usuwa markdown (bloki kodu i tabele → placeholder, linki → tekst linku, URL-e → nic, inline code → treść), dzieli na zdania, stosuje słownik wymowy `pronunciation.txt` i wykrywa angielskie identyfikatory (`config.py`, `TtsSnapshot`, `list_output_devices`, `--fast`).
2. Opcjonalnie (`tts_summarize`) `corrector.summarize()` streszcza długie odpowiedzi przez Ollamę; każda awaria = czytanie pełnego tekstu.
3. `tts.PiperWorkerBackend` steruje procesem `tts_worker.py` (jedyny moduł importujący GPL-owy `piper`) przez pipe'y: żądania JSON, odpowiedzi jako ramki PCM. Angielskie fragmenty są fonemizowane głosem `en_US-lessac-medium` i wstrzykiwane jako `[[ … ]]` do polskiego zdania (`tts_codeswitch=inject`); alternatywa `splice` skleja audio dwóch głosów.
4. `tts.Player` odtwarza przez `sounddevice.OutputStream` (22050 Hz, bloki 100 ms), więc `stop()` działa w ≤ 100 ms. PTT wywołuje `speaker.stop()` przed obsługą dyktowania.

Polityka kolejki: `latest` (nowa odpowiedź przerywa poprzednią, domyślnie) lub `append`. Zdarzenie `SubagentStop` jest ignorowane, chyba że `tts_speak_subagents=true`. Ikona tray jest zielona podczas mówienia; stany dyktowania mają pierwszeństwo. Każde żądanie kończy się jedną linią `SPEAK id=… outcome=spoken|stopped|superseded|empty|disabled|ignored_event|error … first_audio_ms=… audio_s=…`. Szczegóły: `specs/002-speak-claude-answers/`.

## 3. Technologie

### 3.1 Kluczowa decyzja: podział GPU między Whisper i LLM

Pierwotny projekt zakładał laptop z **MX450 (2 GB VRAM)** i wymuszał Whisper na CPU. Obecna maszyna (pomiar 16.09.2026) to **RTX 3080 Laptop 8 GB VRAM, i7-12700H, 32 GB RAM** — Whisper `medium` (`int8_float16`) i LLM działają razem na GPU. Budżet jest jednak ciasny: Whisper `medium` + pulpit + przeglądarka zajmują ~4,5 GB, więc **dla LLM zostaje ~3,5 GB**. Model, który się w tym nie mieści, Ollama po cichu dzieli między GPU i CPU i korekta zwalnia kilkukrotnie (problem nr 1 z v1). Dlatego:

- model korekty musi mieć **≤ ~3 GB w VRAM** po załadowaniu (`ollama ps` → `100% GPU`),
- `ollama_num_gpu` w configu (`-1` = decyduje Ollama, `0` = wymuś CPU, `N` = liczba warstw na GPU) pozwala to sprawdzić i wymusić,
- `tools/model_bench.py` mierzy latencję i jakość GPU vs CPU dla dowolnych modeli (patrz §5.3).

Ta sama aplikacja (i ten sam `config.json`) jeździ między dwoma laptopami — RTX 3080 8 GB i MX450 2 GB — więc silnik STT jest wybierany **profilem z menu tray ("STT profile (per machine)")**: jeden klik ustawia model, urządzenie, typ obliczeń i `beam_size`, a model przeładowuje się w tle (stary obsługuje dyktowanie, aż nowy będzie gotów). Profile leżą w `stt_profiles` w configu i można je edytować; gdy ręcznie ustawione wartości nie pasują do żadnego, menu pokazuje "Custom". Domyślne: **RTX: large-v3-turbo / cuda / float16 / beam 5** (najlepsza polska fleksja i pojedyncze słowa), **RTX: medium / cuda / int8_float16 / beam 2** (dotychczasowe), **MX450: small / cpu / int8 / beam 2** (2 GB VRAM zostaje dla Ollamy). Na CPU large-v3-turbo nie ma sensu (RTF rzędu 0,5–1), na RTX zmierzone medium daje RTF 0,14 (mediana z 506 wypowiedzi, 16.09.2026).

Zmierzone (16.09.2026, 47 zdań PL+EN, `Corrector.correct()`): na GPU każdy z testowanych modeli 2–5B poprawia zdanie w **0,6–0,9 s**; na samym CPU to **1,5–8 s** (qwen3.5:2b ~6 s, gemma4 e2b ~1,5–4 s). GPU jest więc obowiązkowe dla trybu per-sentence.

### 3.2 Stos technologiczny

| Komponent | Biblioteka | Dlaczego ta |
|---|---|---|
| Speech-to-text | **faster-whisper** (CTranslate2) | Najszybszy Whisper na CPU (int8); wystawia `no_speech_prob`/`avg_logprob` (potrzebne do filtrów halucynacji) i znaczniki czasu słów (potrzebne do trybu realtime); jeden model obsługuje wszystkie 3 tryby |
| Model STT | **`small` multilingual, int8, CPU** | `base` wyraźnie gorszy dla polskiej fleksji; `medium` na CPU laptopa ~3–4× wolniejszy od czasu rzeczywistego (zabiłby tryby 1 i 2). Konfigurowalne — benchmark `large-v3-turbo` w M6 |
| Przechwytywanie audio | **sounddevice** (PortAudio) | 16 kHz mono, ramki 512 próbek (32 ms) — dokładnie rozmiar oczekiwany przez Silero VAD; strumień otwarty cały czas (gating ramek stanem klawisza) — zero opóźnienia otwierania urządzenia |
| Detekcja mowy (VAD) | **pysilero-vad** (onnxruntime, bez torch) | Sprawdzony w v1 Silero; instalacja bez PyTorch (~oszczędność 2 GB) |
| Globalny skrót | **keyboard** | Niskopoziomowy hak WH_KEYBOARD_LL, bez uprawnień administratora, rozróżnia lewy/prawy modyfikator, umie tłumić klawisz; fallback: pynput |
| Korekta AI | **Ollama** (HTTP, `requests`) + **qwen3.5:2b** | Już zainstalowane; `localhost:11434`, `temperature=0`, kary za powtórzenia wyzerowane (patrz 5.3), `keep_alive=30m` |
| Wpisywanie tekstu | **pywin32** (schowek) + **ctypes/SendInput** (Unicode) | Szczegóły w §4.4 |
| Tray | **pystray** + Pillow | Dojrzała, lekka, natywny backend Windows |

Instalacja bez PyTorch — całość poniżej ~300 MB. **Uwaga na Python 3.13**: jeśli `ctranslate2` nie ma jeszcze koła cp313, tworzymy venv na Pythonie 3.12 (`py -3.12 -m venv`) — decyzja w pierwszym milestone.

---

## 4. Architektura

### 4.1 Struktura projektu

```
VoiceCommander2/
  main.py            # start, testy środowiska (CUDA/Ollama), ładowanie modelu, tray
  config.py          # dataclass ⇄ JSON w %APPDATA%, zapis atomowy
  hotkey.py          # hak klawiatury, zdarzenia PTT, tłumienie klawisza
  audio.py           # strumień sounddevice, gating ramek, bufor pre-roll; lista urządzeń wyjściowych
  text_prep.py       # (002) markdown → zdania i fragmenty pl/en, słownik wymowy
  tts.py             # (002) Speaker (kolejka, stop), PiperWorkerBackend (nadzór procesu), Player
  tts_worker.py      # (002) proces potomny z GPL-owym piper; jedyny import `piper`
  speak_server.py    # (002) HTTP 127.0.0.1:47321 /speak /stop /health + generator snippetów hooka
  vad.py             # Silero VAD + maszyna stanów segmentacji
  stt.py             # faster-whisper (singleton), filtry halucynacji, czarne listy
  streaming_stt.py   # tryb 1: pętla LocalAgreement
  corrector.py       # klient Ollama, prompty pl/en, timeouty, fallback, warm-up
  injector.py        # schowek + SendInput Unicode, ochrona haseł/UIPI/modyfikatorów
  pipeline.py        # kolejki, wątek potoku, dyspozycja trybów 1/2/3
  tray.py            # menu pystray, stany ikony
  autostart.py       # klucz HKCU Run
  logsetup.py        # log rotowany w %APPDATA%\VoiceCommander2\logs
  assets/blocklist_pl.txt, blocklist_en.txt
```

### 4.2 Model wątków
Jeden proces, zwykłe wątki + `queue.Queue` (bez asyncio — wszystkie zależności są blokujące/callbackowe, a CTranslate2 zwalnia GIL podczas inferencji):

- **main**: pętla pystray
- **hak klawiatury** (wątek biblioteki): ustawia stan PTT, publikuje zdarzenia wciśnięcia/puszczenia
- **callback PortAudio** (wątek biblioteki): gdy PTT trzymany, pcha ramki 32 ms do `audio_q`
- **segmenter**: konsumuje `audio_q`, maszyna stanów VAD, emituje segmenty do `segment_q`
- **potok** (jeden, sekwencyjny — **gwarantuje kolejność wpisywanego tekstu**): `segment_q` → STT → filtr halucynacji → korekta → wpisanie
- **worker streamingu** (tylko tryb 1): bufor kroczący + LocalAgreement, emituje zatwierdzone słowa wprost do injectora

### 4.3 Przepływ danych per tryb

**Tryb 3 (on-release):** PTT wciśnięty → ramki do bufora → PTT puszczony → kontrola VAD (czy w ogóle była mowa? cisza = nic nie rób) → transkrypcja całości → filtr halucynacji → korekta Ollama (limit 15 s) → wklejenie przez schowek. Nagrania >30 s cięte na kawałki, sklejane przed korektą.

**Tryb 2 (per-sentence):** segmenter emituje zdanie po każdych ~500 ms ciszy (oraz wymuszenie przy 30 s / puszczeniu PTT) → potok per zdanie: STT → korekta (limit 8 s) → wklejenie ze spacją na końcu. Użytkownik mówi dalej, kolejka buforuje, kolejność zachowana.

**Tryb 1 (realtime):** algorytm **LocalAgreement-2** na tym samym modelu: co ~1,2 s ponowna transkrypcja niezatwierdzonego okna bufora; zatwierdzany jest najdłuższy wspólny prefiks słów dwóch ostatnich hipotez; tylko nowo zatwierdzone słowa są wpisywane (SendInput Unicode). Bufor przycinany na znacznikach czasu zatwierdzonych słów przy >12 s. Gdy CPU nie nadąża, słowa pojawiają się seriami (degradacja łagodna). Po puszczeniu PTT — dokończenie ogona.

### 4.4 Wpisywanie tekstu (injector)

1. **Wklejanie przez schowek** (tryby 2/3): zapis zawartości schowka → `SetClipboardData(CF_UNICODETEXT)` → Ctrl+V przez SendInput → ~50 ms → przywrócenie schowka. Idealne polskie znaki, szybkie dla długich tekstów. Gdy w schowku format nietekstowy — pominięcie przywracania + jednorazowe ostrzeżenie.
2. **SendInput z KEYEVENTF_UNICODE** (tryb 1 + fallback): wstrzykiwanie znaków UTF-16 wprost przy kursorze; nie dotyka schowka; natywna obsługa diakrytyków. (pyautogui/pynput odrzucone: wolne i zawodne dla Unicode.)
3. **Zabezpieczenia**: pola haseł (styl `ES_PASSWORD` + UIA) — tekst porzucany, mrugnięcie ikony; okna elevated (UIPI) — detekcja i sygnalizacja; **neutralizacja trzymanych modyfikatorów** — przed wstrzyknięciem sprawdzenie `GetAsyncKeyState` i tymczasowe "puszczenie" modyfikatorów, żeby znaki nie zamieniły się w skróty Ctrl+litera.

---

## 5. Parametry

### 5.1 VAD (sprawdzone w v1)

| Parametr | Wartość |
|---|---|
| Próg startu mowy | 0,50 |
| Próg kontynuacji (histereza) | 0,35 |
| Cisza kończąca zdanie | 500 ms |
| Minimalna długość mowy | 300 ms |
| Maksymalna długość segmentu | 30 s (cięcie wymuszone) |
| Margines przed/po segmencie | 250 ms |

### 5.2 Whisper
`language` z configu (nigdy auto), `beam_size=2`, `condition_on_previous_text=False` (mniej halucynacji), własny VAD (wbudowany wyłączony), `cpu_threads = max(4, rdzenie_fizyczne − 2)`.

**Filtr halucynacji** (z v1): odrzuć segment gdy (`no_speech_prob > 0.6` **i** `avg_logprob < −1.0`) **lub** `avg_logprob < −1.2` **lub** tekst pasuje do czarnej listy ("napisy stworzone przez społeczność amara.org", "dziękuję za uwagę", "zapraszam do subskrypcji", …; osobne pliki pl/en).

### 5.3 Prompt korekty (polski; angielski analogiczny)

Pełny tekst w `corrector.py` (`_SYSTEM_PL` / `_SYSTEM_EN`). Konstrukcja (po analizie logów 15.09.2026, ~40% korekt modelu 2B zawierało regresję):

- **lista tego, co wolno** zmienić (literówki, interpunkcja, wielkie litery, zamknięta lista wypełniaczy "yyy/eee/mmm/hmm" i bezpośrednie powtórzenia) — zamiast otwartego "usuń wtrącenia", które wycinało "w takim razie" i całe człony zdań,
- **lista tego, czego NIE wolno**: osoba/liczba/czas czasownika, synonimy, nazwy własne i żargon IT (skill, branch, commit, feature'y, Claude, low-poly…), słowa z apostrofem, liczby, liczba i kolejność zdań,
- **znaki pauzy usuwane deterministycznie przed promptem** (`corrector.strip_pause_marks`, flaga `fix_pause_marks` w configu). Whisper zamyka segment na każdej pauzie w namyśle: wstawia "..." albo kropkę i zaczyna kolejne słowo wielką literą. Model 2B nie stosuje reguł o kropkach w żadną stronę (benchmark 16.09.2026), więc przypadki jednoznaczne naprawia kod: "..." w środku zdania → spacja, przed wielką literą → kropka, na końcu → usunięty; kropka + mała litera ("na stylistyce. i potem") → kropka usunięta, z wyjątkiem skrótów (np., m.in., tzn., ok.) i liczb. Kropka przed wielką literą jest usuwana tylko dla zamkniętej listy słów, które nie otwierają dyktowanego zdania (oraz, ani, albo, lub, ponieważ, gdyż, który/która/które…; EN: and, or, nor, which, whereas), np. "szablonu. Ani też" → "szablonu ani też". Reszta — "Zrób to. I to jest ważne", "Ale", "Bo", "But", "So" — zostaje decyzją modelu: prompt ma osobny punkt na liście dozwolonych zmian i przykład 4 (PL/EN), który pokazuje usunięcie kropki-pauzy przy zachowaniu kropki między dwoma pełnymi zdaniami. Obowiązuje też dla surowego transkryptu przy fallbacku.
- **3 przykłady few-shot z realnej dziedziny** (polecenia do asystenta programisty), w tym: zachowana 2. osoba i zdrobnienie, pytanie z godzinami, wielokropek + skróty (MCP, GitHub). Przykład ze zmianą liczby ("dwa jabłka yyy znaczy trzy" → "trzy") usunięty — uczył model redagowania treści wbrew regule "nie zmieniaj liczb".

Wywołanie: `POST /api/chat`, `stream=false`, `think=false`, `keep_alive="30m"`, `num_predict = max(80, 3 × liczba słów)` z fallbackiem przy `done_reason="length"`. Opcje próbkowania: `temperature=0`, **`presence_penalty=0`, `frequency_penalty=0`, `repeat_penalty=1.0`** — karta modelu qwen3.5 w Ollamie ma domyślnie `presence_penalty=1.5`, a Ollama dokłada `repeat_penalty=1.1`; obie kary penalizują tokeny już obecne w kontekście, czyli dosłownie przepisywanie wejścia, i były główną przyczyną parafraz ("żebyś"→"abyś", "ignorujemy"→"ignoruję", ucinanie zdań). Warm-up przy starcie. Oczekiwana latencja: **1–3 s na zdanie**.

**Kontrola jakości odpowiedzi** (`corrector.sanity_check`, na słowach po odfiltrowaniu wypełniaczy): odrzuć korektę i wpisz surowy transkrypt, gdy liczba słów spadła poniżej 75% lub wzrosła powyżej 150%, gdy zniknęła liczba lub słowo z apostrofem, albo gdy zmieniono więcej niż 25% słów wejścia (min. 2). Obcinanie obejmujących cudzysłowów i echa znaczników.

**Zestaw odporności**: `tools/correction_eval.py` — 15 surowych transkryptów z logów (pytanie, polecenie "git push", żargon, wielokropki) przez `Corrector.correct()` na żywej Ollamie; każdy przypadek ma chroniony fragment, który musi przetrwać. Uruchamiać po każdej zmianie promptu lub opcji.

**Benchmark modeli**: `tools/model_bench.py <model> [<model>...]` — te same 15 zdań + 20 kolejnych z logów + 12 zdań EN, dla każdego modelu raz z domyślnym umieszczeniem (GPU) i raz z `num_gpu=0` (CPU). Raportuje medianę/p90/max latencji, liczbę fallbacków sanity-check, zachowane fragmenty chronione i niezmienione wyjścia; pełne wyjścia trafiają do `specs/bench/bench_<data>.md` do przeglądu ręcznego. Wyniki z 16.09.2026 i uzasadnienie wyboru modelu: `specs/bench/README.md`.

---

## 6. Obsługa błędów i ryzyka

| Awaria | Wykrycie | Reakcja |
|---|---|---|
| Ollama nie działa / brak modelu | błąd połączenia / 404 | wpisz surowy transkrypt; status "correction unavailable"; ponawiaj warm-up co 60 s |
| Ollama za wolna | timeout (8/15 s) | surowy transkrypt + log latencji |
| LLM odpowiada zamiast poprawiać | heurystyka długości | surowy transkrypt |
| Halucynacja Whispera na ciszy | progi + czarna lista | segment porzucony po cichu |
| Brak koła ctranslate2 dla Pythona 3.13 | błąd pip przy instalacji | venv na Pythonie 3.12 |
| Okno elevated zjada wpisywany tekst (UIPI) | `OpenProcess` → ACCESS_DENIED | log + mrugnięcie ikony; opcja uruchomienia jako admin |
| Pole hasła | styl `ES_PASSWORD` | nigdy nie wpisuj |
| Menedżer schowka / wyścig o schowek | zrzut nietekstowy | przywrócenie zrzutu; config `injection_method` = tylko SendInput |
| Trzymane modyfikatory psują wstrzykiwanie | `GetAsyncKeyState` | tymczasowe key-up wokół wstrzyknięcia |
| Streaming nie nadąża za CPU | opóźnienie zatwierdzania | słowa seriami; log RTF; sugestia modelu `base` dla trybu 1 |
| Odpięty/zmieniony mikrofon | błąd callbacku PortAudio | ponowne otwarcie strumienia na domyślnym urządzeniu |
| Druga instancja aplikacji | mutex przy starcie | druga instancja kończy się komunikatem |
| Dyktowanie >30 s (tryb 3) | długość segmentu | cięcie przez VAD, przetwarzanie kawałków, sklejenie przed korektą |

---

## 7. Plan wdrożenia (milestones)

1. **M1 — Szkielet** (pół dnia): venv (weryfikacja kół cp313, w razie czego 3.12), config, logi, tray ze statycznym menu, PTT na prawym Ctrl (logowanie zdarzeń), nagrywanie ramek do WAV do odsłuchu. Czyste zamykanie wątków.
2. **M2 — Tryb 3 bez LLM**: stt.py (small int8, filtry), injector (schowek + ochrona haseł), minimalny potok: puszczenie PTT → transkrypcja → wpisanie. **Pierwsze użyteczne dyktowanie.** Test polskich znaków w Notatniku.
3. **M3 — Korekta**: corrector.py (prompt, warm-up, timeouty, fallback), przełączniki korekty i języka w tray. Pomiar latencji STT/LLM per wypowiedź.
4. **M4 — VAD + tryb 2**: segmenter z tabelą parametrów, przełącznik trybów, test kolejności (3 zdania ciągiem).
5. **M5 — Tryb 1**: LocalAgreement, emisja słów SendInput Unicode, neutralizacja modyfikatorów, domknięcie ogona po puszczeniu.
6. **M6 — Szlif**: autostart, pauza, stany ikony, mutex, detekcja UIPI, strojenie czarnej listy, benchmark `large-v3-turbo`, opcjonalnie PyInstaller `--onedir` (nigdy onefile — DLL-e CTranslate2/onnxruntime).

Uruchamianie: `pythonw.exe main.py` (bez okna konsoli), autostart przez rejestr. Ikona tray pojawia się natychmiast, status "loading model…" do czasu gotowości (~2–4 s).

## 8. Weryfikacja

- **Instrumentacja latencji od M2**: log per wypowiedź (długość audio, czas STT, czas LLM, czas wpisania) → empiryczne porównanie trybów 1/2/3 (deklarowany cel użytkownika).
- **Macierz testów ręcznych**: cele = Notatnik, VS Code, pole tekstowe w Chrome, Word, cmd jako admin (oczekiwana odmowa z sygnalizacją), pole hasła (oczekiwane pominięcie). Wejścia = zdanie kanoniczne ("Wczoraj poszłem na piknik…" → oczekiwane "poszedłem… odbył się"), zdanie z wypełniaczami ("yyy no więc…"), zdanie angielskie po przełączeniu języka, 2 s ciszy (nic nie wpisane), monolog 45 s (cięcie na kawałki).
- **Zestaw odporności korekty**: `tools/correction_eval.py` — 15 stałych surowych transkryptów z logów (w tym pytanie i polecenie — weryfikacja, że model poprawia, a nie odpowiada/wykonuje) przez `Corrector.correct()`; przegląd po każdej zmianie promptu lub opcji próbkowania.
- **Test awarii Ollamy**: zatrzymaj Ollamę, dyktuj, potwierdź fallback do surowego tekstu w limicie czasu.
- **Test schowka**: skopiuj obraz, dyktuj w trybie 3, potwierdź przywrócenie obrazu (lub pojedyncze ostrzeżenie).

## 9. Możliwe usprawnienia na przyszłość

- **Komendy głosowe**: "nowa linia", "przecinek", "usuń ostatnie zdanie" (planowane w v1, nigdy nie zbudowane).
- **Słownik użytkownika**: nazwy własne, żargon — wstrzykiwane do promptu korekty (initial_prompt Whispera + kontekst LLM).
- **Profile per aplikacja**: np. w terminalu bez interpunkcji końcowej, w Wordzie pełna korekta.
- **Tryb toggle** obok push-to-talk (wciśnij raz start, drugi raz stop) — dla długich dyktand.
- **Wybór mikrofonu** w menu tray.
- **Eksperyment**: korekta w trybie 1 po puszczeniu klawisza (backspace × liczba znaków + wklejenie poprawionej całości) — flaga w configu, domyślnie wyłączona.
- **Benchmark `large-v3-turbo` int8 na CPU** — jeśli latencja akceptowalna w trybie 3, znacząco lepsza polszczyzna.
- **Historia dyktowań** (lokalny plik) z możliwością ponownego wklejenia.

---

## 10. Wyniki krytyki projektu (recenzja adwersaryjna)

Projekt przeszedł przegląd krytyczny. Główny wniosek: **trzy sztandarowe obietnice (realtime, korekta w 1–3 s, niezawodny PTT) podkopuje ta sama przyczyna — wszystko (Whisper, część LLM, hak klawiatury) dzieli jeden, termicznie ograniczony CPU laptopa.** Tryb 1 i timeouty trybu 2 traktujemy jako **hipotezy do zbenchmarkowania w M2–M3**, nie jako pewne funkcje.

### Poprawki przyjęte do projektu w wyniku krytyki

1. **Hak klawiatury (krytyczne):** callback haka ma być trywialny (ustaw flagę, wróć natychmiast) — inaczej Windows po cichu usuwa hak, gdy proces jest zajęty transkrypcją (limit LowLevelHooksTimeout). Dodatkowo watchdog ponownie rejestrujący hak + zapasowa detekcja puszczenia klawisza przez `GetAsyncKeyState` (hak nie działa na bezpiecznym pulpicie UAC — bez tego nagrywanie "wisi" po monicie UAC). Obowiązkowy test AltGr + polskie znaki (ą/ę/ż) przy aktywnym haku — znany feler biblioteki `keyboard` przy hakowaniu Ctrl (AltGr = LCtrl+RAlt).
2. **Ochrona przed zmianą fokusu (krytyczne):** zapamiętanie HWND okna z fokusem w momencie puszczenia PTT; przed wpisaniem weryfikacja, że fokus się nie zmienił (użytkownik zdąży zrobić Alt+Tab podczas 10–30 s przetwarzania) — w razie zmiany tekst wstrzymany + sygnalizacja.
3. **Latencja LLM (krytyczne):** przy częściowym odciążeniu na 2 GB VRAM realny czas to raczej 6–20 s na dłuższe zdanie, nie 1–3 s. Przed ustaleniem timeoutów pomiar rzeczywistych tok/s; jawne wyłączenie trybu "thinking" modelu Qwen (`think:false`); rozważenie mniejszego kwantu mieszczącego się w całości w VRAM.
4. **`num_predict` skalowany z długością wejścia** + detekcja `done_reason:"length"` → w razie ucięcia fallback do surowego transkryptu (stały limit 400 uciąłby korektę 45-sekundowego monologu w połowie).
5. **Schowek:** zapis formatów `ExcludeClipboardContentFromMonitorProcessing`/`CanIncludeInClipboardHistory` — bez tego każde dyktowanie ląduje w historii schowka Win+V, a przy włączonej synchronizacji schowka w chmurze **tekst opuściłby laptopa** (złamanie zasady 100% lokalnie). Retry na `OpenClipboard`, adaptacyjne (nie stałe 50 ms) opóźnienie przed przywróceniem.
6. **Mikrofon otwierany tylko na czas PTT** (koszt ~100–200 ms ukryty w pre-rollu) — stale otwarty strumień świeci wskaźnikiem mikrofonu w Windows 11 i drenuje baterię; razem z hakiem i autostartem tworzy też profil "keyloggera" dla antywirusa (dystrybucja jako skrypt, nie exe z PyInstallera).
7. **Tryb 2:** cisza kończąca zdanie podniesiona do ~800–1000 ms (500 ms tnie zdania w pół myśli → LLM "poprawia" fragmenty osobno), limit głębokości kolejki + pomijanie korekty przy zaległościach, kontekst poprzedniego zdania w promptcie korekty.
8. **Tryb 1:** przed budową (M5) obowiązkowy benchmark: powtarzana transkrypcja okien 8–12 s pod obciążeniem ciągłym; jeśli jeden przebieg > ~1 s — tryb 1 działa na modelu `base`/`tiny` z krótkim oknem albo zostaje wycięty. Obietnica "~1–2 s za głosem" na `small` jest na tym CPU nierealna.
9. **Test korekty zmieniony:** zdanie kanoniczne ("Wczoraj poszłem na piknik…") jest jednocześnie przykładem few-shot w promptcie — test skażony; ewaluacja na 30–50 zdaniach spoza promptu, zbudowanych z *prawdziwych* wyjść Whispera `small` (nie z ręcznie pisanych błędów).
10. **Detekcja UIPI poprawiona:** `OpenProcess` z `PROCESS_QUERY_LIMITED_INFORMATION` *udaje się* dla procesów elevated — trzeba czytać poziom integralności tokenu. Ochrona pól haseł opisywana jako "best effort" (pola w przeglądarkach wymagają UIA, nie stylu `ES_PASSWORD`).

### Ryzyka przyjęte świadomie (bez zmiany projektu)

- **Whisper `small` ma dla polskiego ~15–25% WER** — korekta LLM może zamieniać błędy STT na płynne, ale *inne* zdania ("wypolerowane śmieci"). Mitygacja: benchmark `large-v3-turbo` w M6, słownik użytkownika w przyszłości; to ograniczenie darmowego, lokalnego STT na tym sprzęcie.
- **qwen3.5:2b może czasem nie posłuchać promptu** (odpowiedzieć na pytanie, wtrącić angielski) — łapane heurystykami tylko z grubsza; przełącznik wyłączenia korekty zostaje pod ręką.
- **Uczciwa latencja trybu 3** dla 30 s dyktowania: realnie 20–40 s całkowitego oczekiwania na tym sprzęcie — do zmierzenia i zakomunikowania, nie do ukrycia.
- `keep_alive=30m` trzyma ~1,8 GB VRAM — konfigurowalne, świadomy kompromis (szybkość korekty vs. inne użycie GPU).
