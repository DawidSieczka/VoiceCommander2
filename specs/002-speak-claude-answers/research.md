# Research: Spoken Read-Back of Claude Code Answers

Date: 2026-09-15. Legend: **[V]** verified on this machine (RTX 3080 Laptop 8 GB, Win 11
26200, Python 3.12.10 Store build, Claude Code 2.1.272); **[S]** verified against a cited
source; **[I]** inferred.

## R1. How to get the answer text out of Claude Code

**Decision**: `Stop` hook, HTTP variant (`"type": "http"`) posting to the app's loopback
server; command-script variant generated as a fallback. Read `last_assistant_message`
only.

**Verified [V]**: in an isolated probe directory with both hook types configured, one
`claude -p` turn produced identical JSON on the command hook's stdin and in the HTTP POST
body (keys: `session_id, transcript_path, cwd, prompt_id, permission_mode,
hook_event_name, stop_hook_active, last_assistant_message, background_tasks,
session_crons`). `last_assistant_message` contained the markdown verbatim (`**bold**`,
backticks). HTTP request: `Content-Type: application/json`, `User-Agent: axios/1.15.2`.
With the server stopped, the next turn completed with no visible error and the command
hook still ran.

**Verified [S]** (code.claude.com/docs/en/hooks): HTTP hooks exist with `url`, `headers`,
`allowedEnvVars`, `timeout`; all HTTP hook failures are non-blocking; `Stop` and
`SubagentStop` carry `last_assistant_message`; neither supports `matcher`; command hooks
accept `"async": true`; settings edits are picked up by a file watcher; `Stop` fires in
CLI, `-p`, desktop, IDE and web. anthropics/claude-code#74340 confirms `transcript_path`
lags a turn; #79542 is an open feature request for built-in TTS (no shipped feature).

**Rejected**: scraping the terminal (fragile); `transcript_path` parsing (racy); a
`MessageDisplay` hook (display-only, 10 s timeout, no text field documented); building the
hook as a Claude Code plugin (over-engineering for one user).

**Consequence**: the endpoint must answer instantly and never return a `decision`, so it
can neither delay nor loop Claude Code.

## R2. TTS engine

**Decision**: Piper via PyPI `piper-tts` 1.8.0 on CPU. Polish voice
`pl_PL-jarvis_wg_glos-medium` (WitoldG, MIT), English phonemiser/voice
`en_US-lessac-medium` (rhasspy, MIT).

**Verified [V]**: `pip install piper-tts` on Store Python 3.12 pulls a prebuilt
`cp39-abi3-win_amd64` wheel with bundled `espeak-ng-data` and `espeakbridge.pyd`; no
Visual Studio build tools, no separate espeak-ng, no `piper-phonemize`. Deps:
`onnxruntime` (already in `.venv` at 1.30.0 via `pysilero-vad`), `pathvalidate`.
Jarvis: 22 050 Hz mono, single speaker, loads with 1.8.0 without warnings. Cold import
≈ 4.5 s, voice load ≈ 8–10 s each (first run; 5.8 s in a second venv), synthesis of a
3-sentence Polish answer: first chunk 337 ms, total 1.5 s for 6.8 s of audio (RTF 0.22
cold, 0.08–0.10 warm). Python API: `PiperVoice.load(path)`, `voice.synthesize(text,
SynthesisConfig(length_scale=…, volume=…))` → one `AudioChunk` per sentence
(`audio_int16_array`, `sample_rate`); `voice.phonemize(text)`; no `sentence_silence` in the
API (insert zeros in the player).

**GPU [V]**: `onnxruntime-gpu` 1.26 + cuDNN failed on the first Conv node (cuDNN frontend
error) and silently fell back to CPU; installing `onnxruntime-gpu` next to `onnxruntime`
corrupts the shared package directory. CPU is 5–10× real time anyway. The GPU is also
already ≈ 7.1 / 8 GB busy (ComfyUI, Ollama, Whisper medium, browser). **Do not use GPU for
TTS.**

