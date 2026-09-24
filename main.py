"""VoiceCommander2 — fully local push-to-talk dictation for Windows.

Run: pythonw main.py  (tray icon appears immediately; model loads in background)
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading

import config as cfgmod
import logsetup
from corrector import Corrector
from hotkey import PushToTalk
from injector import Injector
from pipeline import Pipeline
from stt import Transcriber
from tray import Tray

log = logging.getLogger("main")


def _single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "VoiceCommander2_SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main() -> int:
    logsetup.setup()
    if not _single_instance():
        log.error("another instance is already running — exiting")
        return 1

    cfg = cfgmod.load()
    cfgmod.save(cfg)  # materialize defaults for "Open config file"
    log.info("starting (mode=%s, lang=%s, ptt=%s, model=%s)",
             cfg.mode, cfg.language, cfg.ptt_key, cfg.stt_model)

    transcriber = Transcriber(cfg)
    corrector = Corrector(cfg)
    injector = Injector(cfg.injection_method)

    tray: Tray | None = None

    # Tray state: dictation states win over "speaking" (spec FR-017).
    _view = {"dictation": "loading", "speaking": False}
    _view_lock = threading.Lock()

    def _render_state() -> None:
        with _view_lock:
            d, s = _view["dictation"], _view["speaking"]
        state = d if d != "idle" else ("speaking" if s else "idle")
        if tray:
            tray.set_state(state, {"idle": "ready", "recording": "recording…",
                                   "processing": "processing…", "speaking": "speaking…"}.get(state, state))

    def on_status(state: str) -> None:
        with _view_lock:
            _view["dictation"] = state
        _render_state()

    def on_speaking(active: bool) -> None:
        with _view_lock:
            _view["speaking"] = active
        _render_state()

    pipeline = Pipeline(cfg, transcriber, corrector, injector, on_status)

    # Spoken read-back of Claude Code answers (feature 002). The GPL Piper engine
    # runs in a worker subprocess; nothing here imports it.
    from speak_server import SpeakServer, write_hook_material
    from tts import PiperWorkerBackend, Player, Speaker, VoiceStore
    speaker = Speaker(cfg, PiperWorkerBackend(VoiceStore(cfgmod.models_dir(cfg) / "tts")),
                      Player(lambda: cfg.tts_output_device),
                      summarize=corrector.summarize,
                      corrector_available=lambda: corrector.available,
                      on_speaking=on_speaking)
    speak_server = SpeakServer(cfg, speaker, on_state_change=lambda: tray and tray.refresh_menu())
    _hook_port = {"port": None}

    def _ensure_hook_material() -> None:
        if _hook_port["port"] != cfg.tts_server_port:
            try:
                write_hook_material(cfg.tts_server_port)
                _hook_port["port"] = cfg.tts_server_port
            except Exception:
                log.exception("could not write hook material")
        if cfg.tts_hook_autoinstall:
            # User-level settings.json -> every project on this machine. Idempotent
            # merge with backup; other hooks are preserved (claude_hooks.py).
            import claude_hooks
            outcome = claude_hooks.ensure_installed(cfg.tts_server_port)
            if outcome != "unchanged":
                log.info("Claude Code hook autoinstall: %s", outcome)

    def on_ptt_press() -> None:
        speaker.stop("PTT pressed")   # barge-in first — before the paused/model-ready guards
        pipeline.ptt_pressed()

    ptt = PushToTalk(cfg.ptt_key, on_ptt_press, pipeline.ptt_released)

    def on_exit() -> None:
        log.info("exit requested")
        ptt.stop()
        corrector.stop()
        speak_server.shutdown()
        speaker.shutdown()
        pipeline.shutdown()
        if tray:
            tray.stop()

    def on_config_change() -> None:
        injector.method = cfg.injection_method
        pipeline.reopen_mic()
        transcriber.reload_if_changed()
        corrector.refresh()
        speaker.apply_config()
        if cfg.tts_enabled:
            _ensure_hook_material()
        log.info("config changed: mode=%s lang=%s correction=%s ptt=%s mic=%r tts=%s",
                 cfg.mode, cfg.language, cfg.ai_correction, cfg.ptt_key, cfg.input_device,
                 cfg.tts_enabled)

    tray = Tray(cfg, on_config_change, on_exit, ptt.set_key,
                corrector_status=lambda: corrector.status,
                speaker=speaker)

    def on_ollama_change() -> None:
        if tray:
            tray.refresh_menu()

    corrector.on_change = on_ollama_change

    def load_model() -> None:
        try:
            import vad
            vad.init_shared()  # pay the has_speech detector cost at startup, not on first dictation
            transcriber.load()
            on_status("idle")
        except Exception:
            log.exception("model load failed")
            if tray:
                tray.set_state("idle", "MODEL LOAD FAILED — see logs")

    threading.Thread(target=load_model, name="model-loader", daemon=True).start()
    corrector.start_keepalive()
    ptt.start()

    if cfg.tts_server_enabled:
        speak_server.start()        # listens even when read-back is off, so /health works
    if cfg.tts_enabled:
        _ensure_hook_material()
        speaker.ensure_loaded()     # warm the engine in the background (thread tts-loader)

    tray.run()  # blocks until Exit
    log.info("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
