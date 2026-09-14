"""The live signal runner, restored.

WHAT IT FIXES. paper_plans.py proposes a plan for every ENTER signal fired in
the last fifteen minutes. Nothing wrote signals - the runner was removed with
the signals FEATURE, and only strategy_rules.run_strategies survived because
backtest/engine.py imports it. So assisted and automatic desks polled a table
nobody filled and proposed nothing, forever: 1,894 rows in `signals`, none
newer than 2026-09-13, all orphans of the removal rather than output.

These pin the wire, not the strategies. The strategies have their own tests.
"""
import datetime as dt

import pytest

import signal_engine as se


def _opp(band_id, side, **over):
    row = {
        "band_id": band_id, "side": side, "city_key": "london",
        "resolution_date": "2026-09-15", "band_lo": 20, "band_hi": 21,
        "open_low": False, "open_high": False, "band_label": "20°C", "unit": "C",
        "model_prob": 0.31, "market_price": 0.22, "edge_net_pp": 9.0,
        "tradeable": True, "block_reason": None, "confidence": 0.62,
        "regime_label": "NORMAL", "market_state": "LIVE",
        "fillable_usd_5c": 250.0, "token_yes": "ty", "token_no": "tn", "spread": 0.02,
    }
    row.update(over)
    return row


@pytest.fixture
def board(monkeypatch):
    """Serve v_opportunities / live_weather / the derived tables from memory."""
    def go(opps, live=None):
        def fake_rest_all(path, params=None, **kw):
            if path == "v_opportunities":
                return opps
            if path == "live_weather":
                return live or []
            return []
        monkeypatch.setattr(se, "rest_all", fake_rest_all)
        monkeypatch.setattr(se, "rest", lambda path, params=None: [])
    return go


# ---------------------------------------------------------------------------
# The context it hands the strategies
# ---------------------------------------------------------------------------
def test_the_two_sides_of_a_band_fold_into_one_view(board):
    """v_opportunities is one row per band per SIDE. A strategy reasons about
    a BAND - it compares the YES price against the NO price - so handing it
    two half-views would make every two-sided rule unreachable."""
    board([_opp("b1", "YES", market_price=0.22, edge_net_pp=9.0),
           _opp("b1", "NO",  market_price=0.80, edge_net_pp=-2.0)])
    views = se._band_views()
    assert len(views) == 1
    v = views[0]
    assert v.yes_price == 0.22 and v.no_price == 0.80
    assert v.yes_edge_net_pp == 9.0 and v.no_edge_net_pp == -2.0
    assert v.band_id == "b1" and v.city_key == "london"


def test_a_band_priced_on_one_side_only_is_still_a_band(board):
    """The missing side is None and not tradeable, which is what it means.
    Dropping the band entirely would hide it from exit rules that hold it."""
    board([_opp("b1", "YES")])
    v = se._band_views()[0]
    assert v.yes_price is not None
    assert v.no_price is None and v.no_tradeable is False


def test_a_blocked_side_is_carried_with_its_reason(board):
    """no_verified_skill blocks most of the board. A strategy must be able to
    see WHY, not just that it cannot trade."""
    board([_opp("b1", "YES", tradeable=False, block_reason="no_verified_skill")])
    v = se._band_views()[0]
    assert v.yes_tradeable is False
    assert v.yes_block_reason == "no_verified_skill"


def test_live_weather_rides_along_when_present(board):
    board([_opp("b1", "YES")],
          live=[{"city_key": "london", "running_max_c": 19.4,
                 "peak_window_state": "IN_WINDOW", "day_decided": False}])
    v = se._band_views()[0]
    assert v.running_max_c == 19.4 and v.peak_window_state == "IN_WINDOW"
    assert v.day_decided is False


def test_absent_live_weather_is_none_not_zero(board):
    """A running maximum of 0.0 is a claim about the day. None is the absence
    of one, and the running-max strategies must not fire on it."""
    board([_opp("b1", "YES")], live=[])
    v = se._band_views()[0]
    assert v.running_max_c is None
    assert v.day_decided is False


