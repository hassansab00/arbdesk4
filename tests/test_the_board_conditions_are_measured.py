"""What a strategy is waiting for has to be measured, and stay true.

"i want all confiured and ready to deploy wen selected." They are all
configured - nine implementations, nine in REGISTRY, nine enabled, and on the
"Wide edge, all US" desk all nine ticked. Five of them had still never proposed
anything, and the page's only account of that was "nothing has met its
conditions", which names no condition and so cannot be acted on.

v_board_conditions counts the preconditions instead, and the Strategies page
pairs each strategy with the one it depends on. Two things can rot:

  1 THE COPIED GATE. s5's gate lives in scripts/signal_engine.py as three
    boolean tests, and ad4_77_board_conditions.sql restates them to count how
    many cities pass. That is a deliberate second copy - the number is
    otherwise invisible - and a second copy without a test is a drift waiting
    to happen. Measured 20 Sep it was ONE city of 54, because 42 have no
    station feed at all, so this is the difference between "s5 is broken" and
    "s5 is a twelve-city strategy and today one of them qualifies".

  2 A NEW STRATEGY WITH NOTHING SAID ABOUT IT. Add a tenth to REGISTRY and it
    appears on the page with no line saying what it needs, which is exactly
    the silence this work removed.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

CONDITIONS_SQL = (ROOT / "sql" / "ad4_77_board_conditions.sql").read_text(encoding="utf-8")
ENGINE = (ROOT / "scripts" / "signal_engine.py").read_text(encoding="utf-8")
PAGE = (ROOT / "web" / "app" / "strategies" / "page.tsx").read_text(encoding="utf-8")


def _s5_gate_in_engine():
    """The three tests signal_engine.py ANDs together for s5_allowed."""
    block = ENGINE[ENGINE.index("s5_allowed=bool("):]
    return block[: block.index("),") + 1]


def _lock_eligible_expression():
    """Just the lock_eligible filter, not a window around it.

    The first version of this took 600 characters before `as lock_eligible`,
    which reached back into the station_backed count - so deleting the station
    test from the gate still found the words `source_kind` nearby and passed.
    A test that can be satisfied by a neighbouring line is not testing this
    line.
    """
    end = CONDITIONS_SQL.index("as lock_eligible")
    start = CONDITIONS_SQL.rindex("count(*) filter", 0, end)
    return CONDITIONS_SQL[start:end]


def test_the_lock_gate_counts_what_the_engine_actually_requires():
    """All three parts, or the count means something the engine does not."""
    engine = _s5_gate_in_engine()
    sql = _lock_eligible_expression()
    for part, why in (
        ("day_decided", "s5 locks a maximum, which is meaningless before the day is decided"),
        ("running_max_basis", "a running max from ONE reading is a floor, not a maximum"),
        ("source_kind", "locking a model's interpolation locks a number nobody measured"),
    ):
        assert part in engine, f"the engine no longer gates s5 on {part}"
        assert part in sql, (
            f"v_board_conditions counts lock_eligible without {part}, but the engine "
            f"requires it - {why}. The number on the page would overstate what s5 can do."
        )


def test_the_lock_gate_is_a_conjunction_in_both():
    """Any one of the three on its own passes far more cities: 21 day-decided,
    40 series-based, 12 station-backed, but 1 with all three."""
    engine = _s5_gate_in_engine()
    assert engine.count("and") >= 2, "the engine's s5 gate stopped being a conjunction"
    sql = _lock_eligible_expression()
    assert sql.count(" and ") >= 2, (
        "lock_eligible is no longer the conjunction the engine requires, so it counts "
        "cities s5 would refuse"
    )


def test_every_registered_strategy_says_what_it_needs():
    """The silence this work removed must not come back for a tenth strategy."""
    from strategies import REGISTRY

    needs = set(re.findall(r"^\s{2}(s\d+_\w+):\s*\{", PAGE, re.M))
    missing = sorted(set(REGISTRY) - needs)
    assert not missing, (
        f"these are in REGISTRY with no line saying what they are waiting for: {missing}. "
        "A strategy that proposes nothing and explains nothing is the exact state this fixed."
    )


def test_no_line_describes_a_strategy_that_does_not_exist():
    """The other direction: a NEEDS entry for a removed strategy is a promise
    about code that is gone."""
    from strategies import REGISTRY

    needs = set(re.findall(r"^\s{2}(s\d+_\w+):\s*\{", PAGE, re.M))
    stale = sorted(needs - set(REGISTRY))
    assert not stale, f"described on the page but not in REGISTRY: {stale}"


@pytest.mark.parametrize("column", ["lock_eligible", "inside_peak_window"])
def test_a_counted_condition_is_actually_selected_by_the_view(column):
    """The page reads these by name off v_board_conditions. A rename in the
    view and not the page shows an empty count rather than an error."""
    assert f"as {column}" in CONDITIONS_SQL, f"{column} is no longer produced by the view"
    assert f'"{column}"' in PAGE, f"the page no longer reads {column}"
