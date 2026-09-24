"""Every strategy trades its own shadow ledger (plan v2 P5.1).

The database half - one ledger per registered strategy, opened by the
registration, trading only that strategy, and a portfolio account that opens
suspended - is in tests/database/paper-contracts.cjs. This is the engine half.

Before P5.1 every strategy sized against ONE bankroll, the summed free cash of
every desk that could act, and every strategy's signals went through one set
of conflict rules and one ladder solve. So what a strategy proposed depended
on how many desks existed and on what every other strategy was doing: s4
holding NO on a band blocked s3's YES there, and two strategies wanting the
same band had one of them zeroed ("the band is one position").

On shadow ledgers each strategy is its own book. It sizes against its own
ledger's cash, its conflicts are with its own positions, its ladder is solved
inside its own book, and nothing another strategy does limits it. That is the
point of the ledgers: clean per-strategy evidence at a realistic size.
"""

import datetime as dt

import pytest

import paper_plans
import signal_engine as se
import strategy_rules as sr
from paper_engine import Portfolio
from strategies.base import BandView, Context, Signal, Strategy, StrategyConfig


def _band(band_id="b1"):
    return BandView(
        band_id=band_id, city_key="nyc", resolution_date="2026-09-25",
        band_lo=20.0, band_hi=21.0, open_low=False, open_high=False,
        band_label="20C", unit="C", model_prob_yes=0.5,
        yes_price=0.20, no_price=0.40, yes_edge_net_pp=0.2, no_edge_net_pp=0.1,
        yes_tradeable=True, yes_block_reason=None,
        no_tradeable=True, no_block_reason=None,
        confidence=0.7, regime_label="SHARP", market_state="LIVE",
        fillable_usd_5c_yes=50_000.0,
        decision_snapshot={"cost_version": "00000000-0000-0000-0000-00000000c057"})


def _signal(strategy_id, side, band_id="b1", prob=0.5, price=0.20):
    return Signal(strategy_id=strategy_id, band_id=band_id, side=side, action="ENTER",
                  reason="test", price_at_fire=price, prob_at_fire=prob, edge_at_fire=0.2,
                  suggested_shares=0.0, confidence=0.7, regime_label="SHARP",
                  severity="info", dedupe_key=f"{strategy_id}:{band_id}:{side}")


class _Wants(Strategy):
    """A strategy that always wants one side of b1, and never exits."""
    side = "YES"

    def entry_signals(self, ctx):
        return [_signal(self.config.strategy_id, self.side)]

    def exit_signals(self, ctx, open_positions):
        return []


class _WantsYes(_Wants):
    side = "YES"


class _WantsNo(_Wants):
    side = "NO"
    # NO pays 1 - p; at p=0.5 a NO at 0.40 has an edge too.

    def entry_signals(self, ctx):
        return [_signal(self.config.strategy_id, "NO", prob=0.5, price=0.40)]


def _cfg(sid):
    return StrategyConfig(strategy_id=sid, name=sid, side="BOTH", universe=["ALL"],
                          regime_filter=[], conflict_class="default",
                          capital_cap_pct=5.0, max_concurrent=10, enabled=True)


@pytest.fixture
def two_strategies(monkeypatch):
    monkeypatch.setitem(sr.REGISTRY, "sYES", _WantsYes)
    monkeypatch.setitem(sr.REGISTRY, "sNO", _WantsNo)
    ctx = Context(bands=[_band()], settings={}, now=dt.datetime(2026, 9, 25, tzinfo=dt.timezone.utc))
    return ctx, [_cfg("sYES"), _cfg("sNO")]


def _entries(fired):
    return {(s.strategy_id, s.side): s for s in fired if s.action == "ENTER"}


# --------------------------------------------------------------------------
# run_strategies: one book per strategy
# --------------------------------------------------------------------------

def test_one_strategy_does_not_block_another_on_its_own_ledger(two_strategies):
    ctx, cfgs = two_strategies
    books = {"sYES": Portfolio(bankroll=1000.0), "sNO": Portfolio(bankroll=1000.0)}
    fired, blocked, conflicts = sr.run_strategies(ctx, cfgs, [], portfolios=books)
    assert set(_entries(fired)) == {("sYES", "YES"), ("sNO", "NO")}, \
        "each strategy's decision must reach its own ledger"
    assert blocked == [] and conflicts == []


