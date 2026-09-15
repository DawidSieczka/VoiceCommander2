r"""Global Claude Code hook installer for spoken read-back (feature 002).

Merges ONE `Stop` HTTP hook pointing at the local speak server into the user-level
`%USERPROFILE%\.claude\settings.json` (applies to every project). Idempotent: running
it twice changes nothing; a port change rewrites only our entry; every other hook
(agent-watch etc.) is preserved byte-for-byte in structure. A timestamped backup
is written before the first modification of a session.

CLI:  python claude_hooks.py install [--port 47321] [--settings PATH]
      python claude_hooks.py remove  [--settings PATH]
      python claude_hooks.py status  [--settings PATH]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("tts.hooks")

SETTINGS_PATH = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "settings.json"
EVENT = "Stop"
MARKER = "/speak"      # our hook is the only HTTP hook posting to 127.0.0.1:<port>/speak


def hook_url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}/speak"


def hook_entry(port: int) -> dict:
    return {"type": "http", "url": hook_url(port), "timeout": 5}


def _is_ours(h: dict) -> bool:
    return (isinstance(h, dict) and h.get("type") == "http"
            and str(h.get("url", "")).startswith("http://127.0.0.1:")
            and str(h.get("url", "")).endswith(MARKER))


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings.json root is not an object")
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_bytes(path.read_bytes())
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def status(path: Path = SETTINGS_PATH) -> tuple[str, Optional[int]]:
    """('installed', port) | ('missing', None) | ('error', None)."""
    try:
        data = _load(path)
    except Exception:
        return "error", None
    for group in (data.get("hooks") or {}).get(EVENT) or []:
        for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
            if _is_ours(h):
                try:
                    return "installed", int(h["url"].split(":")[2].split("/")[0])
                except Exception:
                    return "installed", None
    return "missing", None


def install(port: int, path: Path = SETTINGS_PATH) -> str:
    """Returns 'installed' | 'updated' | 'unchanged'. Raises on unreadable settings
    (never overwrites a file it cannot parse)."""
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' is not an object")
    groups = hooks.setdefault(EVENT, [])
    if not isinstance(groups, list):
        raise ValueError(f"'hooks.{EVENT}' is not a list")
    want = hook_entry(port)
    for group in groups:
        if not isinstance(group, dict):
            continue
        for i, h in enumerate(group.get("hooks") or []):
            if _is_ours(h):
                if h == want:
                    return "unchanged"
                group["hooks"][i] = want
                _save(path, data)
                log.info("Claude Code hook updated -> %s in %s", want["url"], path)
                return "updated"
    groups.append({"hooks": [want]})
    _save(path, data)
    log.info("Claude Code hook installed -> %s in %s", want["url"], path)
    return "installed"


def remove(path: Path = SETTINGS_PATH) -> str:
    """Returns 'removed' | 'absent'. Leaves every other hook untouched."""
    data = _load(path)
    groups = (data.get("hooks") or {}).get(EVENT)
    if not isinstance(groups, list):
        return "absent"
    changed = False
    kept_groups = []
    for group in groups:
        if isinstance(group, dict):
            kept = [h for h in (group.get("hooks") or []) if not _is_ours(h)]
            if len(kept) != len(group.get("hooks") or []):
                changed = True
            if kept or group.get("matcher"):
                group["hooks"] = kept
                if kept:
                    kept_groups.append(group)
            # a group left with no hooks is dropped
        else:
            kept_groups.append(group)
    if not changed:
        return "absent"
    if kept_groups:
        data["hooks"][EVENT] = kept_groups
    else:
        data["hooks"].pop(EVENT, None)
    _save(path, data)
    log.info("Claude Code hook removed from %s", path)
    return "removed"


def ensure_installed(port: int, path: Path = SETTINGS_PATH) -> str:
    """Install/update without raising; returns the outcome word (incl. 'error: …')."""
    try:
        return install(port, path)
    except Exception as e:
        log.warning("could not install Claude Code hook in %s: %s", path, e)
        return f"error: {e}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install the VoiceCommander2 read-back hook into Claude Code (user-level).")
    ap.add_argument("action", choices=("install", "remove", "status"))
    ap.add_argument("--port", type=int, default=None, help="speak server port (default: from config.json)")
    ap.add_argument("--settings", type=Path, default=SETTINGS_PATH)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.action == "status":
        st, port = status(args.settings)
        print(f"{st}" + (f" (port {port})" if port else "") + f" - {args.settings}")
        return 0 if st == "installed" else 1
    if args.action == "remove":
        print(remove(args.settings))
        return 0
    port = args.port
    if port is None:
        try:
            import config as cfgmod
            port = cfgmod.load().tts_server_port
        except Exception:
            port = 47321
    print(install(port, args.settings), "->", hook_url(port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
