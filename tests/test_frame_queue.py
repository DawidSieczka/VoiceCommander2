"""T010: the PortAudio callback only enqueues; all analysis runs on the
audio-worker thread, in order (FR-013)."""
from tests.conftest import base_cfg, frame, wait_until


def test_frames_dispatched_in_order_on_worker_thread(make_pipeline):
    p, stt, _, _ = make_pipeline(base_cfg(mode="per_sentence"))
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    for i in range(20):
        p._on_frame(frame(i))          # simulates the PortAudio callback thread
    assert wait_until(lambda: len(p._segmenter.fed) == 20)
    values = [f[0][0] for f in p._segmenter.fed]
    assert values == list(range(20))   # order preserved
    threads = {f[1] for f in p._segmenter.fed}
    assert threads == {"audio-worker"}  # never the caller's thread


def test_frames_before_press_and_after_release_are_dropped(make_pipeline):
    p, stt, _, _ = make_pipeline(base_cfg(mode="per_sentence"))
    p._on_frame(frame(1))              # mic_always_open flow while idle
    p.ptt_pressed()
    assert wait_until(lambda: p._cur is not None)
    p._on_frame(frame(2))
    p.ptt_released()
    assert wait_until(lambda: p._cur is not None and not p._recording)
    p._on_frame(frame(3))
    assert wait_until(lambda: len(p._segmenter.fed) == 1)
    assert p._segmenter.fed[0][0][0] == 2


def test_segmenter_reset_on_press(make_pipeline):
    p, *_ = make_pipeline(base_cfg(mode="per_sentence"))
    p.ptt_pressed()
    assert wait_until(lambda: p._segmenter.resets == 1)
