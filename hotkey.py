"""Global push-to-talk hotkey.

Critique-driven design:
- The low-level hook callback must be trivial (set a flag, return) or Windows
  silently unhooks us while the CPU is busy transcribing.
- The hook does not run on the secure desktop (UAC prompts), so a key release
  can be missed; a GetAsyncKeyState watchdog polls the real key state and
  synthesizes the release event.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Callable

import keyboard

log = logging.getLogger("hotkey")

MAX_HOLD_S = 120  # failsafe: synthesize release after this long (secure-desktop missed key-up)


class PushToTalk:
    """Fires on_press()/on_release() around a held PTT key. Thread-safe."""

    def __init__(self, key: str, on_press: Callable[[], None], on_release: Callable[[], None]):
        self._key = key
        self._on_press = on_press
        self._on_release = on_release
        self._held = False
        self._held_since = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._hook = None
        self._watchdog = threading.Thread(target=self._watch, name="ptt-watchdog", daemon=True)

    def start(self) -> None:
        self._register()
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
        self._unregister()

    def set_key(self, key: str) -> None:
        with self._lock:
            self._key = key
        self._unregister()
        self._register()
        log.info("PTT key changed to %r", key)

    @property
    def key(self) -> str:
        return self._key

    # --- internals ---

    def _register(self) -> None:
        # suppress=True keeps the key from reaching the app underneath.
        # Exception: right alt = AltGr on the Polish layout — suppressing it is
        # notoriously buggy (fake-LCtrl event storms) and unnecessary: releasing
        # Ctrl+Alt does not activate app menus the way a lone Alt does.
        suppress = self._key != "right alt"
        try:
            self._hook = keyboard.hook_key(self._key, self._event, suppress=suppress)
        except Exception:
            log.exception("hook_key failed for %r; retrying without suppression", self._key)
            self._hook = keyboard.hook_key(self._key, self._event, suppress=False)
        # Polish layout: the physical right alt is AltGr and arrives as the
        # separate key 'alt gr' (scan 541), never as 'right alt' — hook both.
        self._extra_hook = None
        if self._key == "right alt":
            try:
                self._extra_hook = keyboard.hook_key("alt gr", self._event, suppress=False)
            except Exception:
                log.warning("could not hook 'alt gr' alias for right alt")

    def _unregister(self) -> None:
        for attr in ("_hook", "_extra_hook"):
            hook = getattr(self, attr, None)
            if hook is not None:
                try:
                    keyboard.unhook(hook)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _event(self, e) -> None:
        # Keep this trivial: flip state, hand off to a worker thread.
        log.debug("raw event: %s name=%r scan=%s", e.event_type, e.name, e.scan_code)
        pressed = e.event_type == "down"
        fire = None
        with self._lock:
            if pressed and not self._held:
                self._held = True
                self._held_since = time.monotonic()
                fire = self._on_press
            elif not pressed and self._held:
                self._held = False
                fire = self._on_release
        if fire:
            threading.Thread(target=self._safe_call, args=(fire,), daemon=True).start()

    @staticmethod
    def _safe_call(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("PTT callback failed")

    def _watch(self) -> None:
        """Backup release detection.

        GetAsyncKeyState is blind to a suppressed key (the hook eats the event
        before it updates system key state), so the primary check asks the
        keyboard library's own hook-tracked state. A max-hold failsafe covers
        the secure-desktop case (UAC prompt swallows the key-up entirely).
        """
        while not self._stop.wait(0.15):
            with self._lock:
                held, key, since = self._held, self._key, self._held_since
            if not held:
                continue
            aliases = [key] + (["alt gr"] if key == "right alt" else [])
            release_reason = None
            try:
                if not any(keyboard.is_pressed(k) for k in aliases):
                    time.sleep(0.05)
                    if not any(keyboard.is_pressed(k) for k in aliases):
                        release_reason = "hook state says released"
            except Exception:
                pass
            if release_reason is None and time.monotonic() - since > MAX_HOLD_S:
                release_reason = f"held longer than {MAX_HOLD_S}s (missed key-up?)"
            if release_reason:
                fire = None
                with self._lock:
                    if self._held:
                        self._held = False
                        fire = self._on_release
                if fire:
                    log.warning("watchdog synthesizing release: %s", release_reason)
                    self._safe_call(fire)
