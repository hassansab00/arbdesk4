"""The automatic strategy transitions (plan v2 P5.2)."""
import datetime as dt
import math

import pytest

import strategy_lifecycle as sl

NOW = dt.datetime(2026, 10, 30, 3, 0, tzinfo=dt.timezone.utc)
LEDGER = {"account_id": "a1", "starting_cash": 1000}


@pytest.fixture(autouse=True)
def no_operational_archive(monkeypatch):
    monkeypatch.setattr(sl,'archived_trades',lambda:{})


def _trades(pnls):
    base = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    return [{"trade_id": f"t{i:03d}", "net_pnl": p, "closed_at": (base + dt.timedelta(hours=i)).isoformat()}
            for i, p in enumerate(pnls)]


def test_log_growth_compounds_on_the_ledger():
    g = sl.log_growths(1000, _trades([100, -110]))
    assert g == pytest.approx([math.log(1100 / 1000), math.log(990 / 1100)])


def test_trades_are_taken_in_closing_order_whatever_order_they_arrive():
    t = _trades([100, -110])
    assert sl.log_growths(1000, list(reversed(t))) == sl.log_growths(1000, t)


def test_nothing_moves_under_the_minimum_sample():
    losing = _trades([-10] * (sl.MIN_DECISIONS - 1))
    assert sl.decide({"state": "shadow"}, LEDGER, losing, NOW) is None


def test_a_strategy_losing_beyond_its_noise_is_suspended():
    losing = _trades([-10, -8, -12, -9, -11] * 8)                 # 40 decisions, all losses
    move = sl.decide({"state": "shadow"}, LEDGER, losing, NOW)
    assert move and move[0] == "suspended"
    assert "40 settled decisions" in move[1] and "< 0" in move[1]


def test_a_strategy_that_might_be_breaking_even_is_left_alone():
    # Mean slightly negative but noisy: the upper bound is above zero.
    noisy = _trades([30, -32] * 20)
    mean, hi = sl.upper_bound(sl.log_growths(1000, noisy))
    assert mean < 0 < hi
    assert sl.decide({"state": "shadow"}, LEDGER, noisy, NOW) is None


def test_a_winning_strategy_stays_in_shadow():
    assert sl.decide({"state": "shadow"}, LEDGER, _trades([10] * 50), NOW) is None


def test_the_upper_bound_is_the_one_sided_90_percent_bound():
    g = [0.01 * ((i % 5) - 2) for i in range(50)]
    mean, hi = sl.upper_bound(g)
    n = len(g)
    sd = math.sqrt(sum((x - mean) ** 2 for x in g) / (n - 1))
    assert hi == pytest.approx(mean + 1.2816 * sd / math.sqrt(n))


def test_a_suspended_strategy_is_retested_after_fourteen_days():
    since = (NOW - dt.timedelta(days=14)).isoformat()
    move = sl.decide({"state": "suspended", "since": since}, LEDGER, [], NOW)
    assert move and move[0] == "shadow" and "14 days" in move[1]
    early = (NOW - dt.timedelta(days=13, hours=23)).isoformat()
    assert sl.decide({"state": "suspended", "since": early}, LEDGER, [], NOW) is None


def test_a_losing_portfolio_strategy_is_suspended_on_its_shadow_record():
    losing = _trades([-10, -8, -12, -9, -11] * 8)
    move = sl.decide({"state": "portfolio"}, LEDGER, losing, NOW)
    assert move and move[0] == "suspended"
    assert sl.decide({"state": "portfolio"}, LEDGER, _trades([10] * 50), NOW) is None


@pytest.mark.parametrize("state", ["research", "retired"])
def test_no_other_state_moves_by_itself(state):
    """Research -> shadow and retirement are people's decisions."""
    losing = _trades([-10] * 60)
    assert sl.decide({"state": state, "since": "2026-01-01T00:00:00+00:00"}, LEDGER, losing, NOW) is None


def test_a_shadow_strategy_with_no_ledger_is_not_judged():
    assert sl.decide({"state": "shadow"}, None, _trades([-10] * 60), NOW) is None


def test_main_moves_through_the_rpc_and_logs(monkeypatch):
    calls, logged = [], {}
    states = [{"strategy_id": "sA", "state": "shadow", "since": "2026-10-01T00:00:00+00:00"},
              {"strategy_id": "sB", "state": "suspended", "since": "2026-10-01T00:00:00+00:00"},
              {"strategy_id": "sC", "state": "research", "since": "2026-10-01T00:00:00+00:00"}]

    def fake_rest(table, params):
        if table == "strategy_state":
            return states
        assert dict(params)["kind"] == "eq.shadow"
        return [{"account_id": "aA", "strategy_id": "sA", "starting_cash": 1000}]

    monkeypatch.setattr(sl, "rest", fake_rest)
    monkeypatch.setattr(sl, "rest_all", lambda t, p, order: [] if t=='strategy_state_history' else _trades([-10] * 45))
    monkeypatch.setattr(sl, "rpc", lambda fn, params: calls.append((fn, params)))
    monkeypatch.setattr(sl, "log_run", lambda job, status, rows, detail: logged.update(job=job, rows=rows, detail=detail))
    moves = sl.main(now=NOW)
    assert [(m["strategy_id"], m["to"]) for m in moves] == [("sA", "suspended"), ("sB", "shadow")]
    assert all(fn == "set_strategy_state" for fn, _ in calls)
    assert logged["job"] == "strategy_lifecycle" and logged["rows"] == 2
    assert logged["detail"]["looked_at"]["sA"]["settled"] == 45


