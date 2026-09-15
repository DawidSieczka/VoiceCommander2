r"""Configuration: dataclass <-> JSON in %APPDATA%\VoiceCommander2\config.json (atomic writes)."""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "VoiceCommander2"

APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = APPDATA_DIR / "config.json"
LOG_DIR = APPDATA_DIR / "logs"
MODELS_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME / "models"


@dataclass
class AppConfig:
    language: str = "pl"                  # "pl" | "en"
    mode: str = "on_release"              # "realtime" | "per_sentence" | "on_release"
    ai_correction: bool = True
    ptt_key: str = "f9"                   # keyboard-library key name
    paused: bool = False

    # STT
    stt_model: str = "small"              # base|small|medium|large-v3-turbo
    stt_device: str = "cpu"               # cpu by design (MX450 2GB is owned by Ollama)
    stt_compute_type: str = "int8"
    beam_size: int = 2

    # VAD / segmentation (ms)
    vad_start_threshold: float = 0.50
    vad_continue_threshold: float = 0.35
    vad_end_silence_ms: int = 900         # raised from 500 per design critique (mode 2 fragments)
    vad_min_speech_ms: int = 300
    vad_max_segment_s: int = 30
    vad_padding_ms: int = 250

    # Ollama correction
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:2b"
    ollama_keep_alive: str = "30m"
    correction_timeout_sentence_s: float = 20.0
    correction_timeout_release_s: float = 45.0

    # Audio input: "" = Windows default input device, otherwise device name
    input_device: str = ""
    # True: stream stays open (no first-words clipping; mic indicator always on).
    # False: mic opens on PTT press (~150-200 ms of speech lost at the start).
    mic_always_open: bool = True

    # Injection
    injection_method: str = "clipboard"   # "clipboard" | "sendinput"

    # Mode 2 backpressure: skip correction when queue deeper than this
    max_correction_backlog: int = 2

    # Performance A/B toggles (tray > Performance (A/B)); OFF = legacy behavior.
    perf_eager_stt: bool = False           # on_release: transcribe while PTT is held
    perf_fast_injection: bool = False      # SendInput for short texts + adaptive clipboard wait
    perf_pipelined_correction: bool = False  # per_sentence: overlap STT with correction
    short_text_chars: int = 120            # fast injection: type directly at or below (> 0)
    clipboard_wait_max_ms: int = 300       # fast injection: adaptive wait cap (50-1000)

    # Realtime (mode 1)
    realtime_interval_s: float = 1.2
    realtime_window_max_s: float = 12.0

    # Spoken read-back of Claude Code answers (feature 002; tray > Read Claude answers)
    tts_enabled: bool = False
    tts_backend: str = "piper"                 # "piper" (worker subprocess)
    tts_voice_pl: str = "pl_PL-jarvis_wg_glos-medium"
    tts_voice_en: str = "en_US-lessac-medium"
    tts_speed: float = 1.0                     # 0.5-2.0; Piper length_scale = 1/speed
    tts_output_device: str = ""                # "" = system default output device
    tts_queue_policy: str = "latest"           # "latest" (new answer interrupts) | "append"
    tts_strip_code: bool = True                # code blocks/tables -> spoken placeholder
    tts_read_inline_code: bool = True          # keep `inline code` content
    tts_codeswitch: str = "inject"             # "inject" EN phonemes into PL voice | "splice" two voices
    tts_summarize: bool = False                # long answers -> Ollama summary first
    tts_summary_threshold: int = 600           # cleaned characters
    tts_summary_timeout_s: float = 8.0
    tts_speak_subagents: bool = False          # also speak SubagentStop payloads
    tts_server_enabled: bool = True
    tts_server_port: int = 47321               # loopback only


@dataclass(frozen=True)
class PerfSnapshot:
    """Immutable per-dictation settings, captured at PTT press.

    A dictation in flight completes under the settings it started with (FR-003);
    everything downstream reads this snapshot, never live cfg.
    """
    eager_stt: bool
    fast_injection: bool
    pipelined_correction: bool
    short_text_chars: int
    clipboard_wait_max_ms: int
    mode: str
    language: str

    @staticmethod
    def from_config(cfg: AppConfig) -> "PerfSnapshot":
        return PerfSnapshot(
            eager_stt=cfg.perf_eager_stt,
            fast_injection=cfg.perf_fast_injection,
            pipelined_correction=cfg.perf_pipelined_correction,
            short_text_chars=max(1, cfg.short_text_chars),
            clipboard_wait_max_ms=min(1000, max(50, cfg.clipboard_wait_max_ms)),
            mode=cfg.mode,
            language=cfg.language,
        )


@dataclass(frozen=True)
class TtsSnapshot:
    """Immutable read-back settings, captured when a speak request is accepted.

    A request in flight completes under the settings it started with; a tray
    change applies from the next request (same rule as PerfSnapshot).
    """
    enabled: bool
    voice_pl: str
    voice_en: str
    speed: float
    output_device: str
    queue_policy: str
    strip_code: bool
    read_inline_code: bool
    codeswitch: str
    summarize: bool
    summary_threshold: int
    summary_timeout_s: float
    speak_subagents: bool
    language: str

    @staticmethod
    def from_config(cfg: AppConfig, corrector_available: bool = False) -> "TtsSnapshot":
        return TtsSnapshot(
            enabled=bool(cfg.tts_enabled),
            voice_pl=cfg.tts_voice_pl,
            voice_en=cfg.tts_voice_en,
            speed=min(2.0, max(0.5, float(cfg.tts_speed))),
            output_device=cfg.tts_output_device,
            queue_policy="append" if cfg.tts_queue_policy == "append" else "latest",
            strip_code=bool(cfg.tts_strip_code),
            read_inline_code=bool(cfg.tts_read_inline_code),
            codeswitch="splice" if cfg.tts_codeswitch == "splice" else "inject",
            summarize=bool(cfg.tts_summarize) and corrector_available,
            summary_threshold=max(100, int(cfg.tts_summary_threshold)),
            summary_timeout_s=min(60.0, max(1.0, float(cfg.tts_summary_timeout_s))),
            speak_subagents=bool(cfg.tts_speak_subagents),
            language=cfg.language,
        )


def load() -> AppConfig:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        known = {f.name for f in dataclasses.fields(AppConfig)}
        return AppConfig(**{k: v for k, v in raw.items() if k in known})
    except FileNotFoundError:
        return AppConfig()
    except Exception:
        # Corrupt config: fall back to defaults rather than crash a background app.
        return AppConfig()


def save(cfg: AppConfig) -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(dataclasses.asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
