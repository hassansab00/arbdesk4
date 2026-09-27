"""Retiring a strategy has to mean retired in both engines, and it must not delete.

Five strategies were switched off on 2026-09-22 - s1_buy_low_sell_signal,
s3_concentration, s6_anchor_insurance, s8_two_bucket_cover, s9_ladder_basket -
and the sort was not by P&L. s5_running_max_lock is -32.8c on the dollar, worse
than any of the five, and it stays on. What the five share is the variable
their entry test reads:

    s1  edge_net_pp clears a bar
    s3  ranks by model_prob_yes, requires yes_edge_net_pp > 0
    s6  anchor is max(yes_edge_net_pp)
    s8  ranks by model_prob_yes, gates the pair on min_pair_prob
    s9  gates on ev_per_dollar, computed from sum(model_prob_yes)

all of which reduce to (model - price - fee) > 0. Measured at the D-0 midday
instant, that bet loses in all ten price buckets, -3.9c to -38.2c, eight
outside the interval - and the model it leans on is the weaker of the two
numbers: the market's top two bands contain the winner 26.1% of the time, the
model's top two 15.3%. s5, s7 and s2 record model_prob_yes on the signals they
fire but none of them DECIDES on it, which is the line these tests hold.

THE DEFECT THIS FOUND, which mattered more than the five. v_trade_plan's
would_fire array reimplements each entry test in SQL so the board and the
engine agree about what would happen. Not one of its six arms read
strategies.enabled. So:

  - a retired strategy went on labelling rows "s1 would take YES at 0.14", and
    Hassan would have had no way to tell a live proposal from a dead one;
  - and the thresholds those arms used came from v_strategy_params, which
    coalesces to the PYTHON defaults when a row is absent - so deleting a seed
    row, the other way to retire something, would have pinned the mirror to a
    hard-coded 0.30 with nothing left that could change it.

Retiring a strategy has to mean one thing in both places or it does not mean
anything. Every arm now tests its own sN_enabled, and a missing row coalesces
to false, not to a default that keeps firing.

AND THE BOARD HAD TO STOP ERASING THE REASON. v_strategy_board's verdict
short-circuited on `not s.enabled` to "off - it cannot propose anything", so
the moment a measured verdict was acted on, the board stopped stating it. A
strategy carrying a retirement stamp now reports the stamp.

NOTHING IS DELETED. The five rows stay, their signals stay, and the board goes
on marking that history to settlement. One column moves, and it is the same
column the page toggle writes.
"""

import pathlib
import re

import pytest

import signal_engine
from strategies.base import StrategyConfig


ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations"
            / "20260922150000_five_strategies_that_bet_the_model_against_the_price.sql")
PLAN = ROOT / "sql/ad4_34_trade_plan.sql"
BOARD = ROOT / "sql/ad4_33_control.sql"
SEED = ROOT / "sql/ad4_strategies_seed.sql"

RETIRED = [
    "s1_buy_low_sell_signal",
    "s3_concentration",
    "s6_anchor_insurance",
    "s8_two_bucket_cover",
    "s9_ladder_basket",
]
KEPT = ["s2_combination_arb", "s4_tail_fade", "s5_running_max_lock", "s7_pre_peak_gradient"]


# --------------------------------------------------------------------------
# 1. The migration: exactly those five, nothing deleted, safe to re-run.
# --------------------------------------------------------------------------

def test_the_migration_exists():
    assert MIG.exists(), f"{MIG.name} is the retirement - without it nothing is retired"


def test_the_migration_retires_exactly_the_five():
    sql = MIG.read_text()
    acted_on = set(re.findall(r"'(s\d_[a-z_]+)'", sql.split("update public.strategies")[1]))
    assert acted_on == set(RETIRED), acted_on
    # s4 is in the same family and is NOT retired. The file has to say so - a
    # reader who finds four of five model-vs-price strategies off and the
    # fifth on deserves the reason in the same place as the decision.
    assert "s4_tail_fade" in sql, "the file must say why s4 was left on, not skip it silently"
    assert "s5_running_max_lock is -32.8c" in sql, (
        "the file must say that the sort is not by P&L - the worst per dollar "
        "is one of the ones kept"
    )


def test_the_migration_deletes_nothing():
    sql = MIG.read_text().lower()
    for verb in ("delete from", "drop table", "drop view", "truncate", "alter table"):
        assert verb not in sql, f"{verb} has no business in a retirement"
    assert "set enabled = false" in sql, "retirement is one column moving, and this is the column"


def test_the_migration_will_not_re_disable_something_switched_back_on():
    # `enabled` is a page toggle, not a roster. A migration that re-ran and
    # forced it back to false would silently undo Hassan's click.
    sql = MIG.read_text()
    assert "not (coalesce(extra, '{}'::jsonb) ? 'retired_on')" in sql, (
        "the guard must be the retirement STAMP, not the flag - guarding on "
        "`and enabled` re-retires anything switched back on"
    )


