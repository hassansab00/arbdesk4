"""Timing describes one day. It must only be applied to that day.

live_weather and v_trade_timing each hold ONE row per city, describing the
day currently in progress there: the running maximum so far, the countdown to
the peak, whether the day is effectively decided. Every consumer keyed them by
city alone, so a market resolving on any other date was handed all of it.

Measured on 2026-09-16, before the fix: for every city the 09-16 and 09-17
rows of v_trade_plan carried an identical running_max_c, implied_max_c,
day_decided and entry window. Tel Aviv read "Day decided - this band is
settled, not traded" on tomorrow's market, fourteen of tomorrow's twenty-two
bands were flagged out_of_reach because TODAY topped out at 26C, and s5 -
whose whole premise is "the day is over and the maximum is locked in this
band" - fired on a 2026-09-17 band because the 16th's maximum landed in it.

Separately, v_opportunities filtered on current_date, which is UTC, while a
maximum-temperature market resolves on the city's LOCAL day. Every city east
of UTC therefore leaked a settled city-day onto the board: 91 signals fired
that way in one 48-hour window, and 594 of 2,244 rows vanished when it was
corrected.

These pin the joins. The SQL is checked as text because the views are built
by dependency order across three files and there is no fixture database that
carries them.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "sql" / "ad4_34_trade_plan.sql"
TIMING = ROOT / "sql" / "ad4_33_control.sql"
RECONCILE = ROOT / "sql" / "ad4_13_reconcile.sql"
ENGINE = ROOT / "scripts" / "signal_engine.py"


def _uncommented(path):
    """The SQL with comment lines removed.

    Every one of these files explains the bug it fixes in prose directly above
    the fix, so a naive substring search matches the explanation and passes on
    code that still has the bug. That has happened three times in this repo.
    """
    return "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("--"))


def test_the_trade_plan_matches_timing_on_the_day_as_well_as_the_city():
    sql = _uncommented(PLAN)
    joins = re.findall(r"left join v_trade_timing t on([^\n]*(?:\n\s+and[^\n]*)*)", sql, re.I)
    assert joins, "no timing join found at all"
    for cond in joins:
        flat = " ".join(cond.split())
        assert "local_date" in flat, (
            "timing joined on the city alone hands today's running maximum to "
            f"every other day's market: {flat}")


def test_the_board_admits_a_city_day_by_that_city_s_own_date():
    sql = _uncommented(RECONCILE)
    assert "m.resolution_date >= current_date\n" not in sql + "\n", (
        "current_date is UTC; a max-temperature market resolves on the city's "
        "local day, so every city east of UTC keeps a settled day on the board")
    assert "m.resolution_date >= (now() at time zone coalesce(c.timezone, 'UTC'))::date" in sql


def test_the_timing_view_states_which_day_it_is_about():
    sql = _uncommented(TIMING)
    assert "as local_date" in sql, (
        "a consumer cannot match on the day unless the view says which day it is")


def test_local_date_is_the_last_column_so_the_view_replaces_in_place():
    """v_city_day_plan and v_campaign_state hang off this chain. Inserting a
    column mid-list turns `create or replace` into a cascade that drops both."""
    sql = _uncommented(TIMING)
    body = sql[sql.index("create or replace view v_trade_timing as"):]
    body = body[: body.index("\nfrom win w")]
    aliases = re.findall(r"\bas\s+([a-z_][a-z_0-9]*)\s*$", body, re.I | re.M)
    assert aliases[-1] == "local_date", \
        f"local_date must be the final column of v_trade_timing, found {aliases[-1]}"


def test_the_engine_refuses_timing_that_states_another_day():
    src = "\n".join(l for l in ENGINE.read_text(encoding="utf-8").splitlines()
                    if not l.strip().startswith("#"))
    assert "def for_this_day(" in src, (
        "signal_engine keyed live_weather and v_trade_timing by city alone; s5 "
        "fired on tomorrow's band because today's maximum landed in it")
    assert 'row.get("local_date")' in src
    assert 'base["resolution_date"]' in src