def test_the_shared_book_still_resolves_conflicts_for_the_backtest(two_strategies):
    # The backtest passes one portfolio and replays one combined book; that
    # path keeps the old rule. Without this the change would be silent there.
    ctx, cfgs = two_strategies
    fired, blocked, conflicts = sr.run_strategies(ctx, cfgs, [], Portfolio(bankroll=1000.0))
    assert len(_entries(fired)) == 1 and len(blocked) == 1
    assert conflicts[0]["kind"] == "opposite_side_same_band"


def test_a_strategy_conflicts_only_with_its_own_positions(two_strategies):
    ctx, cfgs = two_strategies
    books = {"sYES": Portfolio(bankroll=1000.0), "sNO": Portfolio(bankroll=1000.0)}
    # sNO holds NO on b1: sYES's YES there is sYES's own business ...
    others = [{"band_id": "b1", "side": "NO", "strategy_id": "sNO", "shares": 5}]
    fired, blocked, _ = sr.run_strategies(ctx, cfgs, others, portfolios=books)
    assert ("sYES", "YES") in _entries(fired), "another strategy's position blocked this one"
    # ... but sYES holding NO on b1 itself still blocks sYES buying YES.
    own = [{"band_id": "b1", "side": "NO", "strategy_id": "sYES", "shares": 5}]
    fired, blocked, conflicts = sr.run_strategies(ctx, cfgs, own, portfolios=books)
    assert ("sYES", "YES") not in _entries(fired)
    assert [s.strategy_id for s in blocked] == ["sYES"]
    assert conflicts[0]["strategy_a"] == "sYES" and conflicts[0]["strategy_b"] == "sYES"


def test_each_strategy_sizes_against_its_own_ledger(two_strategies):
    ctx, cfgs = two_strategies
    big = sr.run_strategies(ctx, cfgs[:1], [], portfolios={"sYES": Portfolio(bankroll=1000.0)})[0]
    small = sr.run_strategies(ctx, cfgs[:1], [], portfolios={"sYES": Portfolio(bankroll=500.0)})[0]
    assert big[0].suggested_shares > 0
    assert big[0].suggested_shares == pytest.approx(2 * small[0].suggested_shares), \
        "a strategy's size must scale with its own ledger and nothing else"


def test_a_strategy_with_no_ledger_sizes_to_zero(two_strategies):
    ctx, cfgs = two_strategies
    fired, _, _ = sr.run_strategies(ctx, cfgs, [], portfolios={"sYES": Portfolio(bankroll=1000.0)})
    e = _entries(fired)
    assert e[("sNO", "NO")].suggested_shares == 0.0, "a strategy with no ledger has nowhere to trade"
    assert e[("sYES", "YES")].suggested_shares > 0


# --------------------------------------------------------------------------
# signal_engine: where the ledgers are read and the ladders solved
# --------------------------------------------------------------------------

def test_ledgers_are_the_active_shadow_accounts(monkeypatch):
    seen = {}

    def fake_rest(table, params):
        seen["table"], seen["params"] = table, dict(params)
        return [{"account_id": "a1", "strategy_id": "sYES", "cash": 1000, "reserved_cash": 250},
                {"account_id": "a2", "strategy_id": None, "cash": 5, "reserved_cash": 0}]

    monkeypatch.setattr(se, "rest", fake_rest)
    ledgers = se._ledgers()
    assert seen["table"] == "paper_accounts"
    assert seen["params"]["kind"] == "eq.shadow" and seen["params"]["status"] == "eq.active"
    assert list(ledgers) == ["sYES"]
    assert se._portfolios(ledgers)["sYES"].bankroll == 750.0, "the bankroll is the ledger's FREE cash"


def test_an_unreadable_ledger_table_sizes_nothing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("paper_accounts unreachable")
    monkeypatch.setattr(se, "rest", boom)
    assert se._ledgers() == {}, "no fallback to a summed or configured bankroll"


def test_a_ledgers_drawdown_is_its_own(monkeypatch):
    ledgers = {"sA": {"account_id": "a1"}, "sB": {"account_id": "a2"}}
    monkeypatch.setattr(se, "rest", lambda t, p: [
        {"account_id": "a1", "equity": 800, "high_water": 1000, "spent_today_usd": 12}])
    risks = se._risk_states(ledgers)
    assert risks["sA"] == (800.0, 1000.0, 12.0)
    assert risks["sB"] == (None, None, 0.0), "a ledger with no row keeps full scale"

    def boom(*a, **k):
        raise RuntimeError("view missing")
    monkeypatch.setattr(se, "rest", boom)
    assert se._risk_states(ledgers) == {"sA": (None, None, 0.0), "sB": (None, None, 0.0)}


