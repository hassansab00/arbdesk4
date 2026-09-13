import math

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
