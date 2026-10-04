"""P2.2 part 3: decisions name their call, and no refit serves unrecorded (4 Oct).

The external plan's P2.2 acceptance: the page's calls, the paper decisions and
the recorded evaluation name the same forecast identity, and a refit does not
silently replace the incumbent. These tests hold the migration, the tick's
wiring and the suites to that. tests/database/decision-prediction.cjs runs the
view and the registry function on Postgres; tests/test_engine_shadow.py and
tests/test_s10_shadow.py run the tick's side.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase" / "migrations" / "20261004210000_decisions_name_their_call.sql").read_text()
CONTRACT = (ROOT / "supabase" / "migrations" / "20261004190000_one_prediction_contract.sql").read_text()
DOC = (ROOT / "docs" / "P22_PREDICTION_CONTRACT.md").read_text()


def test_a_decision_names_a_call_as_the_contract_names_it():
    """prediction_source takes the contract's recorded_in values, so a
    decision's call joins v_prediction_contract on (prediction_id, recorded_in)."""
    recorded_in = set(re.findall(r"'([a-z0-9_]+_checkpoints)'(?:::text)?\s*as recorded_in|select [a-z]\.\w+::text, '([a-z0-9_]+)'",
                                 CONTRACT))
    sources = {a or b for a, b in recorded_in}
    assert sources == {"prediction_checkpoints", "s10_shadow_checkpoints", "variant_shadow_checkpoints"}, sources
    m = re.search(r"check \(prediction_source in\s*\(([^)]*)\)\)", MIG)
    assert m and {x.strip().strip("'") for x in m.group(1).split(",")} == sources
    assert "((prediction_id is null) = (prediction_source is null))" in MIG


def test_every_link_the_view_can_give_is_documented():
    links = re.findall(r"then '([a-z_]+)'\n", MIG[MIG.index("create or replace view public.v_decision_prediction"):
                                                  MIG.index("comment on view public.v_decision_prediction")])
    assert links + ["not_recorded"] == ["recorded", "signal_path", "no_call", "checkpoint", "same_tick", "not_recorded"]
    for link in links + ["not_recorded"]:
        assert f"`{link}`" in DOC, f"{link} is not explained in docs/P22_PREDICTION_CONTRACT.md"


def test_the_same_tick_window_is_the_measured_one():
    """Live, 4 Oct: the rd1 row lies within 38.7 s of 1,470 of 1,844
    s10_winner decisions, and none lies between 60 s and 10 min."""
    assert "abs(extract(epoch from s.decided_at - d.decided_at)) < 60" in MIG
    assert "s.model_version like 'rd1:%'" in MIG, "before part 3, every S10 decision read rd1"


def test_the_registry_function_is_the_service_role_s_and_runs_hourly_in_the_database():
    assert "revoke all on function public.record_model_versions() from public, anon, authenticated;" in MIG
    assert "grant execute on function public.record_model_versions() to service_role;" in MIG
    assert "revoke all on public.v_decision_prediction from public, anon, authenticated;" in MIG
    assert "grant select on public.v_decision_prediction to service_role;" in MIG
    # pg_cron, not a new Actions schedule (Rule 7); guarded so the harness applies it
    assert "if exists (select 1 from pg_namespace where nspname = 'cron') then" in MIG
    assert "cron.schedule('ad4_record_model_versions', '50 * * * *', 'select public.record_model_versions()')" in MIG
    assert "set search_path = ''" in MIG


