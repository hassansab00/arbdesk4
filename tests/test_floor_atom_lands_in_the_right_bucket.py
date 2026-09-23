"""The floor atom lands on the bucket the venue will read (plan v2 P3.1).

22 Sep: London had read 25.0 C and the model put 100% on the 24 C bucket, and
an observed 80 F put 0.711 on 78-79 F. The floor sat on a bucket split, so the
atom - every draw below it - fell into the bucket below the day's. In 12 of 48
same-day cities the top pick was a bucket the engine itself called impossible.

The model now: R = venue_round(running max), b_R holds R, the final maximum is
max(R, X); b_R gets the atom A = P(X settles at or below b_R), buckets above
keep their Normal mass, buckets below get nothing - except q_down * A one
bucket below and q_up * A one above, the measured chance the venue's
thermometer reads one bucket off ours (P2.3).
"""
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import probability_engine as pe  # noqa: E402

Q_DOWN, Q_UP = pe.DEFAULT_Q_DOWN, pe.DEFAULT_Q_UP


def celsius_ladder():
    return [
        {"band_id": "<=23", "band_lo": None, "band_hi": 24, "open_low": True, "open_high": False},
        {"band_id": "24", "band_lo": 24, "band_hi": 25, "open_low": False, "open_high": False},
        {"band_id": "25", "band_lo": 25, "band_hi": 26, "open_low": False, "open_high": False},
        {"band_id": "26", "band_lo": 26, "band_hi": 27, "open_low": False, "open_high": False},
        {"band_id": ">=27", "band_lo": 27, "band_hi": None, "open_low": False, "open_high": True},
    ]


def fahrenheit_ladder():
    return [
        {"band_id": "<=75", "band_lo": None, "band_hi": 76, "open_low": True, "open_high": False},
        {"band_id": "76-77", "band_lo": 76, "band_hi": 78, "open_low": False, "open_high": False},
        {"band_id": "78-79", "band_lo": 78, "band_hi": 80, "open_low": False, "open_high": False},
        {"band_id": "80-81", "band_lo": 80, "band_hi": 82, "open_low": False, "open_high": False},
        {"band_id": "82-83", "band_lo": 82, "band_hi": 84, "open_low": False, "open_high": False},
        {"band_id": ">=84", "band_lo": 84, "band_hi": None, "open_low": False, "open_high": True},
    ]


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def probs(centre, sigma, unit, ladder, floor, q_down=Q_DOWN, q_up=Q_UP):
    return dict(pe.compute_band_probabilities(centre, sigma, unit, ladder, floor_c=floor,
                                              q_down=q_down, q_up=q_up))


def test_the_london_case():
    """R = 25.0 C, mu = 24.0, sigma = 0.5: the day is in the 25 bucket."""
    p = probs(24.0, 0.5, "C", celsius_ladder(), 25.0)
    assert p["24"] <= Q_DOWN + 1e-6, f"the 24 C bucket kept {p['24']:.4f} of a day already at 25"
    assert p["25"] >= 0.80
    assert p["<=23"] == 0.0


def test_the_same_in_fahrenheit():
    """R = 80 F: 78-79 F keeps at most the measurement share, not 0.711."""
    p = probs(f_to_c(79.0), 1.0, "F", fahrenheit_ladder(), f_to_c(80.0))
    assert p["78-79"] <= Q_DOWN + 1e-9
    assert p["76-77"] == 0.0 and p["<=75"] == 0.0


def test_a_reading_above_the_closed_top_goes_to_the_open_high_tail():
    p = probs(24.0, 1.0, "C", celsius_ladder(), 31.2, q_down=0.0, q_up=0.0)
    assert p[">=27"] == pytest.approx(1.0)
    p = probs(24.0, 1.0, "C", celsius_ladder(), 31.2)
    assert p[">=27"] >= 1.0 - Q_DOWN - 1e-9, "the tail lost more than the measurement share"


def test_the_venue_reads_whole_degrees_half_up():
    """venue_round is sql/ad4_82's: PostgreSQL round() on a numeric."""
    assert pe.venue_round(24.5, "C") == 25
    assert pe.venue_round(24.49, "C") == 24
    assert pe.venue_round(-2.5, "C") == -3
    assert pe.venue_round(f_to_c(80.0), "F") == 80
    assert pe.venue_round(22.2, "F") == 72          # 71.96 F


@pytest.mark.parametrize("seed", range(40))
def test_invariants_hold_for_any_day(seed):
    """Any centre, width, reading and unit: the ladder sums to 1 and a bucket
    the engine calls impossible holds nothing."""
    rng = random.Random(seed)
    unit = rng.choice(["C", "F"])
    ladder = celsius_ladder() if unit == "C" else fahrenheit_ladder()
    lo_c, hi_c = (20.0, 30.0) if unit == "C" else (f_to_c(70.0), f_to_c(90.0))
    for _ in range(25):
        mu = rng.uniform(lo_c, hi_c)
        sigma = rng.uniform(0.05, 4.0)
        floor = rng.choice([None, rng.uniform(lo_c, hi_c)])
        qd, qu = rng.uniform(0, 0.2), rng.uniform(0, 0.2)
        p = probs(mu, sigma, unit, ladder, floor, qd, qu)
        assert sum(p.values()) == pytest.approx(1.0, abs=1e-9)
        assert min(p.values()) >= 0.0
        for dead in pe.impossible_band_ids(floor, unit, ladder):
            assert p[dead] == 0.0, (unit, mu, sigma, floor, dead)


def test_the_tolerance_is_gone():
    assert not hasattr(pe, "OBSERVED_FLOOR_TOLERANCE_C"), (
        "the 0.5 C tolerance is what put the atom one bucket low; it may not move mass again")
