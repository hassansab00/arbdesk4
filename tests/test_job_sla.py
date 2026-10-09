"""Every scheduled job has an SLA, and every SLA follows its job's schedule
(plan v2 P6.3; WXPredict build 2.D, R14; 9 Oct).

run_health_watchdog() fails on a job whose last 'ok' is older than its row in
public.job_sla (v_job_last_ok). That is only worth anything if the table is
complete and its cadences are the real ones: a job left out is never overdue,
and a cadence copied wrong is either always red or never. So the cadences are
read here from the schedulers themselves - the clock's schedule, pg_cron, the
n8n templates, the tick's own hours - not from the table that lists them.
"""
import importlib.util
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
SLA_MIGRATION = MIGRATIONS / "20261009200000_the_watchdog_knows_when_each_job_last_worked.sql"
AD4_91 = (ROOT / "sql" / "ad4_91_database_jobs.sql").read_text()


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sla():
    """{job: (cadence_minutes, cadence, scheduled_by)} from the seed."""
    text = SLA_MIGRATION.read_text()
    block = text[text.index("insert into public.job_sla"):]
    block = block[:block.index("on conflict")]
    rows = re.findall(r"\('([A-Za-z0-9_.]+)', (\d+), '([^']*)', '([^']*)'\)", block)
    out = {job: (int(c), cadence, by) for job, c, cadence, by in rows}
    assert len(out) == len(rows), "a job is listed twice"
    return out


def _rule(cadence):
    """The table's generated column, in Python: one cadence plus max(30 min, cadence / 12)."""
    return cadence + max(30, cadence // 12)


def test_the_rule_is_the_plans_own_examples():
    """Plan v2 P6.3 names the tick at 90 min and the databank and archive at 26 h."""
    sla = _sla()
    assert _rule(sla["tick"][0]) == 90
    assert _rule(sla["databank"][0]) == 26 * 60
    assert _rule(sla["archive_observations"][0]) == 26 * 60
    text = SLA_MIGRATION.read_text()
    assert "generated always as (cadence_minutes + greatest(30, cadence_minutes / 12)) stored" in text


def test_every_job_the_clock_dispatches_has_an_sla_at_its_cadence():
    """The jobs v_run_arrivals expects of each dispatched workflow, at the
    clock's own cadence for that workflow."""
    arrivals = _module("test_run_arrivals")
    clock = _module("test_github_actions").clock()
    sla = _sla()
    for file, job, _ in arrivals._all_expected():
        runs = clock.get(file, 0)
        if not runs:
            continue                               # off the clock (forecasts.yml): another file runs it
        assert job in sla, f"{job} is dispatched by the clock ({file}) and has no SLA"
        cadence = sla[job][0]
        if file == "weather_model.yml":
            assert cadence == 7 * 24 * 60, (job, cadence)    # weekly: Mondays at 08 UTC
        else:
            assert cadence == 30 * 24 * 60 // runs, (file, job, cadence, runs)


def test_the_n8n_jobs_follow_their_templates():
    sla = _sla()
    for job, template in [("P0.2_market_discovery", "P0.2_market_discovery"),
                          ("P0.3_book_volume_snapshot", "P0.3_book_volume_snapshot"),
                          ("P1.5_open_meteo", "P1.5_open_meteo"),
                          ("P0.5_refresh_rules_text", "P0.5_refresh_rules_text")]:
        wf = json.loads((ROOT / "n8n" / f"{template}.template.json").read_text())
        trigger = next(n for n in wf["nodes"] if n["type"].endswith("scheduleTrigger"))
        rule = trigger["parameters"]["rule"]["interval"][0]
        minutes = (rule["hoursInterval"] * 60 if rule["field"] == "hours"
                   else rule["daysInterval"] * 24 * 60)
        assert sla[job][0] == minutes, (job, sla[job][0], minutes)


def test_the_tick_side_jobs_follow_their_hours():
    import ingest_nws
    import ingest_nws_monitor
    sla = _sla()
    for job, module in [("P1.3_nws_forecast", ingest_nws), ("P1.4_nws_gridpoint", ingest_nws),
                        ("P1.2_nws_monitor", ingest_nws_monitor)]:
        hours = sorted(module.HOURS_UTC)
        gaps = {(b - a) % 24 for a, b in zip(hours, hours[1:] + hours[:1])}
        assert len(gaps) == 1, (job, hours)
        assert sla[job][0] == gaps.pop() * 60, (job, hours)


def _cron(name):
    """The last schedule any migration or sql file gives a pg_cron job."""
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")) + sorted((ROOT / "sql").glob("*.sql")):
        for m in re.finditer(rf"cron\.schedule\(\s*'{re.escape(name)}',\s*'([^']+)'", path.read_text()):
            found = m.group(1)
    assert found, f"no pg_cron schedule named {name}"
    return found


def test_the_pg_cron_jobs_follow_their_schedules():
    runs_per_30_days = _module("test_github_actions").runs_per_30_days
    sla = _sla()
    for job, cron_name in [("P6.1_clock", "ad4_clock"), ("P6.1_clock_check", "ad4_clock_check"),
                           ("refresh_page_cache", "ad4_refresh_page_cache"),
                           ("P2.2_paper_maintenance", "ad4_p2_2_paper_maintenance")]:
        runs = runs_per_30_days(_cron(cron_name))
        assert sla[job][0] == 30 * 24 * 60 // runs, (job, cron_name, runs)


def test_every_archive_dataset_has_an_sla():
    """archive_observations.py logs one archive_<dataset> row per dataset; a
    dataset added there must be added here, or it can stop unseen."""
    import archive_observations
    sla = _sla()
    datasets = {"archive_" + key for key in archive_observations.TABLES} | {"archive_pull_releases"}
    assert {j for j in sla if j.startswith("archive_")} == datasets


def test_every_job_with_an_sla_is_one_something_writes():
    """A job no script, function or workflow writes is overdue forever."""
    sources = "".join(p.read_text() for pattern in ("scripts/*.py", "sql/*.sql", "supabase/migrations/*.sql",
                                                     "n8n/*.json")
                      for p in sorted(ROOT.glob(pattern)) if p != SLA_MIGRATION)
    for job in _sla():
        if job.startswith("archive_") and job != "archive_pull_releases":
            continue                                     # f"archive_{name}", checked above
        assert re.search(rf"""["']{re.escape(job)}["']""", sources), f"nothing writes {job}"


def test_the_watchdog_reads_the_sla_and_keeps_its_old_checks():
    i = AD4_91.index("create or replace function public.run_health_watchdog()")
    body = AD4_91[i:AD4_91.index("end $$;", i)]
    assert "to_regclass('public.v_job_last_ok') is not null" in body, "a fresh install without the view still runs"
    assert "past their SLA" in body and "'overdue_jobs'" in body
    # what it did before is still there
    for kept in ("stale_book", "stale_forecast", "failed_jobs_24h", "missed_runs_24h", "not_ok_24h",
                 "no_market_volume", "'emailed', false, 'email_enabled', false"):
        assert kept in body, kept
    # implausible edges are information; any other anomaly still fails
    assert "kind = 'implausible_edge'" in body
    assert "v_anom - v_info_anom > 0" in body