def test_the_migration_records_why():
    sql = MIG.read_text()
    for key in ("retired_on", "retired_because", "retired_record"):
        assert f"'{key}'" in sql, f"a retirement with no {key} is a flag nobody can explain"
    assert "coalesce(extra, '{}'::jsonb) ||" in sql, (
        "the stamp must MERGE into extra - overwriting it drops the strategy's config"
    )


def test_the_migration_states_the_measured_record_for_each():
    # s8 and s9 have four settled signals each. Quoting that as evidence would
    # be a lie; the file says so instead of rounding it up to a verdict.
    sql = MIG.read_text()
    assert "under 30" in sql
    assert "retired on the shared mechanism, not on this" in sql


# --------------------------------------------------------------------------
# 2. The Python engine already asked. Keep it asking.
# --------------------------------------------------------------------------

def test_the_engine_only_loads_enabled_strategies():
    src = (ROOT / "scripts/signal_engine.py").read_text()
    assert '("enabled", "eq.true")' in src, (
        "the engine must ask the database for enabled strategies, not filter afterwards"
    )


def test_the_one_engines_strategies_are_not_run_here(monkeypatch):
    """The tick decides for them (P5.12 part 3b); here they only logged a
    NONE or HOLD per city-day the engine never made."""
    import engine_shadow
    import signal_engine as se
    rows = [{"strategy_id": sid} for sid in ("s1_buy_low_sell_signal",) + engine_shadow.STRATEGIES]
    monkeypatch.setattr(se, "rest", lambda path, params=None, **k: rows)
    assert [c.strategy_id for c in se._enabled_strategies()] == ["s1_buy_low_sell_signal"]


def test_a_disabled_strategy_proposes_nothing():
    cfg = StrategyConfig(strategy_id="s1_buy_low_sell_signal", name="x", side="BOTH",
                         universe=["ALL"], regime_filter=["SHARP"], conflict_class="directional",
                         capital_cap_pct=5.0, max_concurrent=20, enabled=False)
    assert cfg.enabled is False
    src = (ROOT / "scripts/strategies/base.py").read_text()
    assert "if not self.config.enabled:" in src, (
        "base.run() must refuse before it evaluates anything - the enabled "
        "filter on the query is the fast path, not the guarantee"
    )


# --------------------------------------------------------------------------
# 3. The SQL mirror has to agree, which is the defect this found.
# --------------------------------------------------------------------------

def _arms(sql):
    """Every `then 'sN_...' end` arm of v_trade_plan's would_fire, with its body."""
    body = sql.split("as would_fire")[0].split("array_remove(array[")[-1]
    out = []
    for chunk in re.split(r"(?=\s+case when )", body):
        m = re.search(r"then '(s\d_[a-z_]+)' end", chunk)
        if m:
            out.append((m.group(1), chunk))
    return out


def test_the_mirror_has_arms_to_check():
    arms = _arms(PLAN.read_text())
    assert len(arms) >= 6, f"expected v_trade_plan's six would_fire arms, found {len(arms)}"


@pytest.mark.parametrize("n", [1, 3, 4, 5, 7])
def test_every_arm_tests_whether_its_strategy_is_switched_on(n):
    arms = [(sid, body) for sid, body in _arms(PLAN.read_text()) if sid.startswith(f"s{n}_")]
    assert arms, f"no would_fire arm for s{n}"
    for sid, body in arms:
        assert f"p.s{n}_enabled" in body, (
            f"the {sid} arm fires without asking whether {sid} is enabled - "
            "that is the board telling Hassan a retired strategy would trade"
        )


def test_every_strategy_the_mirror_names_has_an_enabled_flag_to_read():
    plan = PLAN.read_text()
    named = {sid for sid, _ in _arms(plan)}
    params = plan.split("create or replace view v_trade_plan")[0]
    for sid in named:
        n = sid.split("_")[0]
        assert f"as {n}_enabled" in params, f"v_strategy_params never defines {n}_enabled"
        assert f"where strategy_id = '{sid}'" in params, (
            f"{n}_enabled must read {sid}'s own row"
        )


@pytest.mark.parametrize("n", [1, 3, 4, 5, 7])
def test_a_missing_strategy_row_reads_as_off_not_as_a_default(n):
    params = PLAN.read_text().split("create or replace view v_trade_plan")[0]
    line = next(l for l in params.splitlines() if f"as s{n}_enabled" in l)
    assert re.search(r"\), *false\) *as", line), (
        f"s{n}_enabled coalesces to something other than false - a deleted row "
        "must read as OFF, the way every threshold beside it reads as its "
        "Python default"
    )


def test_the_enabled_flag_is_read_from_the_table_not_from_extra():
    # The thresholds live in extra; whether it runs does not. Reading `enabled`
    # out of the jsonb would make the switch the page writes and the switch the
    # mirror reads two different things.
    params = PLAN.read_text().split("create or replace view v_trade_plan")[0]
    for line in params.splitlines():
        if "_enabled" in line and "select" in line:
            assert "select enabled from strategies" in line, line


