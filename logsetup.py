r"""Rotating file logging in %APPDATA%\VoiceCommander2\logs."""
import logging
import logging.handlers
import sys

from config import LOG_DIR


def setup() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = logging.handlers.RotatingFileHandler(
        LOG_DIR / "voicecommander2.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)

    # Console handler helps when run with python.exe; harmless under pythonw.
    if sys.stderr is not None:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(ch)
    return root
