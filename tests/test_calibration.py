"""The calibration fit, checked against distortions whose answer is known.

A calibration map is the most dangerous thing on the desk if it is wrong: it
silently rescales every probability, and a bad map looks exactly like a good
one from the outside. So the tests here do not check that the code runs - they
inject a KNOWN distortion and require the fit to recover it.
"""
import math
import random

import pytest

import calibration as cal


def synth(n, distort, seed=7):
    """n samples whose true probability is uniform, with the model's STATED
    probability distorted by `distort`. Outcomes are drawn from the TRUE
    probability, so a correct fit must undo the distortion."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p_true = rng.uniform(0.02, 0.98)
        out.append((distort(p_true), 1 if rng.random() < p_true else 0))
    return out


def overconfident(p, k=1.4):
    """Push away from 0.5 in log-odds: the classic overconfident model."""
    return cal.sigmoid(k * cal.logit(p))


def underconfident(p, k=0.7):
    return cal.sigmoid(k * cal.logit(p))


def biased(p, shift=0.6):
    return cal.sigmoid(cal.logit(p) + shift)


def test_logit_and_sigmoid_round_trip():
    for p in (0.01, 0.1, 0.5, 0.9, 0.99):
        assert cal.sigmoid(cal.logit(p)) == pytest.approx(p, abs=1e-9)


def test_sigmoid_is_stable_at_extremes():
    # The naive 1/(1+exp(-z)) overflows for large negative z. It must not.
    assert 0.0 <= cal.sigmoid(-800) < 1e-300
    assert cal.sigmoid(800) == pytest.approx(1.0)


def test_fit_recovers_overconfidence():
    """A model distorted by k=1.4 must be corrected by a ~= 1/1.4 ~= 0.71."""
    a, b, _ = cal.fit_platt(synth(1500, overconfident), iters=1200, lr=0.6)
    assert a == pytest.approx(1 / 1.4, rel=0.25), f"a={a}"
    assert abs(b) < 0.25, f"b={b} should be near zero - the distortion had no bias"


def test_fit_recovers_underconfidence():
    a, _, _ = cal.fit_platt(synth(1500, underconfident), iters=1200, lr=0.6)
    assert a == pytest.approx(1 / 0.7, rel=0.3), f"a={a}"
    assert a > 1.15, "an underconfident model must be pushed outward, not pulled in"


def test_fit_finds_a_standing_bias():
    a, b, _ = cal.fit_platt(synth(1500, biased), iters=1200, lr=0.6)
    assert b < -0.3, f"a model biased toward YES must be corrected downward, got b={b}"


def test_an_already_calibrated_model_is_left_alone():
    a, b, _ = cal.fit_platt(synth(1500, lambda p: p), iters=1200, lr=0.6)
    assert a == pytest.approx(1.0, abs=0.2), f"a={a}"
    assert abs(b) < 0.2, f"b={b}"


def test_calibration_improves_the_score_it_is_fitted_on():
    s = synth(1500, overconfident)
    a, b, _ = cal.fit_platt(s, iters=1200, lr=0.6)
    after = [(cal.apply_platt(p, a, b), y) for p, y in s]
    assert cal.brier(after) < cal.brier(s)
    assert cal.log_loss(after) < cal.log_loss(s)


def test_the_description_names_the_direction():
    # The two numbers are useless to a reader; the words are the finding.
    assert "OVERCONFIDENT" in cal.describe(0.7, 0.0)
    assert "UNDERCONFIDENT" in cal.describe(1.4, 0.0)
    assert "well scaled" in cal.describe(1.0, 0.0)
    assert "YES" in cal.describe(1.0, 0.5)
    assert "NO" in cal.describe(1.0, -0.5)


def test_apply_never_leaves_the_unit_interval():
    for p in (1e-9, 0.001, 0.5, 0.999, 1 - 1e-9):
        for a, b in ((0.5, -2.0), (2.0, 3.0), (1.0, 0.0)):
            q = cal.apply_platt(p, a, b)
            assert 0.0 <= q <= 1.0, f"p={p} a={a} b={b} -> {q}"


def test_brier_and_log_loss_agree_on_a_perfect_model():
    perfect = [(1.0, 1), (0.0, 0)] * 50
    assert cal.brier(perfect) == pytest.approx(0.0)
    assert cal.log_loss(perfect) == pytest.approx(0.0, abs=1e-5)


# --------------------------------------------------------------------------
# The engine's side of it. A calibration map rescales every probability the
# desk produces, so the guards around applying one matter more than the fit.
# --------------------------------------------------------------------------
import probability_engine as pe


@pytest.fixture(autouse=True)
def _clear_cal():
    pe._calibration = None
    yield
    pe._calibration = None


def _map(monkeypatch, value):
    monkeypatch.setattr(pe, "rest", lambda *a, **k: [{"value": value}] if value is not None else [])


def test_no_map_means_no_change(monkeypatch):
    _map(monkeypatch, None)
    assert pe._calibrate(0.3) == 0.3


def test_a_map_the_fitter_rejected_is_not_applied(monkeypatch):
    """applies=false is written when calibration did not improve the score on
    its own training data. Honouring it is the difference between a correction
    and a superstition."""
    _map(monkeypatch, {"method": "platt", "a": 0.5, "b": 0.0, "applies": False})
    assert pe._calibrate(0.3) == 0.3


def test_a_fitted_map_is_applied(monkeypatch):
    _map(monkeypatch, {"method": "platt", "a": 0.7, "b": 0.0, "applies": True,
                       "n": 900, "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE})
    out = pe._calibrate(0.2)
    assert out != 0.2
    # a < 1 pulls toward 0.5, so a 20% band should come back higher
    assert 0.2 < out < 0.5, out


def test_an_unknown_method_is_ignored(monkeypatch):
    _map(monkeypatch, {"method": "isotonic", "a": 0.5, "b": 0.0, "applies": True})
    assert pe._calibrate(0.3) == 0.3


def test_a_broken_settings_read_does_not_stop_pricing(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("settings unreachable")
    monkeypatch.setattr(pe, "rest", boom)
    assert pe._calibrate(0.42) == 0.42
    assert "no calibration map" in capsys.readouterr().err


def test_the_map_is_read_once_per_run(monkeypatch):
    calls = []
    monkeypatch.setattr(pe, "rest", lambda *a, **k: (calls.append(1), [])[1])
    for _ in range(5):
        pe._calibrate(0.5)
    assert len(calls) == 1


def test_calibrated_output_stays_a_probability(monkeypatch):
    _map(monkeypatch, {"method": "platt", "a": 0.6, "b": -1.5, "applies": True,
                       "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE})
    for p in (0.0, 1e-9, 0.001, 0.5, 0.999, 1.0):
        q = pe._calibrate(p)
        assert 0.0 <= q <= 1.0, (p, q)
