"""The meta-allocator for the portfolio account (plan v2 P5.10)."""
import datetime as dt
import math
import random

import pytest

import meta_allocator as ma

CFG = dict(ma.GATE_DEFAULTS)
NOW = dt.datetime(2026, 11, 20, 3, 0, tzinfo=dt.timezone.utc)


def _trades(pnls, *, stake=10.0, days=None, city=lambda i: f"c{i % 7}", regime=lambda i: "NORMAL"):
    """Closed trades staking `stake` each, one per hour, spread over `days` dates."""
    base = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    days = days or len(pnls)
    out = []
    for i, p in enumerate(pnls):
        out.append({"trade_id": f"t{i:04d}", "net_pnl": p, "closed_at": (base + dt.timedelta(hours=i)).isoformat(),
                    "shares": stake / 0.5, "avg_fill_price": 0.5, "fee_paid": 0, "gas_paid": 0,
                    "regime_label": regime(i), "city_key": city(i),
                    "resolution_date": str((base + dt.timedelta(days=i % days)).date())})
    return out


def _winner(n=80, days=40):
    # Wins of +6 and losses of -4 on $10 stakes, 60% winners: a clear edge.
    return _trades([6 if i % 5 < 3 else -4 for i in range(n)], days=days)


# ---- log-growth per dollar -------------------------------------------------------

def test_log_growth_per_dollar_is_the_ledger_growth_over_the_fraction_staked():
    d = ma.decisions(1000, _trades([5, -10]))
    assert d[0]["g"] == pytest.approx(math.log(1005 / 1000) / (10 / 1000))
    assert d[1]["g"] == pytest.approx(math.log(995 / 1005) / (10 / 1005))


def test_a_total_loss_is_finite():
    d = ma.decisions(1000, _trades([-10]))
    assert d[0]["g"] == pytest.approx(math.log(990 / 1000) / 0.01)
    assert math.isfinite(d[0]["g"])


def test_a_trade_with_no_stake_is_not_a_decision_but_its_pnl_still_moves_equity():
    t = _trades([3, 5])
    t[0]["shares"] = 0
    d = ma.decisions(1000, t)
    assert len(d) == 1
    assert d[0]["g"] == pytest.approx(math.log(1008 / 1003) / (10 / 1003))


# ---- the gate --------------------------------------------------------------------

def test_a_clear_winner_passes_the_gate():
    passed, stats, failures = ma.gate(ma.decisions(1000, _winner()), CFG)
    assert passed, failures
    assert stats["decisions"] == 80 and stats["settled_dates"] == 40 and stats["lower_80"] > 0


@pytest.mark.parametrize("n,days,why", [(59, 40, "59 decisions < 60"), (80, 29, "29 settled dates < 30")])
def test_the_gate_needs_the_sample(n, days, why):
    passed, _s, failures = ma.gate(ma.decisions(1000, _winner(n, days)), CFG)
    assert not passed and why in failures


def test_the_gate_needs_a_positive_lower_bound():
    # Mean positive but noisy: the lower 80% bound is below zero.
    noisy = _trades([9 if i % 2 else -8.6 for i in range(80)], days=40)
    decs = ma.decisions(1000, noisy)
    passed, stats, failures = ma.gate(decs, CFG)
    gs = [d["g"] for d in decs]
    mean = sum(gs) / len(gs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in gs) / (len(gs) - 1))
    assert stats["lower_80"] == pytest.approx(mean - 0.8416 * sd / math.sqrt(len(gs)), abs=1e-6)
    assert not passed and any("lower 80% bound" in f for f in failures)


def test_one_lucky_city_day_does_not_pass_the_gate():
    pnls = [1 if i % 2 else -1 for i in range(80)]
    pnls[0] = 50                                     # one city-day makes the whole gain
    t = _trades(pnls, days=40)
    passed, stats, failures = ma.gate(ma.decisions(1000, t), CFG)
    assert stats["top_city_day"]["share"] > 0.25
    assert any("of the gain" in f for f in failures)
    assert not passed


# ---- the posterior and the draw -------------------------------------------------

def test_the_posterior_with_no_data_is_the_prior():
    assert ma.posterior([]) == (0.0, ma.PRIOR_KAPPA, ma.PRIOR_ALPHA, ma.PRIOR_BETA)


