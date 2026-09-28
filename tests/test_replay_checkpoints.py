"""Plan v2 P7.3: the replay's scoring is the contract's (docs/S10_MAX_TEMP_WINNER.md)."""
import datetime as dt
import math

from backtest import replay_checkpoints as rc


def test_the_replay_scores_under_the_frozen_contract():
    assert rc.CONTRACT == "s10-contract-v1"
    assert "d1_eve" not in rc.CHECKPOINTS and len(rc.CHECKPOINTS) == 5
    assert rc.MIN_DAYS == 20


def test_wilson_interval():
    lo, hi = rc.wilson(50, 100)
    assert round(lo, 3) == 0.404 and round(hi, 3) == 0.596
    assert rc.wilson(0, 0) == (None, None)


def test_the_market_reads_only_mids_known_at_the_decision_time():
    mids = {"a": [(100, 0.2), (200, 0.9)], "b": [(150, 0.5)], "c": [(10, 0.3)]}
    probs, top = rc.market_probs(["a", "b", "c"], mids, 160)
    # a's 0.9 is after the decision, so a reads 0.2; c's mid is 150 s old and counts
    assert top == "b"
    assert probs is not None and math.isclose(sum(probs.values()), 1.0)
    probs, top = rc.market_probs(["a", "b", "c"], mids, 10 + rc.MARKET_MAX_AGE_S + 1)
    assert probs is None                       # c too old -> the ladder is not wholly priced
    assert rc.market_probs(["x"], mids, 160) == (None, None)


def test_a_difference_is_the_mean_over_days_like_its_interval():
    rows = ([{"target_date": "2026-09-01", "model_ll": 1.0, "proxy_ll": 2.0}] * 9
            + [{"target_date": "2026-09-02", "model_ll": 2.0, "proxy_ll": 1.0}])
    mean, n, days, _ = rc.paired(rows, "model_ll", "proxy_ll")
    assert (n, days) == (10, 2) and mean == 0.0           # days weigh equally; rows would say +0.8


def test_top_one_is_compared_on_the_same_rows():
    rows = [{"target_date": "d1", "winner": "w", "model_top": "w", "market_top": "x"},
            {"target_date": "d1", "winner": "w", "model_top": "x", "market_top": None},
            {"target_date": "d2", "winner": "w", "model_top": "x", "market_top": "w"}]
    n, km, ko, days, mean, _ = rc.paired_hits(rows, "market")
    assert (n, km, ko, days, mean) == (2, 1, 1, 2, 0.0)


def test_a_verdict_needs_twenty_days_and_an_interval_clear_of_zero():
    assert rc.verdict((0.3, 100, 19, (0.1, 0.5))).startswith("not judged")
    assert rc.verdict((0.3, 100, 20, (0.1, 0.5))).startswith("model better")
    assert rc.verdict((-0.3, 100, 25, (-0.5, -0.1))).startswith("model worse")
    assert rc.verdict((0.1, 100, 25, (-0.1, 0.3))) == "not shown"


def test_the_checkpoints_are_planned_on_the_citys_clock():
    inputs = {"peaks": [["london", 9, 15.0]],
              "markets": [["m1", "london", "2026-09-10", "w", "C", "Europe/London"],
                          ["m2", "paris", "2026-09-10", "w", "C", "Europe/Paris"]]}
    planned, notes = rc.plan_rows(inputs)
    london = {cp: loc for (m, cp, loc, utc) in planned if m[1] == "london"}
    assert london["morning"] == dt.datetime(2026, 9, 10, 9, 0) and london["prepeak_2h"] == dt.datetime(2026, 9, 10, 13, 0)
    paris = [cp for (m, cp, loc, utc) in planned if m[1] == "paris"]
    assert paris == ["morning", "noon"] and notes == {f"{cp}: no measured peak hour": 1
                                                     for cp in ("prepeak_2h", "prepeak_1h", "postpeak_1h")}


# ---------------------------------------------------------------------------
# the market the engine would have read (P5.3 amended: S10's market weight)
# ---------------------------------------------------------------------------

def test_the_engine_market_is_the_newest_book_read_the_engines_way():
    tops = rc.index_tops({"rows": [["a", 100, 0.40, 0.44], ["a", 200, 0.50, 0.54], ["b", 150, None, 0.30],
                                    ["a", 900, 0.90, 0.95]]})
    got = rc.engine_market(["a", "b"], tops, 300)
    # a: newest at or before 300 is (0.50, 0.54) -> 0.52; b ask-only -> half its ask, 0.15
    assert abs(got["a"] - 0.52 / 0.67) < 1e-12 and abs(got["b"] - 0.15 / 0.67) < 1e-12
    assert rc.engine_market(["a", "b"], tops, 120) is None                  # b not yet quoted
    assert rc.engine_market(["a", "b"], tops, 150 + rc.MARKET_MAX_AGE_S + 1) is None   # b too old


def test_the_market_weight_evidence_uses_only_complete_matching_ladders():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "s10_market_weight", pathlib.Path(__file__).resolve().parents[1] / "tools" / "s10_market_weight.py")
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    base = {"city_key": "x", "target_date": "2026-09-20", "checkpoint": "postpeak_1h", "winner": "a",
            "model": {"a": 0.7, "b": 0.3}}
    rows, dropped = t.usable([dict(base, market={"a": 0.5, "b": 0.5}), dict(base, market=None),
                              dict(base, market={"a": 0.5, "c": 0.5}), dict(base, winner="z", market={"a": 0.5, "b": 0.5})])
    assert [r[1] for r in rows] == ["s10:post_peak"]
    assert dropped == {"no complete market ladder": 1, "model and market ladders differ": 1,
                       "winner not on the ladder": 1}
    ll = t.losses(rows)
    assert ll["all"]["ll"][0.0] > ll["all"]["ll"][1.0]           # the model put more on the winner


def test_a_report_names_the_source_it_was_built_from():
    # P3.10 part 2 feeds the venue's record to this replay: its report must not
    # claim the database export or a thinly observed market.
    meta = {"exported_at": "2026-09-26T23:20:00+00:00"}
    old = rc.report([], {}, {}, meta)
    assert "the database export of 2026-09-26T23:20Z (`tools/p73_replay_inputs.sql`)" in old
    assert "The market is thinly observed" in old
    new = rc.report([], {}, {}, dict(meta, source="the venue's own record", market_note="- **Hourly prices.**"))
    assert "from the venue's own record." in new and "- **Hourly prices.**" in new
    assert "p73_replay_inputs" not in new and "thinly observed" not in new