# ---------------------------------------------------------------------------
# What it writes, and when it writes nothing
# ---------------------------------------------------------------------------
def test_no_enabled_strategies_is_ok_and_writes_nothing(monkeypatch):
    """Ten strategies ship disabled on purpose. A desk with none switched on
    reports ok and says so - it is not a failure and must not look like one."""
    monkeypatch.setattr(se, "_enabled_strategies", lambda: [])
    logged, wrote = {}, []
    monkeypatch.setattr(se, "log_run", lambda *a, **k: logged.update(status=a[1], detail=a[3]))
    monkeypatch.setattr(se, "insert", lambda *a, **k: wrote.append(a))
    assert se.main() == 0
    assert wrote == [], "nothing may be written when nothing is enabled"
    assert logged["status"] == "ok"
    assert logged["detail"]["strategies_enabled"] == 0


def test_a_fired_signal_is_written_where_paper_plans_looks(monkeypatch):
    """paper_plans.py filters action = ENTER on `signals`, so the row has to
    carry the action, the band, and a fired_at inside its window."""
    cfg = object()
    monkeypatch.setattr(se, "_enabled_strategies", lambda: [cfg])
    monkeypatch.setattr(se, "_open_positions", lambda: [])
    monkeypatch.setattr(se, "_band_views", lambda: [
        se.BandView(band_id="b1", city_key="london", resolution_date="2026-09-15",
                    band_lo=20, band_hi=21, open_low=False, open_high=False,
                    band_label="20C", unit="C", model_prob_yes=0.31, yes_price=0.22,
                    no_price=0.8, yes_edge_net_pp=9.0, no_edge_net_pp=-2.0,
                    yes_tradeable=True, yes_block_reason=None, no_tradeable=True,
                    no_block_reason=None, confidence=0.6, regime_label="NORMAL",
                    market_state="LIVE")])
    monkeypatch.setattr(se, "_context", lambda views: object())

    from strategies.base import Signal
    fired = [Signal(strategy_id="s1_buy_low_sell_signal", band_id="b1", side="YES",
                    action="ENTER", reason="edge 9pp", price_at_fire=0.22,
                    prob_at_fire=0.31, edge_at_fire=9.0, suggested_shares=120.0,
                    confidence=0.6, regime_label="NORMAL", severity="info",
                    dedupe_key="s1|b1|YES")]
    monkeypatch.setattr(se, "run_strategies", lambda *a, **k: (fired, [], []))
    monkeypatch.setattr(se, "log_run", lambda *a, **k: None)

    written = {}
    monkeypatch.setattr(se, "insert", lambda table, rows: written.setdefault(table, rows))
    assert se.main() == 0

    rows = written["signals"]
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "ENTER" and r["side"] == "YES" and r["band_id"] == "b1"
    assert r["city_key"] == "london", "paper_plans keys per city; an unset city hides the signal"
    assert r["strategy_id"] == "s1_buy_low_sell_signal"
    assert r["suggested_shares"] == 120.0
    assert r["status"] == "pending_approval", "a signal is a proposal, never an order"
    fired_at = dt.datetime.fromisoformat(r["fired_at"])
    assert (dt.datetime.now(dt.timezone.utc) - fired_at).total_seconds() < 60


def test_the_runner_comes_before_the_proposals_it_feeds():
    """One job, in order. paper_plans.py reads a fifteen-minute window, so a
    signal written after it has run is a signal it never sees."""
    import os
    wf = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".github", "workflows", "pipeline_intraday.yml")).read()
    assert "scripts/signal_engine.py" in wf, "the runner must be scheduled at all"
    assert wf.index("scripts/signal_engine.py") < wf.index("scripts/paper_plans.py"), \
        "signals must be written before the step that reads them"