def test_retest_cannot_be_resuspended_by_its_previous_losing_record():
    old = [dict(t, opened_at=t['closed_at']) for t in _trades([-10] * 45)]
    state = {'state':'shadow','retest_since':'2026-10-20T00:00:00+00:00'}
    assert sl.decide(state, LEDGER, old, NOW) is None
    # An old decision settling during the retest changes cash, not the sample.
    late = dict(old[0], closed_at='2026-10-25T00:00:00+00:00')
    unknown = dict(late); unknown.pop('opened_at')
    assert sl.log_growths(1000, [late, unknown], state['retest_since']) == []


def test_retest_compounds_existing_equity_and_requires_forty_fresh_losses():
    boundary='2026-10-20T00:00:00+00:00'
    old=dict(trade_id='old',net_pnl=-200,opened_at='2026-10-01T00:00:00+00:00',closed_at='2026-10-02T00:00:00+00:00')
    fresh=[dict(trade_id=f'new{i}',net_pnl=-1,opened_at=boundary,
                closed_at=(dt.datetime(2026,10,21,tzinfo=dt.timezone.utc)+dt.timedelta(hours=i)).isoformat()) for i in range(40)]
    growths=sl.log_growths(1000,[old,*fresh],boundary)
    assert len(growths)==40 and growths[0]==pytest.approx(math.log(799/800))
    state={'state':'portfolio','retest_since':boundary}
    assert sl.decide(state,LEDGER,[old,*fresh[:-1]],NOW) is None
    move=sl.decide(state,LEDGER,[old,*fresh],NOW)
    assert move[0]=='suspended' and '40 settled decisions' in move[1] and boundary in move[1]


def test_main_keeps_latest_retest_boundary_across_promotion(monkeypatch):
    calls,logs=[],[]
    boundary='2026-10-20T00:00:00+00:00'
    monkeypatch.setattr(sl,'rest',lambda table,params: (
        [dict(strategy_id='fixture',state='portfolio',since='2026-10-25T00:00:00+00:00')]
        if table=='strategy_state' else [dict(LEDGER,strategy_id='fixture')]))
    def rows(table,params,order):
        if table=='strategy_state_history':
            assert params['from_state']=='eq.suspended' and params['to_state']=='eq.shadow'
            assert params['changed_at']=='lte.'+NOW.isoformat()
            return [dict(strategy_id='fixture',changed_at='2026-10-10T00:00:00Z'),dict(strategy_id='fixture',changed_at=boundary)]
        assert params['closed_at']=='lte.'+NOW.isoformat()
        assert 'opened_at' in params['select']
        return [dict(t,opened_at=t['closed_at']) for t in _trades([-10]*45)]
    monkeypatch.setattr(sl,'rest_all',rows)
    monkeypatch.setattr(sl,'rpc',lambda *a:calls.append(a))
    monkeypatch.setattr(sl,'log_run',lambda *a:logs.append(a))
    assert sl.main(now=NOW)==[] and calls==[]
    measured=logs[0][3]['looked_at']['fixture']
    assert measured['settled']==0 and measured['settled_history']==45 and measured['retest_since']==boundary


def test_main_keeps_archived_losses_and_deduplicates_trades(monkeypatch):
    old=[dict(t,account_id='a1',opened_at=t['closed_at']) for t in _trades([-10]*45)]
    monkeypatch.setattr(sl,'archived_trades',lambda:{t['trade_id']:t for t in old})
    monkeypatch.setattr(sl,'rest',lambda table,params: [dict(strategy_id='fixture',state='shadow')]
        if table=='strategy_state' else [dict(LEDGER,strategy_id='fixture')])
    monkeypatch.setattr(sl,'rest_all',lambda table,params,order: [] if table=='strategy_state_history' else old[-2:])
    calls,logs=[],[]
    monkeypatch.setattr(sl,'rpc',lambda fn,params:calls.append(params))
    monkeypatch.setattr(sl,'log_run',lambda *a:logs.append(a))
    sl.main(now=NOW)
    assert calls[0]['p_state']=='suspended' and '45 settled decisions' in calls[0]['p_reason']
    assert logs[0][3]['looked_at']['fixture']['settled_history']==45
