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

    def on_status(state: str) -> None:
        if tray:
            tray.set_state(state, {"idle": "ready", "recording": "recording…",
                                   "processing": "processing…"}.get(state, state))

    pipeline = Pipeline(cfg, transcriber, corrector, injector, on_status)

    ptt = PushToTalk(cfg.ptt_key, pipeline.ptt_pressed, pipeline.ptt_released)

    def on_exit() -> None:
        log.info("exit requested")
        ptt.stop()
        corrector.stop()
        pipeline.shutdown()
        if tray:
            tray.stop()

    def on_config_change() -> None:
        injector.method = cfg.injection_method
        pipeline.reopen_mic()
        transcriber.reload_if_changed()
        log.info("config changed: mode=%s lang=%s correction=%s ptt=%s mic=%r",
                 cfg.mode, cfg.language, cfg.ai_correction, cfg.ptt_key, cfg.input_device)

    tray = Tray(cfg, on_config_change, on_exit, ptt.set_key)

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

    tray.run()  # blocks until Exit
    log.info("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
