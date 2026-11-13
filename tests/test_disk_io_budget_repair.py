from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def test_installer_and_migration_remain_identical_and_install_last():
    sql = (ROOT / 'sql/ad4_disk_io_budget_repair.sql').read_text()
    old = (ROOT / 'supabase/migrations/20261112234255_disk_io_budget_repair.sql').read_text()
    correction = (ROOT / 'supabase/migrations/20261113081006_cache_source_invalidation.sql').read_text()
    pattern = r'create or replace function arbdesk_private.page_cache_source_token\(\).*?revoke all on function arbdesk_private.page_cache_source_token\(\) from public, anon, authenticated;'
    original = re.search(pattern, old, re.S).group()
    current = re.search(pattern, correction, re.S).group()
    assert sql == old.replace(original, current)
    assert "c.relkind in ('r','p')" in current
    order = [s.strip() for s in (ROOT / 'sql/INSTALL_ORDER.txt').read_text().splitlines()
             if s.strip() and not s.startswith('#')]
    assert order.index('ad4_disk_io_budget_repair.sql') > order.index('ad4_page_status.sql')

def test_cache_controls_keep_time_limits_and_expensive_work_off_the_hourly_path():
    sql = (ROOT / 'sql/ad4_disk_io_budget_repair.sql').read_text()
    fast = sql.split("if p_group='fast' then",1)[1].split("elsif p_group='research'",1)[0]
    assert 'mv_prediction_hindsight' not in fast
    assert 'mv_prediction_ladder' in fast
    assert "statement_timeout = ''120s''" in sql and "statement_timeout = ''240s''" in sql
    assert "set work_mem = '8MB'" in sql
    assert "date_trunc('hour'" in sql and "relname<>'ingest_log'" in sql
