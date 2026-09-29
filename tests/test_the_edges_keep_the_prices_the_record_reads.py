"""The edges keep the prices the record reads (plan v2 P1.6 phase 3, step 3.1,
29 Sep).

edges kept every pricing for 14 days (7 under pressure), and two readers take a
superseded row of it: v_hit_ladders the YES price by 18:00 local on the eve,
v_city_hit_history the YES price before the local day. Measured 29 Sep: no
head-to-head day was left before 22 Sep (0 of 335 city-days 13-21 Sep) and no
hit-ladder market price before 23 Sep - the prune had taken them. Each band's
two marks are now frozen into derived_edge_marks, both readers read the frozen
price first, the prune view never offers a mark not frozen yet, and edges
keeps two days.

tests/database/edge-marks.cjs proves the behaviour over the real migration.
This holds the pieces in place: the migration carries the sql/ files' own
text (the live definitions), the freeze runs every night, and the keep is
the prune function's own floor.
"""
import pathlib
import re

import archive_observations as ao
import mirror_to_repo as mirror

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase" / "migrations"
       / "20260929220000_the_edges_keep_the_prices_the_record_reads.sql").read_text(encoding="utf-8")
# v_city_hit_history_live was redefined after 3.1 (audit repair 3 part 2 appended
# the priced centre); the newest definition is the one sql/ad4_85 must equal.
MIG_HISTORY = (ROOT / "supabase" / "migrations"
               / "20260930004500_the_hit_record_shows_the_priced_centre.sql").read_text(encoding="utf-8")


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _between(text, opener, closer):
    i = text.index(opener)
    return text[i:text.index(closer, i + len(opener)) + len(closer)]


def test_the_migration_is_the_files_own_text():
    """sql/ is what a fresh install builds; the migration is what production
    has. They must be the same objects, or the next install undoes the step."""
    e80 = _src("sql/ad4_80_prune_edge_history.sql")
    pieces = [
        _between(e80, "create table if not exists public.derived_edge_marks",
                 "grant select on public.v_edge_marks_live to service_role;"),
        _between(e80, "create or replace view v_prunable_edge_history as", "and u.edge_id is null;"),
        _between(_src("sql/ad4_88_hit_tournament.sql"), "create or replace view public.v_hit_ladders as",
                 "and k.cutoff_at = d.cutoff_at;"),
        _between(_src("sql/ad4_97_evidence_cache.sql"), "create or replace function public.freeze_edge_marks()",
                 "grant execute on function public.freeze_edge_marks() to service_role;"),
    ]
    for piece in pieces:
        assert piece in MIG, f"the migration no longer carries sql/'s text for:\n{piece[:120]}"
    history = _between(_src("sql/ad4_85_city_hit_history.sql"), "create view v_city_hit_history as",
                       "order by m.for_date desc, m.city_key;").replace(
        "create view v_city_hit_history as", "create or replace view public.v_city_hit_history_live as", 1)
    assert history in MIG_HISTORY, "the newest migration of v_city_hit_history_live is not sql/ad4_85's text"


def test_both_readers_take_the_frozen_mark_first_at_their_own_cutoff():
    """A frozen price for another cutoff serves nobody: each reader matches
    the mark on its own cutoff, so a day whose date was corrected falls back
    to the edge instead of reading the wrong evening."""
    ladders = _between(_src("sql/ad4_88_hit_tournament.sql"), "create or replace view public.v_hit_ladders as",
                       "and k.cutoff_at = d.cutoff_at;")
    assert "k.mark = 'eve' and k.cutoff_at = d.cutoff_at" in ladders
    assert re.search(r"case when k\.band_id is not null then k\.market_price\s+else \(select e\.market_price", ladders)
    history = _src("sql/ad4_85_city_hit_history.sql")
    assert "k.mark = 'day' and k.cutoff_at = l.day_starts_at" in history
    assert re.search(r"case when k\.band_id is not null then k\.market_price\s+else e\.market_price end as market_pre",
                     history)


def test_the_marks_are_each_readers_own_cutoff():
    """eve: the newest by 18:00 local the evening before, inclusive, as
    v_hit_ladders reads (<=). day: the newest before the local day began,
    exclusive, as v_city_hit_history reads (<)."""
    view = _between(_src("sql/ad4_80_prune_edge_history.sql"),
                    "create or replace view public.v_edge_marks_live as", "limit 1) x;")
    assert "(((m.resolution_date - 1)::timestamp + interval '18 hours')" in view
    assert "(m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC'))" in view
    assert "e.computed_at <= k.cutoff_at" in view
    assert "(k.mark = 'eve' or e.computed_at < k.cutoff_at)" in view
    assert "e.side = 'YES'" in view


def test_the_prune_never_offers_a_mark_not_frozen():
    view = _between(_src("sql/ad4_80_prune_edge_history.sql"),
                    "create or replace view v_prunable_edge_history as", "and u.edge_id is null;")
    unfrozen = _between(view, "unfrozen_marks as (", ")\n)")
    assert "from public.v_edge_marks_live m" in unfrozen
    assert "d.cutoff_at = m.cutoff_at" in unfrozen, "a mark frozen at another cutoff does not serve the reader"
    assert re.search(r"left join unfrozen_marks u on u\.edge_id = s\.edge_id\s+where r\.rn > 1\s+and u\.edge_id is null;",
                     view)


def test_a_mark_is_frozen_once_its_cutoff_is_six_hours_past_and_never_rewritten():
    """edge_engine stamps a run's rows with the moment the run began, so a row
    before a cutoff can land after it."""
    fn = _between(_src("sql/ad4_97_evidence_cache.sql"), "create or replace function public.freeze_edge_marks()",
                  "$fn$;")
    assert "m.cutoff_at < now() - interval '6 hours'" in fn
    assert "on conflict (band_id, mark) do nothing" in fn
    assert "do update" not in fn


def test_the_freeze_runs_every_night_with_the_other_evidence():
    common = _src("scripts/common.py")
    body = common[common.index("def refresh_feature_cache"):common.index("def log_run")]
    assert 'rpc("freeze_edge_marks")' in body


def test_edges_keep_two_days_the_prune_functions_own_floor():
    spec = ao.TABLES["edges"]
    assert spec["keep_days"] == 2 and spec["min_keep_days"] == 2
    fn = _src("sql/ad4_80_prune_edge_history.sql")
    assert "if p_keep_days < 2 then" in fn, "the database's floor moved - the archive would ask for what it refuses"


def test_the_marks_reach_the_repository():
    spec = mirror.TABLES["derived_edge_marks"]
    assert spec["kind"] == "append" and spec["time"] == "frozen_at"
    assert spec["pk"] == ["band_id", "mark"]
