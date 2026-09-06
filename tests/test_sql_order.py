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
NOT_INSTALLED = {"ad4_98_ui_health.sql", "ad4_diagnose.sql", "ad4_99_verify.sql"}


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


# ---------------------------------------------------------------------------
# WRITE ACCESS
#
# n8n read every table and was refused every write:
#     permission denied for table markets (42501)
# The grants that make service_role able to write lived in exactly one file,
# ad4_13_reconcile.sql, in the middle of a 38-file sequence. A database built
# by running a SUBSET - which is what happens every time someone is told "run
# 30 onwards" - therefore had tables the collectors could read and not write,
# and the workflows reported success having saved nothing.
#
# The fix is that no single file is load-bearing for this any more: preflight
# grants as soon as the tables exist, ad4_38 grants on demand and reports, and
# ad4_13 still does it too. These tests keep all three honest.
# ---------------------------------------------------------------------------

GRANT_FILES = ["ad4_00_preflight.sql", "ad4_13_reconcile.sql", "ad4_38_grants.sql"]


def _sql(name):
    return open(os.path.join(SQL, name)).read()


def test_the_write_path_is_granted_by_more_than_one_file():
    """Any ONE of these being skipped must not leave the collectors unable to
    write. That is the whole failure this file set exists to prevent."""
    for f in GRANT_FILES:
        s = _sql(f)
        assert "grant all on all tables in schema public to service_role" in s, (
            f"{f} does not grant service_role the tables")


def test_the_grant_is_blanket_not_a_list_of_table_names():
    """Naming tables is how the last version went out of date: every new sql/
    file adds tables and none of them remembered to come back and grant."""
    s = _sql("ad4_38_grants.sql")
    body = s[: s.index("-- 6. Report.")]
    assert "grant all on all tables" in body
    assert "grant all on all sequences" in body
    assert "grant execute on all functions" in body
    assert "alter default privileges" in body, "tables created later must be covered too"


def test_repairing_the_write_path_does_not_open_it_to_the_browser():
    """The service key gets everything; anon and authenticated get SELECT. A
    repair that quietly widened the browser's access would be worse than the
    bug it fixes."""
    s = _sql("ad4_38_grants.sql")
    assert "revoke insert, update, delete, truncate" in s
    for bad in ["grant all on all tables in schema public to anon",
                "grant insert", "grant update", "grant delete"]:
        assert bad not in s.replace("grant execute on all functions", ""), bad


def test_the_repair_handles_row_level_security_too():
    """ad4_rls.sql turns RLS on for markets, bands and 28 others with a
    SELECT-only policy. A grant alone does not get past that - on Supabase
    service_role is BYPASSRLS, and off Supabase it may not be."""
    s = _sql("ad4_38_grants.sql")
    assert "bypassrls" in s.lower()
    assert "service_role_all" in s, "needs a fallback for a database with no superuser"


def test_the_repair_reports_what_it_found():
    """A 42501 says nothing about which of the three causes it was. The point
    of this file is that after running it you do not have to guess."""
    s = _sql("ad4_38_grants.sql")
    assert "create or replace view v_write_access" in s
    for table in ["markets", "bands", "book_snapshots", "trades_observed",
                  "weather_observations", "weather_forecasts", "live_weather"]:
        assert f"('{table}'" in s, f"v_write_access does not cover {table}"


def test_the_workflows_point_at_the_file_that_fixes_it():
    """The n8n error message and the SQL file have to agree, or the operator
    is sent to a file that does not exist."""
    import glob
    import json

    named = set()
    for path in glob.glob(os.path.join(ROOT, "n8n", "*.json")):
        blob = json.dumps(json.load(open(path)))
        for token in ["ad4_38_grants.sql", "ad4_98_ui_health.sql"]:
            if token in blob:
                named.add(token)
    assert "ad4_38_grants.sql" in named, "no workflow tells you how to fix a 42501"
    for token in named:
        assert os.path.exists(os.path.join(SQL, token)), f"workflows name a missing {token}"
