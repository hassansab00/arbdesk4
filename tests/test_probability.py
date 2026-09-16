import math

import pytest

import probability_engine as pe


def celsius_bands():
    """9 closed 1C-wide bands (20..28) + 2 open tails, like a C city."""
    bands = [{"band_id": "open_low", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False}]
    for lo in range(20, 29):
        bands.append({"band_id": f"b{lo}", "band_lo": lo, "band_hi": lo + 1, "open_low": False, "open_high": False})
    bands.append({"band_id": "open_high", "band_lo": 29, "band_hi": None, "open_low": False, "open_high": True})
    return bands


def fahrenheit_bands():
    """9 closed 2F-wide bands (60..78) + 2 open tails, like an F city."""
    bands = [{"band_id": "open_low", "band_lo": None, "band_hi": 60, "open_low": True, "open_high": False}]
    lo = 60
    for _ in range(9):
        bands.append({"band_id": f"b{lo}", "band_lo": lo, "band_hi": lo + 2, "open_low": False, "open_high": False})
        lo += 2
    bands.append({"band_id": "open_high", "band_lo": lo, "band_hi": None, "open_low": False, "open_high": True})
    return bands


def test_celsius_bands_sum_to_one():
    probs = pe.compute_band_probabilities(24.3, 1.5, "C", celsius_bands())
    total = sum(p for _, p in probs)
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_fahrenheit_bands_sum_to_one():
    probs = pe.compute_band_probabilities(21.1, 1.5, "F", fahrenheit_bands())
    total = sum(p for _, p in probs)
    assert math.isclose(total, 1.0, abs_tol=1e-9)


def test_lattice_concentrates_mass_in_correct_celsius_band():
    # centre exactly on integer 24, tiny sigma -> almost all mass in band [24,25)
    probs = dict(pe.compute_band_probabilities(24.0, 0.01, "C", celsius_bands()))
    assert probs["b24"] > 0.99


def test_lattice_concentrates_mass_in_correct_fahrenheit_band():
    # target settlement value F=70 -> Celsius equivalent
    target_f = 70
    centre_c = (target_f - 32) * 5.0 / 9.0
    probs = dict(pe.compute_band_probabilities(centre_c, 0.01, "F", fahrenheit_bands()))
    # F=70 falls in the [70,72) band
    assert probs["b70"] > 0.99


def test_unit_edge_c_offsets_by_half_step():
    # A naive model that integrated straight over [band_lo, band_hi) in
    # whatever unit was given would badly misplace mass for F cities, since
    # 1F of band-index width is not 1C of temperature. The lattice edge for
    # reported integer boundary N is the half-step point N-0.5, converted
    # through the city's unit.
    assert math.isclose(pe.unit_edge_c("C", 24), 23.5)
    # F=69.5 (boundary-0.5) converted to Celsius:
    expected_f_edge = (69.5 - 32.0) * 5.0 / 9.0
    assert math.isclose(pe.unit_edge_c("F", 70), expected_f_edge)
    assert not math.isclose(pe.unit_edge_c("F", 70), 69.5)  # would be wrong if unit were ignored


def test_bias_correction_shifts_which_band_wins():
    # 24.9C rounds to the nearest whole degree, 25 - the "25" bucket is
    # [24.5, 25.5), and 24.9 sits inside it.
    bands = celsius_bands()
    uncorrected = dict(pe.compute_band_probabilities(24.9, 0.05, "C", bands))
    assert uncorrected["b25"] > 0.99
    # A 1.0C station bias correction moves the corrected centre to 23.9,
    # which now rounds to 24 instead - the whole point of bias correction is
    # that it can move you to a different band, not just nudge probability
    # within the same one.
    bias_c = 1.0
    corrected = dict(pe.compute_band_probabilities(24.9 - bias_c, 0.05, "C", bands))
    assert corrected["b24"] > 0.99


def test_open_tail_gets_all_remaining_mass():
    # centre way below the closed range -> open_low should take ~all mass
    probs = dict(pe.compute_band_probabilities(-50.0, 2.0, "C", celsius_bands()))
    assert probs["open_low"] > 0.999


