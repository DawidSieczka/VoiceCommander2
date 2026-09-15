# Feature Specification: Spoken Read-Back of Claude Code Answers (local TTS)

**Feature Branch**: `002-speak-claude-answers`

**Created**: 2026-09-15

**Status**: Draft

**Input**: User handoff: "When Claude Code finishes an answer in the terminal, VoiceCommander2
reads it aloud with a natural Polish voice ('Jarvis'), pronouncing English technical names
correctly. Zero cloud. Use Claude Code hooks (Stop → HTTP POST to a local server in the tray
app), Piper as the first engine, optional Chatterbox later. Pressing push-to-talk stops the
reading."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hear the answer without looking at the terminal (Priority: P1)

As a user who dictates prompts to Claude Code and then switches attention elsewhere, I want
the final answer of each Claude Code turn to be read aloud in Polish as soon as it is
finished, so that I know what was done and whether a decision is needed without reading
the terminal.

**Why this priority**: This is the whole feature. Everything else refines it.

**Independent Test**: With the application running and the Claude Code hook installed, ask
Claude Code a question in a terminal. Within about one second of the answer appearing, a
Polish voice starts reading it. The same text POSTed by hand to the local endpoint (see
quickstart) is read identically, which proves the path without Claude Code.

**Acceptance Scenarios**:

1. **Given** the application is running with read-back enabled and the hook is installed,
   **When** Claude Code finishes a turn with a short plain-text answer, **Then** speech
   starts within 1.5 s of the hook firing and the whole answer is read.
2. **Given** the application is NOT running, **When** Claude Code finishes a turn,
   **Then** Claude Code shows no error, is not delayed noticeably, and continues normally.
3. **Given** read-back is disabled in the tray, **When** an answer arrives, **Then**
   nothing is played and the request is acknowledged silently (logged, not spoken).
4. **Given** an answer arrives while a previous answer is still being read, **When** the
   new request is accepted, **Then** the previous reading stops and the new one starts
   (policy "latest wins"; see FR-009).

---

### User Story 2 - Interrupt the voice instantly (Priority: P1)

As a user, I want to stop the reading immediately by pressing my push-to-talk key (I am
about to speak anyway) or by choosing "Stop reading" in the tray, so that the voice never
talks over me or wastes my time.

**Why this priority**: Without a reliable stop, the feature is an annoyance rather than a
help; it also prevents the microphone from recording the speaker output.

**Independent Test**: Start a long read-back, press the push-to-talk key: audio stops
within 100 ms and dictation begins as usual. Repeat with the tray item.

**Acceptance Scenarios**:

1. **Given** speech is playing, **When** the PTT key goes down, **Then** playback stops
   within 100 ms, the pending queue is cleared, and the dictation flow is unaffected.
2. **Given** speech is playing and the application is paused or the STT model is still
   loading, **When** the PTT key goes down, **Then** playback still stops.
3. **Given** speech is playing, **When** "Stop reading" is chosen in the tray, **Then**
   playback stops and the tray returns to the idle state.

---

### User Story 3 - Technical names sound right (Priority: P2)

As a Polish-speaking developer, I want English identifiers, file names and product names
inside a Polish answer (e.g. `config.py`, `pytest`, `JSON`, `Claude`) to be pronounced as
an English speaker would say them, not letter-by-letter in Polish phonetics, so the answer
is intelligible.

**Why this priority**: Answers from Claude Code are dense with identifiers; mispronounced
identifiers make the reading hard to follow. It is second only to "it speaks at all".

**Independent Test**: POST the sentence "Zaktualizowałem plik config.py i uruchomiłem
pytest." and listen: "config.py" and "pytest" sound English; the rest sounds Polish.

**Acceptance Scenarios**:

