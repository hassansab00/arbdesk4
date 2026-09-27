"""Plan v2 P7.5 part 1: the S10 view and its variants, as a pure decision
(scripts/strategies/s10_max_temp_winner.py). Fixtures: the real frozen
checkpoint ladders and books of 26 Sep (tests/fixtures/s10_checkpoints_26sep.json;
frozen checkpoints start 24 Sep, so the plan's 22 Sep ladders do not exist)."""
import datetime as dt
import json
import pathlib

import pytest

import holdings_solver as hs
import strategies
import strategies.s10_max_temp_winner as s10

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "s10_checkpoints_26sep.json"
ROWS = json.loads(FIXTURE.read_text())["rows"]


def _age(r):
    return (dt.datetime.fromisoformat(r["decided_at"]) - dt.datetime.fromisoformat(r["reading_at"])).total_seconds() / 60


def _decide(variant, r, **kw):
    args = dict(bands=r["bands"], unit=r["unit"], probs=r["probs"], book=r["market"],
                floor_c=r["running_max_c"], floor_basis="series", reading_age_min=_age(r))
    args.update(kw)
    return s10.decide(variant, **args)


def _ladder(lo, hi, probs, asks, bids=None):
    """A whole-degree C ladder lo..hi with open tails, and its book."""
    bands = ([{"band_id": "lo", "band_lo": None, "band_hi": lo, "open_low": True, "open_high": False}]
             + [{"band_id": f"b{k}", "band_lo": k, "band_hi": k + 1, "open_low": False, "open_high": False}
                for k in range(lo, hi)]
             + [{"band_id": "hi", "band_lo": hi, "band_hi": None, "open_low": False, "open_high": True}])
    ids = [b["band_id"] for b in bands]
    p = {i: probs.get(i, 0.0) for i in ids}
    total = sum(p.values())
    p = {i: v / total for i, v in p.items()}
    book = {i: {"ask": asks.get(i), "bid": (bids or {}).get(i)} for i in ids}
    return bands, p, book


def test_the_variants_differ_only_in_constraints():
    for r in ROWS:
        views = [_decide(v, r)["p_post"] for v in s10.VARIANTS]
        assert views[0] == views[1] == views[2]
        assert abs(sum(views[0].values()) - 1) < 1e-9


def test_no_buy_on_a_bucket_the_day_has_ruled_out():
    # Most of the model's mass on 20-21 C, but the station has read 23 C: 20-21
    # is two buckets under the floor's bucket and cannot win (P3.1).
    bands, p, book = _ladder(18, 27, {"b20": 0.6, "b22": 0.1, "b23": 0.2, "b24": 0.1},
                             {i: 0.3 for i in ("b20", "b22", "b23", "b24")})
    for v in s10.VARIANTS:
        d = s10.decide(v, bands=bands, unit="C", probs=p, book=book, floor_c=23.0,
                       floor_basis="station", reading_age_min=10)
        assert "b20" in d["ruled_out"] and d["target"] != "b20"
        assert not (d["action"] == "BUY" and d["target"] in d["ruled_out"])
    # ... while the same ladder with no floor would buy it
    d = s10.decide("s10_winner", bands=bands, unit="C", probs=p, book=book, floor_c=None,
                   floor_basis="station", reading_age_min=10)
    assert d["action"] == "BUY" and d["target"] == "b20"


@pytest.mark.parametrize("basis,age", [("live_model", 10), (None, 10), ("series", 200), ("station", None)])
def test_no_buy_on_stale_or_model_readings(basis, age):
    for r in ROWS:
        for v in s10.VARIANTS:
            d = _decide(v, r, floor_basis=basis, reading_age_min=age)
            assert d["action"] == "WAIT" and d["reason"]


def test_a_dead_book_is_never_bought_the_real_tokyo_case():
    """26 Sep noon, Tokyo: the book had 23-24 C at 0.998 (it won) and 24-25 C at
    0.003 with no bid, while the engine gave 24-25 C 0.34. Growth at 0.003 looks
    enormous and is not real."""
    r = next(x for x in ROWS if x["city_key"] == "tokyo" and x["checkpoint"] == "noon")
    for v in s10.VARIANTS:
        d = _decide(v, r)
        assert d["action"] != "BUY", (v, d)
    for r in ROWS:
        for v in s10.VARIANTS:
            d = _decide(v, r)
            if d["action"] == "BUY":
                assert s10.buyable(r["market"], d["target"])[0]


