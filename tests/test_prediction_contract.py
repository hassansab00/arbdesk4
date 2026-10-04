"""P2.2 part 1: one prediction contract and a version registry (4 Oct).

The external plan's P2.2 names the fields one versioned prediction contract
must carry and the states a predictor version moves through. These tests hold
the migration's view and registry to that list, the seed to the design note
(docs/P22_PREDICTION_CONTRACT.md), and the registry to the nightly mirror.
tests/database/prediction-contract.cjs runs them on Postgres.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase" / "migrations" / "20261004190000_one_prediction_contract.sql").read_text()
DOC = (ROOT / "docs" / "P22_PREDICTION_CONTRACT.md").read_text()

# The plan's thirteen fields, as the view names them.
FIELDS = {
    "model family": "model_family",
    "artifact version": "artifact_version",
    "serving role": "serving_role",
    "station": "station",
    "target local date": "target_date",
    "actual as-of time": "as_of",
    "input provenance": "input_provenance",
    "raw forecast": "raw_forecast_c",
    "priced centre": "priced_centre_c",
    "uncertainty": "uncertainty_c",
    "full bucket probabilities": "probs",
    "predicted top": "top_band_id",
    "fallback state": "fallback_state",
}
STATES = ("captured", "fitted", "shadow", "eligible", "served", "retired")


def _view_head():
    body = MIG[MIG.index("create or replace view public.v_prediction_contract"):]
    return body[:body.index("union all")]


def test_the_view_carries_every_field_the_plan_names():
    head = _view_head()
    for words, column in FIELDS.items():
        assert re.search(rf"\b{column}\b", head), f"{words} ({column}) is not in v_prediction_contract"
    assert "as prediction_id" in head, "every call needs an identity"


def test_every_branch_has_the_same_number_of_columns():
    body = MIG[MIG.index("create or replace view public.v_prediction_contract"):]
    body = body[:body.index("comment on view public.v_prediction_contract")]
    branches = body.split("union all")
    assert len(branches) == 3, "engine, S10 and the variant"

    def count(branch):
        sel = branch[branch.lower().index("select") + len("select"):branch.lower().index("\n  from public.")]
        depth, n = 0, 1
        for ch in sel:
            depth += ch == "("
            depth -= ch == ")"
            n += ch == "," and depth == 0
        return n
    assert len({count(b) for b in branches}) == 1, [count(b) for b in branches]


def test_the_roles_are_what_serves_today():
    """The engine's checkpoint is served; S10 and the variants are shadow."""
    body = MIG[MIG.index("create or replace view public.v_prediction_contract"):]
    assert "'served'::text                                         as serving_role" in body
    assert body.count("'shadow'") == 2


def test_the_registry_speaks_the_plan_s_states_and_keeps_its_history():
    m = re.search(r"constraint model_registry_state check \(state in\s*\(([^)]*)\)\)", MIG)
    assert m and tuple(s.strip().strip("'") for s in m.group(1).split(",")) == STATES
    assert "before update or delete on public.model_registry" in MIG
    assert "before truncate on public.model_registry" in MIG
    assert "order by family, version, horizon, decided_at desc, event_id desc" in MIG
    assert "revoke all on public.v_prediction_contract from public, anon, authenticated;" in MIG


def test_the_seed_is_the_design_note_s_table():
    seed = MIG[MIG.index("insert into public.model_registry"):MIG.index("create or replace view public.v_prediction_contract")]
    rows = re.findall(r"\('([a-z_0-9]+)', '([^']+)', '([^']+)', '([a-z]+)'", seed)
    assert len(rows) == 9
    for family, version, horizon, state in rows:
        assert state in STATES
        lines = [l for l in DOC.splitlines() if l.startswith(f"| {family} |")]
        assert lines, f"{family} is seeded but not in the design note's table"
        assert any(f"| {state} |" in l and (version in l or family in ("engine", "station_width", "trajectory",
                                                                       "weather_model", "forecast_postprocess"))
                   for l in lines), (family, version, state)
    assert "where not exists (select 1 from public.model_registry r" in seed, "the seed is written once"


def test_the_note_puts_the_silent_refits_to_hassan():
    assert "## For Hassan: refits that already serve without a candidate step" in DOC
    assert "is your decision, not this PR's" in DOC


def test_every_registry_row_reaches_the_repository():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import mirror_to_repo
    assert mirror_to_repo.TABLES["model_registry"] == {"kind": "append", "time": "decided_at", "pk": ["event_id"]}


def test_the_checkpoint_keeps_the_two_fields_the_contract_needs():
    assert "alter table public.prediction_checkpoints add column if not exists priced_from text;" in MIG
    assert "alter table public.prediction_checkpoints add column if not exists raw_forecast_c numeric;" in MIG
    tick = (ROOT / "scripts" / "tick.py").read_text()
    assert '"priced_from": _priced_from(reasons),' in tick
    assert '"raw_forecast_c": head.get("forecast_max_c"),' in tick
