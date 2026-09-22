"""The observation gate, the sixth retirement, and two things measurement killed.

FOUR STRATEGY-LAYER ITEMS, and two of them end in "no".

ITEM 4 - THE INTERNATIONAL UNDER-READ - IS REFUTED, NOT FIXED. 27 of 30
remaining settlement misses are hourly-only cities where the true peak falls
between 24 daily reports, and the obvious remedy is a per-city bias
correction. Measured:

    a single global offset      delta = 0 is already optimal. Every non-zero
                                delta from -1.0 to +2.0 scores equal or worse
                                (90.7% at 0.0 and 0.1, 89.3% at 0.5).
    a per-city offset, fitted   train on the first half of each city's settled
    on half and tested on the   ladders, test on the second: 277 -> 278 of
    other                       312. One row in 312, and only 7 test rows even
                                drew a non-zero offset.

One row is noise. The residual is not a systematic offset that can be fitted
away; it is irreducible from a 24-sample feed. What would fix it is a second
source, and that is a data decision, not a correction. This is written down so
nobody spends another afternoon re-deriving it.

ITEM 5 - THE OBSERVATION GATE - IS THE ONE THAT SHIPPED. s5 and s7 are built
entirely on the station feed, and that feed names the venue's winning band 9
times in 10 across 29 cities and about 2 in 3 across three of them. Both were
firing everywhere on identical terms. They now carry min_observation_trust and
a city under it is not theirs to trade.

ITEM 6 - s4_tail_fade - retired, sixth of the same family. 451 marked signals,
-2.2c on the dollar, 77.4% of its calls right. That last number is why it
outlived the other five by a day: being right three times in four is a good
rule that still loses money, because a NO leg on a tail band costs 90-odd
cents to win a few.

ITEM 9 - s7's CADENCE - IS UNDER-SAMPLED, NOT BLOCKED. Measured across the
roster by turning each city's peak hour into a UTC instant and asking which of
pipeline_intraday's six daily runs lands in the 60 minutes before it:
15 of 48 cities are caught, and all 48 would be at an hourly cadence. Hourly
on Actions costs ~540 runs a month against a 430 budget, so it needs n8n - a
second signal writer, which is a design change. 15 cities a day is ~105
city-days a week, which is enough to find out whether s7 works at all. Build
the cadence for a strategy that has earned it, not for one that has never
fired.
"""

import pathlib
import re

import pytest

from strategies.base import BandView, Strategy, StrategyConfig


ROOT = pathlib.Path(__file__).resolve().parents[1]
TRUST_MIG = (ROOT / "supabase/migrations"
                  / "20260922230000_a_rule_is_only_as_good_as_the_thermometer_under_it.sql")
S4_MIG = (ROOT / "supabase/migrations" / "20260922234500_the_sixth_of_the_same_family.sql")
PLAN = ROOT / "sql/ad4_34_trade_plan.sql"
SEED = ROOT / "sql/ad4_strategies_seed.sql"


class _S(Strategy):
    def entry_signals(self, ctx):
        return []

    def exit_signals(self, ctx, open_positions):
        return []


def _strategy(floor=None):
    extra = {} if floor is None else {"min_observation_trust": floor}
    return _S(StrategyConfig("sX", "x", "YES", ["ALL"], [], "directional",
                             5.0, 10, enabled=True, extra=extra))


def _band(trust=None, city="nyc"):
    return BandView(
        band_id="b", city_key=city, resolution_date="2026-09-22",
        band_lo=20.0, band_hi=21.0, open_low=False, open_high=False,
        band_label="b", unit="C", model_prob_yes=0.3,
        yes_price=0.2, no_price=0.8, yes_edge_net_pp=0.05, no_edge_net_pp=0.0,
        yes_tradeable=True, yes_block_reason=None, no_tradeable=True,
        no_block_reason=None, confidence=0.7, regime_label="SHARP",
        market_state="open", observation_trust=trust)


# --------------------------------------------------------------------------
# Item 5: the gate.
# --------------------------------------------------------------------------

def test_a_city_below_the_floor_is_not_this_strategys_to_trade():
    assert _strategy(0.80).applies_to(_band(trust=0.667)) is False


def test_a_city_above_the_floor_is():
    assert _strategy(0.80).applies_to(_band(trust=0.95)) is True


def test_exactly_at_the_floor_passes():
    assert _strategy(0.80).applies_to(_band(trust=0.80)) is True


def test_unmeasured_is_not_untrustworthy():
    """None means fewer than ten settled ladders, not a bad thermometer.

    Reading it as zero would stop every new city trading the day this
    shipped, which is the opposite of what the measurement says.
    """
    assert _strategy(0.80).applies_to(_band(trust=None)) is True


def test_a_strategy_with_no_floor_is_untouched():
    assert _strategy(None).applies_to(_band(trust=0.10)) is True


def test_the_gate_still_defers_to_the_switch():
    s = _strategy(0.80)
    s.config.enabled = False
    assert s.applies_to(_band(trust=1.0)) is False


def test_the_gate_lives_in_the_base_class():
    src = (ROOT / "scripts/strategies/base.py").read_text()
    assert "_city_thermometer_is_good_enough" in src
    assert src.count("min_observation_trust") == 1, (
        "two copies of this rule in two strategies is two rules that drift"
    )


