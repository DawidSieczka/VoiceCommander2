"""Piper TTS worker process — the ONLY module that imports `piper` (GPL-3.0).

Run by tts.PiperWorkerBackend as a subprocess; talks over stdio pipes so the
MIT-licensed application and the GPL engine stay separate programs
(research R3). Stdout carries binary frames, stderr carries the worker's log.

Request (one JSON object per line on stdin):
  {"id": int, "op": "load",      "voices": {"pl": "<path.onnx>", "en": "<path.onnx>"}}
  {"id": int, "op": "synth",     "text": str, "lang": "pl"|"en", "length_scale": float, "volume": float}
  {"id": int, "op": "phonemize", "text": str, "lang": "pl"|"en"}
  {"id": int, "op": "ping"}
  {"op": "quit"}

Response frames on stdout:  <u32 big-endian length> <1 byte type> <payload>
  type b"A": payload = <u32 id> <u32 sample_rate> <int16 little-endian PCM>   (one per sentence)
  type b"J": payload = UTF-8 JSON {"id": int, "event": "loaded"|"done"|"error"|"phonemes"|"pong", ...}

A "synth" request ends with exactly one "done" (or "error") event after its
audio frames. Requests are served strictly in order on one thread.
"""
from __future__ import annotations

import json
import struct
import sys
import time
import traceback

_out = sys.stdout.buffer
_in = sys.stdin.buffer


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _frame(kind: bytes, payload: bytes) -> None:
    _out.write(struct.pack(">I", len(payload) + 1) + kind + payload)
    _out.flush()


def _event(req_id: int, event: str, **extra) -> None:
    body = {"id": req_id, "event": event}
    body.update(extra)
    _frame(b"J", json.dumps(body, ensure_ascii=False).encode("utf-8"))


def main() -> int:
    voices: dict[str, object] = {}
    piper_mod = None
    for raw in _in:
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            _log("worker: bad request line")
            continue
        op = req.get("op")
        req_id = int(req.get("id", 0))
        if op == "quit":
            return 0
        try:
            if op == "ping":
                _event(req_id, "pong")
            elif op == "load":
                t0 = time.perf_counter()
                if piper_mod is None:
                    import piper as piper_mod  # noqa: F811 — deferred: 4-5 s cold import
                from piper import PiperVoice
                voices.clear()
                loaded = {}
                for lang, path in (req.get("voices") or {}).items():
                    t1 = time.perf_counter()
                    v = PiperVoice.load(path)
                    voices[lang] = v
                    loaded[lang] = {"sample_rate": int(v.config.sample_rate),
                                    "load_s": round(time.perf_counter() - t1, 2)}
                _event(req_id, "loaded", voices=loaded, total_s=round(time.perf_counter() - t0, 2))
            elif op == "synth":
                from piper import SynthesisConfig
                lang = req.get("lang", "pl")
                v = voices.get(lang) or voices.get("pl") or next(iter(voices.values()), None)
                if v is None:
                    _event(req_id, "error", reason="no voice loaded")
                    continue
                syn = SynthesisConfig(length_scale=float(req.get("length_scale", 1.0)),
                                      volume=float(req.get("volume", 1.0)))
                t0 = time.perf_counter()
                n = 0
                samples = 0
                for chunk in v.synthesize(req.get("text", ""), syn):
                    pcm = chunk.audio_int16_bytes
                    _frame(b"A", struct.pack(">II", req_id, int(chunk.sample_rate)) + pcm)
                    n += 1
                    samples += len(pcm) // 2
                _event(req_id, "done", chunks=n, samples=samples,
                       synth_ms=int((time.perf_counter() - t0) * 1000))
            elif op == "phonemize":
                lang = req.get("lang", "en")
                v = voices.get(lang)
                if v is None:
                    _event(req_id, "error", reason=f"no {lang} voice loaded")
                    continue
                phon = v.phonemize(req.get("text", ""))
                _event(req_id, "phonemes", phonemes=["".join(p) for p in phon])
            else:
                _event(req_id, "error", reason=f"unknown op {op!r}")
        except Exception as e:  # never die on a bad request; the supervisor logs the reason
            _log("worker: " + traceback.format_exc())
            try:
                _event(req_id, "error", reason=f"{type(e).__name__}: {e}")
            except Exception:
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
