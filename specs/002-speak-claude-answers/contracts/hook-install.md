# Contract: Claude Code hook installation

**Revised 2026-09-15 (user decision):** the application installs the hook itself, globally,
so a fresh machine works without manual steps. `claude_hooks.py` merges exactly one
`Stop` HTTP hook into the user-level `%USERPROFILE%\.claude\settings.json` when read-back is
enabled (`tts_hook_autoinstall`, default on), idempotently: other hooks and keys are
preserved, a port change rewrites only our entry, a timestamped `settings.json.bak-*` is
written before each modification, and an unparsable settings file is never overwritten.
CLI: `python claude_hooks.py install|status|remove`; tray: "Claude Code hook: …" /
"Remove Claude Code hook".

The snippets and the helper script below are still generated into
`%APPDATA%\VoiceCommander2\hooks\` for the manual path (autoinstall off, older Claude Code
needing the command variant, or a locked-down settings file). Claude Code picks up settings
edits automatically (file watcher); `/hooks` is an inspection menu only.

Facts verified on 2026-09-15 against docs (code.claude.com/docs/en/hooks) and empirically
on Claude Code 2.1.272:

- `Stop` and `SubagentStop` carry `last_assistant_message`; no `matcher` support on
  either; both fire in interactive, `-p`, desktop, IDE and web sessions.
- `type: http` hooks POST the same JSON as command-hook stdin, `Content-Type:
  application/json`, User-Agent axios. All failures (connection refused, timeout, bad
  response) are non-blocking: Claude Code proceeds and prints nothing. Confirmed: with the
  server down the turn completed with no visible error and the other hook still ran.
- Command hooks support `"async": true` (runs in background, timeout not enforced).
- Default hook timeout is 600 s; the snippets set 5 s explicitly.

## Variant A (preferred): HTTP hook

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "http", "url": "http://127.0.0.1:47321/speak", "timeout": 5 }
        ]
      }
    ]
  }
}
```

Merge into the existing `"hooks"` object of `~/.claude/settings.json` (the user already
has `PreToolUse`, `SubagentStop`, `SessionEnd` entries there; the new `Stop` array must be
added next to them, not replace them). If `allowedHttpHookUrls` is ever set, it must
include the loopback URL.

## Variant B: command hook (fallback for older Claude Code or locked-down HTTP hooks)

```json
{ "type": "command",
  "command": "python \"C:/Users/<user>/AppData/Roaming/VoiceCommander2/hooks/vc2_speak.py\"",
  "timeout": 5, "async": true }
```

`vc2_speak.py` (generated, stdlib only):

- reads all of stdin, parses JSON, exits 0 on any parse error;
- POSTs the raw body unchanged to `http://127.0.0.1:<port>/speak` with a 2 s timeout;
- swallows every exception and always exits 0; never prints to stdout (a Stop hook's
  stdout JSON could be interpreted as a decision);
- never inspects `transcript_path`.

Note for the generated README: the interpreter must be an absolute path when `python` is
not on PATH for the shell Claude Code uses (Git Bash on this machine); the generator
writes the path of the interpreter running VoiceCommander2 (`sys.executable`) into the
snippet.

## SubagentStop (opt-in)

Not installed by default. If the user adds the same hook under `SubagentStop`, the server
speaks those payloads only when `tts_speak_subagents=true`; otherwise they are logged as
`ignored_event`. Rationale: the user's sessions spawn many subagents; reading each would
be noise.

## Loop safety

The endpoint returns `202 {}` and never a `decision`/`continue` JSON, so it can never send
Claude back to work; `stop_hook_active` is logged but does not change behaviour.

## Uninstall

Remove the `Stop` entry from settings. Nothing else is left behind besides the files in
`%APPDATA%\VoiceCommander2\hooks\`.
