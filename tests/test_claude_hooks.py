"""Global Claude Code hook installer: idempotent merge, preserves foreign hooks,
port update, removal, never overwrites unparsable settings."""
import json

import pytest

import claude_hooks as ch

FOREIGN = {
    "model": "claude-fable-5-1",
    "hooks": {
        "PreToolUse": [{"matcher": "Agent|Task", "hooks": [{"type": "command", "command": "start.ps1", "async": True}]}],
        "SubagentStop": [{"hooks": [{"type": "command", "command": "stop.ps1"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "other-stop.ps1", "timeout": 30}]}],
    },
}


def _write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_install_into_missing_file_creates_it(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    assert ch.status(p) == ("missing", None)
    assert ch.install(47321, p) == "installed"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"hooks": {"Stop": [{"hooks": [{"type": "http", "url": "http://127.0.0.1:47321/speak", "timeout": 5}]}]}}
    assert ch.status(p) == ("installed", 47321)


def test_install_preserves_foreign_hooks_and_is_idempotent(tmp_path):
    p = tmp_path / "settings.json"
    _write(p, FOREIGN)
    assert ch.install(47321, p) == "installed"
    assert ch.install(47321, p) == "unchanged"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["model"] == "claude-fable-5-1"
    assert data["hooks"]["PreToolUse"] == FOREIGN["hooks"]["PreToolUse"]
    assert data["hooks"]["SubagentStop"] == FOREIGN["hooks"]["SubagentStop"]
    stop = data["hooks"]["Stop"]
    assert stop[0] == FOREIGN["hooks"]["Stop"][0]          # foreign Stop group untouched, first
    assert stop[1] == {"hooks": [ch.hook_entry(47321)]}
    assert len(stop) == 2
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1                                 # one backup for the single write


def test_port_change_updates_only_our_entry(tmp_path):
    p = tmp_path / "settings.json"
    _write(p, FOREIGN)
    ch.install(47321, p)
    assert ch.install(50000, p) == "updated"
    data = json.loads(p.read_text(encoding="utf-8"))
    ours = [h for g in data["hooks"]["Stop"] for h in g["hooks"] if h.get("type") == "http"]
    assert ours == [ch.hook_entry(50000)]
    assert ch.status(p) == ("installed", 50000)


def test_remove_leaves_foreign_hooks(tmp_path):
    p = tmp_path / "settings.json"
    _write(p, FOREIGN)
    ch.install(47321, p)
    assert ch.remove(p) == "removed"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["hooks"]["Stop"] == FOREIGN["hooks"]["Stop"]
    assert data["hooks"]["PreToolUse"] == FOREIGN["hooks"]["PreToolUse"]
    assert ch.remove(p) == "absent"
    assert ch.status(p) == ("missing", None)


def test_remove_drops_stop_key_when_it_was_only_ours(tmp_path):
    p = tmp_path / "settings.json"
    _write(p, {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "x"}]}]}})
    ch.install(47321, p)
    ch.remove(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "Stop" not in data["hooks"] and "SessionEnd" in data["hooks"]


def test_unparsable_settings_are_never_overwritten(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(Exception):
        ch.install(47321, p)
    assert p.read_text(encoding="utf-8") == "{ not json"
    assert ch.status(p) == ("error", None)
    assert ch.ensure_installed(47321, p).startswith("error")
    assert not list(tmp_path.glob("*.bak-*"))


def test_cli_status_and_install(tmp_path, capsys):
    p = tmp_path / "settings.json"
    assert ch.main(["status", "--settings", str(p)]) == 1
    assert ch.main(["install", "--port", "47321", "--settings", str(p)]) == 0
    assert ch.main(["status", "--settings", str(p)]) == 0
    out = capsys.readouterr().out
    assert "installed" in out and "47321" in out
