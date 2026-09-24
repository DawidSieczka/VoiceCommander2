"""System tray icon and context menu (pystray). Menu text is English by requirement."""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Callable

import pystray
from PIL import Image, ImageDraw

import autostart
import config as cfgmod
from config import AppConfig

log = logging.getLogger("tray")

_COLORS = {"idle": (128, 134, 139), "recording": (214, 69, 69),
           "processing": (235, 155, 50), "loading": (100, 120, 200),
           "speaking": (70, 170, 110)}

_TTS_SPEEDS = ((0.8, "0.8× slower"), (1.0, "1.0× normal"), (1.2, "1.2× faster"), (1.5, "1.5× fast"))


def _make_icon(state: str) -> Image.Image:
    """Simple microphone glyph tinted by state."""
    color = _COLORS.get(state, _COLORS["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([24, 8, 40, 36], radius=8, fill=color)          # capsule
    d.arc([16, 20, 48, 46], start=0, end=180, fill=color, width=4)      # cradle
    d.line([32, 46, 32, 54], fill=color, width=4)                       # stem
    d.line([22, 56, 42, 56], fill=color, width=4)                       # base
    return img


class Tray:
    def __init__(self, cfg: AppConfig, on_change: Callable[[], None], on_exit: Callable[[], None],
                 set_ptt_key: Callable[[str], None],
                 corrector_status: Callable[[], str] = lambda: "ok",
                 speaker=None):
        self.cfg = cfg
        self._on_change = on_change
        self._on_exit = on_exit
        self._set_ptt_key = set_ptt_key
        self._corrector_status = corrector_status  # "ok" | "offline" | "no_model" | ...
        self._speaker = speaker                    # tts.Speaker or None (feature 002)
        self._status_text = "loading model..."
        self._icon = pystray.Icon("VoiceCommander2", _make_icon("loading"),
                                  "VoiceCommander2", menu=self._menu())

    # --- public API (thread-safe) ---

    def run(self) -> None:
        self._icon.run()

    def set_state(self, state: str, status: str | None = None) -> None:
        if status is not None:
            self._status_text = status
        try:
            self._icon.icon = _make_icon(state)
            self._icon.title = f"VoiceCommander2 — {self._status_text}"
            self._icon.update_menu()
        except Exception:
            pass

    def refresh_menu(self) -> None:
        """Re-render menu labels/enabled state without touching icon or status."""
        try:
            self._icon.update_menu()
        except Exception:
            pass

    def stop(self) -> None:
        self._icon.stop()

    # --- menu ---

    def _ai_correction_label(self) -> str:
        status = self._corrector_status()
        if status in ("ok", "starting"):
            return "AI correction"
        reason = {"offline": "Ollama not running",
                  "no_model": f"run: ollama pull {self.cfg.ollama_model}"}.get(status, "Ollama error")
        return f"AI correction — {reason}"

    def _save(self) -> None:
        cfgmod.save(self.cfg)
        self._on_change()
        self._icon.update_menu()

    def _set_stt_device(self, device: str, compute: str):
        """Device and compute type switch together (CUDA falls back to CPU int8
        automatically if the GPU/driver/CUDA libs are unusable — see stt.load)."""
        def do(icon, item):
            self.cfg.stt_device = device
            self.cfg.stt_compute_type = compute
            self._save()
        return do

    def _installed_ollama_models(self) -> list[str] | None:
        """Names from the local Ollama (`GET /api/tags`), cached for a few
        seconds because pystray re-renders the submenu on every open. None when
        the server does not answer. Per machine by construction."""
        import time
        import requests
        ts, names = getattr(self, "_models_cache", (0.0, None))
        if time.monotonic() - ts < 5:
            return names
        try:
            r = requests.get(f"{self.cfg.ollama_url}/api/tags", timeout=1.5)
            r.raise_for_status()
            names = sorted(m["name"] for m in r.json().get("models", []) if m.get("name"))
        except Exception:
            names = None
        self._models_cache = (time.monotonic(), names)
        return names

    def _ollama_model_items(self):
        """One radio item per model installed in this machine's Ollama; the
        configured model is listed even when missing here (config travels
        between laptops), marked so the user sees why correction is gray."""
        def choose(name):
            def do(icon, item):
                self.cfg.ollama_model = name
                self._save()
            return do
        names = self._installed_ollama_models()
        current = self.cfg.ollama_model
        if names is None:
            yield pystray.MenuItem("Ollama not running", None, enabled=False)
            yield pystray.MenuItem(f"{current} (configured)", None, checked=lambda item: True,
                                   radio=True, enabled=False)
            return
        if current not in names:
            yield pystray.MenuItem(f"{current} (not installed here — ollama pull)", None,
                                   checked=lambda item: True, radio=True, enabled=False)
        for name in names:
            yield pystray.MenuItem(name, choose(name),
                                   checked=lambda item, n=name: self.cfg.ollama_model == n,
                                   radio=True)

    def _stt_profile_items(self):
        """One radio item per preset in cfg.stt_profiles; 'Custom' when the
        current model/device/compute/beam match none of them."""
        def choose(name):
            def do(icon, item):
                cfgmod.apply_stt_profile(self.cfg, name)
                self._save()
            return do
        for name in self.cfg.stt_profiles:
            yield pystray.MenuItem(name, choose(name),
                                   checked=lambda item, n=name: cfgmod.stt_profile_name(self.cfg) == n,
                                   radio=True)
        yield pystray.MenuItem("Custom (edit config.json)", None,
                               checked=lambda item: cfgmod.stt_profile_name(self.cfg) is None,
                               radio=True, enabled=False)

    def _mic_items(self):
        """Dynamic submenu: system default + currently present input devices."""
        from audio import list_input_devices

        def choose(name):
            def do(icon, item):
                self.cfg.input_device = name
                self._save()
            return do

        yield pystray.MenuItem("System default", choose(""),
                               checked=lambda item: self.cfg.input_device == "", radio=True)
        for name in list_input_devices():
            yield pystray.MenuItem(name, choose(name),
                                   checked=(lambda n: lambda item: self.cfg.input_device == n)(name),
                                   radio=True)

    # --- spoken read-back (feature 002) ---

    def _tts_ready(self) -> bool:
        return self._speaker is not None and self._speaker.state == "ready" and not self._speaker.server_reason

    def _tts_label(self) -> str:
        if self._speaker is None:
            return "Read Claude answers — unavailable"
        if self._speaker.server_reason:
            return f"Read Claude answers — {self._speaker.server_reason}"
        state = self._speaker.state
        if state == "ready" or (state == "loading" and self._speaker.reason == "not loaded yet"):
            return "Read Claude answers"
        reason = {"missing": "piper not installed", "downloading": "downloading voices…",
                  "loading": "loading voices…"}.get(state, self._speaker.reason or "error")
        return f"Read Claude answers — {reason}"

    def _tts_settings_enabled(self) -> bool:
        # Settings stay editable unless the engine is missing outright; the
        # voice download/load is triggered by enabling the feature.
        return self._speaker is not None and self._speaker.state != "missing"

    def _voice_items(self):
        from tts import KNOWN_VOICES, VoiceStore

        def choose(name):
            def do(icon, item):
                self.cfg.tts_voice_pl = name
                self._save()
            return do

        store = VoiceStore()
        installed = set(store.installed("pl_"))
        for name in sorted(set(installed) | {v for v in KNOWN_VOICES if v.startswith("pl_")}):
            label = name if name in installed else f"{name} (download on select)"
            yield pystray.MenuItem(label, choose(name),
                                   checked=(lambda n: lambda item: self.cfg.tts_voice_pl == n)(name),
                                   radio=True)

    def _output_items(self):
        from audio import list_output_devices

        def choose(name):
            def do(icon, item):
                self.cfg.tts_output_device = name
                self._save()
            return do

        yield pystray.MenuItem("System default", choose(""),
                               checked=lambda item: self.cfg.tts_output_device == "", radio=True)
        for name in list_output_devices():
            yield pystray.MenuItem(name, choose(name),
                                   checked=(lambda n: lambda item: self.cfg.tts_output_device == n)(name),
                                   radio=True)

    def _speed_items(self):
        def choose(value):
            def do(icon, item):
                self.cfg.tts_speed = value
                self._save()
            return do
        for value, label in _TTS_SPEEDS:
            yield pystray.MenuItem(label, choose(value),
                                   checked=(lambda v: lambda item: abs(self.cfg.tts_speed - v) < 0.01)(value),
                                   radio=True)

    def _stop_reading(self, icon, item):
        if self._speaker is not None:
            self._speaker.stop("tray Stop reading")

    def _hook_label(self) -> str:
        import claude_hooks
        st, port = claude_hooks.status()
        if st == "installed":
            return f"Claude Code hook: installed (port {port})" if port == self.cfg.tts_server_port \
                else f"Claude Code hook: installed for port {port} — click to update"
        if st == "error":
            return "Claude Code hook: settings.json unreadable"
        return "Claude Code hook: install globally (~/.claude/settings.json)"

    def _install_hook(self, icon, item):
        import claude_hooks
        outcome = claude_hooks.ensure_installed(self.cfg.tts_server_port)
        log.info("hook install from tray: %s", outcome)
        self._icon.update_menu()

    def _remove_hook(self, icon, item):
        import claude_hooks
        try:
            log.info("hook remove from tray: %s", claude_hooks.remove())
        except Exception:
            log.exception("hook removal failed")
        self._icon.update_menu()

    def _menu(self) -> pystray.Menu:
        cfg = self.cfg

        def set_attr(name, value):
            def do(icon, item):
                setattr(cfg, name, value)
                if name == "ptt_key":
                    self._set_ptt_key(value)
                self._save()
            return do

        def toggle(name):
            def do(icon, item):
                setattr(cfg, name, not getattr(cfg, name))
                self._save()
            return do

        def checked(name, value=None):
            return (lambda item: getattr(cfg, name) == value) if value is not None \
                else (lambda item: bool(getattr(cfg, name)))

        def toggle_autostart(icon, item):
            autostart.set_enabled(not autostart.is_enabled())
            self._icon.update_menu()

        def open_path(path):
            def do(icon, item):
                target = str(path)
                if os.path.isdir(target):
                    subprocess.Popen(["explorer", target])
                else:
                    subprocess.Popen(["notepad", target])
            return do

        return pystray.Menu(
            pystray.MenuItem(lambda item: f"VoiceCommander2 — {self._status_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Language", pystray.Menu(
                pystray.MenuItem("Polish", set_attr("language", "pl"), checked=checked("language", "pl"), radio=True),
                pystray.MenuItem("English", set_attr("language", "en"), checked=checked("language", "en"), radio=True),
            )),
            pystray.MenuItem("Mode", pystray.Menu(
                pystray.MenuItem("On release", set_attr("mode", "on_release"), checked=checked("mode", "on_release"), radio=True),
                pystray.MenuItem("Per sentence", set_attr("mode", "per_sentence"), checked=checked("mode", "per_sentence"), radio=True),
                pystray.MenuItem("Realtime (no AI correction)", set_attr("mode", "realtime"), checked=checked("mode", "realtime"), radio=True),
            )),
            # Grayed out (with the reason) while the correction backend is not
            # usable; the checkbox state is preserved so correction resumes
            # automatically when Ollama comes back.
            pystray.MenuItem(lambda item: self._ai_correction_label(),
                             toggle("ai_correction"), checked=checked("ai_correction"),
                             enabled=lambda item: self._corrector_status() in ("ok", "starting")),
            pystray.MenuItem("Correction model (installed in Ollama)", pystray.Menu(self._ollama_model_items)),
            pystray.MenuItem("Push-to-talk key", pystray.Menu(
                pystray.MenuItem("F9 (default)", set_attr("ptt_key", "f9"), checked=checked("ptt_key", "f9"), radio=True),
                pystray.MenuItem("Right Ctrl", set_attr("ptt_key", "right ctrl"), checked=checked("ptt_key", "right ctrl"), radio=True),
                pystray.MenuItem("Right Alt (AltGr — conflicts with Polish chars)", set_attr("ptt_key", "right alt"), checked=checked("ptt_key", "right alt"), radio=True),
                pystray.MenuItem("Scroll Lock", set_attr("ptt_key", "scroll lock"), checked=checked("ptt_key", "scroll lock"), radio=True),
            )),
            pystray.MenuItem("Microphone", pystray.Menu(self._mic_items)),
            pystray.MenuItem("STT profile (per machine)", pystray.Menu(self._stt_profile_items)),
            pystray.MenuItem("Recognition model", pystray.Menu(
                pystray.MenuItem("Small — fastest, least accurate", set_attr("stt_model", "small"), checked=checked("stt_model", "small"), radio=True),
                pystray.MenuItem("Medium — balanced (GPU)", set_attr("stt_model", "medium"), checked=checked("stt_model", "medium"), radio=True),
                pystray.MenuItem("Large-v3-turbo — most accurate (GPU; slow on CPU)", set_attr("stt_model", "large-v3-turbo"), checked=checked("stt_model", "large-v3-turbo"), radio=True),
            )),
            pystray.MenuItem("STT device", pystray.Menu(
                pystray.MenuItem("CPU (int8)", self._set_stt_device("cpu", "int8"),
                                 checked=checked("stt_device", "cpu"), radio=True),
                pystray.MenuItem("GPU — CUDA (int8_float16)", self._set_stt_device("cuda", "int8_float16"),
                                 checked=checked("stt_device", "cuda"), radio=True),
            )),
            pystray.MenuItem("Performance (A/B)", pystray.Menu(
                pystray.MenuItem("Eager transcription (on-release mode)", toggle("perf_eager_stt"), checked=checked("perf_eager_stt")),
                pystray.MenuItem("Fast injection", toggle("perf_fast_injection"), checked=checked("perf_fast_injection")),
                pystray.MenuItem("Overlapped correction (per-sentence)", toggle("perf_pipelined_correction"), checked=checked("perf_pipelined_correction")),
            )),
            pystray.MenuItem(lambda item: self._tts_label(), pystray.Menu(
                pystray.MenuItem("Enabled", toggle("tts_enabled"), checked=checked("tts_enabled"),
                                 enabled=lambda item: self._tts_settings_enabled()),
                pystray.MenuItem("Stop reading", self._stop_reading,
                                 enabled=lambda item: self._speaker is not None and self._speaker.speaking),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Voice", pystray.Menu(self._voice_items),
                                 enabled=lambda item: self._tts_settings_enabled()),
                pystray.MenuItem("Speed", pystray.Menu(self._speed_items),
                                 enabled=lambda item: self._tts_settings_enabled()),
                pystray.MenuItem("Output device", pystray.Menu(self._output_items),
                                 enabled=lambda item: self._tts_settings_enabled()),
                pystray.MenuItem(lambda item: "Summarise long answers" if self._corrector_status() in ("ok", "starting")
                                 else "Summarise long answers — Ollama unusable",
                                 toggle("tts_summarize"), checked=checked("tts_summarize"),
                                 enabled=lambda item: self._corrector_status() in ("ok", "starting")),
                pystray.MenuItem("Queue: latest answer wins",
                                 lambda icon, item: (setattr(cfg, "tts_queue_policy",
                                                             "append" if cfg.tts_queue_policy == "latest" else "latest"),
                                                     self._save()),
                                 checked=lambda item: cfg.tts_queue_policy == "latest"),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda item: self._hook_label(), self._install_hook),
                pystray.MenuItem("Remove Claude Code hook", self._remove_hook),
                pystray.MenuItem("Open hook instructions", open_path(cfgmod.APPDATA_DIR / "hooks" / "README-hooks.txt")),
                pystray.MenuItem("Open pronunciation dictionary", open_path(cfgmod.APPDATA_DIR / "pronunciation.txt")),
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Paused", toggle("paused"), checked=checked("paused")),
            pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: autostart.is_enabled()),
            pystray.MenuItem("Open config file", open_path(cfgmod.CONFIG_PATH)),
            pystray.MenuItem("Open dictionary (vocabulary hints)", open_path(cfgmod.APPDATA_DIR / "dictionary.txt")),
            pystray.MenuItem("Open logs", open_path(cfgmod.LOG_DIR)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", lambda icon, item: self._on_exit()),
        )
