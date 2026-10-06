"""Calibration has to say which of its states it is in, and the page has to
have a word for each one.

"can we build a status and idicator in te predicitive section were it states
if calibration is pendin or runnin or failed or watever."

Nothing said. calibration.py has run every day since 16 Sep, fitted a map
every time, written a detailed payload to ingest_log and returned - and the
Predictive page showed calibrated-looking numbers without stating that no
calibration is in force. It is not: measured 2026-09-20, the gate wants 30
distinct settlement dates and the desk has 8.

TWO FACTS, AND EITHER ALONE MISLEADS. The countdown is real - 22 more days of
settlements. But on the evidence that exists the fit makes the out-of-sample
Brier WORSE (0.726816 -> 0.741337), so 30 dates is necessary and not
sufficient. A progress bar on its own would promise a switch-on the
measurement does not support, which is why the view reports both and the
component renders both.

NO SECOND OPINION ABOUT THE GATE. The view does not restate
MIN_SETTLEMENT_DATES; it reads `gate_unmet` and `applies`, which the fitting
run itself wrote. Every threshold stays in scripts/calibration.py, and these
tests hold that line.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

VIEW = (ROOT / "sql" / "ad4_78_calibration_status.sql").read_text(encoding="utf-8")
COMPONENT = (ROOT / "web" / "components" / "CalibrationStatus.tsx").read_text(encoding="utf-8")
PAGE = (ROOT / "web" / "app" / "predictive" / "page.tsx").read_text(encoding="utf-8")


def _sql_without_comments():
    """The view's SQL with `--` lines dropped.

    Every threshold and every state name is discussed in the header comment in
    prose that reads exactly like the code, so a scan over the raw file matches
    the explanation instead of the statement. The running-max guard learned the
    same lesson.
    """
    return "\n".join(
        line for line in VIEW.splitlines() if not line.strip().startswith("--")
    )


def _state_case():
    """The `case ... end as state` expression, located by its own boundaries
    rather than by a character count that whitespace can invalidate."""
    sql = _sql_without_comments()
    end = sql.index("as state")
    start = sql.rindex("case", 0, end)
    return sql[start:end]


def _states_the_view_emits():
    """Every literal the view's state CASE can return."""
    states = set(re.findall(r"then '(\w+)'", _state_case()))
    states.update(re.findall(r"else '(\w+)'", _state_case()))
    # The never_run branch is the UNION arm, not the CASE.
    states.add("never_run")
    return states


def _states_the_page_handles():
    look = COMPONENT[COMPONENT.index("const LOOK"): COMPONENT.index("export default")]
    return set(re.findall(r"^\s{2}(\w+):\s*\{", look, re.M))


def test_the_page_has_a_word_for_every_state_the_view_can_report():
    """An unhandled state falls through to the never_run label, which would
    read "Never run" over a calibration that had just failed."""
    missing = sorted(_states_the_view_emits() - _states_the_page_handles())
    assert not missing, f"v_calibration_status can report {missing}, and the page has no label for them"


def test_the_page_does_not_invent_states_the_view_never_reports():
    stale = sorted(_states_the_page_handles() - _states_the_view_emits())
    assert not stale, f"the page labels {stale}, which the view cannot produce"


def test_a_desk_that_never_calibrated_still_returns_a_row():
    """An empty result and a healthy one look identical to a component that
    only renders what it receives."""
    assert "union all" in VIEW.lower()
    assert "not exists (select 1 from public.ingest_log where job = 'calibration')" in VIEW
    assert "'never_run'" in VIEW


def test_failed_and_stale_are_distinguished_from_pending():
    """'Red' cannot tell a job that raised from one that ran and correctly
    declined to apply, and the remedy is different for each."""
    for state in ("failed", "stale", "pending_evidence", "fitted_not_applied", "applied"):
        assert state in _states_the_view_emits(), f"{state} is no longer a reachable state"
    case = _state_case()
    assert case.index("'failed'") < case.index("'stale'") < case.index("'applied'"), (
        "the state CASE is evaluated in order: a failed run must be reported as failed "
        "even if its payload still carries an applies flag from a previous shape"
    )


def test_the_gate_threshold_is_not_restated_in_sql_or_on_the_page():
    """MIN_SETTLEMENT_DATES lives in scripts/calibration.py. The view reads
    gate_unmet, which the fitting run wrote, so the number cannot drift."""
    import calibration

    threshold = str(calibration.MIN_SETTLEMENT_DATES)
    sql = _sql_without_comments()
    body = sql[sql.index("create or replace view"):]
    assert threshold not in body, (
        f"the view hardcodes {threshold}, which is MIN_SETTLEMENT_DATES - two copies of a "
        "gate is how the page starts promising a switch-on the engine will not perform"
    )
    assert "gate_unmet" in body, "the view no longer reads the gate the run itself reported"


def test_the_validation_comparison_is_shown_not_just_the_countdown():
    """The fit currently makes the out-of-sample Brier worse. Showing only
    'N of 30 dates' would read as a countdown to switch-on."""
    assert "validation_improves" in VIEW
    assert "brier_before" in VIEW and "brier_after" in VIEW
    assert "validation_improves" in COMPONENT, "the component drops the one caveat that matters"
    assert "more dates alone will not switch it on" in COMPONENT


def test_the_indicator_is_actually_on_the_predictive_page():
    """A component nobody renders is the same as no component."""
    assert "CalibrationStatus" in PAGE
    assert "<CalibrationStatus />" in PAGE


def test_the_stop_migration_carries_the_shipped_view_verbatim():
    """20261005190000 (WXPredict build, wave A.3) rebuilds the view live. A
    copy that drifted from this file would put a view in production that the
    repository does not describe."""
    mig = (ROOT / "supabase" / "migrations"
           / "20261005190000_five_fits_that_reach_no_price_stop.sql").read_text(encoding="utf-8")
    end = "grant select on public.v_calibration_status to anon, authenticated, service_role;"
    stmt = VIEW[VIEW.index("create or replace view public.v_calibration_status"):VIEW.index(end) + len(end)]
    assert stmt in mig


def test_stopped_is_never_reported_over_a_map_in_force():
    """Stopping the refit does not stop the last map it wrote: the engine reads
    settings.calibration_map, not the job (Codex on #316). So 'stopped' needs
    both: the job no longer expected, and no stored map claiming `applies`.
    tests/database/stopped-fits.cjs runs it."""
    sql = _sql_without_comments()
    fitter = sql[sql.index("fitter as ("):sql.index("\nselect\n")]
    assert "clock_expected_jobs" in fitter
    assert "s.key = 'calibration_map'" in fitter and "'applies'" in fitter
    case = _state_case()
    assert case.index("'stopped'") < case.index("'failed'")
    assert "when f.stopped" in case, "'stopped' is decided by the fitter CTE alone"
