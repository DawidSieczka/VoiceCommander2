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
import threading
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

# --- delayed-render clipboard (fast-injection path) ---
#
# WM_RENDERFORMAT is the only Windows signal that positively says "the paste
# actually read our data" (sequence numbers change on writes, not reads), so the
# post-paste wait can adapt instead of sleeping a fixed worst case. The owner
# window is per-thread (a Win32 window is bound to its creator's message queue,
# and injection may run on the pipeline thread or the inject-worker thread).

WM_RENDERFORMAT = 0x0305
WM_RENDERALLFORMATS = 0x0306
GMEM_MOVEABLE = 0x0002
HWND_MESSAGE = wt.HWND(-3)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM)

user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


# Explicit signatures for every handle-returning/handle-taking API used on this
# path: ctypes defaults to 32-bit c_int, which silently TRUNCATES 64-bit
# handles (GlobalAlloc/GlobalLock/CreateWindowExW/...) on 64-bit Python.
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = wt.LPVOID
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.restype = wt.BOOL
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.restype = wt.HGLOBAL
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.SetLastError.argtypes = [wt.DWORD]
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.OpenClipboard.restype = wt.BOOL
user32.OpenClipboard.argtypes = [wt.HWND]
user32.EmptyClipboard.restype = wt.BOOL
user32.GetClipboardOwner.restype = wt.HWND
user32.RegisterClipboardFormatW.restype = wt.UINT
user32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
user32.RegisterClassW.restype = wt.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.PeekMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.IsWindow.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]


def _global_handle(data: bytes) -> Optional[int]:
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return None
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return None
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    return h


