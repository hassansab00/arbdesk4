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
