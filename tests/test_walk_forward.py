"""The shared forward-only evaluation (plan v2 P3.4): folds, bucket score, gate."""
import pytest

import walk_forward as w


def test_no_test_block_ever_trains_on_its_own_future():
    for n in range(2, 40):
        for start, stop in w.walk_forward_folds(n, 4):
            assert 0 < start < stop <= n


def test_the_fahrenheit_ladder_pays_on_even_two_degree_buckets():
    bands = w.ladder_around("F", 21.5)          # 70.7 F
    closed = [b for b in bands if not b["open_low"] and not b["open_high"]]
    assert all(b["band_hi"] - b["band_lo"] == 2 and b["band_lo"] % 2 == 0 for b in closed)
    assert any(b["band_lo"] == 70 for b in closed), "70.7 F reads 71, which is in 70-71"


def test_the_celsius_ladder_pays_on_single_degrees():
    bands = w.ladder_around("C", 22.4)
    closed = [b for b in bands if not b["open_low"] and not b["open_high"]]
    assert all(b["band_hi"] - b["band_lo"] == 1 for b in closed)


def test_a_sharp_right_call_scores_near_zero_and_a_sharp_wrong_one_does_not():
    right = w.bucket_scores(22.0, 0.2, "C", 22.1)
    wrong = w.bucket_scores(25.0, 0.2, "C", 22.1)
    assert right[0] < 0.05 and right[3] is True
    assert wrong[0] > 2.0 and wrong[3] is False


def test_one_bucket_off_scores_better_than_five():
    near = w.bucket_scores(23.0, 0.3, "C", 22.0)[0]
    far = w.bucket_scores(27.0, 0.3, "C", 22.0)[0]
    assert near < far


def test_the_score_is_on_the_published_ladder_floor_atom_included():
    """A day already at 25.0 C: a forecast of 24 puts its atom on the 25 bucket,
    so the settled 25 is scored as a hit, not as a miss one bucket low."""
    rps, _ll, p_win, hit = w.bucket_scores(24.0, 0.5, "C", 25.2, floor_c=25.0)
    assert hit and p_win > 0.8


def test_the_gate_needs_twenty_days():
    g = w.gate([0.1] * 19)
    assert g["applied"] is False and "needs 20" in g["reason"]


def test_the_gate_needs_the_whole_interval_above_zero():
    assert w.gate([0.05, 0.06, 0.04, 0.05] * 6)["applied"] is True
    noisy = [0.5, -0.45] * 12
    g = w.gate(noisy)
    assert g["applied"] is False and "stays in shadow" in g["reason"]


def test_one_date_is_one_piece_of_evidence():
    assert w.per_day([("2026-09-01", 1.0), ("2026-09-01", 3.0), ("2026-09-02", 0.0)]) == [2.0, 0.0]