def test_a_served_nightly_fit_follows_its_switch_and_hassan_s_decision():
    for family, setting in (("station_correction", "station_correction_pricing"),
                            ("station_mos", "station_mos_pricing"),
                            ("station_width", "station_width_pricing")):
        assert f"'{family}', '{setting}'," in MIG, family
    assert "'served', 'rule:nightly refit (Rule 11); Hassan 4 Oct'," in MIG
    fn = MIG[MIG.index("create or replace function public.record_model_versions()"):MIG.index("comment on function")]
    # SERVED MEANS PRICED (review of #306): what the engine priced with, as
    # every price's label names it - never the coefficient tables
    assert "from public.band_probabilities b join public.model_versions m on m.version_id = b.forecast_version" in fn
    assert "select substring(p.priced_from from %L), p.decided_at from public.prediction_checkpoints p" in fn
    for table in ("derived_station_correction", "derived_mos_coefficients", "derived_station_width"):
        assert table not in fn, f"{table} holds what was fitted, which may never price"
    for rx in ("station-correction:[0-9-]+:[0-9a-f]+", "station-mos:[0-9-]+:[0-9a-f]+", "station-width:[0-9-]+:[0-9a-f]+"):
        assert rx in fn, rx
    assert "window_h      constant numeric := 36;" in fn
    # one horizon: a price label names versions, not leads (review of #306)
    assert "hz := 'as priced';" in fn and "'lead >= '" not in fn
    # superseded only by a version first priced after this one's last price
    assert "where (val ->> 'first')::timestamptz > cur.last_at" in fn
    # served only while its switches are on; retired at once when one goes off
    assert "may_serve := own_on and (f.family = 'station_correction' or corr_on);" in fn
    assert "if newer is not null or not may_serve then" in fn
    # one run's retirements in the order the versions first priced, so the
    # page's tie-break on event_id leaves the newer fit newest (review of #306)
    assert "order by (priced -> r.version ->> 'first')::timestamptz nulls first, r.decided_at, r.event_id" in fn
    # an older fit that never priced is retired once a newer fit supersedes it
    assert "format('superseded by the newer fit %s before it priced', cur.version)" in fn
    # the view marks each family's newest retired version and counts the rest,
    # appended after event_id; the page fetches the standing rows and those only
    view = MIG[MIG.index("create or replace view public.v_learning_status as"):]
    assert "       r.note,\n       r.event_id,\n" in view
    assert "order by r.decided_at desc, r.event_id desc) = 1 as newest_retired" in view
    assert "count(*) filter (where r.state = 'retired') over (partition by r.family)  as retired_in_family" in view
    page = (ROOT / "web" / "components" / "PredictionLineup.tsx").read_text()
    assert '.or("state.neq.retired,newest_retired.is.true")' in page
    assert "## Decided by Hassan, 4 Oct: the nightly refits stay automatic" in DOC


def test_the_tick_writes_the_call_each_decision_acted_on():
    shadow = (ROOT / "scripts" / "engine_shadow.py").read_text()
    s10 = (ROOT / "scripts" / "s10_shadow.py").read_text()
    assert '"prediction_id": pid, "prediction_source": source' in shadow
    assert 'call = (checkpoint_id, "prediction_checkpoints") if checkpoint_id else None' in shadow
    assert 'return entry.get("probs"), ((rid, "s10_shadow_checkpoints") if rid else None)' in shadow
    assert "stored = stored_calls(rows, version, rest_all)" in s10
    # the ladders are filled after the write, from what it stored
    rec = s10[s10.index("def record("):s10.index("def _challenger(")]
    assert rec.index('out["written"] = upsert(') < rec.index("stored_calls(rows, version, rest_all)")


def test_the_suites_run_the_new_test():
    db = json.loads((ROOT / "tests" / "database" / "package.json").read_text())["scripts"]["test"]
    assert "node decision-prediction.cjs" in db


def test_the_decisions_archive_keeps_every_column_of_the_table():
    """Review of #306: archive_observations exports `decisions` from a fixed
    list and then prunes. A column the list leaves out is lost from the
    Release the day its rows are pruned - here the call each decision acted
    on, and the decision_id a paper order names."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import archive_observations as ao
    declared = set()
    for f in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
        sql = f.read_text()
        m = re.search(r"create table if not exists public\.decisions \((.*?)\n\);", sql, re.S)
        if m:
            for line in m.group(1).splitlines():
                w = line.strip().split()
                if w and not w[0].startswith(("constraint", "--", "unique", "primary")):
                    declared.add(w[0])
        declared |= set(re.findall(r"alter table public\.decisions add column if not exists (\w+)", sql))
    spec = ao.TABLES["decisions"]
    assert {"decision_id", "prediction_id", "prediction_source"} <= declared
    assert declared <= set(spec["columns"]), sorted(declared - set(spec["columns"]))
    assert spec["pk"] in spec["columns"], "the key is written to the file, not only paged on"
