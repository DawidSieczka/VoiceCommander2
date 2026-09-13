"""T008: config backward/forward compatibility (FR-015) + snapshot clamping."""
import json

import config as cfgmod
from config import AppConfig, PerfSnapshot


def test_old_config_loads_with_defaults(tmp_path, monkeypatch):
    old = {"language": "en", "mode": "per_sentence", "stt_model": "small"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfg = cfgmod.load()
    assert cfg.language == "en"
    assert cfg.perf_eager_stt is False
    assert cfg.perf_fast_injection is False
    assert cfg.perf_pipelined_correction is False
    assert cfg.short_text_chars == 120
    assert cfg.clipboard_wait_max_ms == 300


def test_unknown_keys_ignored(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"totally_unknown": 1, "mode": "realtime"}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    assert cfgmod.load().mode == "realtime"


def test_round_trip_preserves_new_fields(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    monkeypatch.setattr(cfgmod, "APPDATA_DIR", tmp_path)
    cfg = AppConfig(perf_eager_stt=True, short_text_chars=200)
    cfgmod.save(cfg)
    loaded = cfgmod.load()
    assert loaded.perf_eager_stt is True
    assert loaded.short_text_chars == 200


def test_snapshot_clamps_thresholds():
    snap = PerfSnapshot.from_config(AppConfig(short_text_chars=-5, clipboard_wait_max_ms=5000))
    assert snap.short_text_chars == 1
    assert snap.clipboard_wait_max_ms == 1000
    snap = PerfSnapshot.from_config(AppConfig(clipboard_wait_max_ms=10))
    assert snap.clipboard_wait_max_ms == 50


def test_snapshot_is_immutable():
    snap = PerfSnapshot.from_config(AppConfig())
    try:
        snap.eager_stt = True
        raised = False
    except Exception:
        raised = True
    assert raised
