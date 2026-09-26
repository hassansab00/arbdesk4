"""The belief layer (plan v2 P5.3): posterior maths, pooling, and Rule 11."""
import datetime as dt
import math

import pytest

import belief as bf

FAR = dt.date(2040, 1, 1)   # after every fixture date, so every ladder counts


def _ladders(n_days, p_top, top_wins_every, city="nyc", checkpoint="noon",
             start=dt.date(2026, 8, 1)):
    """n_days ladders of two buckets: 'top' at p_top, 'rest' at 1 - p_top.

    'top' wins on every top_wins_every-th day (so its realised frequency is
    1 / top_wins_every), 'rest' wins otherwise.
    """
    cps, outs = [], []
    for i in range(n_days):
        cid = f"{city}-{checkpoint}-{i}"
        cps.append({"checkpoint_id": cid, "city_key": city,
                    "target_date": (start + dt.timedelta(days=i)).isoformat(),
                    "checkpoint": checkpoint, "probs": {"top": p_top, "rest": 1 - p_top}})
        outs.append({"checkpoint_id": cid, "ladder_has_winner": True,
                     "winner_band_id": "top" if i % top_wins_every == 0 else "rest"})
    return cps, outs


# --------------------------------------------------------------------------
# The maths
# --------------------------------------------------------------------------

def test_a_bucket_on_a_bin_edge_belongs_to_the_bin_above():
    assert bf.bin_of(0.60) == 12 and bf.bin_of(0.5999) == 11
    assert bf.bin_of(0.0) == 0 and bf.bin_of(1.0) == bf.N_BINS - 1


def test_beta_mean_and_sd():
    mean, sd = bf.beta_mean_sd(3.0, 7.0)
    assert mean == pytest.approx(0.3)
    assert sd == pytest.approx(math.sqrt(3 * 7 / (100 * 11)))


def test_with_no_data_the_posterior_is_the_model_with_a_wide_sd():
    mean, sd, version = bf.posterior(0.6)
    assert mean == pytest.approx(0.6)
    assert sd == pytest.approx(math.sqrt(0.6 * 0.4 / (bf.K0 + 1)))
    assert version == bf.PRIOR_VERSION


def test_an_empty_table_is_still_the_prior():
    table = bf.fit([], [], dt.date(2026, 9, 1))
    assert table["n"] == 0
    assert bf.posterior(0.6, table)[0] == pytest.approx(0.6)


def test_a_heavy_history_converges_on_what_the_bin_actually_won():
    # The model says 0.60; buckets there won one day in two (0.50).
    cps, outs = _ladders(2000, 0.60, 2)
    table = bf.fit(cps, outs, FAR)
    mean, sd, _ = bf.posterior(0.60, table)
    assert mean == pytest.approx(0.50, abs=0.01)
    assert sd < 0.02, "two thousand settled buckets should leave little doubt"


def test_a_little_history_barely_moves_it():
    cps, outs = _ladders(bf.N_MIN, 0.60, 2)
    table = bf.fit(cps, outs, FAR)
    mean, _, _ = bf.posterior(0.60, table)
    # 30 prior pseudo-counts at 0.60 plus 20 buckets at 0.50.
    assert mean == pytest.approx((30 * 0.6 + 10) / 50, abs=1e-9)


def test_a_bin_under_the_minimum_sample_stays_on_the_prior():
    cps, outs = _ladders(bf.N_MIN - 1, 0.60, 2)
    table = bf.fit(cps, outs, FAR)
    assert bf.posterior(0.60, table)[0] == pytest.approx(0.60)


def test_the_posterior_is_bounded():
    # Buckets at 0.97 that won every time must still not be believed at 1.
    cps, outs = _ladders(5000, 0.97, 1)
    table = bf.fit(cps, outs, FAR)
    assert bf.posterior(0.97, table)[0] <= bf.P_HI
    assert bf.posterior(0.0, None)[0] >= bf.P_LO


def test_the_ladder_sums_to_one():
    cps, outs = _ladders(500, 0.60, 2)
    table = bf.fit(cps, outs, FAR)
    lad = bf.ladder_posterior({"a": 0.60, "b": 0.30, "c": 0.10}, table)
    assert sum(m for m, _ in lad.values()) == pytest.approx(1.0)
    assert all(sd > 0 for _, sd in lad.values())


# --------------------------------------------------------------------------
# Partial pooling
# --------------------------------------------------------------------------

def test_a_narrow_scope_with_little_data_sits_on_the_broad_answer():
    # Plenty of noon history says 0.60 buckets win half the time; the
    # morning class has too few rows of its own to say anything.
    noon_c, noon_o = _ladders(1000, 0.60, 2, checkpoint="noon")
    morn_c, morn_o = _ladders(5, 0.60, 1, checkpoint="morning", start=dt.date(2026, 6, 1))
    table = bf.fit(noon_c + morn_c, noon_o + morn_o, FAR)
    pooled = bf.posterior(0.60, table)[0]
    morning = bf.posterior(0.60, table, checkpoint="morning")[0]
    assert morning == pytest.approx(pooled), "five rows moved a whole class off the pool"