# --------------------------------------------------------------------------
# 4. The board must not erase the reason the moment the switch is thrown.
# --------------------------------------------------------------------------

def test_the_board_states_a_retirement_instead_of_just_off():
    board = BOARD.read_text()
    verdict = board.split("as verdict")[0]
    off = verdict.index("'off - it cannot propose anything'")
    stamp = verdict.index("? 'retired_on'")
    assert stamp < off, (
        "the retirement arm has to come BEFORE the bare `not s.enabled` arm, "
        "or it is unreachable and the board says 'off' for everything"
    )
    assert "s.extra ->> 'retired_record'" in verdict


def test_the_board_falls_back_to_the_reason_when_there_is_no_record():
    verdict = BOARD.read_text().split("as verdict")[0]
    assert "coalesce(s.extra ->> 'retired_record'" in verdict
    assert "s.extra ->> 'retired_because'" in verdict


# --------------------------------------------------------------------------
# 5. The line the five are on: what the entry test READS.
# --------------------------------------------------------------------------

# The deciding line in each, quoted. A regex over column names would call s1
# model-free - it reads the edge through a local named `edge_net` - and calling
# the headline retirement model-free is worse than having no test.
DECIDES_ON_THE_MODEL = {
    "s1_buy_low_sell_signal": ["if edge_net <= bar:"],
    "s3_concentration":       ["key=lambda b: (b.model_prob_yes or 0.0)",
                               "band.yes_edge_net_pp <= 0"],
    "s6_anchor_insurance":    ["key=lambda b: (b.yes_edge_net_pp or -1e9)"],
    "s8_two_bucket_cover":    ["ladder.sort(key=lambda b: b.model_prob_yes",
                               "prob = a.model_prob_yes + b2.model_prob_yes"],
    "s9_ladder_basket":       ["prob = sum(b.model_prob_yes for b in legs)",
                               'm["ev_per_dollar"] < min_ev'],
}


@pytest.mark.parametrize("sid", RETIRED)
def test_each_retired_strategy_decides_on_the_model(sid):
    src = (ROOT / f"scripts/strategies/{sid}.py").read_text()
    for line in DECIDES_ON_THE_MODEL[sid]:
        assert line in src, (
            f"{sid} was retired as a model-vs-price strategy on the strength of "
            f"`{line}`, and that line is gone - either it no longer decides on "
            "the model, in which case the retirement needs re-arguing, or it "
            "moved and this test needs re-pointing. Both need a human."
        )


def test_the_retired_set_is_the_documented_set():
    assert set(DECIDES_ON_THE_MODEL) == set(RETIRED)


@pytest.mark.parametrize("sid", ["s2_combination_arb", "s5_running_max_lock",
                                 "s7_pre_peak_gradient"])
def test_the_kept_strategies_do_not_decide_on_the_model(sid):
    src = (ROOT / f"scripts/strategies/{sid}.py").read_text()
    gates = [l for l in src.splitlines()
             if re.match(r"\s*(if|elif|while)\b", l) and "edge_net_pp" in l]
    assert not gates, (
        f"{sid} is kept BECAUSE it does not bet the model against the price, "
        f"and it now gates on an edge: {gates}"
    )


def test_recording_the_model_on_a_signal_is_not_deciding_on_it():
    # s5 and s7 both carry prob_at_fire=band.model_prob_yes. That is the
    # decision lineage doing its job, and it is not a reason to retire them -
    # this test exists so the distinction is not lost the next time someone
    # greps for model_prob_yes and finds seven hits.
    for sid in ("s5_running_max_lock", "s7_pre_peak_gradient"):
        src = (ROOT / f"scripts/strategies/{sid}.py").read_text()
        assert "prob_at_fire=band.model_prob_yes" in src


# --------------------------------------------------------------------------
# 6. The seed cannot quietly turn one back on.
# --------------------------------------------------------------------------

def test_the_seed_still_ships_every_strategy_disabled():
    seed = SEED.read_text()
    values = seed.split("on conflict")[0]
    ids = set(re.findall(r"\('(s\d_[a-z_]+)', ", values))
    assert set(RETIRED) | set(KEPT) <= ids, ids
    # The insert lists ... capital_cap_pct, max_concurrent, enabled, extra, so
    # the flag is the literal between max_concurrent and the extra jsonb.
    flags = re.findall(r", *\d+, *(true|false), *$", values, re.M)
    assert len(flags) == len(ids), f"{len(flags)} enabled literals for {len(ids)} strategies"
    assert set(flags) == {"false"}, (
        "a seeded strategy must ship disabled - nothing trades until it is "
        "switched on deliberately"
    )


def test_the_seed_cannot_re_enable_what_the_migration_retired():
    assert "on conflict (strategy_id) do nothing" in SEED.read_text(), (
        "the seed upserting over an existing row would hand a retired "
        "strategy its seed `enabled` back on every install"
    )
