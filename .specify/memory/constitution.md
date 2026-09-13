<!--
Sync Impact Report
- Version change: (template, unversioned) → 1.0.0 (initial ratification)
- Modified principles: all placeholders replaced (initial adoption)
- Added sections:
  - Core Principles (7): Local-First & Private by Design; Never Lose the User's Words;
    Responsiveness Is the Product; Fail Soft, Keep Running; Windows Integration Safety;
    Simplicity & Small Surface; Observability of Every Decision
  - Additional Constraints (stack, hardware envelope, languages)
  - Development Workflow & Quality Gates
  - Governance
- Removed sections: none
- Deferred TODOs: none
-->

# VoiceCommander2 Constitution

VoiceCommander2 is a fully local push-to-talk dictation utility for Windows: hold a key,
speak, release — the transcribed (and optionally AI-corrected) text appears at the caret
of the focused window. It runs as a background tray application on consumer hardware.

## Core Principles

### I. Local-First & Private by Design (NON-NEGOTIABLE)

Audio, transcripts, and corrected text MUST NEVER leave the user's machine. The only
permitted network access is: (a) the one-time download of STT models into the local model
cache, and (b) HTTP calls to a local Ollama instance on localhost. No telemetry, no
crash reporting, no cloud APIs. Dictated text MUST be excluded from Windows clipboard
history and cloud clipboard sync when the clipboard injection path is used. Any feature
that would require sending user content off-device is out of scope by definition.

### II. Never Lose the User's Words

The user's dictation is irreplaceable; every processing stage MUST degrade to preserving
it rather than discarding it. Concretely: AI correction failure, timeout, or sanity-check
rejection MUST fall back to the raw transcript; refused or failed injection MUST leave the
text recoverable in the log; hallucination guards MUST log what they rejected and why.
Silent loss of recognized speech is a critical bug regardless of the cause.

### III. Responsiveness Is the Product

Perceived latency between releasing the PTT key and text appearing is the primary quality
metric. Rules that follow from it: work MUST be moved before key-release whenever the
audio is already available (incremental VAD/STT during recording is preferred over
post-hoc batch work); the PortAudio callback and the low-level keyboard hook MUST stay
trivial (hand off to queues/threads — heavy work there causes dropped frames or silent
unhooking); fixed sleeps in hot paths require an explicit justification comment; per-stage
timing (RTF, LLM seconds) MUST be logged so regressions are measurable, and changes that
knowingly worsen release-to-text latency MUST state the trade-off in the plan/spec.

### IV. Fail Soft, Keep Running

This is a background utility: a crash is worse than a degraded feature. Worker threads
MUST catch and log exceptions at their boundaries instead of dying. Missing dependencies
(Ollama down, CUDA broken, microphone absent, clipboard busy) MUST result in a working
fallback (raw transcript, CPU int8, retry on next PTT press, SendInput typing) — never a
dead tray icon. Corrupt configuration falls back to defaults. Only one instance runs at
a time. The application MUST remain usable with nothing but a microphone and the STT
model present.

### V. Windows Integration Safety

Injecting keystrokes and touching the clipboard are invasive; guards are mandatory, not
optional polish. Text MUST NOT be injected when focus has drifted from the window captured
at dictation time, nor into detectable password fields. Held modifier keys MUST be
released before synthetic input so characters do not become shortcuts. The user's
clipboard content MUST be restored after clipboard-based injection whenever technically
possible, and known-unrestorable cases MUST be surfaced. Known OS constraints (UIPI,
secure desktop, AltGr quirks) are documented facts to design around, not bugs to hide.

### VI. Simplicity & Small Surface

The codebase stays flat and small: one module per concern, plain functions and small
classes, no frameworks, no plugin systems, no abstraction layers with a single
implementation. New third-party dependencies require explicit justification against the
current list in `requirements.txt`. Configuration remains a flat dataclass serialized to
one JSON file; new options MUST have sensible defaults so existing configs keep working.
Prefer deleting code to generalizing it. YAGNI applies.

### VII. Observability of Every Decision

Every non-obvious runtime decision MUST be visible in the rotating log with its reason:
rejected segments (no-speech probability, log-probability, blocklist), correction
fallbacks, watchdog-synthesized key releases, device fallbacks, injection refusals, and
per-stage timings. A user reporting "it typed nothing" MUST be diagnosable from the log
alone, without a debugger. Log lines state cause, not just effect.

## Additional Constraints

- **Stack**: Python 3.12 on Windows 11; faster-whisper (CTranslate2) for STT; Silero VAD;
  Ollama (localhost) for optional correction; `keyboard` for the global hotkey; `pystray`
  for the tray UI; `sounddevice`/PortAudio for capture. Audio format is fixed at 16 kHz
  mono int16, 32 ms frames.
- **Hardware envelope**: consumer laptop; STT runs on CPU (int8) by default because the
  small GPU (2 GB) is reserved for the Ollama correction model. Features MUST be usable
  at real-time factor ≈ 1 on such hardware; heavier options (larger models, GPU STT) stay
  opt-in via config.
- **Languages**: Polish and English dictation are first-class; language-specific assets
  (blocklists, correction prompts) come in pairs. Tray/menu text is English.
- **User data locations**: config and logs in `%APPDATA%\VoiceCommander2`, models in
  `%LOCALAPPDATA%\VoiceCommander2\models`, user dictionary alongside the config. New
  persistent state MUST use these locations.

## Development Workflow & Quality Gates

- Features follow the Spec Kit flow: specification → plan → tasks → implementation.
  Specs and plans MUST be checked against this constitution; conflicts are resolved
  before implementation starts, not during review.
- State machines and concurrency-sensitive code (segmenter, streaming agreement logic,
  PTT watchdog, pipeline ordering) MUST have unit tests runnable without a microphone,
  GPU, or Ollama — synthetic audio and fakes are the norm. Code whose behavior depends
  on live Windows APIs (injection, tray) is exercised by a documented manual smoke test:
  dictate into Notepad in each mode and verify log output.
- Ordering guarantee: dictated segments MUST be injected in the order they were spoken,
  whatever concurrency exists upstream. Any pipeline change MUST preserve this invariant.
- Every latency-affecting change MUST be validated by comparing logged per-stage timings
  before and after on the same hardware; claims of "faster" without numbers do not merge.
- Threads are named, daemonized deliberately, and shut down cleanly on Exit; a change
  that can leave a zombie audio stream or hook installed after Exit is a blocking defect.

## Governance

This constitution supersedes ad-hoc practices for VoiceCommander2. All specs, plans, and
implementations produced via Spec Kit MUST be checked for compliance with the principles
above; a deliberate violation MUST be recorded in the plan's Complexity/Deviation section
with its justification.

Amendments: edit `.specify/memory/constitution.md` via the `/speckit-constitution`
command (or an equivalent reviewed change), including a Sync Impact Report and a version
bump. Versioning is semantic: MAJOR for removing or redefining a principle, MINOR for
adding a principle or materially expanding guidance, PATCH for clarifications and
wording. The sole maintainer approves amendments; the Sync Impact Report comment is
removed when the amended file is committed.

Compliance review: at the start of each `/speckit-plan` run, the plan's Constitution
Check gate MUST cite the principles it touches (privacy, word preservation, latency,
fallbacks, injection safety) and state how the feature satisfies them.

**Version**: 1.0.0 | **Ratified**: 2026-09-13 | **Last Amended**: 2026-09-13
