"""Spoken read-back: engine supervisor, playback and the speak queue (feature 002).

Speaker            accepts SpeakRequests, applies the queue policy, prepares text,
                   synthesises sentence by sentence and plays them; stop() is instant.
PiperWorkerBackend supervises tts_worker.py (the GPL Piper engine) over stdio pipes.
Player             one sounddevice OutputStream per request at the voice's sample rate.
VoiceStore         voice files under MODELS_DIR/tts, one-time download.

Every request ends in exactly one `SPEAK …` log line (contracts/http-api.md).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np

import text_prep
from config import APPDATA_DIR, MODELS_DIR, AppConfig, TtsSnapshot

log = logging.getLogger("tts")

TTS_DIR = MODELS_DIR / "tts"
OUTCOMES = ("spoken", "stopped", "superseded", "empty", "disabled", "ignored_event", "error")

_HF = "https://huggingface.co"
KNOWN_VOICES: dict[str, str] = {
    "pl_PL-jarvis_wg_glos-medium": f"{_HF}/WitoldG/polish_piper_models/resolve/main/",
    "pl_PL-justyna_wg_glos-medium": f"{_HF}/WitoldG/polish_piper_models/resolve/main/",
    "pl_PL-meski_wg_glos-medium": f"{_HF}/WitoldG/polish_piper_models/resolve/main/",
    "pl_PL-zenski_wg_glos-medium": f"{_HF}/WitoldG/polish_piper_models/resolve/main/",
    "pl_PL-darkman-medium": f"{_HF}/rhasspy/piper-voices/resolve/main/pl/pl_PL/darkman/medium/",
    "pl_PL-gosia-medium": f"{_HF}/rhasspy/piper-voices/resolve/main/pl/pl_PL/gosia/medium/",
    "pl_PL-mc_speech-medium": f"{_HF}/rhasspy/piper-voices/resolve/main/pl/pl_PL/mc_speech/medium/",
    "en_US-lessac-medium": f"{_HF}/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/",
}

SENTENCE_GAP_S = 0.15
BLOCK_S = 0.1                      # player write granularity -> stop latency bound
VOLUME = 0.9


# ============================================================== requests

class EngineUnavailable(Exception):
    pass


@dataclass
class SpeakRequest:
    id: int
    source: str                     # "hook" | "manual"
    event: str                      # "Stop" | "SubagentStop" | "manual" | ...
    session_id: str
    cwd: str
    lang: Optional[str]
    raw_text: str
    snapshot: TtsSnapshot
    received_at: float = field(default_factory=time.monotonic)
    stop_event: threading.Event = field(default_factory=threading.Event)
    stop_outcome: str = "stopped"        # set by Speaker.stop(): "stopped" | "superseded"
    stop_reason: str = "-"
    # measured
    clean_chars: int = 0
    summarised: bool = False
    summary_ms: int = 0
    first_audio_ms: int = -1
    audio_s: float = 0.0
    sentences: int = 0
    en_spans: int = 0
    voice: str = ""
    _concluded: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def conclude(self, outcome: str, reason: str = "-") -> None:
        if outcome not in OUTCOMES:
            outcome = "error"
        with self._lock:
            if self._concluded:
                return
            self._concluded = True
        log.info(
            "SPEAK id=%d src=%s event=%s session=%s outcome=%s raw_chars=%d clean_chars=%d "
            "summarised=%d summary_ms=%d first_audio_ms=%d audio_s=%.1f sentences=%d en_spans=%d "
            "voice=%s speed=%.2f reason=%s",
            self.id, self.source, self.event, (self.session_id or "-")[:8], outcome,
            len(self.raw_text), self.clean_chars, int(self.summarised), self.summary_ms,
            self.first_audio_ms, self.audio_s, self.sentences, self.en_spans,
            self.voice or "-", self.snapshot.speed, reason.replace(" ", "_") if reason else "-",
        )


# ============================================================== voices

class VoiceStore:
    """Voice files under MODELS_DIR/tts; downloads a known voice once (constitution I
    extension documented in plan.md)."""

    def __init__(self, root: Path = TTS_DIR):
        self.root = root

    def path(self, voice_id: str) -> Path:
        return self.root / f"{voice_id}.onnx"

    def is_present(self, voice_id: str) -> bool:
        p = self.path(voice_id)
        return p.exists() and p.with_suffix(".onnx.json").exists() and p.stat().st_size > 1_000_000

    def installed(self, prefix: str = "") -> list[str]:
        try:
            return sorted(p.stem for p in self.root.glob(f"{prefix}*.onnx")
                          if p.with_suffix(".onnx.json").exists())
        except Exception:
            return []

    def ensure(self, voice_id: str) -> Path:
        if self.is_present(voice_id):
            return self.path(voice_id)
        base = KNOWN_VOICES.get(voice_id)
        if not base:
            raise EngineUnavailable(f"voice {voice_id} missing and not downloadable")
        import requests
        self.root.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx.json", ".onnx"):
            url = f"{base}{voice_id}{suffix}"
            dest = self.root / f"{voice_id}{suffix}"
            part = dest.with_name(dest.name + ".part")
            log.info("downloading voice %s (one-time network use)", url)
            t0 = time.perf_counter()
            with requests.get(url, stream=True, timeout=(5, 120)) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.replace(part, dest)
            log.info("voice file %s: %.1f MB in %.1f s", dest.name,
                     dest.stat().st_size / 1e6, time.perf_counter() - t0)
        return self.path(voice_id)


# ============================================================== engine

def piper_importable() -> bool:
    """True when the GPL engine is installed in this interpreter (not imported here)."""
    try:
        return importlib.util.find_spec("piper") is not None
    except Exception:
        return False


class PiperWorkerBackend:
    """Supervises tts_worker.py; all calls are serialised by the caller (Speaker).

    state: missing | downloading | loading | ready | error ; reason explains non-ready.
    """

    LOAD_TIMEOUT_S = 180.0
    SYNTH_TIMEOUT_S = 30.0
    PHONEMIZE_TIMEOUT_S = 10.0

    def __init__(self, voices: VoiceStore, worker_path: Optional[Path] = None,
                 python: Optional[str] = None):
        self.voices = voices
        self._worker_path = worker_path or Path(__file__).with_name("tts_worker.py")
        self._python = python or sys.executable
        self.state = "missing" if not piper_importable() else "loading"
        self.reason = "piper-tts not installed (pip install -r requirements-tts.txt)" \
            if self.state == "missing" else "not loaded yet"
        self.loaded_key: Optional[tuple[str, str]] = None
        self.sample_rate = 22050
        self._proc: Optional[subprocess.Popen] = None
        self._pending: dict[int, queue.Queue] = {}
        self._seq = 0
        self._lock = threading.Lock()        # protects _proc/_pending/_seq
        self._call_lock = threading.Lock()   # one request in flight at a time
        self._failures = 0
        self._closing = False

    # --- process lifecycle ---

    def _spawn(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                [self._python, str(self._worker_path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags, cwd=str(self._worker_path.parent),
            )
            proc = self._proc
            log.info("tts worker started (pid %d)", proc.pid)
        threading.Thread(target=self._reader, args=(proc,), name="tts-worker-reader", daemon=True).start()
        threading.Thread(target=self._stderr_pump, args=(proc,), name="tts-worker-stderr", daemon=True).start()

    def _kill(self, why: str) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            pend, self._pending = self._pending, {}
            self.loaded_key = None
        for q in pend.values():
            q.put(("J", {"event": "error", "reason": why}))
        if proc is not None and proc.poll() is None:
            log.warning("tts worker killed: %s", why)
            try:
                proc.kill()
            except Exception:
                pass

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proc
            self._closing = True
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(b'{"op":"quit"}\n')
                proc.stdin.flush()
                proc.wait(timeout=2)
            except Exception:
                pass
        self._kill("shutdown")

    def _reader(self, proc: subprocess.Popen) -> None:
        out = proc.stdout
        try:
            while True:
                hdr = out.read(4)
                if len(hdr) < 4:
                    break
                n = struct.unpack(">I", hdr)[0]
                body = out.read(n)
                if len(body) < n:
                    break
                kind, payload = body[:1], body[1:]
                if kind == b"A":
                    rid, sr = struct.unpack(">II", payload[:8])
                    item = ("A", (sr, np.frombuffer(payload[8:], dtype="<i2")))
                else:
                    msg = json.loads(payload.decode("utf-8"))
                    rid = int(msg.get("id", 0))
                    item = ("J", msg)
                with self._lock:
                    q = self._pending.get(rid)
                if q is not None:
                    q.put(item)
        except Exception:
            log.exception("tts worker reader failed")
        finally:
            with self._lock:
                mine = self._proc is proc and not self._closing
            if mine:
                code = proc.poll()
                self._kill(f"worker exited (code {code})")
                self.state, self.reason = "error", f"worker exited (code {code})"

    def _stderr_pump(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log.debug("worker: %s", text)
        except Exception:
            pass

    # --- protocol ---

    def _call(self, req: dict, timeout: float) -> Iterator[tuple[str, object]]:
        """Send one request; yield ('A', (sr, pcm)) / ('J', msg) until a terminal event."""
        self._spawn()
        with self._lock:
            self._seq += 1
            rid = self._seq
            q: queue.Queue = queue.Queue()
            self._pending[rid] = q
            proc = self._proc
        req = dict(req, id=rid)
        try:
            proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except Exception as e:
            self._kill(f"write failed: {e}")
            raise EngineUnavailable(f"worker write failed: {e}")
        try:
            while True:
                try:
                    kind, item = q.get(timeout=timeout)
                except queue.Empty:
                    self._kill(f"timeout after {timeout:.0f} s on {req.get('op')}")
                    raise EngineUnavailable(f"worker timeout on {req.get('op')}")
                yield kind, item
                if kind == "J" and item.get("event") in ("done", "error", "loaded", "phonemes", "pong"):
                    return
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    # --- public API (called from the speaker thread / loader thread only) ---

    def load(self, voice_pl: str, voice_en: str) -> None:
        key = (voice_pl, voice_en)
        with self._call_lock:
            if self.loaded_key == key and self.state == "ready":
                return
            if not piper_importable():
                self.state, self.reason = "missing", "piper-tts not installed (pip install -r requirements-tts.txt)"
                raise EngineUnavailable(self.reason)
            try:
                self.state, self.reason = "downloading", "downloading voices…"
                paths = {"pl": str(self.voices.ensure(voice_pl)), "en": str(self.voices.ensure(voice_en))}
                self.state, self.reason = "loading", "loading voices…"
                t0 = time.perf_counter()
                for kind, item in self._call({"op": "load", "voices": paths}, self.LOAD_TIMEOUT_S):
                    if kind == "J" and item.get("event") == "error":
                        raise EngineUnavailable(item.get("reason", "load failed"))
                    if kind == "J" and item.get("event") == "loaded":
                        self.sample_rate = int(item["voices"]["pl"]["sample_rate"])
                        log.info("tts voices loaded (%s, %s) in %.1f s [thread %s]", voice_pl, voice_en,
                                 time.perf_counter() - t0, threading.current_thread().name)
                self.loaded_key = key
                self.state, self.reason = "ready", ""
                self._failures = 0
            except EngineUnavailable as e:
                self.state, self.reason = "error", str(e)
                raise
            except Exception as e:
                self.state, self.reason = "error", f"{type(e).__name__}: {e}"
                raise EngineUnavailable(self.reason)

    def synthesize(self, text: str, lang: str, speed: float, volume: float = VOLUME) -> Iterator[np.ndarray]:
        """Yield int16 chunks (one per Piper sentence). Raises EngineUnavailable."""
        with self._call_lock:
            if self.state != "ready":
                raise EngineUnavailable(self.reason or "engine not ready")
            req = {"op": "synth", "text": text, "lang": lang,
                   "length_scale": 1.0 / max(0.5, min(2.0, speed)), "volume": volume}
            for kind, item in self._call(req, self.SYNTH_TIMEOUT_S):
                if kind == "A":
                    sr, pcm = item
                    self.sample_rate = sr
                    yield pcm
                elif item.get("event") == "error":
                    raise EngineUnavailable(item.get("reason", "synth failed"))

    def phonemize(self, text: str, lang: str = "en") -> str:
        with self._call_lock:
            if self.state != "ready":
                raise EngineUnavailable(self.reason or "engine not ready")
            for kind, item in self._call({"op": "phonemize", "text": text, "lang": lang},
                                         self.PHONEMIZE_TIMEOUT_S):
                if kind == "J":
                    if item.get("event") == "phonemes":
                        return " ".join(item.get("phonemes") or [])
                    if item.get("event") == "error":
                        raise EngineUnavailable(item.get("reason", "phonemize failed"))
        return ""


# ============================================================== playback

class Player:
    """Blocking writes in 100 ms blocks so stop() lands within one block."""

    def __init__(self, get_device_name: Callable[[], str] = lambda: ""):
        self._get_device_name = get_device_name
        self._stream = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def open(self, sample_rate: int) -> None:
        import sounddevice as sd
        from audio import resolve_output_device
        self.close()
        self._stop.clear()
        stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16",
                                 device=resolve_output_device(self._get_device_name()),
                                 latency="low")
        stream.start()
        with self._lock:
            self._stream = stream
        log.debug("output stream opened (%d Hz)", sample_rate)

    def write(self, pcm: np.ndarray, sample_rate: int) -> bool:
        """Write one chunk; returns False if stopped before it finished."""
        block = max(256, int(sample_rate * BLOCK_S))
        for i in range(0, len(pcm), block):
            if self._stop.is_set():
                return False
            with self._lock:
                stream = self._stream
            if stream is None:
                return False
            try:
                stream.write(np.ascontiguousarray(pcm[i:i + block]))
            except Exception as e:
                if self._stop.is_set():
                    return False
                log.warning("output stream write failed: %s", e)
                return False
        return not self._stop.is_set()

    def silence(self, seconds: float, sample_rate: int) -> bool:
        return self.write(np.zeros(int(seconds * sample_rate), dtype=np.int16), sample_rate)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


# ============================================================== speaker

class Speaker:
    """Queue + policy + text preparation + synthesis/playback orchestration."""

    def __init__(self, cfg: AppConfig, backend, player, summarize: Optional[Callable[[str, float, str], Optional[str]]] = None,
                 corrector_available: Callable[[], bool] = lambda: False,
                 on_speaking: Callable[[bool], None] = lambda _s: None):
        self.cfg = cfg
        self.backend = backend
        self.player = player
        self._summarize = summarize
        self._corrector_available = corrector_available
        self._on_speaking = on_speaking
        self._q: queue.Queue = queue.Queue()
        self._seq = 0
        self._current: Optional[SpeakRequest] = None
        self._lock = threading.Lock()
        self._loader: Optional[threading.Thread] = None
        self.server_reason = ""       # set by the HTTP server (e.g. "port 47321 busy")
        self._thread = threading.Thread(target=self._loop, name="tts-speaker", daemon=True)
        self._thread.start()
        text_prep.seed_pronunciation_file()

    # --- state for the tray ---

    @property
    def state(self) -> str:
        return self.backend.state

    @property
    def reason(self) -> str:
        return self.server_reason or self.backend.reason

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.first_audio_ms >= 0

    # --- voices ---

    def ensure_loaded(self, block: bool = False) -> None:
        """Load/reload voices in the background when config changed (stt.reload_if_changed idiom)."""
        key = (self.cfg.tts_voice_pl, self.cfg.tts_voice_en)
        if self.backend.loaded_key == key and self.backend.state == "ready":
            return
        with self._lock:
            if self._loader is not None and self._loader.is_alive():
                if not block:
                    return
                t = self._loader
            else:
                t = threading.Thread(target=self._load, args=key, name="tts-loader", daemon=True)
                self._loader = t
                t.start()
        if block:
            t.join(timeout=PiperWorkerBackend.LOAD_TIMEOUT_S + 5)

    def _load(self, voice_pl: str, voice_en: str) -> None:
        try:
            self.backend.load(voice_pl, voice_en)
        except EngineUnavailable as e:
            log.warning("tts engine unavailable: %s", e)
        except Exception:
            log.exception("tts engine load failed")

    def apply_config(self) -> None:
        if self.cfg.tts_enabled:
            self.ensure_loaded()

    # --- submit / stop ---

    def submit(self, text: str, *, source: str = "manual", event: str = "manual",
               session_id: str = "", cwd: str = "", lang: Optional[str] = None) -> SpeakRequest:
        snap = TtsSnapshot.from_config(self.cfg, self._corrector_available())
        with self._lock:
            self._seq += 1
            req = SpeakRequest(self._seq, source, event, session_id, cwd, lang, text or "", snap)
        if not snap.enabled:
            req.conclude("disabled", "read-back disabled in tray")
            return req
        if event not in ("Stop", "manual") and not snap.speak_subagents:
            req.conclude("ignored_event", f"{event} not enabled")
            return req
        if not (text or "").strip():
            req.conclude("empty")
            return req
        if snap.queue_policy == "latest":
            self.stop(reason=f"superseded by id={req.id}", outcome="superseded")
        self.ensure_loaded()
        self._q.put(req)
        return req

    def stop(self, reason: str = "stop requested", outcome: str = "stopped") -> None:
        """Instant: flag current, abort audio, drop queued requests. Any thread."""
        drained: list[SpeakRequest] = []
        while True:
            try:
                drained.append(self._q.get_nowait())
            except queue.Empty:
                break
        for r in drained:
            r.conclude(outcome, reason)
        with self._lock:
            cur = self._current
        if cur is not None:
            cur.stop_outcome, cur.stop_reason = outcome, reason
            cur.stop_event.set()
            self.player.stop()
            log.info("speak id=%d interrupted: %s", cur.id, reason)

    def shutdown(self) -> None:
        self.stop("shutdown")
        self._q.put(None)
        self._thread.join(timeout=5)
        self.player.close()
        self.backend.shutdown()

    # --- worker ---

    def _loop(self) -> None:
        while True:
            req = self._q.get()
            if req is None:
                return
            with self._lock:
                self._current = req
            try:
                self._speak(req)
            except Exception as e:
                log.exception("speak id=%d failed", req.id)
                req.conclude("error", f"{type(e).__name__}: {e}")
            finally:
                with self._lock:
                    self._current = None
                self.player.close()
                self._on_speaking(False)

    def _speak(self, req: SpeakRequest) -> None:
        snap = req.snapshot
        if req.stop_event.is_set():
            req.conclude(req.stop_outcome, req.stop_reason)
            return
        prepared = text_prep.prepare(req.raw_text, strip_code=snap.strip_code,
                                     read_inline_code=snap.read_inline_code, lang=req.lang)
        req.clean_chars = prepared.clean_chars
        req.sentences = len(prepared.sentences)
        req.en_spans = prepared.en_spans
        st = prepared.stats
        log.info("speak id=%d prepared: lang=%s sentences=%d en_spans=%d dict_hits=%d "
                 "stripped(code=%d tables=%d urls=%d links=%d inline=%d headings=%d)",
                 req.id, prepared.detected_lang, len(prepared.sentences), prepared.en_spans,
                 prepared.dictionary_hits, st.code_blocks, st.tables, st.urls, st.links,
                 st.inline_code, st.headings)
        if prepared.is_empty():
            req.conclude("empty", "nothing speakable after stripping")
            return

        # optional summary (long answers only)
        if snap.summarize and self._summarize is not None:
            if prepared.clean_chars > snap.summary_threshold:
                t0 = time.perf_counter()
                summary = self._summarize(prepared.clean_text, snap.summary_timeout_s, prepared.detected_lang)
                req.summary_ms = int((time.perf_counter() - t0) * 1000)
                if req.stop_event.is_set():
                    req.conclude(req.stop_outcome, req.stop_reason)
                    return
                if summary:
                    req.summarised = True
                    prepared = text_prep.prepare(summary, strip_code=snap.strip_code,
                                                 read_inline_code=snap.read_inline_code,
                                                 lang=prepared.detected_lang)
                    req.sentences = len(prepared.sentences)
                    log.info("speak id=%d summarised %d -> %d chars in %d ms", req.id,
                             req.clean_chars, prepared.clean_chars, req.summary_ms)
                else:
                    log.info("speak id=%d summary unavailable — reading full text", req.id)
            else:
                log.debug("speak id=%d summary skipped: %d chars <= threshold %d", req.id,
                          prepared.clean_chars, snap.summary_threshold)

        # engine
        self.ensure_loaded(block=True)
        if self.backend.state != "ready":
            req.conclude("error", self.backend.reason or "engine not ready")
            return
        if req.stop_event.is_set():
            req.conclude(req.stop_outcome, req.stop_reason)
            return
        req.voice = snap.voice_pl if prepared.detected_lang == "pl" else snap.voice_en
        sr = self.backend.sample_rate
        opened = False
        for sentence in prepared.sentences:
            if req.stop_event.is_set():
                break
            for pcm in self._synthesize_sentence(sentence, prepared.detected_lang, snap):
                if req.stop_event.is_set():
                    break
                if not opened:
                    self.player.open(sr)
                    opened = True
                    req.first_audio_ms = int((time.monotonic() - req.received_at) * 1000)
                    self._on_speaking(True)
                if not self.player.write(pcm, sr):
                    break
                req.audio_s += len(pcm) / sr
            if req.stop_event.is_set():
                break
            if opened:
                self.player.silence(SENTENCE_GAP_S, sr)
        if req.stop_event.is_set():
            req.conclude(req.stop_outcome, req.stop_reason)
        elif not opened:
            req.conclude("empty", "engine produced no audio")
        else:
            req.conclude("spoken")

    def _synthesize_sentence(self, spans: list[text_prep.Span], lang: str, snap: TtsSnapshot) -> Iterator[np.ndarray]:
        """Code-switching per research R4: inject EN phonemes into the PL voice, or splice voices."""
        if lang == "en" or all(s.lang == "pl" for s in spans):
            text = " ".join(s.text for s in spans)
            yield from self.backend.synthesize(text, lang, snap.speed)
            return
        if snap.codeswitch == "splice":
            for s in spans:
                yield from self.backend.synthesize(s.text, s.lang, snap.speed)
            return
        parts: list[str] = []
        for s in spans:
            if s.lang == "pl" or "[[" in s.text:
                parts.append(s.text)
                continue
            try:
                ph = self.backend.phonemize(s.text, "en")
            except EngineUnavailable:
                ph = ""
            parts.append(f"[[ {ph} ]]" if ph else s.text)
        yield from self.backend.synthesize(" ".join(parts), "pl", snap.speed)
