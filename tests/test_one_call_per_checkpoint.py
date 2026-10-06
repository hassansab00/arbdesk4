"""One call per checkpoint (4 Oct; the external improvement plan's P0.1).

prediction_checkpoints holds one row per city, day, checkpoint and engine
version, and the version is the commit the tick ran on. Two hourly ticks share
15 minutes of the 75-minute window, and the nightly bot commits change the
version between them, so 81 checkpoints were captured twice (25 Sep - 3 Oct)
and every score counted both. The migration marks the first capture and the
scores count it alone; the tick stops writing a checkpoint any version holds.
tests/database/frozen-calls.cjs runs the views on a second capture."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(ROOT, "supabase", "migrations", "20261004140000_one_call_per_checkpoint.sql")


def _read(*p):
    return open(os.path.join(ROOT, *p)).read()


def _stmt(text, opener, closer):
    i = text.index(opener)
    return text[i:text.index(closer, i) + len(closer)]


def _flat(s):
    """Whitespace and comments out, so a statement compares by its tokens."""
    s = re.sub(r"--[^\n]*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _statements():
    return re.findall(r"execute \$v\$(.*?)\$v\$;", _read(MIGRATION), re.S)


def test_the_calls_view_is_the_old_one_with_the_first_capture_appended():
    old = _stmt(_read("supabase", "migrations", "20260926090000_hit_and_miss_scores_frozen_calls.sql"),
                "create or replace view public.v_checkpoint_calls as",
                "left join public.bands mb on mb.band_id = m.market_band_id::uuid;")
    new = _statements()[0]
    o, n = _flat(old), _flat(new)
    # the rank, over every capture, before the grade is joined
    assert ("from ( select q.*, row_number() over (partition by q.city_key, q.target_date, q.checkpoint "
            "order by q.decided_at, q.checkpoint_id) = 1 as first_call, count(*) over (partition by "
            "q.city_key, q.target_date, q.checkpoint) as calls_at_checkpoint from public.prediction_checkpoints q "
            ") p join public.fact_checkpoint_outcome f using (checkpoint_id)") in n
    # everything else is the old statement, with two columns appended last
    back = (n.replace(", p.first_call, p.calls_at_checkpoint from ( select q.*, row_number() over (partition by "
                      "q.city_key, q.target_date, q.checkpoint order by q.decided_at, q.checkpoint_id) = 1 as "
                      "first_call, count(*) over (partition by q.city_key, q.target_date, q.checkpoint) as "
                      "calls_at_checkpoint from public.prediction_checkpoints q ) p join",
                      " from public.prediction_checkpoints p join")
             .replace(", cp.first_call, cp.calls_at_checkpoint::int as calls_at_checkpoint from cp", " from cp"))
    assert back == o


def test_the_scoreboard_counts_first_calls_and_nothing_else_changed():
    old = _stmt(_read("supabase", "migrations", "20260926090000_hit_and_miss_scores_frozen_calls.sql"),
                "create or replace view public.v_checkpoint_scoreboard as",
                "group by grouping sets ((c.checkpoint), (c.checkpoint, c.city_key));")
    new = _statements()[1]
    assert _flat(new) == _flat(old).replace("from public.v_checkpoint_calls c group by",
                                            "from public.v_checkpoint_calls c where c.first_call group by")


def test_the_panel_view_counts_first_calls_and_appends_when_and_who():
    old = _stmt(_read("supabase", "migrations", "20260930004500_the_hit_record_shows_the_priced_centre.sql"),
                "create or replace view public.v_prediction_hindsight as",
                "left join public.v_city_hit_history o on o.city_key = c.city_key and o.for_date = c.for_date")
    new = _statements()[2]
    want = (_flat(old)
            .replace("h.market_hit from public.v_city_hit_history h",
                     "h.market_hit, null::timestamp as scheduled_local, null::text as engine_version "
                     "from public.v_city_hit_history h")
            .replace("c.market_hit from public.v_checkpoint_calls c",
                     "c.market_hit, c.local_decision_time, c.engine_version from public.v_checkpoint_calls c")
            + " where c.first_call")
    assert _flat(new) == want


def test_the_migration_only_replaces_where_the_views_exist():
    mig = _read(MIGRATION)
    assert "to_regclass('public.v_checkpoint_calls') is null" in mig
    assert "to_regclass('public.v_prediction_hindsight') is null" in mig
    assert "drop view" not in mig.lower()


def test_the_tick_asks_whether_any_version_holds_the_checkpoint():
    src = _read("scripts", "tick.py")
    body = src[src.index("def _written(keys):"):src.index("def read_stations")]
    assert '"engine_version"' not in body
    assert "held = _written({(c, t, k) for c, t, k, _ in candidates})" in src


def test_the_panel_says_which_moment_it_shows_and_never_calls_pending_a_miss():
    src = _read("web", "components", "PredictionHindsight.tsx")
    # the page reads the rows once for every panel since wave F (6 Oct)
    assert "scheduled_local,engine_version" in _read("web", "app", "predictive", "page.tsx")
    assert "The headline above is the day-ahead call, not this one." in src
    assert '(hit === null ? "pending" : hit ? "hit" : "miss")' in src
    assert '{r.hit ? "hit" : "miss"}' not in src
    assert "${r.city_key}-${r.for_date}-${r.called_when}" in src
