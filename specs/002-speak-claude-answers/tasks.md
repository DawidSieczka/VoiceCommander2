# Tasks: Spoken Read-Back of Claude Code Answers

**Input**: Design documents from `/specs/002-speak-claude-answers/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: included — the constitution's testing gate requires unit tests for all
state/concurrency code without hardware.

**Organization**: grouped by user story; Phase 0 is a decision spike that must finish
before Phase 3.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: US1…US6 from spec.md

## Phase 0: Spike — code-switching by ear (blocks Phase 3 only)

- [x] T001 Create `requirements-tts.txt` (`piper-tts==1.8.0`, `onnxruntime<2`) and install
      into `.venv`; verify `python -c "import piper"` works (expected: yes, prebuilt wheel).
- [x] T002 Write `tts_worker.py` v0: load pl+en voices from `MODELS_DIR/tts`, ops
      `phonemize`/`synth`, stdio frame protocol per plan.md; run standalone on one sentence.
- [ ] T003 [P] Collect 10 real Polish Claude Code answers with identifiers (from this
      project's session history, trimmed) into `tests/samples/answers/*.md`.
- [ ] T004 Spike script in scratchpad: render each sample as (a) dictionary + English
      phoneme injection and (b) two-voice splicing; the user listens; record the verdict and
      any respelling rules discovered in research.md R4 (**decision gate for T030–T033**).

## Phase 1: Foundation (blocking prerequisites)

- [x] T005 `config.py`: add the 15 `tts_*` fields with defaults from
      contracts/config-and-tray.md and `TtsSnapshot.from_config(cfg, corrector_available)`
      with clamps (speed 0.5–2.0, threshold ≥ 100, timeout 1–60 s).
- [x] T006 [P] `tests/test_config_compat.py`: old config without tts keys loads with
      defaults; unknown keys ignored; snapshot clamps.
- [x] T007 [P] `audio.py`: `list_output_devices()` and `_resolve_output_device(name)`
      mirroring the input helpers (same host API, name-prefix match, warning on stale name).
- [x] T008 [P] `text_prep.py`: `strip_markdown(text, *, strip_code, read_inline_code,
      lang) -> (str, StripStats)`; `split_sentences(text) -> list[str]` with abbreviation
      guard and 350-char splitting; `detect_lang(text)`.
- [x] T009 [P] `tests/test_text_prep.py`: table-driven cases for fenced blocks, tables,
      headings, emphasis, links, URLs, inline code kept/skipped, lists, HTML, emoji,
      abbreviations, long sentence split, language detection; run on `tests/samples`.
- [x] T010 `tts.py` core: `SpeakRequest`, `RequestOutcome` (idempotent `conclude()` →
      one `SPEAK` line), `Speaker` with `_speak_q`, worker thread `tts-worker`, `None`
      sentinel, policies `latest`/`append`, current-id gate, `stop()`, `shutdown()`.
- [x] T011 `tts.py` `Player`: `sd.OutputStream` at engine sample rate, callback pops
      ≤ 100 ms blocks, 150 ms inter-sentence silence, volume 0.9, `stop()` = event + clear +
      `abort()`, on-speaking/on-idle callbacks, stream error → logged + stopped.
- [x] T012 `tests/conftest.py`: `FakeEngine` (silence chunks, configurable per-sentence
      delay, raise mode), `FakePlayer` (records chunks, honours stop, reports timing).
- [x] T013 `tests/test_speak_queue.py`: latest supersedes and logs `superseded by`; append
      preserves order; stop during synthesis discards later chunks; stale-id results are
      dropped; empty text → `outcome=empty`; disabled snapshot → `outcome=disabled`;
      engine raise → `outcome=error`; exactly one summary line per request; shutdown
      drains and joins.
- [x] T014 `tts.py` `PiperWorkerBackend`: spawn `sys.executable tts_worker.py` with
      `CREATE_NO_WINDOW`, reader thread routing frames by id, `load()` in thread
      `tts-loader`, `synthesize()`/`phonemize()` with per-call timeouts, respawn with
      back-off on EOF/timeout, `state`/`reason` for the tray, `terminate→kill` in
      `shutdown()`, unit test with a fake worker script (`tests/test_worker_protocol.py`).
- [x] T015 `tts.py` voice management: known-voice table (data-model §4), `ensure_voices()`
      one-time download to `MODELS_DIR/tts` with `.part` files and log lines, states
      `missing/downloading/loading/ready/error`, `reload_if_changed()` keyed on
      `(voice_pl, voice_en)`.

## Phase 2: US1 + US2 — MVP (speak the answer, stop on PTT)

- [x] T016 [US1] `speak_server.py`: `ThreadingHTTPServer` on `127.0.0.1:port`, thread
      `speak-server`, `POST /speak` (hook JSON or `{text}`, 202 in ≤ 50 ms, 400/413/415
      rules), `POST /stop`, `GET /health`, port-busy → `state=error reason="port N busy"`.
- [x] T017 [P] [US1] `tests/test_speak_server.py`: ephemeral port; both body shapes;
      `ignored_event` for `SubagentStop` when disabled; oversize body; wrong content type;
      health JSON; server responds before the fake engine finishes; shutdown frees the port.
- [x] T018 [US1] `main.py` wiring: construct `Speaker`, `PiperWorkerBackend`, `Player`,
      `SpeakServer` after the pipeline; start server even when `tts_enabled=false` if
      `tts_server_enabled`; `on_config_change` → `speaker.apply_config()` (reload voices
      only when changed, reopen player device only when changed); status map adds
      `speaking → "speaking…"`; shutdown order: server → speaker → backend → existing.
- [x] T019 [US2] `main.py`: `on_ptt_press = lambda: (speaker.stop(), pipeline.ptt_pressed())`
      so stop runs before the paused/model-ready guards; tray "Stop reading" → `speaker.stop()`.
- [x] T020 [US1] `tray.py`: submenu "Read Claude answers" with `Enabled` and `Stop reading`,
      dynamic top label with reason from `speaker.state/reason`, sub-items disabled unless
      `ready`; `_COLORS["speaking"]`; `set_state` precedence recording > processing >
      speaking > idle.
- [x] T021 [US1] `speak_server.py` hook material: write `hook-http.json`,
      `hook-command.json`, `vc2_speak.py` (stdlib, always exit 0, never prints), and
      `README-hooks.txt` to `APPDATA_DIR/hooks` on first enable and port change; tray item
      "Open hook instructions".
- [ ] T022 [US1] Manual smoke quickstart A–C on this machine; record `first_audio_ms`
      for 5 requests in `quickstart-results.md`.

## Phase 3: US3 + US4 — pronunciation and markdown quality

- [x] T030 [US3] `text_prep.py` pronunciation dictionary: `pronunciation.txt` loader
      (`term = spoken [| lang]`, `#` comments, longest-first, whole-token,
      case-insensitive), seed file with ≥ 20 dev terms written on first run, hot-reload per
      request, log `dictionary_hits`.
- [x] T031 [US3] `text_prep.py` identifier heuristic (`is_english_token`): extension,
      camelCase, snake_case, `--flag`, ≤ 6-letter acronym, small English word list; path
      reduction to last component + spoken extension; span builder `[(text, lang)]` with
      merge of adjacent same-lang spans.
- [x] T032 [US3] Code-switching per T004 verdict: either `inject_phonemes(sentence)` (query
      worker `phonemize` for en tokens, embed `[[ … ]]`) or two-voice splicing in `Speaker`
      (synthesise spans per voice, concatenate with 80 ms gaps). Keep the other path
      behind `tts_codeswitch = "inject" | "splice"` only if the spike was inconclusive.
- [x] T033 [P] [US3] `tests/test_lang_spans.py`: heuristic table, dictionary precedence,
      path reduction, span merging, phoneme-injection string shape, sample answers produce
      ≥ 1 en span each where expected.
- [x] T034 [US4] Placeholders: "fragment kodu"/"tabela" (pl) and "code block"/"table" (en)
      when `tts_strip_code`; an answer that is entirely code/table speaks the placeholder
      once; extend `tests/test_text_prep.py`.
- [ ] T035 [US3/US4] Manual smoke quickstart D + the 10 samples; user rates ≥ 8/10
      identifiers understandable (SC-004); adjust seed dictionary.

## Phase 4: US5 + US6 — options and polish

- [x] T040 [US5] `corrector.py`: `summarize(text, timeout_s, lang) -> str | None` with
      `_SUMMARY_PL/_SUMMARY_EN`, `<streszczenie>` markers, `think:false`, `temperature:0`,
      `num_predict` cap, ratio/sentence-count sanity check, never raises; log `LLM summary
      %.1f s ratio=%.2f`.
- [x] T041 [P] [US5] `tests/test_tts_summary.py`: monkeypatch `requests.post` — success,
      timeout, 404, junk output, over-long output → fallback; below threshold → no call;
      stale id after summary → discarded.
- [x] T042 [US5] `Speaker`: call `summarize` only when `snapshot.summarize` and
      `clean_chars > threshold`; log skip reasons.
- [x] T043 [US6] `tray.py`: Voice (dynamic list of downloaded pl voices + "Download default
      voices…"), Speed radios, Output device dynamic list, "Summarise long answers"
      (grayed with corrector reason), "Queue: latest wins", "Open pronunciation dictionary".
- [ ] T044 [US6] Idle unload: worker unloads voices after 30 min idle and reloads on demand
      (log both); state shows `ready (cold)`.
- [x] T045 [P] Docs: README.md and DOKUMENTACJA.md section "Czytanie odpowiedzi Claude
      Code" (install `requirements-tts.txt`, enable in tray, paste hook, Store-Python path
      caveat, licence note on the GPL worker); update `specs/002…/quickstart-results.md`.
- [ ] T046 Manual smoke quickstart E–G; full-day soak (SC-005) with log review: every
      `SPEAK` has an outcome, no zombie process/port after Exit.

## Dependencies & Execution Order

- T001–T002 → T014/T015 (backend needs the worker). T003 → T004, T009, T033, T035.
- T005 → T006, T010, T016, T018. T008 → T009, T010. T010 → T011, T013, T016.
- T014, T015, T016, T018, T019, T020, T021 → T022 (MVP gate).
- T004 verdict → T032. T030–T034 → T035. Phase 4 after T022.

### Parallel Opportunities

- T003, T006, T007, T008/T009 alongside T005/T010.
- T017 alongside T016; T033 alongside T030–T032; T041 alongside T040; T045 anytime after
  T022.

## Implementation Strategy

Ship the MVP (Phase 2) first: plain Polish reading with stop-on-PTT proves the hook path,
latency and shutdown behaviour on the real machine. Phase 3 is where the user's ear
decides; keep both code-switching mechanisms small until the spike verdict. Phase 4 items
are independent toggles and can be merged one by one.
