"""The SQL install order is a fact about the repo, not a note in a doc.

sql/INSTALL_ORDER.txt is what the operator is handed and what the sequence
test runs. A file that exists but is not listed is a file nobody installs; a
file listed but missing is an instruction that fails halfway through. Both
have happened, and both are silent until someone rebuilds the database.
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL = os.path.join(ROOT, "sql")
MANIFEST = os.path.join(SQL, "INSTALL_ORDER.txt")

# Read-only inspection files. They change nothing and are deliberately not in
# the install sequence; the manifest names them in a trailing comment block.
NOT_INSTALLED = {"ad4_diagnose.sql", "ad4_99_verify.sql"}


def _listed():
    out = []
    for line in open(MANIFEST):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def test_every_sql_file_is_listed_exactly_once():
    listed = _listed()
    assert len(listed) == len(set(listed)), "a file is listed twice in INSTALL_ORDER.txt"

    on_disk = {f for f in os.listdir(SQL) if f.endswith(".sql")} - NOT_INSTALLED
    missing = sorted(on_disk - set(listed))
    assert not missing, f"SQL files exist but are in no install order: {missing}"

    phantom = sorted(set(listed) - on_disk)
    assert not phantom, f"INSTALL_ORDER.txt names files that do not exist: {phantom}"


def test_the_read_only_files_are_not_in_the_sequence():
    listed = set(_listed())
    for f in NOT_INSTALLED:
        assert f not in listed, f"{f} inspects a database; it is not part of installing one"
        assert os.path.exists(os.path.join(SQL, f)), f"{f} is named as read-only but does not exist"


def test_dependencies_come_before_their_dependents():
    """The orderings that actually broke a run, asserted rather than remembered.

    Each pair below is a failure that happened: ad4_rpc selects
    v_opportunities.score, which ad4_phase2_ranking adds, and it fails with
    `column "score" does not exist` - a message that names neither file.
    """
    order = {name: i for i, name in enumerate(_listed())}
    pairs = [
        ("ad4_phase2.sql", "ad4_rpc.sql"),               # v_opportunities
        ("ad4_phase2_ranking.sql", "ad4_rpc.sql"),        # v_opportunities.score
        ("ad4_00_preflight.sql", "ad4_phase2.sql"),       # the tables themselves
        ("ad4_18_databank.sql", "ad4_31_predictive.sql"), # fact_forecast_outcome
        ("ad4_20_schedules.sql", "ad4_32_run_scope.sql"), # should_run
        ("ad4_26_temp_trend.sql", "ad4_33_control.sql"),  # v_city_peak_approach
        ("ad4_33_control.sql", "ad4_34_trade_plan.sql"),  # v_trade_timing
        ("ad4_strategies_seed.sql", "ad4_33_control.sql"),# strategies
    ]
    for first, second in pairs:
        assert first in order, f"{first} missing from INSTALL_ORDER.txt"
        assert second in order, f"{second} missing from INSTALL_ORDER.txt"
        assert order[first] < order[second], f"{first} must install before {second}"
