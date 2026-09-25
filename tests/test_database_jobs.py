"""Two n8n jobs moved into the database (plan v2 P6.2, sql/ad4_91_database_jobs.sql).

P2.2 Paper Maintenance and P4.1 Health Watchdog only ever read and wrote this
database, and cost 8 n8n executions a day between them. pg_cron runs them now.
These tests hold the move to what the n8n versions did.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SQL = (ROOT / "sql" / "ad4_91_database_jobs.sql").read_text()


def _schedules():
    return dict(re.findall(r"cron\.schedule\('([a-z0-9_]+)', '([^']+)'", SQL))


def test_both_jobs_run_at_the_utc_minutes_n8n_ran_them():
    # n8n evaluates cron in UTC+3: '31 */6' fired at 03/09/15/21:31 UTC, '58 */6' at :58
    # (ingest_log, 24-25 Sep: P2.2 21:31 and 03:31, P4.1 03:58, 09:58, 15:58, 21:58).
    assert _schedules() == {"ad4_p2_2_paper_maintenance": "31 3,9,15,21 * * *",
                            "ad4_p4_1_health_watchdog": "58 3,9,15,21 * * *"}


def test_they_log_the_same_job_names_the_workflows_page_reads():
    assert "log_ingest('P2.2_paper_maintenance'" in SQL
    assert "log_ingest('P4.1_health_watchdog'" in SQL
    page = (ROOT / "web" / "app" / "workflows" / "page.tsx").read_text()
    assert 'job: "P2.2_paper_maintenance"' in page and 'job: "P4.1_health_watchdog"' in page


def test_the_workflows_page_switch_still_governs_them():
    assert "should_run('P2.2_paper_maintenance', 'schedule')" in SQL
    assert "should_run('P4.1_health_watchdog', 'schedule')" in SQL


def test_the_status_rules_are_the_n8n_ones():
    # P2.2: unbalanced books error; queued orders or no verdict attention; else ok.
    assert "when v_books_ok is false then 'error'" in SQL
    assert "when v_queued > 0 or v_books_ok is null then 'attention' else 'ok'" in SQL
    # P4.1: a failed check is 'attention', never 'error'.
    assert "case when cardinality(v_failures) > 0 then 'attention' else 'ok' end" in SQL


def test_the_stale_book_threshold_covers_the_book_cadence():
    """P0.3 captures books every 2 hours since 25 Sep; a threshold under that
    fails a healthy desk."""
    tpl = json.loads((ROOT / "n8n" / "P0.3_book_volume_snapshot.template.json").read_text())
    (trig,) = [n for n in tpl["nodes"] if n["type"].endswith("scheduleTrigger")]
    cadence_min = trig["parameters"]["rule"]["interval"][0]["hoursInterval"] * 60
    threshold = int(re.search(r"if v_age > (\d+) then v_failures := v_failures \|\| format\('stale_book", SQL).group(1))
    assert threshold > cadence_min + 140 / 60, (threshold, cadence_min)


def test_nobody_but_the_service_role_can_run_them():
    for fn in ("run_paper_maintenance", "run_health_watchdog"):
        assert f"revoke all on function public.{fn}() from public, anon, authenticated;" in SQL
        assert f"grant execute on function public.{fn}() to service_role;" in SQL


def test_email_stays_off():
    assert "'emailed', false, 'email_enabled', false" in SQL
