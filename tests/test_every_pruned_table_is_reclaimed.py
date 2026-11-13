"""Every archive-pruned table uses routine maintenance without full rewrites."""
import re
from pathlib import Path
import pytest
import archive_observations as ao

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "sql/ad4_66_reclaim_archived_tables.sql").read_text()

def test_every_archived_table_has_autovacuum_and_an_allowed_receipt():
    tune, receipt = SQL.split("create or replace function public.request_reclaim", 1)
    for spec in ao.TABLES.values():
        assert "'"+spec["table"]+"'" in tune
        assert "'"+spec["table"]+"'" in receipt
    assert "autovacuum_enabled=true" in tune
    assert "autovacuum_vacuum_scale_factor=0.05" in tune
    assert "autovacuum_analyze_scale_factor=0.05" in tune

def test_routine_maintenance_never_schedules_full_rewrites():
    assert "cron.schedule(" not in SQL
    assert "cron.unschedule(j.jobid)" in SQL
    assert "vacuum[[:space:](]+.*full" in SQL
    body = SQL.split("create or replace function public.request_reclaim", 1)[1]
    assert "execute format" not in body
    assert "'mode','autovacuum','completed',false" in body
    assert "raise exception" in body and "not a table the archive prunes" in body

def test_receipt_allowlist_has_no_unarchived_table():
    body = SQL.split("v_allowed constant text[] := array[",1)[1].split("];",1)[0]
    assert set(re.findall(r"'([a-z_]+)'",body)) == {s["table"] for s in ao.TABLES.values()}

def test_only_service_role_can_request_maintenance():
    assert "revoke all on function public.request_reclaim(text) from public, anon, authenticated" in SQL
    assert "grant execute on function public.request_reclaim(text) to service_role" in SQL

def test_a_failed_reclaim_request_does_not_fail_the_archive(monkeypatch):
    def boom(fn, params=None):
        raise RuntimeError("maintenance unavailable")
    monkeypatch.setattr(ao, "_rpc", boom)
    result = ao.request_reclaim("research_captures")
    assert result["ok"] is False and "maintenance unavailable" in result["error"]

def test_the_archive_records_the_maintenance_receipt():
    code = (ROOT / "scripts/archive_observations.py").read_text()
    prune = code[code.index("def prune_one("):code.index("def request_reclaim(")]
    assert 'reclaim = request_reclaim(spec["table"])' in prune
    assert '"reclaim": reclaim' in prune