def test_the_posterior_is_the_conjugate_update():
    gs = [0.1, 0.3, -0.2, 0.4]
    mu, kappa, alpha, beta = ma.posterior(gs)
    n, xbar = 4, sum(gs) / 4
    assert kappa == ma.PRIOR_KAPPA + n
    assert mu == pytest.approx(n * xbar / (ma.PRIOR_KAPPA + n))           # prior mean 0
    assert alpha == ma.PRIOR_ALPHA + n / 2
    ss = sum((x - xbar) ** 2 for x in gs)
    assert beta == pytest.approx(ma.PRIOR_BETA + ss / 2 + ma.PRIOR_KAPPA * n * xbar ** 2 / (2 * (ma.PRIOR_KAPPA + n)))


def test_draws_centre_on_the_posterior_mean():
    rng = random.Random(1)
    post = ma.posterior([0.2] * 200)
    draws = [ma.draw_mean(post, rng) for _ in range(4000)]
    assert sum(draws) / len(draws) == pytest.approx(post[0], abs=0.005)


def test_a_thin_regime_uses_the_pooled_posterior():
    # 70 NORMAL and 10 SHARP decisions: SHARP is under MIN_REGIME, so the
    # strategy's draw is the NORMAL posterior's and the POOLED one's, mixed.
    decs = ma.decisions(1000, _trades([6 if i % 5 < 3 else -4 for i in range(80)], days=40,
                                      regime=lambda i: "SHARP" if i < 10 else "NORMAL"))
    seen = []
    orig = ma.draw_mean
    try:
        ma.draw_mean = lambda post, rng: seen.append(post) or 0.0
        ma.strategy_draw(decs, random.Random(0))
    finally:
        ma.draw_mean = orig
    pooled = ma.posterior([d["g"] for d in decs])
    normal = ma.posterior([d["g"] for d in decs if d["regime"] == "NORMAL"])
    assert seen == [normal, pooled]                  # regimes in sorted order: NORMAL, SHARP


# ---- weights -----------------------------------------------------------------------

def test_weights_are_proportional_to_the_positive_draws():
    w = ma.capped_weights({"a": 0.1, "b": 0.1, "c": 0.2, "d": 0.2, "e": -0.3}, 0.4)
    assert w == pytest.approx({"a": 1 / 6, "b": 1 / 6, "c": 1 / 3, "d": 1 / 3, "e": 0.0})


def test_the_cap_water_fills():
    w = ma.capped_weights({"a": 0.9, "b": 0.05, "c": 0.05}, 0.4)
    assert w["a"] == pytest.approx(0.4)
    assert w["b"] == pytest.approx(0.3) and w["c"] == pytest.approx(0.3)
    assert sum(w.values()) == pytest.approx(1.0)


def test_one_strategy_gets_the_cap_and_the_rest_stays_in_cash():
    assert ma.capped_weights({"a": 0.5}, 0.4) == pytest.approx({"a": 0.4})
    assert ma.capped_weights({"a": -0.5, "b": 0.0}, 0.4) == {"a": 0.0, "b": 0.0}


def test_a_weight_moves_at_most_the_step_a_night():
    out = ma.step_limited({"a": 0.4, "b": 0.0}, {"a": 0.1, "b": 0.35})
    assert out == {"a": 0.2, "b": 0.25}


def test_a_strategy_that_left_the_portfolio_state_drops_out_at_once():
    out = ma.step_limited({"a": 0.4}, {"a": 0.3, "gone": 0.4})
    assert out == {"a": 0.4}


def test_the_step_never_lets_the_total_pass_one():
    out = ma.step_limited({"a": 0.4, "b": 0.4, "c": 0.4}, {"a": 0.35, "b": 0.35, "c": 0.3})
    assert sum(out.values()) <= 1.0 + 1e-9
    assert all(0 <= v <= 0.4 for v in out.values())


def test_the_allocation_is_reproducible_from_its_version():
    recs = {"a": ma.decisions(1000, _winner()), "b": ma.decisions(1000, _winner(90, 45))}
    w1, d1 = ma.allocate(recs, {}, 0.4, seed="thompson-v1:2026-11-20")
    w2, d2 = ma.allocate(recs, {}, 0.4, seed="thompson-v1:2026-11-20")
    assert (w1, d1) == (w2, d2)
    assert all(0 <= v <= ma.MAX_STEP for v in w1.values())    # first night: at most one step