1. **Given** a Polish sentence containing an ASCII identifier, **When** it is spoken,
   **Then** the identifier is rendered with English pronunciation (or with an entry from
   the user's pronunciation dictionary if one exists).
2. **Given** a term listed in the pronunciation dictionary (e.g. "npm" → "en pe em"),
   **When** it appears in an answer, **Then** the dictionary form is spoken.
3. **Given** a file path like `specs/002/plan.md`, **When** spoken, **Then** the reading
   is short and natural (e.g. "plan md") rather than every slash and directory.

---

### User Story 4 - Markdown and code are not read verbatim (Priority: P2)

As a user, I want the voice to skip fenced code blocks, tables and markdown syntax, and to
read only the prose, so that I hear the message rather than punctuation soup.

**Why this priority**: Most Claude Code answers contain markdown; reading `**`, `#`,
pipes and code would make the feature unusable in practice.

**Independent Test**: POST an answer with headings, a bullet list, a table and a fenced
code block; only the prose sentences and list items are heard, in order.

**Acceptance Scenarios**:

1. **Given** an answer with a fenced code block, **When** spoken, **Then** the block is
   replaced by a short spoken placeholder ("fragment kodu" / "code block") or omitted,
   per configuration.
2. **Given** headings, bold/italic markers, links and inline code, **When** spoken,
   **Then** markers and URLs are removed, link text and inline-code content are kept.
3. **Given** a markdown table, **When** spoken, **Then** it is replaced by a placeholder
   ("tabela" / "table") and not read cell by cell.

---

### User Story 5 - Long answers are summarised first (Priority: P3)

As a user, I want long answers to be condensed by the local LLM into two or three Polish
sentences stating what was done and what needs my decision, so that the reading takes
seconds rather than minutes.

**Why this priority**: Valuable but optional; the feature is complete without it, and it
adds an Ollama round-trip that can fail or be slow.

**Independent Test**: POST an answer longer than the configured threshold with Ollama
running: the spoken text is a short summary. Stop Ollama and repeat: the full cleaned
text is read instead (never nothing).

**Acceptance Scenarios**:

1. **Given** summarisation is enabled and the cleaned text exceeds the threshold, **When**
   Ollama responds within the timeout, **Then** the summary is spoken and the log shows
   the LLM time and the length ratio.
2. **Given** Ollama is unavailable, times out, or returns a summary that fails the sanity
   check, **When** the answer arrives, **Then** the full cleaned text is spoken.
3. **Given** the cleaned text is below the threshold, **When** it arrives, **Then** no
   LLM call is made.

---

### User Story 6 - Choose voice, speed, output device and engine from the tray (Priority: P3)

As a user, I want a "Read Claude answers" submenu in the tray to turn the feature on/off,
pick the voice, adjust speed, choose the output device, and see why the feature is
unavailable when it is (engine not installed, voice not downloaded).

**Why this priority**: Configuration comfort; all values are also editable in
`config.json`.

**Independent Test**: Toggle each item in the tray and verify the next read-back reflects
it without restarting the application.

**Acceptance Scenarios**:

1. **Given** the TTS engine package is not installed, **When** the tray menu opens,
   **Then** the "Read Claude answers" item is grayed out with a reason, exactly like AI
   correction when Ollama is down.
2. **Given** a different voice is selected, **When** the next answer arrives, **Then**
   it is spoken with that voice (loaded in the background; the previous voice keeps
   serving until the new one is ready).

---

### Edge Cases

- Hook fires for a turn whose final message is empty or whitespace (e.g. the turn ended in a
  tool call): nothing is spoken; the request is logged as `outcome=empty`.
- Several Claude Code sessions run at once (the user does run several): every session's
  Stop hook posts; the "latest wins" policy prevents a backlog, and the log records the
  session id and `cwd` so the user can tell which session spoke.
- `SubagentStop` fires for each subagent; the default hook installs only `Stop` so
  subagent chatter is not read. The endpoint accepts and ignores `hook_event_name` values
  other than `Stop` unless configured otherwise.
- `stop_hook_active` is `true` (Claude was already sent back to work by another Stop
  hook): the endpoint still speaks; it never returns a blocking decision.
- Answer is entirely code or a table: the placeholder sentence is spoken so the user knows
  an answer arrived.
- The request body is not the hook JSON but `{"text": "..."}` (manual curl, other tools):
  accepted on the same endpoint.
- The port is already taken by another process: the server fails to start; the tray shows
  the reason; dictation keeps working.
- Voice files missing and no network: engine reports "voice not available"; the tray item
  is grayed out with that reason; nothing crashes.
- Output device disappears mid-playback (Bluetooth headset off): the stream error is
  logged, playback stops, the next request reopens the current default device.
- The user speaks (PTT) while the summary LLM call is in flight: the in-flight request is
  discarded when it returns (its request id is stale).
- Non-Polish answer (English session): text is spoken with the English voice when the
  request says `lang: en` or when the language heuristic says so; otherwise Polish.
- Very long single sentence (> 500 characters, no punctuation): split on commas/length so
  the first audio still starts within 1.5 s.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The application MUST expose an HTTP endpoint bound to `127.0.0.1` only, on a
  configurable port (default 47321), with `POST /speak`, `POST /stop`, `GET /health`.
- **FR-002**: `POST /speak` MUST accept both the raw Claude Code hook payload (using its
  `last_assistant_message` field) and a minimal `{"text": ..., "lang"?: ...}` body, and
  MUST answer within 50 ms with HTTP 202 and an empty JSON object; all processing happens
  after the response so Claude Code is never delayed. `transcript_path` MUST NOT be read.
- **FR-003**: Every accepted request MUST be logged with: request id, source
  (`hook`/`manual`), session id and `cwd` when present, raw length, cleaned length,
  whether it was summarised, time to first audio, total audio seconds, and outcome
  (`spoken`, `stopped`, `empty`, `disabled`, `error`). Spoken text itself is logged
  truncated to 120 characters (same convention as `STT` and `LLM` lines).
- **FR-004**: Text preparation MUST remove fenced code blocks, tables, headings markers,
  emphasis markers, list bullets, URLs, HTML tags and horizontal rules; MUST keep link
  text and inline-code content; MUST replace code blocks/tables with a configurable
  spoken placeholder (default on); and MUST split the result into sentences.
- **FR-005**: The system MUST pronounce English technical tokens correctly by (a) applying
  a user-editable pronunciation dictionary first (Polish respellings or explicit
  phonemes), then (b) for remaining ASCII-only tokens (identifiers, file names,
  camelCase/snake_case, acronyms) obtaining English phonemes and injecting them into the
  Polish voice so the sentence keeps one timbre; (c) two-voice audio splicing is the
  fallback if (b) proves unintelligible in the listening spike. The dictionary lives
  beside the user's vocabulary file in `%APPDATA%\VoiceCommander2`.
- **FR-006**: The TTS engine MUST run fully offline after a one-time voice download into
  `%LOCALAPPDATA%\VoiceCommander2\models\tts\` (same policy as STT models). No other
  network access is permitted by this feature except HTTP to local Ollama.
- **FR-007**: Playback MUST start with the first sentence while later sentences are still
  being synthesised (streaming per sentence), MUST use a dedicated output stream at the
  voice's sample rate (not the 16 kHz capture rate), and MUST be independent of the
  always-open microphone stream.
- **FR-008**: `stop()` MUST halt audible output within 100 ms, discard queued sentences and
  in-flight synthesis results, and be safe to call from any thread at any time. PTT press
  MUST call it before any other PTT handling (including when paused or model not ready).
- **FR-009**: Queue policy MUST be configurable: `latest` (default: a new request stops and
  replaces the current one) or `append` (requests are read in order).
- **FR-010**: Optional summarisation MUST call local Ollama with a Polish/English prompt
  pair, temperature 0, thinking disabled, a configurable timeout (default 8 s), MUST apply
  a length-ratio sanity check, and MUST fall back to the full cleaned text on any failure.
  It MUST run only when the cleaned text exceeds `tts_summary_threshold` characters.
- **FR-011**: The tray MUST offer: enable/disable, voice list (from downloaded voices),
  speed (0.8×/1.0×/1.2×/1.5×), output device list, "Summarise long answers", "Stop
  reading", and MUST gray out the feature with a reason when the engine or voice is
  unavailable. Menu text is English (constitution).
- **FR-012**: All new configuration keys MUST be flat primitives in `config.json` with
  defaults that keep existing configs working; unknown keys keep being ignored.
- **FR-013**: The engine MUST load in a named background thread; while loading, requests
  are queued (not dropped) up to 30 s, then logged as `outcome=error` with reason.
- **FR-014**: The server thread, playback worker and engine MUST shut down on Exit without
  leaving an open audio stream or a listening socket.
- **FR-015**: The application MUST write a ready-to-paste hook snippet (HTTP variant and
  command-script variant) and the helper script into `%APPDATA%\VoiceCommander2\hooks\`
  on first start of the feature, and the tray MUST offer "Open hook instructions".
  Installing the hook into `~/.claude/settings.json` remains a manual, user-approved
  step (the application never edits Claude Code settings).
- **FR-016**: When read-back is disabled or the engine is unavailable, `POST /speak` MUST
  still return 202 (never an error to the hook) and log the reason.
- **FR-017**: The tray icon MUST show a distinct state while speaking (new colour) and the
  tooltip MUST read "speaking…"; dictation states take precedence when they occur.

### Key Entities

- **SpeakRequest**: id, source, session id, cwd, language, raw text, received-at
  timestamp, snapshot of TTS settings at receipt (voice ids, speed, summarise flag).
- **PreparedText**: list of sentences, each a list of (segment text, language) spans after
  markdown stripping and dictionary substitution; counters for stripped blocks.
- **Voice**: id (e.g. `pl_PL-jarvis_wg_glos-medium`), language, sample rate, local paths,
  download source, load state (`missing`/`loading`/`ready`/`error`).
- **PlaybackSession**: the currently audible request id, start time, sentences played,
  stop reason.
- **PronunciationDictionary**: ordered mapping term → spoken form, user-editable text file.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a 3-sentence answer, audio starts ≤ 1.5 s after the hook fires (logged
  `first_audio_ms`), on CPU, with the medium Polish voice.
- **SC-002**: PTT press silences playback in ≤ 100 ms in 20 of 20 manual trials.
- **SC-003**: Claude Code turn completion is not delayed by more than 50 ms by the hook
  when the application is running, and shows no error when it is not.
- **SC-004**: In a 10-answer sample of real Claude Code output containing identifiers, the
  user rates ≥ 8 of 10 identifier pronunciations as understandable.
- **SC-005**: Zero crashes or stuck states across a full working day; every request has
  exactly one summary log line with an outcome.
- **SC-006**: Unit tests for text preparation, language segmentation, queue policy, stop
  semantics and endpoint parsing run without audio hardware, network or Ollama.

## Assumptions

- Claude Code ≥ 2.1.63 with HTTP hooks; verified on the user's 2.1.272 that the `Stop`
  hook carries `last_assistant_message` with markdown intact and that HTTP hooks POST the
  same JSON and are non-blocking on connection refused.
- Piper voices at 22.05 kHz are acceptable quality for the first release; a higher-quality
  GPU engine is a later, optional backend behind the same interface.
- The machine's GPU is shared with Whisper, Ollama and other tools; the TTS engine runs on
  CPU by default.
- Only the `Stop` event is installed by default; `SubagentStop` read-back is off unless the
  user opts in.
- Reading inline code content aloud (file names, flags) is desired; fenced blocks are not.
- The application's own dictation states (recording/processing) always take precedence
  over speaking for the tray icon.