def test_normal_cdf_symmetry():
    assert math.isclose(pe.normal_cdf(0.0, 0.0, 1.0), 0.5)
    assert pe.normal_cdf(-1.0, 0.0, 1.0) < 0.5 < pe.normal_cdf(1.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# THE LOOP THAT WAS LEFT OPEN
#
# mae_c makes the centre right and sets a starting width. Nothing measured
# whether the resulting distribution turned out HONEST: a model can have
# excellent mae_c and still be systematically overconfident - the centre right,
# the spread too narrow - and every edge computed from a too-narrow
# distribution is overstated, so the desk sizes UP on exactly the trades it
# should size down. v_calibration measured it all along and nothing read it.
#
# sql/ad4_45 measures sd of (observed - forecast)/sigma over settled days,
# which is 1 if and only if the stated sigma was honest, and the engine
# multiplies by it.
# ---------------------------------------------------------------------------

def _engine():
    import importlib
    import probability_engine as pe
    importlib.reload(pe)
    return pe


def test_an_overconfident_city_gets_a_wider_sigma():
    pe = _engine()
    pe._calibration_cache = {
        "over": {"city_key": "over", "sigma_multiplier": 1.42, "applied": True,
                 "n_days": 180, "z_sd": 1.42, "reason": "42% too narrow",
                 "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE},
    }
    mult, row = pe._calibration_for("over")
    assert mult == 1.42 and row is not None


def test_a_city_with_no_measurement_is_untouched():
    """1.0 exactly, so behaviour is identical to before this existed."""
    pe = _engine()
    pe._calibration_cache = {}
    assert pe._calibration_for("anything") == (1.0, None)


def test_an_unapplied_row_changes_nothing():
    """The guards live in SQL - under 30 days, inside the 0.9-1.1 noise band,
    narrowing on thin evidence. The engine must honour `applied` rather than
    reading the multiplier and deciding for itself."""
    pe = _engine()
    pe._calibration_cache = {
        "thin": {"city_key": "thin", "sigma_multiplier": 2.77, "applied": False,
                 "n_days": 10, "z_sd": 2.77, "reason": "10 days",
                 "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE},
    }
    assert pe._calibration_for("thin") == (1.0, None)


def test_a_nonsense_multiplier_is_ignored():
    pe = _engine()
    for bad in (0, -1, None, "abc"):
        pe._calibration_cache = {"c": {"city_key": "c", "sigma_multiplier": bad,
                                       "applied": True, "n_days": 99,
                                       "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE}}
        assert pe._calibration_for("c")[0] == 1.0, bad


def test_legacy_calibration_adjustment_is_ignored():
    pe = _engine()
    pe._calibration_cache = {
        "legacy": {"city_key": "legacy", "sigma_multiplier": 1.8,
                   "applied": True, "n_days": 500, "evidence_scope": None},
    }
    assert pe._calibration_for("legacy") == (1.0, None)


def test_legacy_platt_map_is_ignored(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(pe, "rest", lambda *_: [{"value": {
        "method": "platt", "a": 0.5, "b": 0.1, "n": 500, "applies": True,
    }}])
    pe._calibration = None
    assert pe._calibration_map() is None


def test_verified_platt_map_is_accepted(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(pe, "rest", lambda *_: [{"value": {
        "method": "platt", "a": 0.5, "b": 0.1, "n": 500, "applies": True,
        "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE,
    }}])
    pe._calibration = None
    assert pe._calibration_map()["a"] == 0.5


def test_widening_sigma_lowers_confidence():
    """A distribution that had to be widened is one the desk was overconfident
    about, and the confidence it reports has to say so - otherwise the sizing
    layer reads a corrected number as if it had been right all along."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "scripts", "probability_engine.py")).read()
    block = src[src.index("cal_mult, cal_row = _calibration_for"):]
    block = block[: block.index("probs = compute_band_probabilities")]
    assert "sigma_historical * cal_mult" in block, "the multiplier is not applied to sigma"
    assert "confidence *= min(1.0, 1.0 / cal_mult)" in block
    assert 'reasons.append(' in block, "a price that moved must say why"


# ---------------------------------------------------------------------------
# A DAILY MAXIMUM CANNOT GO DOWN
#
# The lattice priced every band off a forecast and a width and ignored the
# thermometer. On 16 Sep at 16:00 UTC, of the 297 bands resolving that day
# across 27 cities, 28 were already physically impossible - their top was below
# the temperature their own city had recorded hours earlier - and twelve were
# still priced above 2%, the worst at 23.7%. The desk would have bought them
# at a discount it had invented.

def floor_bands():
    """Whole-degree Celsius bands 24 through 27, plus open tails.

    A closed band [lo, hi) covers the integers lo..hi-1, so [26, 27) is the
    single degree 26 and its upper split point is 26.5C.
    """
    return [
        {"band_id": "below", "band_lo": None, "band_hi": 24, "open_low": True, "open_high": False},
        {"band_id": "24", "band_lo": 24, "band_hi": 25, "open_low": False, "open_high": False},
        {"band_id": "25", "band_lo": 25, "band_hi": 26, "open_low": False, "open_high": False},
        {"band_id": "26", "band_lo": 26, "band_hi": 27, "open_low": False, "open_high": False},
        {"band_id": "27", "band_lo": 27, "band_hi": 28, "open_low": False, "open_high": False},
        {"band_id": "above", "band_lo": 28, "band_hi": None, "open_low": False, "open_high": True},
    ]


def test_a_band_the_day_has_already_passed_is_priced_at_zero():
    """27.4C is already recorded, so 24, 25 and 26 cannot happen however the
    afternoon goes."""
    probs = dict(pe.compute_band_probabilities(25.5, 2.0, "C", floor_bands(), floor_c=27.4))
    for dead in ("below", "24", "25", "26"):
        assert probs[dead] == 0.0, f"{dead} survived a maximum that has already passed it"
    assert probs["27"] > 0 and probs["above"] > 0
    assert sum(probs.values()) == pytest.approx(1.0)


def test_the_surviving_bands_still_sum_to_one():
    """27.2C clears band 26's 26.5C top edge by more than the 0.5C tolerance.
    (26.9 would NOT - see the tolerance test below. The margin is the point.)"""
    probs = dict(pe.compute_band_probabilities(25.0, 1.5, "C", floor_bands(), floor_c=27.2))
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs["26"] == 0.0


def test_no_floor_leaves_the_lattice_exactly_as_it_was():
    plain = dict(pe.compute_band_probabilities(25.5, 2.0, "C", floor_bands()))
    explicit = dict(pe.compute_band_probabilities(25.5, 2.0, "C", floor_bands(), floor_c=None))
    assert plain == explicit


def test_the_tolerance_protects_against_a_different_settlement_station():
    """Our station and the venue's can disagree by tenths. A band is only
    killed once the observed maximum has cleared its top edge by more than
    half a degree C - 26.5 is band 26's edge, so 26.8 must NOT kill it."""
    near = dict(pe.compute_band_probabilities(26.0, 1.5, "C", floor_bands(), floor_c=26.8))
    assert near["26"] > 0, "a band was zeroed on a margin smaller than the tolerance"
    clear = dict(pe.compute_band_probabilities(26.0, 1.5, "C", floor_bands(), floor_c=27.1))
    assert clear["26"] == 0.0, "a band survived a maximum well past its top edge"


def test_an_open_high_band_is_never_impossible():
    """However hot it gets, 'above 28' remains reachable."""
    probs = dict(pe.compute_band_probabilities(25.0, 1.5, "C", floor_bands(), floor_c=40.0))
    assert probs["above"] == pytest.approx(1.0)


def test_a_floor_past_the_whole_ladder_falls_back_rather_than_going_uniform():
    """If every band is impossible the ladder is wrong, the station is wrong,
    or the city is mismatched. None of those is improved by publishing a
    uniform distribution over impossibilities."""
    closed_only = [b for b in floor_bands() if not b["open_high"] and not b["open_low"]]
    probs = dict(pe.compute_band_probabilities(25.0, 1.5, "C", closed_only, floor_c=99.0))
    assert sum(probs.values()) == pytest.approx(1.0)
    assert max(probs.values()) > 1.0 / len(closed_only), (
        "it went uniform - the unfloored lattice's opinion was thrown away")


def test_fahrenheit_bands_are_floored_on_the_celsius_edge_not_the_number():
    """running_max_c is Celsius whatever the city settles in. 92-93F has its
    top split point at 93.5F = 34.17C, so 35C kills it and 34C does not."""
    bands = [{"band_id": "92-93", "band_lo": 92, "band_hi": 94, "open_low": False, "open_high": False},
             {"band_id": "94-95", "band_lo": 94, "band_hi": 96, "open_low": False, "open_high": False},
             {"band_id": "above", "band_lo": 96, "band_hi": None, "open_low": False, "open_high": True}]
    hot = dict(pe.compute_band_probabilities(34.0, 1.0, "F", bands, floor_c=35.0))
    assert hot["92-93"] == 0.0
    mild = dict(pe.compute_band_probabilities(34.0, 1.0, "F", bands, floor_c=34.0))
    assert mild["92-93"] > 0


def test_the_floor_is_recorded_so_a_zero_can_be_explained_later():
    """A band priced at zero by arithmetic and one the forecast never reached
    look identical afterwards, and the calibration fitter would learn from the
    difference."""
    import inspect
    src = inspect.getsource(pe.process_city_day)
    assert '"observed_floor_c": observed_floor_c' in src


def test_a_market_resolving_tomorrow_gets_no_floor_from_today():
    """Today's maximum says nothing about tomorrow's."""
    import inspect
    src = inspect.getsource(pe.process_city_day)
    assert "str(for_date) == local_date" in src, (
        "the floor must only apply when the market resolves on the city's own "
        "local date - a UTC date would put half the world on the wrong day")
