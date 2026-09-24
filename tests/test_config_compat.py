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


# --- feature 002: TTS keys (T006) ---

def test_old_config_gets_tts_defaults(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"language": "pl"}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfg = cfgmod.load()
    assert cfg.tts_enabled is False
    assert cfg.tts_voice_pl == "pl_PL-jarvis_wg_glos-medium"
    assert cfg.tts_server_port == 47321
    assert cfg.tts_queue_policy == "latest"
    assert cfg.tts_summarize is False


def test_tts_snapshot_clamps_and_gates_summary():
    from config import TtsSnapshot
    cfg = AppConfig(tts_speed=9.0, tts_summary_threshold=5, tts_summary_timeout_s=0.1,
                    tts_summarize=True, tts_queue_policy="weird", tts_codeswitch="weird")
    snap = TtsSnapshot.from_config(cfg, corrector_available=False)
    assert snap.speed == 2.0 and snap.summary_threshold == 100 and snap.summary_timeout_s == 1.0
    assert snap.summarize is False            # Ollama unusable -> no summary
    assert snap.queue_policy == "latest" and snap.codeswitch == "inject"
    assert TtsSnapshot.from_config(cfg, corrector_available=True).summarize is True
    assert TtsSnapshot.from_config(AppConfig(tts_speed=0.1)).speed == 0.5


# --- STT profiles (tray > STT profile, per machine) ---

def test_old_config_gets_default_stt_profiles(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"stt_model": "medium", "stt_device": "cuda",
                                "stt_compute_type": "int8_float16", "beam_size": 2}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfg = cfgmod.load()
    assert len(cfg.stt_profiles) == 3
    assert cfgmod.stt_profile_name(cfg).startswith("RTX laptop — medium")


def test_apply_stt_profile_sets_all_engine_fields_and_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    monkeypatch.setattr(cfgmod, "APPDATA_DIR", tmp_path)
    cfg = AppConfig()
    name = next(n for n in cfg.stt_profiles if n.startswith("MX450"))
    cfgmod.apply_stt_profile(cfg, name)
    assert (cfg.stt_model, cfg.stt_device, cfg.stt_compute_type, cfg.beam_size) == ("small", "cpu", "int8", 2)
    cfgmod.save(cfg)
    loaded = cfgmod.load()
    assert cfgmod.stt_profile_name(loaded) == name


def test_hand_edited_engine_is_custom_and_user_profiles_are_kept(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    raw = {"stt_model": "base", "stt_device": "cpu", "stt_compute_type": "int8", "beam_size": 1,
           "stt_profiles": {"Mine": {"stt_model": "base", "stt_device": "cpu",
                                     "stt_compute_type": "int8", "beam_size": 3}}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfg = cfgmod.load()
    assert list(cfg.stt_profiles) == ["Mine"]          # user's own list replaces the defaults
    assert cfgmod.stt_profile_name(cfg) is None        # beam differs -> Custom
    cfgmod.apply_stt_profile(cfg, "Mine")
    assert cfg.beam_size == 3 and cfgmod.stt_profile_name(cfg) == "Mine"


# --- models_dir override (long-path / small-C: workaround) ---

def test_models_dir_defaults_to_localappdata_and_can_be_overridden():
    from pathlib import Path
    assert cfgmod.models_dir(AppConfig()) == cfgmod.MODELS_DIR
    assert cfgmod.models_dir(AppConfig(models_dir=r"D:\VoiceCommander2\models")) == Path(r"D:\VoiceCommander2\models")
