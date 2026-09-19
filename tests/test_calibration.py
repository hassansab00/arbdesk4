"""The calibration fit, checked against distortions whose answer is known.

A calibration map is the most dangerous thing on the desk if it is wrong: it
silently rescales every probability, and a bad map looks exactly like a good
one from the outside. So the tests here do not check that the code runs - they
inject a KNOWN distortion and require the fit to recover it, and they check
the two things the previous version got wrong.

IT WAS FITTED AND SCORED ON THE SAME ROWS. Brier 0.074160 -> 0.073713, in
sample, and `applies` rested on that number. Adding a parameter always lowers
the error on the data it was fitted to; there is a test below that this can no
longer set applies.

AND THE ROWS WERE NOT INDEPENDENT. 3,702 "samples" were about eleven mutually
exclusive bands from each of 340 city-days across 8 settlement dates, with
exactly one band winning per ladder by construction. The unit of evidence is
the LADDER and the unit of independence is closer to the DATE.
"""
import math
import random

import pytest

import calibration as cal


def ladder(n_bands, winner, peak, seed=None):
    """One ladder: n_bands probabilities summing to 1, with `peak` on the
    highest and the rest spread evenly, and `winner` the band that settled."""
    rest = (1.0 - peak) / (n_bands - 1)
    ps = [peak] + [rest] * (n_bands - 1)
    return [(p, 1 if i == winner else 0) for i, p in enumerate(ps)]


