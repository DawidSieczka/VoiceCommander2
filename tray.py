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
           "processing": (235, 155, 50), "loading": (100, 120, 200)}


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
                 set_ptt_key: Callable[[str], None]):
        self.cfg = cfg
        self._on_change = on_change
        self._on_exit = on_exit
        self._set_ptt_key = set_ptt_key
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

    def stop(self) -> None:
        self._icon.stop()

    # --- menu ---

    def _save(self) -> None:
        cfgmod.save(self.cfg)
        self._on_change()
        self._icon.update_menu()

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
            pystray.MenuItem("AI correction", toggle("ai_correction"), checked=checked("ai_correction")),
            pystray.MenuItem("Push-to-talk key", pystray.Menu(
                pystray.MenuItem("F9 (default)", set_attr("ptt_key", "f9"), checked=checked("ptt_key", "f9"), radio=True),
                pystray.MenuItem("Right Ctrl", set_attr("ptt_key", "right ctrl"), checked=checked("ptt_key", "right ctrl"), radio=True),
                pystray.MenuItem("Right Alt (AltGr — conflicts with Polish chars)", set_attr("ptt_key", "right alt"), checked=checked("ptt_key", "right alt"), radio=True),
                pystray.MenuItem("Scroll Lock", set_attr("ptt_key", "scroll lock"), checked=checked("ptt_key", "scroll lock"), radio=True),
            )),
            pystray.MenuItem("Microphone", pystray.Menu(self._mic_items)),
            pystray.MenuItem("Recognition model", pystray.Menu(
                pystray.MenuItem("Small — fastest, least accurate", set_attr("stt_model", "small"), checked=checked("stt_model", "small"), radio=True),
                pystray.MenuItem("Medium — balanced (GPU)", set_attr("stt_model", "medium"), checked=checked("stt_model", "medium"), radio=True),
                pystray.MenuItem("Large-v3-turbo — most accurate, slow (CPU)", set_attr("stt_model", "large-v3-turbo"), checked=checked("stt_model", "large-v3-turbo"), radio=True),
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
