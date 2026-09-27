"""Plan v2 P5.12 part 1: one decision engine for live and replay
(scripts/decision_engine.py). Pure: the same inputs give the same decision."""
import json
import pathlib

import decision_engine as de
import risk_rails
import strategies.s10_max_temp_winner as s10

LEDGER = {"equity_usd": 1000.0, "cash_usd": 1000.0}
VIEW = {"strategy_id": "t", "city_key": "c", "resolution_date": "2026-09-28",
        "probs": {"a": 0.1, "b": 0.5, "c": 0.3, "d": 0.1}}
BOOK = {"a": {"ask": 0.10, "bid": 0.08}, "b": {"ask": 0.35, "bid": 0.33, "depth_usd": 500.0},
        "c": {"ask": 0.30, "bid": 0.28}, "d": {"ask": 0.10, "bid": 0.08}}


# One bucket at a clearing edge. Under the plan's priors (the belief layer's
# prior sd, sizing on the worst quarter of draws, lambda 0.25, h 0.002, the 3%
# city-day rail) a lone 50% bucket trades at 30c and not at 35c (measured).
BOOK_ONE = dict(BOOK, b={"ask": 0.25, "bid": 0.23, "depth_usd": 500.0})


def _paid(orders):
    return sum(o["usd"] for o in orders)


def _payout_by_outcome(orders, ids):
    return {k: sum(o["shares"] for o in orders if o["band_id"] == k and o["side"] == "YES") for k in ids}


def test_the_same_inputs_give_the_same_decision():
    a = de.decide(VIEW, book=BOOK, ledger=LEDGER)
    b = de.decide(VIEW, book=BOOK, ledger=LEDGER)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    assert a["action"] == "BUY" and a["versions"]["engine"] == de.ENGINE_VERSION


def test_lambda_comes_before_the_city_day_rail():
    """Capping and then scaling shrinks twice: 0.0012 of growth, under h's 0.002."""
    d = de.decide(VIEW, book=BOOK, ledger=LEDGER)
    assert "city_day" in d["binding"]
    assert abs(_paid(d["orders"]) - risk_rails.DEFAULTS["city_day_frac"] * 1000.0) < 0.05
    assert d["g_target"] - d["g_now"] > 0.002


def test_the_rails_hold():
    rails = dict(risk_rails.DEFAULTS, max_price=0.32)
    d = de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, on_market_usd=20.0), rails=rails)
    assert _paid(d["orders"]) <= 10.0 + 0.05                      # 3% of 1,000 less 20 on the market
    assert all(o["limit_price"] <= 0.32 for o in d["orders"])
    assert de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, on_market_usd=30.0))["reason_code"] == "city_day_full"
    assert de.decide(VIEW, book=BOOK, ledger=LEDGER, halted=True)["reason_code"] == "halted"
    lost = de.decide(VIEW, book=BOOK, ledger=dict(LEDGER, equity_usd=950.0, pnl_today_usd=-50.0))
    assert lost["reason_code"] == "daily_loss" and not lost["orders"]


def test_no_trade_when_every_bucket_costs_its_probability():
    book = {k: {"ask": p, "bid": p - 0.02} for k, p in VIEW["probs"].items()}
    d = de.decide(VIEW, book=book, ledger=LEDGER)
    assert d["action"] == "NONE" and not d["orders"]


def test_a_small_edge_stays_inside_the_no_trade_band():
    for ask in (0.35, 0.45):
        d = de.decide(dict(VIEW, only=["b:YES"]), book=dict(BOOK, b={"ask": ask, "bid": ask - 0.02}), ledger=LEDGER)
        assert d["reason_code"] == "no_trade_band" and d["g_target"] - d["g_now"] <= 0.002
    assert de.decide(dict(VIEW, only=["b:YES"]), book=dict(BOOK, b={"ask": 0.30, "bid": 0.28}),
                     ledger=LEDGER)["action"] == "BUY"


def test_a_lock_view_never_ends_below_what_it_paid():
    probs = {"a": 0.1, "b": 0.5, "c": 0.3, "d": 0.1}
    book = {"a": {"ask": 0.08}, "b": {"ask": 0.40}, "c": {"ask": 0.25}, "d": {"ask": 0.08}}   # asks sum 0.81
    d = de.decide(dict(VIEW, probs=probs, lock=True), book=book, ledger=LEDGER)
    assert d["action"] == "BUY"
    paid = _paid(d["orders"])
    assert min(_payout_by_outcome(d["orders"], probs).values()) >= paid - 0.05


def test_only_restricts_the_book_to_the_strategys_assets():
    d = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER)
    assert d["action"] == "BUY" and {o["band_id"] for o in d["orders"]} == {"b"}


def test_a_shrinking_gap_waits_under_the_prior_timing_rule():
    d = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER,
                  state={"hours_to_peak": 5, "regime": "normal", "gap_prev": 0.40})
    assert d["action"] == "WAIT" and d["reason_code"] == "timing"
    assert d["timing"]["rule"] == "prior" and d["timing"]["gap_trend"] == "shrinking"


def test_holding_the_target_already_is_a_hold():
    first = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER)
    shares = first["orders"][0]["shares"]
    again = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE,
                      ledger={"equity_usd": 1000.0, "cash_usd": 1000.0 - first["orders"][0]["usd"],
                              "on_market_usd": first["orders"][0]["usd"], "held": {"b": (shares, 0.0)},
                              "held_usd": first["orders"][0]["usd"]})
    assert again["action"] == "HOLD" and not again["orders"]


def test_s10s_target_goes_through_the_engine_alone():
    """P7.5 picks the bucket, the engine sizes it: a real 26 Sep ladder."""
    rows = json.loads((pathlib.Path(__file__).parent / "fixtures" / "s10_checkpoints_26sep.json").read_text())["rows"]
    r = next(x for x in rows if x["city_key"] == "london" and x["checkpoint"] == "postpeak_1h")
    pick = s10.decide("s10_winner", bands=r["bands"], unit=r["unit"], probs=r["probs"], book=r["market"],
                      floor_c=r["running_max_c"], floor_basis="series", reading_age_min=17)
    assert pick["action"] == "BUY"
    d = de.decide({"strategy_id": "s10_winner", "probs": r["probs"], "only": [f"{pick['target']}:YES"]},
                  book=r["market"], ledger=LEDGER)
    assert d["action"] == "BUY" and {o["band_id"] for o in d["orders"]} == {pick["target"]}
    assert _paid(d["orders"]) <= 30.0 + 0.05
