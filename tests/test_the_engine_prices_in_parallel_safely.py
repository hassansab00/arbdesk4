"""The engine prices city-days on a thread pool (plan v2 P6.1) - safely.

Measured 24 Sep 04:43Z: "Band probabilities" took 231 s for 65 city-days,
priced one after another, each a dozen round trips. Every lazily-filled cache
in the engine sets itself to {} and then fills, so a second thread arriving
mid-fill would read an empty cache and price without calibration, trajectory,
post-processing, divergence or the measurement layer, with no error. main()
fills them all serially before the pool starts; these tests hold that.
"""
import probability_engine as pe

CACHES = ("_calibration", "_measurement_cache", "_calibration_cache",
          "_trajectory_cache", "_postprocess_cache", "_divergence_cache",
          "_station_cache", "_station_width_cfg")


def test_warming_fills_every_shared_cache(monkeypatch):
    for name in CACHES:
        monkeypatch.setattr(pe, name, None)
    monkeypatch.setattr(pe, "rest", lambda *a, **k: [])
    monkeypatch.setattr(pe, "rest_all", lambda *a, **k: [])
    monkeypatch.setattr(pe, "get_cities", lambda *a, **k: [])
    pe._warm_caches()
    for name in CACHES:
        assert getattr(pe, name) is not None, f"{name} is still unfilled when the pool starts"


def test_main_warms_before_the_pool_and_keeps_submission_order():
    src = open(pe.__file__).read()
    body = src[src.index("def main():"):]
    assert body.index("_warm_caches()") < body.index("ThreadPoolExecutor(")
    assert "pool.map(" in body, "map keeps submission order; as_completed would not"


def test_every_lazy_cache_is_warmed():
    """A new lazily-filled cache must be added to _warm_caches too."""
    import re
    src = open(pe.__file__).read()
    declared = set(re.findall(r"^(_[a-z_]+) = None$", src, re.M))
    assert declared == set(CACHES), f"lazy caches changed: {sorted(declared ^ set(CACHES))}"


def test_the_version_registry_is_serialised():
    import common
    src = open(common.__file__).read()
    assert "with _version_lock:" in src


def test_a_test_fake_is_still_what_rest_talks_to(monkeypatch):
    import common
    calls = []

    class R:
        status_code = 200
        text = "[]"
        def json(self):
            return []
        def raise_for_status(self):
            pass

    monkeypatch.setattr(common, "_cfg", lambda: {"url": "https://x", "key": "k"})
    monkeypatch.setattr(common.requests, "get", lambda *a, **k: calls.append(a) or R())
    assert common.rest("t") == [] and calls, "the session bypassed the test's fake"
