"""Text injection into the focused window.

Two mechanisms:
- clipboard paste (save -> set -> Ctrl+V -> restore), with Win+V history
  exclusion formats so dictated text never enters clipboard history/cloud sync;
- SendInput KEYEVENTF_UNICODE (mode 1 / fallback), no clipboard involved.

Guards (from v1 + critique):
- focus-drift: capture target HWND early, verify before injecting;
- password fields: best-effort ES_PASSWORD style check — never inject;
- held modifiers: synthesize key-ups around injection so chars don't become
  Ctrl+letter shortcuts.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
from typing import Optional

import win32clipboard
import win32con

log = logging.getLogger("injector")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# --- SendInput plumbing ---

ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    # Must be present in the union: sizeof(INPUT) is validated by SendInput and
    # the union's real size is driven by MOUSEINPUT, not KEYBDINPUT.
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN, VK_V = 0x11, 0x12, 0x10, 0x5B, 0x5C, 0x56
_MODIFIERS = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)


def _send_inputs(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d/%d events (GetLastError=%d)",
                    sent, len(inputs), kernel32.GetLastError())


def _key_event(vk: int, up: bool) -> INPUT:
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def _unicode_events(text: str) -> list[INPUT]:
    ev = []
    for ch in text:
        for unit in [ord(ch)] if ord(ch) <= 0xFFFF else \
                [int.from_bytes(ch.encode("utf-16-le")[i:i + 2], "little") for i in (0, 2)]:
            ev.append(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0))))
            ev.append(INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
    return ev


# --- focus / password inspection ---

class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]


def focused_window() -> Optional[int]:
    """Top-level foreground window handle (stable identity for drift check)."""
    hwnd = user32.GetForegroundWindow()
    return hwnd or None


def _focused_control() -> Optional[int]:
    fg = user32.GetForegroundWindow()
    if not fg:
        return None
    tid = user32.GetWindowThreadProcessId(fg, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return info.hwndFocus or fg
    return fg


def _is_password_field() -> bool:
    """Best-effort: catches Win32 ES_PASSWORD edits; browser fields are not detectable this way."""
    ctrl = _focused_control()
    if not ctrl:
        return False
    style = user32.GetWindowLongW(ctrl, win32con.GWL_STYLE)
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(ctrl, cls, 64)
    if "edit" in cls.value.lower() and (style & win32con.ES_PASSWORD):
        return True
    return False


def _release_held_modifiers() -> list[int]:
    held = [vk for vk in _MODIFIERS if user32.GetAsyncKeyState(vk) & 0x8000]
    if held:
        _send_inputs([_key_event(vk, up=True) for vk in held])
        time.sleep(0.02)
    return held


# --- clipboard ---

CF_EXCLUDE_HISTORY = "ExcludeClipboardContentFromMonitorProcessing"
CF_CAN_INCLUDE_HISTORY = "CanIncludeInClipboardHistory"


def _open_clipboard(retries: int = 10) -> bool:
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.03)
    return False


def _snapshot_clipboard():
    """Returns ('text', str) | ('none', None) | ('other', None)."""
    if not _open_clipboard():
        return ("other", None)
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            try:
                return ("text", win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT))
            except Exception:
                return ("other", None)
        if win32clipboard.EnumClipboardFormats(0) == 0:
            return ("none", None)
        return ("other", None)
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_text(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        # Keep dictated text out of Win+V history / cloud clipboard sync.
        for name, value in ((CF_EXCLUDE_HISTORY, "1"), (CF_CAN_INCLUDE_HISTORY, "0")):
            try:
                fmt = win32clipboard.RegisterClipboardFormat(name)
                win32clipboard.SetClipboardData(fmt, value.encode("utf-16-le") + b"\x00\x00")
            except Exception:
                pass
        return True
    finally:
        win32clipboard.CloseClipboard()


class Injector:
    def __init__(self, method: str = "clipboard"):
        self.method = method
        self._warned_clipboard = False

    def inject(self, text: str, target_hwnd: Optional[int] = None) -> bool:
        """Inject text at the caret of the focused window.

        target_hwnd: window captured at PTT release; if focus moved elsewhere
        since, injection is refused (focus-drift guard).
        """
        if not text:
            return True
        if target_hwnd is not None and focused_window() != target_hwnd:
            log.warning("focus changed during processing — injection refused")
            return False
        if _is_password_field():
            log.warning("focused control looks like a password field — injection refused")
            return False

        if self.method == "clipboard":
            return self._inject_clipboard(text)
        return self._inject_unicode(text)

    def type_text(self, text: str) -> None:
        """Streaming path (mode 1): unconditional Unicode typing, no clipboard."""
        if text:
            self._inject_unicode(text)

    def _inject_unicode(self, text: str) -> bool:
        _release_held_modifiers()
        events = _unicode_events(text)
        # Chunk to keep SendInput batches modest.
        for i in range(0, len(events), 200):
            _send_inputs(events[i:i + 200])
        return True

    def _inject_clipboard(self, text: str) -> bool:
        kind, saved = _snapshot_clipboard()
        if not _set_clipboard_text(text):
            log.warning("clipboard busy — falling back to Unicode typing")
            return self._inject_unicode(text)

        _release_held_modifiers()
        _send_inputs([_key_event(VK_CONTROL, False), _key_event(VK_V, False),
                      _key_event(VK_V, True), _key_event(VK_CONTROL, True)])

        # Adaptive-ish delay before restore: give slow targets time to process WM_PASTE.
        time.sleep(0.30)

        if kind == "text" and saved is not None:
            if not _set_clipboard_text_plain(saved):
                log.warning("failed to restore clipboard text")
        elif kind == "other" and not self._warned_clipboard:
            self._warned_clipboard = True
            log.warning("clipboard held non-text data; it was not restored (one-time warning)")
        return True


def _set_clipboard_text_plain(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        return True
    finally:
        win32clipboard.CloseClipboard()