class _ClipOwnerWindow:
    """Hidden message-only window that owns delayed-rendered clipboard text."""

    def __init__(self):
        self.text = ""
        self.rendered_at: Optional[float] = None
        self._wndproc = WNDPROC(self._wnd_proc)  # keep alive: Windows holds a raw pointer
        cls_name = f"VC2ClipOwner_{threading.get_ident()}"
        wc = WNDCLASSW(lpfnWndProc=self._wndproc, lpszClassName=cls_name,
                       hInstance=kernel32.GetModuleHandleW(None))
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError()
        self.hwnd = user32.CreateWindowExW(0, cls_name, None, 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, None, wc.hInstance, None)
        if not self.hwnd:
            raise ctypes.WinError()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_RENDERFORMAT:
                self._render()
                return 0
            if msg == WM_RENDERALLFORMATS:
                # Per API contract this handler must open/verify/close itself.
                if user32.OpenClipboard(hwnd):
                    try:
                        if user32.GetClipboardOwner() == hwnd:
                            self._render()
                    finally:
                        user32.CloseClipboard()
                return 0
        except Exception:
            log.exception("clipboard render failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _render(self) -> None:
        h = _global_handle(self.text.encode("utf-16-le") + b"\x00\x00")
        if h is None:
            log.warning("clipboard render: GlobalAlloc/Lock failed")
            return
        if not user32.SetClipboardData(win32con.CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            log.warning("clipboard render: SetClipboardData failed")
            return
        self.rendered_at = time.monotonic()

    def set_delayed(self, text: str) -> bool:
        """Put a delayed-render CF_UNICODETEXT promise (+ history-exclusion
        formats) on the clipboard. Returns False if the clipboard is busy."""
        self.text = text
        self.rendered_at = None
        for _ in range(10):
            if user32.OpenClipboard(self.hwnd):
                break
            time.sleep(0.03)
        else:
            return False
        try:
            user32.EmptyClipboard()
            # Delayed rendering: SetClipboardData(fmt, NULL) returns NULL on
            # SUCCESS too (the return value is the data handle, and there is no
            # data yet) — success/failure is distinguished only via last-error.
            kernel32.SetLastError(0)
            user32.SetClipboardData(win32con.CF_UNICODETEXT, None)
            if kernel32.GetLastError() != 0:
                return False
            # Keep dictated text out of Win+V history / cloud sync (constitution I).
            for name, value in ((CF_EXCLUDE_HISTORY, "1"), (CF_CAN_INCLUDE_HISTORY, "0")):
                fmt = user32.RegisterClipboardFormatW(name)
                h = _global_handle(value.encode("utf-16-le") + b"\x00\x00")
                if fmt and h is not None and not user32.SetClipboardData(fmt, h):
                    kernel32.GlobalFree(h)
            return True
        finally:
            user32.CloseClipboard()

    def pump_until_rendered(self, cap_ms: int) -> bool:
        """Dispatch messages for this thread until the paste consumed our data
        (WM_RENDERFORMAT handled) or the cap elapses."""
        msg = wt.MSG()
        deadline = time.monotonic() + cap_ms / 1000.0
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if self.rendered_at is not None:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.005)


_tls = threading.local()


def _clip_owner() -> Optional[_ClipOwnerWindow]:
    win = getattr(_tls, "clip_owner", None)
    if win is None:
        try:
            win = _ClipOwnerWindow()
        except Exception:
            log.exception("failed to create clipboard owner window")
            win = False  # sentinel: do not retry every injection
        _tls.clip_owner = win
    return win or None


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
        self._warned_delayed_render = False

    def inject(self, text: str, target_hwnd: Optional[int] = None, *,
               fast: bool = False, short_chars: int = 120,
               wait_max_ms: int = 300) -> tuple[bool, Optional[str]]:
        """Inject text at the caret of the focused window.

        target_hwnd: window captured at PTT release; if focus moved elsewhere
        since, injection is refused (focus-drift guard).
        fast: A/B fast-injection toggle — short texts are typed directly and
        the clipboard wait adapts to actual paste consumption.

        Returns (ok, refusal_reason); reason is "refused_focus" or
        "refused_password" when refused, None otherwise.
        """
        if not text:
            return True, None
        if target_hwnd is not None and focused_window() != target_hwnd:
            log.warning("focus changed during processing — injection refused")
            return False, "refused_focus"
        if _is_password_field():
            log.warning("focused control looks like a password field — injection refused")
            return False, "refused_password"

        if fast and len(text) <= short_chars:
            log.debug("fast injection: typing %d chars directly (no clipboard)", len(text))
            return self._inject_unicode(text), None
        if self.method == "clipboard":
            if fast:
                return self._inject_clipboard_fast(text, wait_max_ms), None
            return self._inject_clipboard(text), None
        return self._inject_unicode(text), None

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

    def _inject_clipboard_fast(self, text: str, wait_max_ms: int) -> bool:
        """Clipboard paste with an adaptive post-paste wait (delayed rendering).

        Never slower than the legacy fixed wait: the pump is capped at
        wait_max_ms and falls back to the legacy path if the owner window or
        the delayed promise cannot be set up (research R5 contingency).
        """
        win = _clip_owner()
        if win is None:
            if not self._warned_delayed_render:
                self._warned_delayed_render = True
                log.warning("delayed-render unavailable — using legacy clipboard wait (one-time warning)")
            return self._inject_clipboard(text)

        kind, saved = _snapshot_clipboard()
        if not win.set_delayed(text):
            log.warning("clipboard busy — falling back to Unicode typing")
            return self._inject_unicode(text)

        _release_held_modifiers()
        t0 = time.monotonic()
        _send_inputs([_key_event(VK_CONTROL, False), _key_event(VK_V, False),
                      _key_event(VK_V, True), _key_event(VK_CONTROL, True)])

        if win.pump_until_rendered(wait_max_ms):
            time.sleep(0.03)  # grace: let the target finish its paste handler
            log.debug("paste consumed after %d ms", int((win.rendered_at - t0) * 1000))
        else:
            log.info("paste not observed within %d ms cap — restoring clipboard anyway", wait_max_ms)

        if kind == "text" and saved is not None:
            if not _set_clipboard_text_plain(saved):
                log.warning("failed to restore clipboard text")
        else:
            # Nothing to restore: materialize the promise into real text so no
            # unrendered delayed handle lingers on a window nobody pumps anymore
            # (end state identical to the legacy path: dictated text stays).
            _set_clipboard_text(text)
            if kind == "other" and not self._warned_clipboard:
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
