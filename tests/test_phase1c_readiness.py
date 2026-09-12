from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260912234500_phase1c_operational_readiness.sql"
COMPONENT = ROOT / "web/components/CityReadiness.tsx"
PAGE = ROOT / "web/app/workflows/page.tsx"


def test_readiness_is_additive_and_cannot_tamper_with_evidence():
    sql = MIGRATION.read_text()
    lower = sql.lower()
    for forbidden in ("update public.", "delete from public.", "truncate public."):
        assert forbidden not in lower
    assert "v_city_day_readiness" in sql
    assert "v_operational_health" in sql
    assert sql.count("security_invoker = true") >= 4


def test_readiness_distinguishes_missing_evidence_from_no_trade_signal():
    sql = MIGRATION.read_text()
    assert "no edge evaluation within 12h" in sql
    assert "fresh_tradeable_edge_bands" in sql
    assert "Missing inputs block evaluation; no positive trade signal does not" in sql


def test_workflow_run_metrics_surface_partial_success():
    sql = MIGRATION.read_text()
    assert "p.failed_count" in sql
    assert "then 'attention'" in sql
    assert "p.completed_count as rows" in sql
    assert "p.requested_count as requested" in sql


def test_existing_workflows_page_adds_the_panel_without_replacing_its_controls():
    page = PAGE.read_text()
    component = COMPONENT.read_text()
    assert "<ScheduleControl" in page
    assert "<ScopeControl" in page
    assert "<CityReadiness />" in page
    assert "v_city_day_readiness" in component
    assert "v_operational_health" in component
    assert "The workflow catalogue" in page

