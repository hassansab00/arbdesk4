"""Plan v2 P5.12 part 1: one decision engine for live and replay
(scripts/decision_engine.py). Pure: the same inputs give the same decision."""
import json
import pathlib

import decision_engine as de
import risk_rails
import strategies.s10_max_temp_winner as s10

LEDGER = {"equity_usd": 1000.0, "cash_usd": 1000.0}
# The against-market gate (tested on its own below) is off where a test is
# about sizing, rails or timing: those ladders deliberately disagree with the book.
NO_GATE = {"against_market_gate_on": False}
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
        d = de.decide(dict(VIEW, only=["b:YES"]), book=dict(BOOK, b={"ask": ask, "bid": ask - 0.02}), ledger=LEDGER, params=NO_GATE)
        assert d["reason_code"] == "no_trade_band" and d["g_target"] - d["g_now"] <= 0.002
    assert de.decide(dict(VIEW, only=["b:YES"]), book=dict(BOOK, b={"ask": 0.30, "bid": 0.28}),
                     ledger=LEDGER, params=NO_GATE)["action"] == "BUY"


def test_a_lock_view_never_ends_below_what_it_paid():
    probs = {"a": 0.1, "b": 0.5, "c": 0.3, "d": 0.1}
    book = {"a": {"ask": 0.08}, "b": {"ask": 0.40}, "c": {"ask": 0.25}, "d": {"ask": 0.08}}   # asks sum 0.81
    d = de.decide(dict(VIEW, probs=probs, lock=True), book=book, ledger=LEDGER)
    assert d["action"] == "BUY"
    paid = _paid(d["orders"])
    assert min(_payout_by_outcome(d["orders"], probs).values()) >= paid - 0.05


def test_only_restricts_the_book_to_the_strategys_assets():
    d = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER, params=NO_GATE)
    assert d["action"] == "BUY" and {o["band_id"] for o in d["orders"]} == {"b"}


def test_a_shrinking_gap_waits_under_the_prior_timing_rule():
    d = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER, params=NO_GATE,
                  state={"hours_to_peak": 5, "regime": "normal", "gap_prev": 0.40})
    assert d["action"] == "WAIT" and d["reason_code"] == "timing"
    assert d["timing"]["rule"] == "prior" and d["timing"]["gap_trend"] == "shrinking"


def test_holding_the_target_already_is_a_hold():
    first = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER, params=NO_GATE)
    shares = first["orders"][0]["shares"]
    again = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE,
                      ledger={"equity_usd": 1000.0, "cash_usd": 1000.0 - first["orders"][0]["usd"],
                              "on_market_usd": first["orders"][0]["usd"], "held": {"b": (shares, 0.0)},
                              "held_usd": first["orders"][0]["usd"]}, params=NO_GATE)
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


def test_the_view_is_not_backed_against_the_market_until_proven():
    """edge_engine's rule (Hassan, 24 Sep: never favour losing bets): the view's
    favourite is b, the market's is c (the highest ask), so YES on anything but
    c is blocked while the gate is on - its default."""
    d = de.decide(dict(VIEW, only=["b:YES"]), book=BOOK_ONE, ledger=LEDGER)
    assert d["action"] == "NONE" and d["reason_code"] == "against_market"
    assert de.against_market_assets(VIEW["probs"], BOOK_ONE, ("YES", "NO")) == {"a:YES", "b:YES", "d:YES", "c:NO"}
    assert de.against_market_assets(VIEW["probs"], BOOK, ("YES",)) == set()          # favourites agree
    lock_probs = {"a": 0.10, "b": 0.50, "c": 0.30, "d": 0.10}
    lock_book = {"a": {"ask": 0.08}, "b": {"ask": 0.40}, "c": {"ask": 0.25}, "d": {"ask": 0.08}}
    assert de.decide(dict(VIEW, probs=lock_probs, lock=True), book=lock_book, ledger=LEDGER)["action"] == "BUY"


def test_a_lock_is_still_a_lock_while_other_ladders_hold_money():
    """The lock's floor is this ladder's cash plus its holdings at cost. With
    a floor of 1, $100 on another ladder made every lock 'break' (the replay:
    all 119 lock_breaks of 12-25 Sep fell while s11_lock held one position)."""
    probs = {"a": 0.1, "b": 0.5, "c": 0.3, "d": 0.1}
    book = {"a": {"ask": 0.08}, "b": {"ask": 0.40}, "c": {"ask": 0.25}, "d": {"ask": 0.08}}   # asks sum 0.81
    ledger = dict(LEDGER, cash_usd=900.0, on_market_usd=0.0)            # $100 held elsewhere
    d = de.decide(dict(VIEW, probs=probs, lock=True), book=book, ledger=ledger)
    assert d["action"] == "BUY", d["reason_code"]
    paid = _paid(d["orders"])
    assert min(_payout_by_outcome(d["orders"], probs).values()) >= paid - 0.05


def test_a_leader_with_no_ask_is_still_the_markets_favourite():
    """The P.5 report, question 2 (Hassan, 6 Oct: "yes"): all 36 against_market
    refusals of 29 Sep - 6 Oct were books whose leader was bought up (bid at
    least 0.991) with nobody selling. Reading asks alone named a 0.9c bucket
    the favourite and fired the gate on a disagreement that was not there."""
    probs = {"a": 0.005, "b": 0.99, "c": 0.005}
    book = {"a": {"ask": 0.009, "bid": 0.001}, "b": {"bid": 0.995}, "c": {"ask": 0.004, "bid": 0.001}}
    assert de.market_standing(book["b"]) == 0.995
    assert de.market_standing(book["a"]) == 0.009, "an ask is read before a bid"
    assert de.market_standing({}) is None and de.market_standing({"last": 0.5}) is None
    # the view agrees with the bought-up leader: nothing is against the market
    assert de.against_market_assets(probs, book, ("YES", "NO")) == set()
    # a view that backs another bucket IS against the leader, NO on the leader included
    other = {"a": 0.60, "b": 0.35, "c": 0.05}
    assert de.against_market_assets(other, book, ("YES", "NO")) == {"a:YES", "c:YES", "b:NO"}
    # a bucket with neither price never becomes the favourite
    thin = {"a": {"ask": 0.30}, "b": {}, "c": {"ask": 0.20}}
    assert de.against_market_assets({"a": 0.5, "b": 0.3, "c": 0.2}, thin, ("YES",)) == set()
