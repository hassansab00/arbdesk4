"""The heavy page views read stored rows (plan v2 P6.5; WXPredict build 2.E
part 2, R11; 11 Oct).

133 browser reads died at the 8 s statement timeout in the 24 h to 10 Oct
21:45Z, every one a page load's: the pages read about a dozen views computed
live, at once. Six of them, read only by pages, are now wrappers over stored
copies of their own definitions, as the masthead's v_opportunities became on
9 Oct. v_city_status, which the pages and the tick's Candidate gate both read,
counts settled days from the stored hit record; the gate reads
v_city_status_live, the same definition over the live one.
tests/database/heavy-page-cache.cjs holds the database side; these hold the
two installs and the readers to it.
"""
import pathlib
import re

import engine_shadow

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations/20261011100000_the_heavy_page_views_read_stored_rows.sql").read_text()
SQL = (ROOT / "sql/ad4_89_page_cache.sql").read_text()
ORDER = [l.strip() for l in (ROOT / "sql/INSTALL_ORDER.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]

# view -> (stored copy, unique key, the wrapper's order)
STORED = {
    "v_prediction_hindsight": ("mv_prediction_hindsight", "city_key, for_date, called_when, call_order", ""),
    "v_edge_scaling": ("mv_edge_scaling", "edge_bucket", " order by edge_bucket"),
    "v_calibration": ("mv_calibration", "bucket", " order by bucket"),
    "v_edge_realisation": ("mv_edge_realisation", "edge_bucket", " order by ord"),
    "v_model_forecast_skill": ("mv_model_forecast_skill", "city_key, lead_days", ""),
    "v_condition_skill": ("mv_condition_skill", "city_key", ""),
}
DEFINED_IN = {"v_edge_scaling": "ad4_31_predictive.sql", "v_calibration": "ad4_18_databank.sql",
              "v_edge_realisation": "ad4_18_databank.sql", "v_model_forecast_skill": "ad4_25_model_forecast.sql",
              "v_condition_skill": "ad4_24_nws_gridpoint.sql"}


def _specs(text):
    """{view: (mv, key, order)} from a spec loop's VALUES rows."""
    out = {}
    for m in re.finditer(r"\('(v_[a-z_]+)',\s*'(mv_[a-z_]+)',\s*'([^']*)',(.*?)\)(?:,|\s*\) s\()", text, re.S):
        literals = re.findall(r"'((?:[^']|'')*)'", m.group(4))
        out[m.group(1)] = (m.group(2), m.group(3), literals[-1] if literals else None)
    return out


def _refresh(text):
    i = text.index("create or replace function public.refresh_page_cache()")
    return text[i:text.index("end $$;", i)]


def _code(path):
    return "\n".join(l for l in path.read_text(errors="ignore").splitlines() if not l.lstrip().startswith(("#", "//")))


def test_both_installs_store_the_same_six_views_with_the_same_keys_and_orders():
    mig, sql = _specs(MIG), _specs(SQL)
    assert mig == STORED
    for v, spec in STORED.items():
        assert sql[v] == spec, v


def test_each_wrapper_keeps_its_views_order():
    """The wrapper re-applies the ORDER BY its view has, on the column that
    carries it: PostgREST hands rows in the view's order when a page asks
    none (DataBank reads v_edge_realisation so)."""
    for v, f in DEFINED_IN.items():
        text = (ROOT / "sql" / f).read_text()
        body = text[text.index(f"view {v}" if f"view {v} " in text else f"view public.{v}"):]
        body = body[:body.index(";")]
        order = re.search(r"order by\s+(.+?)\s*$", body, re.I | re.S)
        want = STORED[v][2]
        if not want:
            assert order is None, f"{v} has an order the wrapper drops"
            continue
        assert order, v
        got = order.group(1).strip().lower()
        if v == "v_calibration":
            assert "width_bucket(model_prob, 0, 1, 10) as bucket" in re.sub(r"\s+", " ", body.lower())
            assert got in ("bucket", "1", "width_bucket(model_prob, 0, 1, 10)"), got
        elif v == "v_edge_realisation":
            assert "min(ord) as ord" in re.sub(r"\s+", " ", body.lower())
            assert got in ("ord", "min(ord)", "2"), got
        else:
            assert got == want.split("order by ")[1], (v, got)


def test_the_wrapper_keeps_the_views_own_options():
    """CREATE OR REPLACE VIEW without WITH resets security_invoker; three of
    the six are invoker views (20260913101000) and v_city_status is not."""
    for text in (MIG, SQL):
        assert "select coalesce(' with (' || array_to_string(c.reloptions, ', ') || ')', '')," in text
        assert "'create or replace view public.%I%s as select %s from public.%I%s'" in text
        assert "spec.view_name, opts, cols, spec.mv_name, spec.order_by" in text
        assert "execute format('grant select on public.%I to anon, authenticated', spec.mv_name);" in text
    hardening = (ROOT / "supabase/migrations/20260913101000_phase2a_verified_outcome_hardening.sql").read_text()
    for v in ("v_calibration", "v_edge_realisation", "v_edge_scaling"):
        assert f"alter view public.{v} set (security_invoker = true);" in hardening


def test_the_migration_removes_nothing_and_refuses_an_old_copy():
    assert not re.search(r"\bdrop\b", MIG, re.I), "the migration drops nothing"
    assert not re.search(r"\bcascade\b", MIG, re.I)
    assert "is live but a stored copy exists: run sql/ad4_89_page_cache.sql, which rebuilds it" in MIG
    assert "set local lock_timeout = '5s';" in MIG


def test_only_pages_read_the_six():
    """A stored copy is up to half an hour behind: no script, workflow or
    tool reads one of the six, and no page reads a live copy or a stored
    one by name."""
    names = "|".join(STORED)
    for folder in ("scripts", "n8n", "tools", ".github"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json", ".js", ".yml", ".yaml"):
                assert not re.search(rf"\b({names})(_live)?\b", _code(path)), path
    web = [p for p in (ROOT / "web").rglob("*.ts*") if "node_modules" not in p.parts and ".next" not in p.parts]
    read = set()
    for path in web:
        text = path.read_text(errors="ignore")
        read |= set(re.findall(r"""from\("([a-z_]+)"\)""", text))
    for v, (mv, _, _) in STORED.items():
        assert f"{v}_live" not in read and mv not in read, v
    assert {"v_prediction_hindsight", "v_edge_scaling", "v_calibration", "v_edge_realisation",
            "v_model_forecast_skill", "v_condition_skill", "v_prediction_hindsight_summary", "v_city_status"} <= read
    assert "v_city_status_live" not in read


def test_the_candidate_gate_reads_the_live_hindsight():
    """v_city_status counts settled days from v_prediction_hindsight, now the
    stored rows; the gate reads v_city_status_live, derived from it with the
    live hindsight in its place, so it counts what it counted before."""
    import inspect
    assert 'rest("v_city_status_live"' in inspect.getsource(engine_shadow.read_status)
    for text in (MIG, SQL):
        assert r"regexp_matches(def, '\m(public\.)?v_prediction_hindsight\M', 'g')" in text
        assert "if n <> 1 then" in text
        assert (r"regexp_replace(def, '\m(public\.)?v_prediction_hindsight\M', "
                "'public.v_prediction_hindsight_live')") in text
        assert "revoke all on public.v_city_status_live from public, anon, authenticated;" in text
        assert "grant select on public.v_city_status_live to service_role;" in text
    assert "raise exception 'v_city_status_live does not read the live hindsight alone';" in MIG
    # The install form rebuilds hindsight's live view, so it takes the gate's view down first.
    at = SQL.index("drop view if exists public.v_city_status_live;")
    assert at < SQL.index("execute format('drop view if exists public.%I', spec.view_name || '_live');")


def test_the_refresh_carries_them_and_the_hindsight_after_the_hit_history():
    for text in (MIG, SQL):
        body = _refresh(text)
        listed = re.findall(r"'(mv_[a-z_]+)'", body[body.index("array["):body.index("] loop")])
        assert listed[:5] == ["mv_opportunities", "mv_prediction_ladder", "mv_city_ladder_edges",
                              "mv_forecast_convergence_all", "mv_city_hit_history"]
        assert listed[5:] == [mv for mv, _, _ in STORED.values()]
        assert listed.index("mv_city_hit_history") < listed.index("mv_prediction_hindsight"), (
            "v_prediction_hindsight reads v_city_hit_history, the stored hit history")
        assert "refresh materialized view concurrently" in body
    assert _refresh(MIG) == _refresh(SQL)


def test_the_page_cache_installs_after_every_file_that_defines_the_six():
    at = ORDER.index("ad4_89_page_cache.sql")
    for f in set(DEFINED_IN.values()):
        assert ORDER.index(f) < at, f


def test_postgrest_is_told_about_the_gates_view():
    assert MIG.rstrip().endswith("notify pgrst, 'reload schema';")
