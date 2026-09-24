# VoiceCommander2

Lokalne dyktowanie push-to-talk dla Windows. Pełny opis: [DOKUMENTACJA.md](DOKUMENTACJA.md).

## Uruchomienie

Środowisko jest już przygotowane w `.venv`. Start bez okna konsoli:

```
.venv\Scripts\pythonw.exe main.py
```

(do diagnostyki: `.venv\Scripts\python.exe main.py` — logi także w konsoli)

Ikona mikrofonu pojawi się w zasobniku (prawy dolny róg, może być schowana pod strzałką „^").

## Jak używać

1. Upewnij się, że **Ollama działa** (korekta AI; bez niej aplikacja wpisuje surowy transkrypt).
2. Kliknij w pole tekstowe, w które chcesz dyktować (tam gdzie miga kursor).
3. **Przytrzymaj prawy Ctrl**, mów, puść.
4. Ikona: szara = gotowa, czerwona = nagrywa, pomarańczowa = przetwarza. Po chwili tekst pojawi się w polu.

Prawy przycisk myszy na ikonie → ustawienia: język (Polish/English), tryb (On release / Per sentence / Realtime), korekta AI, klawisz PTT, pauza, autostart, dostęp do configu i logów.

**Performance (A/B)** — trzy przełączniki usprawnień latencji (Eager transcription, Fast injection, Overlapped correction), domyślnie wyłączone. Działają od następnego dyktatu, bez restartu. Każdy dyktat zapisuje w logu linię `DICTATION … total_ms=…` z rozbiciem czasów i stanem przełączników — dyktując to samo z przełącznikiem OFF i ON porównasz realny zysk na swoim sprzęcie.

## Pierwsze uruchomienie

Model Whisper `small` (~460 MB) pobiera się automatycznie do `%LOCALAPPDATA%\VoiceCommander2\models` — to jedyny (jednorazowy) kontakt z internetem. Status „loading model…" znika po załadowaniu.

## Konfiguracja

`%APPDATA%\VoiceCommander2\config.json` — m.in. `stt_model` (`base`/`small`/`medium`/`large-v3-turbo`), progi VAD, timeouty korekty, `injection_method` (`clipboard`/`sendinput`), `ollama_model` (nazwa modelu w Ollamie; w tray: "Correction model" pokazuje modele zainstalowane lokalnie), `fix_pause_marks` (usuwanie znaków pauzy Whispera przed korektą: wielokropków i kropek przed małą literą; kropki przed wielką literą ocenia model, domyślnie włączone), `ollama_num_gpu` (`-1` = Ollama decyduje, `0` = korekta na CPU) `stt_profiles` (nazwane zestawy model/urządzenie/typ obliczeń/beam dla różnych komputerów — przełączane w menu tray "STT profile") oraz `models_dir` (katalog modeli Whisper/TTS; ustaw krótką ścieżkę, np. `D:\VoiceCommander2\models`, gdy Python ze Sklepu przekierowuje `%LOCALAPPDATA%` do `...\Packages\...\LocalCache` — wtedy ścieżka pliku tymczasowego `model.bin` przekracza 260 znaków i pobieranie kończy się `FileNotFoundError`). Logi: `%APPDATA%\VoiceCommander2\logs`.

## Czytanie odpowiedzi Claude Code (TTS)

Aplikacja może czytać na głos końcową odpowiedź każdej tury Claude Code — po polsku, lokalnym głosem Piper (domyślnie `jarvis`), z angielską wymową nazw plików i identyfikatorów. Zero chmury; głosy (2 × ~63 MB) pobierają się raz do katalogu modeli.

1. `pip install -r requirements-tts.txt` w tym samym `.venv` (silnik Piper jest na licencji GPL-3.0 i działa w osobnym procesie `tts_worker.py`).
2. Tray → **Read Claude answers** → **Enabled**. Przy pierwszym włączeniu pobierają się głosy.
3. Hook Claude Code instaluje się sam, globalnie dla wszystkich projektów: przy włączeniu funkcji aplikacja dopisuje jeden wpis `Stop` (HTTP na `127.0.0.1:47321/speak`) do `%USERPROFILE%\.claude\settings.json`, zachowując istniejące hooki i robiąc kopię `settings.json.bak-<data>`. Ręcznie: `python claude_hooks.py install|status|remove` albo tray → **Read Claude answers** → **Claude Code hook**. Autoinstalację wyłącza `tts_hook_autoinstall=false` (wtedy snippet do wklejenia jest w **Open hook instructions**). Wariant HTTP wymaga Claude Code ≥ 2.1.63.
4. Test bez Claude Code:

```powershell
curl -X POST http://127.0.0.1:47321/speak -H "Content-Type: application/json" -d "{\"text\":\"Gotowe. Zaktualizowałem plik config.py.\"}"
```

Wciśnięcie klawisza PTT albo **Stop reading** natychmiast przerywa czytanie. Bloki kodu i tabele są zastępowane słowami „fragment kodu" / „tabela", inline `kod` jest czytany. Wymowę poprawisz w `pronunciation.txt` obok `config.json` (`termin = jak czytać`). Opcjonalnie **Summarise long answers** streszcza długie odpowiedzi lokalną Ollamą przed odczytaniem. Każde żądanie zostawia w logu linię `SPEAK … outcome=… first_audio_ms=…`.

## Znane ograniczenia

- Okna uruchomione jako administrator nie przyjmą tekstu (ograniczenie Windows/UIPI) — wtedy uruchom aplikację jako administrator.
- Pola haseł są pomijane (best-effort; pola w przeglądarkach nie zawsze da się wykryć).
- Tryb Realtime działa bez korekty AI i na słabszym CPU wpisuje słowa seriami; jeśli w logu widzisz ostrzeżenia o RTF > 1, zmień `stt_model` na `base`.