**Alternatives surveyed [S]/[V]**: Chatterbox Multilingual v3 (MIT, Polish, torch,
5–7 GB VRAM, seconds of latency) — only viable as a later optional server backend, and
not concurrently with today's VRAM load; Supertonic 3 (Polish, ONNX) — [V] RTF 1.4–1.6 on
this CPU (slower than real time), repo archived 2026-09-09, OpenRAIL-M weights; Kokoro,
Qwen3-TTS, Orpheus, Dia, KittenTTS, CosyVoice 3, Voxtral — no Polish; Fish/OpenAudio S1,
XTTS-v2, OuteTTS, F5 finetunes, MMS — non-commercial licences; VibeVoice — Polish
"experimental". Windows built-in voices: only English OneCore voices are installed here
[V]; Polish "Paulina/Adam" require the Windows voice pack and are concatenative-era
quality; no Polish "Natural" offline voice exists [S]. Conclusion: nothing permissive
beats Piper for Polish on CPU with sub-second time-to-first-audio.

## R3. Licence: Piper is GPL-3.0-or-later, the app is MIT

`piper-tts` ≥ 1.3 is the OHF-Voice fork licensed GPL-3.0 because espeak-ng is compiled
into the wheel [S]. The FSF's position is that importing a GPL module in-process forms one
combined program, while talking to it through pipes/sockets as a separate process is
"communication at arm's length" (GPL FAQ, MereAggregation / GPLPlugins) [S]. Voice models
are MIT (WitoldG, rhasspy) — no issue.

**Decision**: run Piper in a **worker subprocess** (`tts_worker.py`, started with the
same `.venv` interpreter, text in / int16 PCM out over stdio pipes with a length-prefixed
frame protocol). Reasons beyond licensing: (1) a crash or hang inside ONNX/espeak cannot
take the tray down (constitution IV); (2) "stop" can hard-kill and respawn the worker if a
synthesis call does not return; (3) the 4.5 s import and 8–10 s voice load never run in
the main process; (4) `piper-tts` stays an optional dependency listed in
`requirements-tts.txt`, so the core app installs without it. Cost: ≈ 120 lines
(protocol + supervisor) and one extra process (~350 MB RSS with two voices [I]).

**Alternative kept open**: `PiperInProcessBackend` behind the same `TTSBackend` interface
(≈ 40 lines) if the user decides licensing does not matter for a personal tool. Flagged as
an open decision in plan.md; the worker design does not preclude it.

## R4. Pronouncing English tokens inside Polish text

**Problem [V]**: espeak-ng `pl` applies Polish letter-to-sound rules to ASCII tokens:
`config.py` → "konfik kropka py", `pytest` → "pytest" with Polish vowels, `JSON` → "json"
with a Polish j, `requirements.txt` → "regwirements kropka te-i-ks-te", `GitHub` →
"git hup". No automatic language switching exists in Piper; the phonemiser even strips
espeak's `(en)` markers [S].

**Decision (ordered pipeline)**:

1. **Pronunciation dictionary** (`pronunciation.txt`, user-editable, seeded): whole-token,
   case-insensitive replacements with either a Polish respelling (`GitHub = githab`) or
   explicit phonemes (`JSON = [[ dʒˈeɪsɑːn ]]`). Cheapest, works with any backend.
2. **English phoneme injection**: for remaining ASCII-only tokens matching the identifier
   heuristic (contains a dot-extension, camelCase, snake_case, `--flag`, or is an
   all-caps acronym ≤ 6 letters, or is in a small English-word list), obtain phonemes from
   the English voice's `phonemize()` and embed them as `[[ … ]]` in the Polish sentence.
   **[V]** the Polish voice passes `[[ nˈʌmpi ]]` through unchanged and synthesises
   without missing-phoneme warnings (the shared phoneme map covers ɹ ɚ æ θ ð ʌ ɪ ɑ ʊ ə).
   **[I]** acoustic quality of untrained English phonemes in a Polish model is unknown —
   Task T004 is a listening spike that decides between (2) and (3).
3. **Two-voice splicing** (fallback): synthesise English spans with `en_US-lessac-medium`
   and Polish spans with jarvis, concatenate int16 at the common 22 050 Hz with ~80 ms
   silence. **[V]** mechanism works (4.2 s mixed clip produced); cost is a timbre jump
   mid-sentence. Note jarvis was fine-tuned from the lessac checkpoint, so the jump may be
   mild.

File paths: speak only the last component and read the extension as a word
(`plan.md` → "plan em de"; `config.py` → "config paj" via dictionary), drop directories.
Numbers: espeak-ng `pl` already reads digits in Polish; no `num2words`.

## R5. Markdown → speech text