def test_a_scope_with_its_own_record_moves_off_the_pool():
    noon_c, noon_o = _ladders(1000, 0.60, 2, checkpoint="noon")
    peak_c, peak_o = _ladders(1000, 0.60, 1, checkpoint="postpeak_1h", start=dt.date(2025, 1, 1))
    table = bf.fit(noon_c + peak_c, noon_o + peak_o, FAR)
    assert bf.posterior(0.60, table, checkpoint="postpeak_1h")[0] > \
        bf.posterior(0.60, table, checkpoint="noon")[0] + 0.2


def test_the_checkpoints_fold_into_the_plans_five_classes():
    assert {bf.checkpoint_class(c) for c in
            ("d1_eve", "morning", "noon", "prepeak_2h", "prepeak_1h", "postpeak_1h")} == \
        {"pre_day", "morning", "midday", "pre_peak", "post_peak"}
    assert bf.scopes_for("gulf", "prepeak_1h") == ["pooled", "cluster:gulf", "cluster:gulf|class:pre_peak"]
    assert bf.scopes_for(None, None) == ["pooled"]


# --------------------------------------------------------------------------
# Rule 11: walk-forward, a maximum step, a version
# --------------------------------------------------------------------------

def test_nothing_on_or_after_as_of_is_learned_from():
    cps, outs = _ladders(10, 0.60, 2, start=dt.date(2026, 9, 20))
    obs = bf.observations(cps, outs, dt.date(2026, 9, 25))
    days = {c["target_date"] for c in cps[:5]}
    assert len(obs) == 2 * 5, "only the five days before as_of, two buckets each"
    assert days == {f"2026-09-{d}" for d in range(20, 25)}


def test_a_ladder_without_the_venue_winner_is_not_evidence():
    cps, outs = _ladders(3, 0.60, 2)
    outs[0]["winner_band_id"] = "not-on-the-ladder"
    outs[1]["ladder_has_winner"] = False
    assert len(bf.observations(cps, outs, FAR)) == 2


def test_a_refit_moves_a_bin_at_most_the_maximum_step():
    first_c, first_o = _ladders(1000, 0.60, 2)                 # won 0.50
    v1 = bf.fit(first_c, first_o, FAR)
    later_c, later_o = _ladders(1000, 0.60, 1)                 # now wins every time
    v2 = bf.fit(first_c + later_c, first_o + later_o, FAR, previous=v1)
    w1, n1 = v1["bins"]["pooled"][str(bf.bin_of(0.60))]
    w2, n2 = v2["bins"]["pooled"][str(bf.bin_of(0.60))]
    assert w2 / n2 == pytest.approx((w1 / n1) * (1 + bf.MAX_STEP))
    assert n2 == 2 * n1, "the sample is kept; only its centre is held back"
    assert v2["held_back"] >= 1 and v2["previous"] == v1["version"]


def test_the_version_is_deterministic_and_recorded_on_every_posterior():
    cps, outs = _ladders(100, 0.60, 2)
    a = bf.fit(cps, outs, FAR)
    b = bf.fit(cps, outs, FAR)
    assert a["version"] == b["version"] and a["version"].startswith("belief:2040-01-01:")
    assert bf.posterior(0.6, a)[2] == a["version"]


def test_an_unreadable_parameter_table_means_the_prior(capsys):
    def boom(*a, **k):
        raise RuntimeError("relation strategy_params does not exist")
    assert bf.load(rest=boom) is None
    assert bf.load(rest=_learning_on(lambda *a, **k: [])) is None
    t = bf.load(rest=_learning_on(lambda *a, **k: [{"value": {"bins": {}}, "version": "belief:x"}]))
    assert t["version"] == "belief:x"


def _learning_on(rows_for, enabled=True):
    """A rest() whose settings row says strategy_learning.enabled, and whose
    strategy_params answer is rows_for()."""
    def rest(path, params=None, **k):
        if path == "settings":
            return [{"value": {"enabled": enabled}}]
        return rows_for(path, params)
    return rest


def test_what_was_learned_is_not_used_until_the_flag_is_on():
    """Plan v2 P5.8: until the replay shows learned values beat the priors,
    the loop ships with priors frozen and a flag to enable it."""
    stored = lambda *a, **k: [{"value": {"bins": {}}, "version": "belief:x"}]
    assert bf.load(rest=_learning_on(stored, enabled=False)) is None
    assert bf.load(rest=lambda path, params=None, **k: [] if path == "settings" else stored()) is None
    assert bf.load(rest=_learning_on(stored))["version"] == "belief:x"
