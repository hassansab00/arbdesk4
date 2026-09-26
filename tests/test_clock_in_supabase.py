"""The clock moves from n8n into Supabase (plan v2 P6.2).

n8n's P6.1_clock spent 24 of the desk's ~61 n8n executions a day (25-26 Sep)
starting GitHub workflows. public.clock_tick() does the same from pg_cron with
the Vault token. The cutover must change nothing about WHAT runs WHEN.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIG = open(os.path.join(ROOT, "supabase", "migrations",
                        "20260926130000_the_clock_moves_into_supabase.sql")).read()


def _n8n_clock():
    wf = json.load(open(os.path.join(ROOT, "n8n", "P6.1_clock.template.json")))
    for node in wf["nodes"]:
        m = re.search(r"const CLOCK = (\[.*?\]);\n", node.get("parameters", {}).get("jsCode", ""))
        if m:
            return json.loads(m.group(1))
    raise AssertionError("no CLOCK in the n8n clock")


def _supabase_clock():
    m = re.search(r"jsonb_to_recordset\(\s*'(\[.*?\])'::jsonb", MIG, re.S)
    return json.loads(m.group(1))


def test_the_schedule_is_the_n8n_schedule_entry_for_entry():
    key = lambda e: e["file"]
    assert sorted(_supabase_clock(), key=key) == sorted(_n8n_clock(), key=key)


def test_the_token_stays_in_vault_and_the_browser_cannot_dispatch():
    body = MIG[MIG.index("create or replace function public.clock_tick"):]
    assert "from vault.decrypted_secrets s where s.name = 'github_actions_token'" in body
    assert "security definer" in body.split("$$")[0]
    for fn in ("clock_due(timestamptz)", "clock_tick(timestamptz, boolean)", "clock_check()"):
        assert f"revoke all on function public.{fn} from public, anon, authenticated;" in MIG
    assert "raise notice" not in MIG.lower() or "v_token" not in MIG.split("raise notice")[1][:200]


def test_it_obeys_the_workflows_page_and_checks_githubs_answer():
    assert "public.should_run('P6.1_clock', 'schedule')" in MIG
    assert "h.status_code is distinct from 204" in MIG
    assert "cron.schedule('ad4_clock', '36 * * * *', 'select public.clock_tick()')" in MIG
    assert "cron.schedule('ad4_clock_check', '39 * * * *', 'select public.clock_check()')" in MIG


def test_a_harness_without_cron_net_or_vault_schedules_nothing():
    guard = MIG[MIG.rindex("do $$"):]
    for ns in ("cron", "net", "vault"):
        assert f"nspname = '{ns}'" in guard