**Decision**: stdlib regex pipeline in `text_prep.py` (no new dependency; constitution VI):
fenced blocks → placeholder or removal; tables (lines with ≥ 2 pipes, separator rows) →
placeholder; headings/emphasis/blockquote/list markers stripped; links → link text;
bare URLs removed; inline code → content kept (configurable); HTML tags removed;
horizontal rules removed; emoji stripped; whitespace normalised; sentence split on
`.!?…` followed by space/newline, with abbreviation guard (`np.`, `tzn.`, `e.g.`, digits);
long sentences split at commas or a word boundary at 350 chars. Language heuristic:
Polish if diacritics or Polish stop-words dominate, else English.

**Surveyed [S]**: `claude-tts-hook` (Japanese; transliterates English to katakana — same
idea as R4.1), `claude-voice` (Kokoro, English, strips the same elements, skips replies
that are > 50 % code), `ktaletsk/claude-code-tts` (mistune AST stripper with tests — the
parse-don't-regex alternative if the regex pipeline accumulates edge cases),
`give-claude-code-voice-tts` (asks Claude for a `<spoken>` summary via prompt — rejected:
pollutes every answer and is unreliable). None handle Polish.

## R6. Optional summarisation via Ollama

**Decision**: `summarize(text, timeout_s)` in `corrector.py` style (`/api/chat`,
`think: false`, `temperature: 0`, `(2, timeout)`), Polish/English prompt pair, output
wrapped in `<streszczenie></streszczenie>` markers, sanity check `0.05 < ratio < 0.6` and
≤ 4 sentences, fallback to the full cleaned text on any failure. Default **off**; the
threshold default 600 characters. Measured LLM latency for qwen3.5:2b on this machine is
0.6–1.3 s for short inputs (from the dictation logs); an 8 s timeout keeps the worst case
bounded. A summary request whose id is no longer current is discarded.

## R7. Playback

**Decision**: one `sd.OutputStream(samplerate=22050, channels=1, dtype="int16",
device=<resolved name prefix>)` per request, callback-driven from a `queue.Queue` of
≤ 100 ms blocks; `stop()` = set event + clear queue + `stream.abort()`. **[V]** opening a
22 050 Hz output stream on MME while the app's 16 kHz `InputStream` is open works with no
input overflow flags; default output here is "Headphones (AirPods Pro)". WASAPI shared
mode would reject 22 050 Hz without `WasapiSettings(auto_convert=True)` — irrelevant while
`audio.py` stays on the default (MME) host API, documented in case capture moves to
WASAPI. Sentence gap: 150 ms of zeros. Volume 0.9 to avoid clipping against system
sounds. `list_output_devices()` mirrors `list_input_devices()`.

Barge-in / echo: with `mic_always_open=True` frames are discarded unless PTT is down, and
PTT press stops playback first, so speaker output never reaches STT. No extra gating
needed.

## R8. Server

**Decision**: `http.server.ThreadingHTTPServer` on `127.0.0.1:47321`, thread
`speak-server`, `allow_reuse_address=False` so a second instance fails loudly; `server.
shutdown()` + `server_close()` from `on_exit`. Body limit 1 MiB. Port collision → tray
reason "port 47321 busy", dictation unaffected (constitution IV). No framework dependency.

## R9. Hook material generation

**Decision**: on first enable and on port change, write `hook-http.json`,
`hook-command.json`, `vc2_speak.py`, `README-hooks.txt` to `%APPDATA%\VoiceCommander2\
hooks\`. The command snippet embeds `sys.executable` because `python` on PATH is not
guaranteed in the Git Bash shell Claude Code uses on Windows [S]. The app never edits
`~/.claude/settings.json` (the user's file already holds three unrelated hook groups; a
merge bug there would break their agent-watch tooling).

## R10. Testing approach

Same gates as feature 001: unit tests with fakes and no hardware. `FakeEngine` yields
silence chunks after a configurable delay (to test stop-during-synthesis and stale ids);
`FakePlayer` records writes and honours stop; the server is tested in-process with
`http.client` against an ephemeral port; text_prep and language spans are pure functions
with table-driven tests on real Claude Code answers captured from this project's logs
(≥ 10 samples, Polish with identifiers). Manual smoke per quickstart A–G. Timing: every
request logs `first_audio_ms`, so SC-001 is checked from the log alone.

## Decisions confirmed by the user on 2026-09-15 (all defaults kept, see plan.md)

1. In-process Piper (simpler, GPL applies to the combined program) vs worker subprocess
   (default).
2. Default `tts_summarize` off (chosen) vs on as in the handoff.
3. `SubagentStop` read-back off by default (chosen).
4. Inline code read aloud (chosen) vs skipped.
