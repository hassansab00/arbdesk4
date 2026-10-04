"""P2.2 part 2: the page reads the contract (4 Oct).

/predictive shows each predictor's learning status and the calls side by side
(main, S10 and the challengers apart), through two owner-rights views. These
tests hold the migration to its grants and its blinding rule, and the page to
reading those two views and nothing under them.
tests/database/prediction-lineup.cjs runs the views on Postgres as anon;
web/tests/lineup.test.cjs runs the table logic.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase" / "migrations" / "20261004200000_the_page_reads_the_contract.sql").read_text()
PART1 = (ROOT / "supabase" / "migrations" / "20261004190000_one_prediction_contract.sql").read_text()
COMPONENT = (ROOT / "web" / "components" / "PredictionLineup.tsx").read_text()
PAGE = (ROOT / "web" / "app" / "predictive" / "page.tsx").read_text()
DOC = (ROOT / "docs" / "P22_PREDICTION_CONTRACT.md").read_text()


def _view(name):
    body = MIG[MIG.index(f"create or replace view public.{name}"):]
    return body[:body.index(";\n")]


def test_anon_reads_the_two_page_views_and_nothing_under_them():
    assert "grant select on public.v_learning_status, public.v_prediction_lineup to anon, authenticated, service_role;" in MIG
    for name in ("v_learning_status", "v_prediction_lineup"):
        assert "security_invoker = true" not in _view(name), f"{name} must run with the owner's rights"
    # the contract and the registry stay the service role's: part 2 grants them to nobody new
    for line in MIG.splitlines():
        if line.startswith("grant "):
            assert "v_prediction_contract" not in line and "v_model_registry" not in line, line
            assert "model_registry " not in line, line


def test_part_1_views_run_as_owner_so_the_page_views_can_read_them():
    """A security_invoker view checks as the caller even when read through an
    owner-rights view: anon would be refused prediction_checkpoints (CLAUDE.md,
    23 Sep)."""
    assert "alter view public.v_prediction_contract set (security_invoker = false);" in MIG
    assert "create or replace view public.v_model_registry with (security_invoker = false) as" in MIG
    # v_model_registry gains blind at the end: create or replace may only append columns
    old = re.search(r"family, version, horizon, state, decided_at, decided_by, evidence, rollback_to, note, event_id\n", PART1)
    assert old
    assert "family, version, horizon, state, decided_at, decided_by, evidence, rollback_to, note, event_id, blind\n" in MIG


def test_the_stage_is_the_plan_s_four_words():
    view = _view("v_learning_status")
    for state, stage in (("captured", "data capture"), ("fitted", "candidate fitting"), ("shadow", "evaluation"),
                         ("eligible", "evaluation"), ("served", "serving")):
        assert f"when '{state}'" in view and f"then '{stage}'" in view, (state, stage)
    assert "else 'retired' end" in view


def test_a_blinded_version_shows_no_past_call_and_no_outcome():
    view = _view("v_prediction_lineup")
    assert "and (c.target_date < current_date or w.winner_band_id is not null)) as withheld" in view
    for col in ("top_band_id", "top_label", "top_prob", "priced_centre_c", "uncertainty_c"):
        assert re.search(rf"case when not s\.withheld then \S+ end\s+as {col}", view), col
    for col in ("winner_band_id", "winner_label"):
        assert re.search(rf"case when not s\.blind then \S+ end\s+as {col}", view), col
    assert "case when not s.blind and s.winner_band_id is not null\n            then s.top_band_id = s.winner_band_id end" in view
    # rd3 and da_floor are blinded, each by its own pre-registration, once
    seed = MIG[MIG.index("insert into public.model_registry (family, version, horizon, state, decided_by, evidence, rollback_to, note, blind)"):]
    seed = seed[:seed.index(";\n")]
    assert "('s10', 'rd3:2026-09-25:555719d4a1', 'same day', 'docs/CHALLENGER_C_PREREG.md'" in seed
    assert "('engine_variant', 'da_floor:v1', 'same day', 'docs/P11_DA_FLOOR_PREREG.md'" in seed
    assert seed.rstrip().endswith("and r.horizon = v.horizon and r.blind)"), "the blinding events are written once"


def test_the_engine_counts_once_per_checkpoint_as_the_record_grades_it():
    view = _view("v_prediction_lineup")
    assert "case when c.model_family = 'engine' then '' else c.artifact_version end" in view
    assert "order by c.as_of, c.prediction_id collate \"C\")" in view
    assert "where c.nth = 1" in view
    # fact_checkpoint_outcome.winner_band_id is text (live, 4 Oct): compared as text
    assert "tb.band_id::text = s.top_band_id" in view and "wb.band_id::text = s.winner_band_id" in view


def test_the_first_s10_file_is_registered_retired():
    assert "select 's10', 'rd1:2026-09-25:389620c0d9', 'same day', 'retired'" in MIG
    assert "| s10 | `rd1:2026-09-25:389620c0d9` |" in DOC and "| retired |" in DOC


def test_the_page_reads_the_two_views_and_nothing_under_them():
    reads = set(re.findall(r'supabase\.from\("([a-z_0-9]+)"\)', COMPONENT))
    assert reads == {"v_learning_status", "v_prediction_lineup"}, reads
    assert '.eq("target_date", date).eq("checkpoint", checkpoint)' in COMPONENT, \
        "one date and checkpoint a read: at most 144 rows live, never the 1,000-row cap"
    assert "<PredictionLineup />" in PAGE
    assert 'import PredictionLineup from "@/components/PredictionLineup";' in PAGE


def test_the_suites_run_the_new_tests():
    db = json.loads((ROOT / "tests" / "database" / "package.json").read_text())["scripts"]["test"]
    assert "node prediction-lineup.cjs" in db
    web = json.loads((ROOT / "web" / "package.json").read_text())["scripts"]["test:routes"]
    assert "node tests/lineup.test.cjs" in web
    routes = json.loads((ROOT / "web" / "tests" / "tsconfig.routes.json").read_text())
    assert "../lib/lineup.ts" in routes["files"]