def test_the_cap_setting_cannot_loosen_the_hard_cap(monkeypatch):
    monkeypatch.setattr(ma, "rest", lambda t, p: [{"value": {"cap_per_strategy": 0.9, "bankroll_usd": 5}}])
    cfg = ma.load_gate()
    assert cfg["cap_per_strategy"] == 0.40 and cfg["bankroll_usd"] == 5


# ---- the nightly run ---------------------------------------------------------------

def _fake(monkeypatch, states, pf_status, trades_by_account):
    calls, logged = [], {}

    def fake_rest(table, params):
        p = dict(params)
        if table == "settings":
            return [{"value": dict(ma.GATE_DEFAULTS)}]
        if table == "strategy_state":
            return [{"strategy_id": s, "state": st} for s, st in states.items()]
        if p.get("kind") == "eq.shadow":
            return [{"account_id": f"acct_{s}", "strategy_id": s, "starting_cash": 1000} for s in states]
        assert p.get("kind") == "eq.portfolio"
        return [{"account_id": "pf", "status": pf_status, "policy": {}}]

    monkeypatch.setattr(ma, "rest", fake_rest)
    monkeypatch.setattr(ma, "rest_all", lambda t, p, order: trades_by_account[p["account_id"][3:]])
    monkeypatch.setattr(ma, "rpc", lambda fn, params: calls.append((fn, params)) or {"ok": True})
    monkeypatch.setattr(ma, "log_run", lambda job, status, rows, detail: logged.update(job=job, status=status, detail=detail))
    return calls, logged


def test_a_passing_strategy_is_promoted_and_the_portfolio_activated(monkeypatch):
    states = {"sA": "shadow", "sB": "shadow", "sC": "research"}
    trades = {"acct_sA": _winner(), "acct_sB": _trades([-1] * 80, days=40), "acct_sC": _winner()}
    calls, logged = _fake(monkeypatch, states, "suspended", trades)
    ma.main(now=NOW)
    fns = [fn for fn, _ in calls]
    assert fns == ["promote_strategy_to_portfolio", "activate_portfolio_account", "set_portfolio_allocation"]
    promote, activate, alloc = (p for _, p in calls)
    assert promote["p_strategy_id"] == "sA" and promote["p_approved_by"] == ma.APPROVER
    assert activate["p_bankroll"] == 10000
    assert set(alloc["p_weights"]) == {"sA"} and alloc["p_weights"]["sA"] <= ma.MAX_STEP
    assert alloc["p_version"] == "thompson-v1:2026-11-20"
    assert logged["job"] == "meta_allocator" and logged["status"] == "ok"
    assert "sC" not in logged["detail"]["looked_at"]           # research is not judged


def test_nothing_is_activated_while_no_strategy_has_earned_it(monkeypatch):
    calls, logged = _fake(monkeypatch, {"sA": "shadow"}, "suspended", {"acct_sA": _winner(40, 20)})
    ma.main(now=NOW)
    assert calls == []
    assert logged["detail"]["looked_at"]["sA"]["failures"]


def test_an_active_portfolio_is_reallocated_even_when_nobody_is_in_it(monkeypatch):
    calls, _ = _fake(monkeypatch, {"sA": "suspended"}, "active", {})
    ma.main(now=NOW)
    assert calls == [("set_portfolio_allocation", {"p_weights": {}, "p_version": "thompson-v1:2026-11-20"})]


def test_a_refused_activation_is_reported(monkeypatch):
    calls, logged = _fake(monkeypatch, {"sA": "portfolio"}, "suspended", {"acct_sA": _winner()})

    def refuse(fn, params):
        if fn == "activate_portfolio_account":
            raise RuntimeError("the portfolio account has history")
        calls.append((fn, params))
    monkeypatch.setattr(ma, "rpc", refuse)
    ma.main(now=NOW)
    assert logged["status"] == "attention" and "has history" in logged["detail"]["activation_error"]
    assert calls == []                                         # no allocation on a desk that is not active