class _W:
    def __init__(self, weight):
        self.weight, self.reason = weight, "test"


def _run_main(monkeypatch, fired, ledgers, earned):
    monkeypatch.setattr(se, "_enabled_strategies", lambda: [object()])
    monkeypatch.setattr(se, "_open_positions", lambda: [])
    monkeypatch.setattr(se, "_band_views", lambda: [_band()])
    monkeypatch.setattr(se, "_context", lambda views: object())
    seen = {}

    def fake_run(ctx, configs, positions, portfolio=None, portfolios=None):
        seen["portfolio"], seen["portfolios"] = portfolio, portfolios
        return fired, [], []

    monkeypatch.setattr(se, "run_strategies", fake_run)
    monkeypatch.setattr(se, "_ledgers", lambda: ledgers)
    monkeypatch.setattr(se, "_earned_weights", lambda: earned)
    monkeypatch.setattr(se, "_risk_states", lambda l: {sid: (None, None, 0.0) for sid in l})
    monkeypatch.setattr(se, "_correlations", lambda cities: {})
    monkeypatch.setattr(se, "_recently_fired", lambda now: set())
    monkeypatch.setattr(se, "log_run", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(se, "insert", lambda table, rows: written.setdefault(table, rows))
    assert se.main() == 0
    return seen, {(r["strategy_id"], r["side"]): r for r in written.get("signals", [])}


def test_two_strategies_on_one_band_are_both_sized(monkeypatch):
    """Before P5.1 the ladder solve was shared, and a second strategy's claim
    on the same band was zeroed. Each ledger is its own book now."""
    fired = [_signal("sA", "YES"), _signal("sB", "YES")]
    for s in fired:
        s.suggested_shares = 1.0
    ledgers = {"sA": {"account_id": "a1", "cash": 1000, "reserved_cash": 0},
               "sB": {"account_id": "a2", "cash": 500, "reserved_cash": 0}}
    seen, rows = _run_main(monkeypatch, fired, ledgers, {})
    assert seen["portfolio"] is None and set(seen["portfolios"]) == {"sA", "sB"}
    a, b = rows[("sA", "YES")]["suggested_shares"], rows[("sB", "YES")]["suggested_shares"]
    assert a > 0 and b > 0, "one strategy's claim zeroed the other's on a separate ledger"
    assert a > b, "each ledger's ladder must be solved on that ledger's own cash"


def test_the_earned_weight_does_not_starve_a_shadow_ledger(monkeypatch):
    """The earned weight splits one pot between strategies - the portfolio
    account's question (P5.10). On a shadow ledger it would starve a weak
    strategy of the evidence that shows whether it is weak."""
    fired = [_signal("sA", "YES")]
    ledgers = {"sA": {"account_id": "a1", "cash": 1000, "reserved_cash": 0}}
    _, rows = _run_main(monkeypatch, fired, ledgers, {"sA": _W(0.0)})
    assert rows[("sA", "YES")]["suggested_shares"] > 0


# --------------------------------------------------------------------------
# paper_plans: only an active desk is offered a signal
# --------------------------------------------------------------------------

def test_only_active_desks_are_offered_signals(monkeypatch):
    seen = {}

    def fake_rest_all(table, params, **kw):
        seen.setdefault(table, dict(params))
        return []

    monkeypatch.setattr(paper_plans, "rest_all", fake_rest_all)
    monkeypatch.setattr(paper_plans, "log_run", lambda *a, **k: None)
    paper_plans.cycle()
    assert seen["paper_accounts"]["status"] == "eq.active", \
        "a suspended or retired desk refuses every entry; offering it one only publishes a refusal"


def test_every_signal_records_the_bankroll_it_was_sized_on():
    """The portfolio account (P5.10) rescales a signal's size to its own pot;
    it needs the base the engine used, not a guess at it."""
    a, b = _signal("sA", "YES"), _signal("sB", "YES")
    a.payload = {"k": 1}
    se.stamp_sized_on([a, b], {"sA": Portfolio(bankroll=812.5)})
    assert a.payload == {"k": 1, "sized_on_usd": 812.5}
    assert b.payload == {"sized_on_usd": 0.0}