def test_the_engine_reads_the_column_not_the_view():
    src = (ROOT / "scripts/signal_engine.py").read_text()
    assert 'observation_trust=trust.get(city)' in src
    assert '"select", "city_key,observation_trust"' in src
    # The view is NAMED in the comment explaining why it is not queried -
    # that is the point of the comment. What must not appear is a read of it.
    assert '"v_settlement_agreement"' not in src, (
        "that view needs band_contains() out of ad4_34 and installs after it; "
        "the column is what every file can read at any point in the install"
    )


@pytest.mark.parametrize("sid", ["s5_running_max_lock", "s7_pre_peak_gradient"])
def test_the_observation_strategies_seed_a_floor(sid):
    seed = SEED.read_text()
    block = seed.split(f"('{sid}'")[1].split("),\n\n")[0]
    assert "min_observation_trust" in block, f"{sid} is built on the feed and sets no floor"


def test_the_sql_mirror_gates_the_same_three_arms():
    sql = PLAN.read_text()
    assert sql.count("coalesce(ct.observation_trust, 1) >= p.s5_min_trust") == 1
    assert sql.count("coalesce(ct.observation_trust, 1) >= p.s7_min_trust") == 2, (
        "s7 has a YES arm and a NO arm and both read the thermometer"
    )
    assert "coalesce(ct.observation_trust, 0)" not in sql, (
        "coalescing to 0 makes unmeasured mean untrustworthy in the mirror "
        "while it means the opposite in Python"
    )


def test_the_mirrors_default_floor_changes_nothing_by_itself():
    sql = PLAN.read_text()
    params = sql.split("create or replace view v_trade_plan")[0]
    for n in ("s5", "s7"):
        line = next(l for l in params.splitlines() if f"as {n}_min_trust" in l)
        assert ", 0)" in line, (
            f"{n}_min_trust must default to 0 - a strategy that has not set a "
            "floor behaves exactly as before"
        )


def test_the_new_column_is_appended_not_inserted():
    """create or replace view can only ADD at the end.

    Placing observation_trust beside reading_age_min, where a reader would
    look for it, makes it an insert into the column list and Postgres refuses
    with 42P16 - the same trap v_peak_hour_coverage hit.
    """
    sql = PLAN.read_text()
    tail = sql[sql.index("end                                                                    as action"):]
    assert "ctb.observation_trust" in tail
    assert tail.index("as observation_trust") < tail.index("from base b")


def test_the_nightly_refresh_runs_after_settlement_freezes():
    y = (ROOT / ".github/workflows/pipeline_daily.yml").read_text()
    assert "refresh_observation_trust" in y
    assert y.index("Freeze settled city-days") < y.index("Refresh observation trust"), (
        "the agreement is judged against ladders that have settled - refreshing "
        "before the freeze measures yesterday"
    )


def test_the_column_is_in_the_preflight_registry():
    assert "('cities','observation_trust','numeric')" in (ROOT / "sql/ad4_00_preflight.sql").read_text()


def test_the_refresh_keeps_a_measurement_it_cannot_remake():
    sql = TRUST_MIG.read_text()
    assert "a.observation_trust is not null" in sql, (
        "a city the view cannot judge today must keep yesterday's number, not "
        "be reset to null because this week's window happens to be short"
    )


# --------------------------------------------------------------------------
# Item 6: the sixth retirement.
# --------------------------------------------------------------------------

def test_s4_is_retired_on_the_family_argument_and_its_own_number():
    sql = S4_MIG.read_text()
    assert "s4_tail_fade" in sql and "set enabled = false" in sql
    assert "451 marked" in sql and "-2.2c" in sql
    assert "77.4%" in sql, (
        "the hit rate is the interesting number here - right three times in "
        "four and still losing - and leaving it out makes the retirement look "
        "like it was losing obviously"
    )


def test_s4_keeps_the_same_stamp_guard_as_the_five():
    sql = S4_MIG.read_text()
    assert "not (coalesce(extra, '{}'::jsonb) ? 'retired_on')" in sql, (
        "without the stamp guard a re-run undoes a deliberate re-enable"
    )
    # "NOTHING IS DELETED" is in the prose, so test the statements, not the file.
    body = sql[sql.index("update public.strategies"):]
    for verb in ("delete", "drop ", "truncate"):
        assert verb not in body.lower()


def test_the_retirement_says_whose_decision_it_was():
    sql = S4_MIG.read_text().lower()
    assert "was not the decision taken" in sql and "checklist" in sql, (
        "s4 was flagged twice as Hassan's call. Acting on it without saying "
        "that is how a scope quietly widens."
    )


# --------------------------------------------------------------------------
# Items 4 and 9: the two that measurement answered with "no".
# --------------------------------------------------------------------------

def test_the_refuted_offset_is_written_down_where_it_will_be_found():
    doc = (ROOT / "docs/OPEN_ITEMS.md").read_text()
    assert "277 -> 278" in doc or "277 → 278" in doc, (
        "a refuted idea with no number attached gets retried"
    )


def test_the_s7_cadence_measurement_is_written_down():
    doc = (ROOT / "docs/OPEN_ITEMS.md").read_text()
    assert "15 of 48" in doc
    assert "430" in doc, "the budget is why hourly is not simply switched on"