def book(n, *, hit_rate, n_bands=11, peak=0.5, seed=3):
    """n ladders whose top band carries `peak` probability and actually wins
    `hit_rate` of the time. peak > hit_rate is an overconfident book."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        winner = 0 if rng.random() < hit_rate else rng.randrange(1, n_bands)
        out.append(ladder(n_bands, winner, peak))
    return out


# ---------------------------------------------------------------------------
# the scaling itself
# ---------------------------------------------------------------------------
def test_tempering_keeps_the_ladder_a_distribution():
    """The property the desk prices against. A ladder that does not sum to one
    makes every edge, EV and Kelly size a quantity computed on something that
    is not a distribution."""
    ps = [0.5, 0.2, 0.15, 0.1, 0.05]
    for T in (0.4, 1.0, 1.0, 3.0, 12.0):
        assert sum(cal.temper(ps, T)) == pytest.approx(1.0)


def test_T_above_one_flattens_and_below_one_sharpens():
    ps = [0.60, 0.25, 0.10, 0.05]
    flat = cal.temper(ps, 3.0)
    sharp = cal.temper(ps, 0.4)
    assert flat[0] < ps[0] and flat[-1] > ps[-1], "T>1 must move mass toward uniform"
    assert sharp[0] > ps[0] and sharp[-1] < ps[-1], "T<1 must concentrate it"


def test_T_of_one_changes_nothing():
    ps = [0.6, 0.25, 0.1, 0.05]
    assert cal.temper(ps, 1.0) == pytest.approx(ps)


# ---------------------------------------------------------------------------
# the fit recovers a known distortion
# ---------------------------------------------------------------------------
def test_the_fit_flattens_an_overconfident_book():
    """Top band priced at 50% and winning 25% of the time."""
    T = cal.fit_temperature(book(600, hit_rate=0.25, peak=0.50))
    assert T > 1.05, f"T={T}: an overconfident ladder must be flattened"


def test_the_fit_sharpens_an_underconfident_book():
    T = cal.fit_temperature(book(600, hit_rate=0.70, peak=0.30))
    assert T < 0.95, f"T={T}: an underconfident ladder must be sharpened"


def test_a_well_calibrated_book_is_left_alone():
    T = cal.fit_temperature(book(600, hit_rate=0.50, peak=0.50))
    assert 0.9 < T < 1.1, f"T={T}: nothing to correct here"


def test_the_description_names_the_direction():
    assert "OVERCONFIDENT" in cal.describe(1.6)
    assert "UNDERCONFIDENT" in cal.describe(0.6)
    assert "well scaled" in cal.describe(1.0)


# ---------------------------------------------------------------------------
# the score is per LADDER, not per band
# ---------------------------------------------------------------------------
def test_the_brier_is_summed_over_the_ladder():
    """Per band, the same error is divided by eleven and every model looks
    eleven times better calibrated than it is."""
    lad = ladder(11, winner=0, peak=0.5)
    per_ladder = cal.multiclass_brier([lad])
    per_band = sum((p - y) ** 2 for p, y in lad) / len(lad)
    assert per_ladder == pytest.approx(per_band * len(lad))


def test_a_perfect_ladder_scores_zero():
    lad = [(1.0, 1)] + [(0.0, 0)] * 10
    assert cal.multiclass_brier([lad]) == pytest.approx(0.0, abs=1e-6)
    assert cal.multiclass_log_loss([lad]) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# what counts as a ladder, and what counts as evidence
# ---------------------------------------------------------------------------
def _rows(dates, cities, n_bands=11, winner=0):
    out = []
    for d in dates:
        for c in cities:
            for i in range(n_bands):
                out.append({"city_key": c, "for_date": d, "band_id": f"{c}{d}{i}",
                            "model_prob": 0.5 if i == 0 else 0.05,
                            "settled_yes": i == winner})
    return out


def test_a_fragment_is_not_a_ladder():
    """Normalising a partial ladder produces numbers that do not describe the
    outcome space they are scored against."""
    rows = _rows(["2026-09-01"], ["london"], n_bands=4)
    ladders, dropped = cal.build_ladders(rows)
    assert ladders == {} and dropped == 1


def test_a_day_with_two_winners_is_not_a_ladder():
    rows = _rows(["2026-09-01"], ["london"])
    rows[1]["settled_yes"] = True
    ladders, dropped = cal.build_ladders(rows)
    assert ladders == {} and dropped == 1


def test_the_split_is_by_date_in_time_order():
    """No shuffle, and no city-day on both sides: a ladder is ONE observation
    and its eleven bands are not eleven."""
    dates = [f"2026-09-{d:02d}" for d in range(1, 11)]
    ladders, _ = cal.build_ladders(_rows(dates, ["london", "paris"]))
    train, val, train_dates, val_dates = cal.split_by_date(ladders)
    assert train_dates == dates[:7] and val_dates == dates[7:]
    assert not (set(train_dates) & set(val_dates))
    assert len(train) == 14 and len(val) == 6


def test_one_date_cannot_be_split_at_all():
    ladders, _ = cal.build_ladders(_rows(["2026-09-01"], ["london", "paris"]))
    train, val, _, _ = cal.split_by_date(ladders)
    assert train == [] and val == []


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------
def test_an_in_sample_improvement_cannot_set_applies():
    """THE BUG THIS PHASE EXISTS FOR. The previous version's `applies` rested
    on the training score, which a fitted parameter always improves."""
    src = open(cal.__file__).read()
    body = src[src.index("    brier_gain ="):src.index("    payload = {")]
    assert "before_br" in body and "after_br" in body
    # both metrics in the verdict are computed on `val`, never on `train`
    scoring = src[src.index("    before_br ="):src.index("    if train and val:")]
    assert "multiclass_brier(val)" in scoring and "multiclass_brier(val, T)" in scoring
    assert "multiclass_brier(train" not in src


def test_the_gate_names_both_halves():
    src = open(cal.__file__).read()
    assert "MIN_SETTLEMENT_DATES = 30" in src
    assert "MIN_COMPLETE_LADDERS = 300" in src


def test_the_market_brier_is_context_and_never_a_verdict():
    """0.003819 against 0.074 looks decisive and is not like-for-like: the two
    sides were not frozen at the same cutoff, and one is per band while the
    other is per ladder."""
    src = open(cal.__file__).read()
    assert "context only" in src
    verdict = src[src.index("    validated = bool("):src.index("    if gate:")]
    assert "mkt" not in verdict


# ---------------------------------------------------------------------------
# the engine side
# ---------------------------------------------------------------------------
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


def test_a_temperature_map_is_applied(monkeypatch):
    _map(monkeypatch, {"method": "temperature", "T": 2.0, "applies": True,
                       "complete_ladders": 400,
                       "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE})
    # p^(1/T), unnormalised - the division by the ladder's sum is the second
    # half of the formula and happens once, where the ladder exists.
    assert pe._calibrate(0.25) == pytest.approx(0.25 ** 0.5)


def test_a_temperature_map_below_the_gate_is_not_applied(monkeypatch):
    """applies=false is what an unmet gate or a failed validation writes.
    Honouring it is the difference between a correction and a superstition."""
    _map(monkeypatch, {"method": "temperature", "T": 2.0, "applies": False,
                       "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE})
    assert pe._calibrate(0.25) == 0.25


def test_tempering_a_whole_ladder_still_sums_to_one(monkeypatch):
    """The engine divides by the ladder's sum after calibrating each band, and
    for temperature scaling that division IS the formula rather than a repair."""
    _map(monkeypatch, {"method": "temperature", "T": 2.5, "applies": True,
                       "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE})
    ps = [0.5, 0.2, 0.15, 0.1, 0.05]
    qs = [pe._calibrate(p) for p in ps]
    total = sum(qs)
    assert sum(q / total for q in qs) == pytest.approx(1.0)
    # ...and it matches what the fitter computed for the same T
    assert [q / total for q in qs] == pytest.approx(cal.temper(ps, 2.5))


def test_a_map_from_another_evidence_scope_is_ignored(monkeypatch):
    """The scope names what the outcomes were verified against. A map fitted
    on something else is a map of a different world."""
    _map(monkeypatch, {"method": "temperature", "T": 2.0, "applies": True,
                       "evidence_scope": "running_max_guess"})
    assert pe._calibrate(0.25) == 0.25
