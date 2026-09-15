"""Hallucination filters in Transcriber.transcribe() (stt.py).

Regression for the 2026-09-15 log analysis: no_speech_prob is one value per
30 s window, so a lone `no_speech_prob > 0.6` rule dropped whole chunks of
correctly recognised speech (medium model, ~15% of chunks). The rule must be
compound (no_speech_prob AND weak avg_logprob), as in faster-whisper itself.
"""
from dataclasses import dataclass

import numpy as np

from stt import Transcriber
from tests.conftest import base_cfg

AUDIO = np.zeros(16000, dtype=np.int16)


@dataclass
class Seg:
    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = -0.3


class FakeModel:
    def __init__(self, segs):
        self.segs = segs

    def transcribe(self, samples, **kwargs):
        return iter(self.segs), None


def make(segs) -> Transcriber:
    t = Transcriber(base_cfg(language="pl"))
    t._model = FakeModel(segs)
    return t


def test_high_no_speech_prob_alone_is_kept():
    # Real Polish speech observed with no_speech_prob 0.6-0.9 and a confident decoder.
    t = make([Seg("Trzeba ją poprawić", no_speech_prob=0.71, avg_logprob=-0.25),
              Seg("między innymi strzałki", no_speech_prob=0.71, avg_logprob=-0.40)])
    assert t.transcribe(AUDIO) == "Trzeba ją poprawić między innymi strzałki"


def test_high_no_speech_prob_with_weak_logprob_is_rejected():
    t = make([Seg("Dziękuję.", no_speech_prob=0.85, avg_logprob=-1.05)])
    assert t.transcribe(AUDIO) == ""


def test_very_low_logprob_is_rejected_regardless_of_no_speech():
    t = make([Seg("Wysokość.", no_speech_prob=0.1, avg_logprob=-1.49)])
    assert t.transcribe(AUDIO) == ""


def test_blocklist_still_applies():
    t = make([Seg("Dziękuję za uwagę.", no_speech_prob=0.1, avg_logprob=-0.2),
              Seg("Zwykłe zdanie.", no_speech_prob=0.1, avg_logprob=-0.2)])
    assert t.transcribe(AUDIO) == "Zwykłe zdanie."
