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

## Pierwsze uruchomienie

Model Whisper `small` (~460 MB) pobiera się automatycznie do `%LOCALAPPDATA%\VoiceCommander2\models` — to jedyny (jednorazowy) kontakt z internetem. Status „loading model…" znika po załadowaniu.

## Konfiguracja

`%APPDATA%\VoiceCommander2\config.json` — m.in. `stt_model` (`base`/`small`/`medium`/`large-v3-turbo`), progi VAD, timeouty korekty, `injection_method` (`clipboard`/`sendinput`). Logi: `%APPDATA%\VoiceCommander2\logs`.

## Znane ograniczenia

- Okna uruchomione jako administrator nie przyjmą tekstu (ograniczenie Windows/UIPI) — wtedy uruchom aplikację jako administrator.
- Pola haseł są pomijane (best-effort; pola w przeglądarkach nie zawsze da się wykryć).
- Tryb Realtime działa bez korekty AI i na słabszym CPU wpisuje słowa seriami; jeśli w logu widzisz ostrzeżenia o RTF > 1, zmień `stt_model` na `base`.
