from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_installers_match_migrations_and_follow_their_dependencies():
    pairs = [('ad4_outcome_health_shared_resolution.sql', '20261113103335_outcome_health_shared_resolution.sql'),
             ('ad4_private_predictive_regression_audit.sql', '20261113103919_private_predictive_regression_audit.sql')]
    order = (ROOT / 'sql/INSTALL_ORDER.txt').read_text().splitlines()
    for installer, migration in pairs:
        assert (ROOT / 'sql' / installer).read_text() == (ROOT / 'supabase/migrations' / migration).read_text()
        assert order.index(installer) > order.index('ad4_page_status.sql')
    audit = (ROOT / 'sql/ad4_private_predictive_regression_audit.sql').read_text()
    for forbidden in ('insert into', 'update public.', 'delete from', 'truncate '):
        assert forbidden not in audit.lower()
