"""Self-check for the pipelined download→process backpressure gate (_ReadyBudget).
Run inside the venv:  .venv\\Scripts\\python test_pipeline_overlap.py
Exercises real code paths (real files, real threads) — no SNAP needed."""
import os, sys, tempfile, threading, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
from s1_pipeline_ui import _ReadyBudget, _SAFE_EST


def _mkzip(d, name, mb):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(b"\0" * (mb * 1024 * 1024))
    return p


def test_footprint_and_chunking():
    with tempfile.TemporaryDirectory() as d:
        z1 = _mkzip(d, "a.zip", 1)   # 1 MB zip -> 1.7 MB footprint
        z2 = _mkzip(d, "b.zip", 1)
        g = _ReadyBudget(budget_bytes=10 * 1024**3, stop_ev=None)
        g.seed(z1); g.seed(z2)
        # each ~1.7MB; a 2.5MB chunk budget fits exactly one, second stays queued
        chunk = g.take_chunk(2.5 * 1024**2)
        assert len(chunk) == 1, f"expected 1 (budget fits one), got {len(chunk)}"
        # oversized single scene still moves (>=1 guarantee)
        chunk2 = g.take_chunk(0.1 * 1024**2)
        assert len(chunk2) == 1, "a lone oversized product must still be returned"
        g.done()   # empty + finished -> take_chunk returns [] instead of blocking
        assert not g.take_chunk(9e9), "queue should now be drained"
        # footprint ~= size * _SAFE_EST
        fp = _ReadyBudget._footprint(z1)
        assert abs(fp - 1024**2 * _SAFE_EST) < 1024, f"footprint off: {fp}"
    print("  ok: footprint + chunk budget + >=1 guarantee")


def test_dedup():
    with tempfile.TemporaryDirectory() as d:
        z = _mkzip(d, "a.zip", 1)
        g = _ReadyBudget(1 * 1024**3, None)
        g.seed(z); g.seed(z); g.add(z)      # same path 3x
        chunk = g.take_chunk(9e9)
        assert len(chunk) == 1, f"dedup failed: {len(chunk)}"
    print("  ok: same product added twice is de-duplicated")


def test_backpressure_blocks_and_frees():
    with tempfile.TemporaryDirectory() as d:
        # budget holds ~1.5 products: the 1st add fits, the 2nd is over budget
        g = _ReadyBudget(int(1024**2 * _SAFE_EST * 1.5), None)
        z1 = _mkzip(d, "a.zip", 1)
        z2 = _mkzip(d, "b.zip", 1)
        released = threading.Event()

        def producer():
            g.add(z1)          # fits budget, returns
            g.add(z2)          # over budget -> BLOCKS here until consumer frees
            released.set()

        t = threading.Thread(target=producer, daemon=True)
        t.start()
        time.sleep(0.5)
        assert not released.is_set(), "producer should be blocked on a full budget"
        # consumer takes one product and frees its footprint -> unblocks producer
        chunk = g.take_chunk(9e9)
        g.free(sum(fp for _, fp in chunk))
        t.join(timeout=3)
        assert released.is_set(), "producer did not resume after free()"
    print("  ok: add() blocks when full, free() releases it")


def test_stop_unblocks_producer():
    with tempfile.TemporaryDirectory() as d:
        stop = threading.Event()
        g = _ReadyBudget(int(1024**2 * _SAFE_EST), stop)
        z1 = _mkzip(d, "a.zip", 1); z2 = _mkzip(d, "b.zip", 1)
        done = threading.Event()

        def producer():
            g.add(z1); g.add(z2)   # 2nd blocks
            done.set()

        t = threading.Thread(target=producer, daemon=True); t.start()
        time.sleep(0.5)
        assert not done.is_set()
        stop.set()                  # user pressed Stop -> blocked add() must return
        t.join(timeout=3)
        assert done.is_set(), "Stop did not release the blocked producer"
    print("  ok: Stop releases a producer blocked on backpressure")


def test_done_drains_consumer():
    g = _ReadyBudget(1 * 1024**3, None)
    got = []

    def consumer():
        while True:
            c = g.take_chunk(9e9)
            if not c:
                break
            got.extend(c)

    t = threading.Thread(target=consumer, daemon=True); t.start()
    time.sleep(0.3)
    g.done()                        # producer finished, nothing queued
    t.join(timeout=3)
    assert not t.is_alive(), "consumer should exit once done() and drained"
    print("  ok: done() lets an idle consumer exit")


if __name__ == "__main__":
    test_footprint_and_chunking()
    test_dedup()
    test_backpressure_blocks_and_frees()
    test_stop_unblocks_producer()
    test_done_drains_consumer()
    print("ALL PASS")