def test_a_switch_happens_only_across_the_hysteresis():
    bands, p, book = _ladder(18, 27, {"b21": 0.25, "b22": 0.45, "b23": 0.2, "b24": 0.1},
                             {"b21": 0.20, "b22": 0.30, "b23": 0.2, "b24": 0.1},
                             {"b21": 0.19, "b22": 0.29, "b23": 0.19, "b24": 0.09})
    held = {"band_id": "b21", "shares": 100.0}
    base = s10.decide("s10_winner", bands=bands, unit="C", probs=p, book=book, floor_c=20.0,
                      floor_basis="station", reading_age_min=5, held=held, ledger_usd=1000.0,
                      h=s10.H_SWITCH_BOUNDS[0])
    assert base["target"] == "b22"
    # Size the holding so the gain net of the exit cost is 0.008: inside h's bounds.
    per_share = base["exit_cost"] / held["shares"]
    held = {"band_id": "b21", "shares": (base["gain"] - 0.008) / per_share}
    seen = set()
    for h in (0.001, 0.003, 0.005, 0.0079, 0.0081, 0.01, 0.02):
        d = s10.decide("s10_winner", bands=bands, unit="C", probs=p, book=book, floor_c=20.0,
                       floor_basis="station", reading_age_min=5, held=held, ledger_usd=1000.0, h=h)
        need = d["gain"] - d["exit_cost"]
        assert abs(need - 0.008) < 1e-9
        assert d["action"] == ("SWITCH" if need > s10.h_switch(h) else "HOLD"), (h, need)
        seen.add(d["action"])
    assert seen == {"SWITCH", "HOLD"}
    # a larger position costs more to leave: the same gain no longer switches
    big = s10.decide("s10_winner", bands=bands, unit="C", probs=p, book=book, floor_c=20.0,
                     floor_basis="station", reading_age_min=5, held={"band_id": "b21", "shares": 1e6},
                     ledger_usd=1000.0)
    assert big["action"] == "HOLD" and big["exit_cost"] > base["exit_cost"]


def test_h_switch_is_bounded():
    assert s10.h_switch() == 0.005
    assert s10.h_switch(0.0) == 0.001 and s10.h_switch(1.0) == 0.02


def test_a_certainly_lost_bucket_is_sold_whatever_else_is_true():
    bands, p, book = _ladder(18, 27, {"b20": 0.5, "b23": 0.5}, {"b20": 0.4, "b23": 0.4}, {"b20": 0.01})
    for v in s10.VARIANTS:
        d = s10.decide(v, bands=bands, unit="C", probs=p, book=book, floor_c=23.0, floor_basis="station",
                       reading_age_min=500, held={"band_id": "b20", "shares": 10})
        assert d["action"] == "SELL"


def test_s10_lock_never_has_a_negative_outcome():
    # A ladder whose asks sum below one: an equal-shares book exists.
    asks = {"lo": 0.02, "b20": 0.05, "b21": 0.25, "b22": 0.35, "b23": 0.15, "b24": 0.05, "hi": 0.02}
    bands, p, book = _ladder(20, 25, {"b21": 0.3, "b22": 0.45, "b23": 0.15, "b24": 0.05, "b20": 0.03,
                                      "lo": 0.01, "hi": 0.01}, asks)
    d = s10.decide("s10_lock", bands=bands, unit="C", probs=p, book=book, floor_c=None,
                   floor_basis="station", reading_age_min=5, ledger_usd=1000.0)
    assert d["action"] == "BUY" and d["target"] == "b22" and d["traded_is_top"]
    ladder = [{"id": b["band_id"], "p": p[b["band_id"]], "yes_price": asks[b["band_id"]], "no_price": None}
              for b in bands]
    w = hs.solve_book(ladder, allow=("YES",), lock=True)["wealth_by_outcome"]
    assert min(w.values()) >= 1.0 - 1e-9
    # and on every real ladder it either holds a lock or does nothing
    for r in ROWS:
        assert _decide("s10_lock", r)["action"] in ("NONE", "WAIT")


def test_growth_may_prefer_a_cheaper_bucket_than_the_winner():
    """P7.5 documents it: growth pays for edge against the price, not for being likely."""
    bands, p, book = _ladder(18, 27, {"b21": 0.5, "b22": 0.3, "b23": 0.2},
                             {"b21": 0.55, "b22": 0.10, "b23": 0.15})
    kw = dict(bands=bands, unit="C", probs=p, book=book, floor_c=None, floor_basis="station", reading_age_min=5)
    w, g = s10.decide("s10_winner", **kw), s10.decide("s10_growth", **kw)
    assert w["target"] == "b21" and w["action"] == "NONE"          # 50% at 55c does not grow
    assert g["target"] == "b22" and g["action"] == "BUY" and g["traded_is_top"] is False


def test_every_decision_says_whether_it_trades_the_predicted_top():
    for r in ROWS:
        for v in s10.VARIANTS:
            d = _decide(v, r)
            assert d["predicted_top"] in d["p_post"]
            if d["action"] in ("BUY", "SWITCH"):
                assert d["traded_is_top"] == (d["target"] == d["predicted_top"])


def test_part_one_is_not_live():
    """Nothing calls S10 until P5.12 wires the engine and a SELL/SWITCH consumer exists."""
    assert not any(k.startswith("s10") for k in strategies.REGISTRY)
    with pytest.raises(ValueError):
        s10.decide("s10_other", bands=[], unit="C", probs={}, book={})
