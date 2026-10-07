"""The page cache keeps the days it shows (WXPredict build 2.A, 7 Oct).

mv_prediction_ladder, the stored copy every page reads through
v_prediction_ladder, keeps yesterday on (current_date - 1 at refresh): 36,640
of its 39,742 rows were for days before yesterday (7 Oct), and no reader read
them. tests/database/page-cache-window.cjs holds the database side. This holds
the readers to it: a page that starts reading the ladder for older days, or a
cache window that stops covering what the pages read, fails here rather than
showing an empty or partial ladder.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations/20261007150000_the_page_cache_keeps_the_days_it_shows.sql").read_text()
SQL = (ROOT / "sql/ad4_89_page_cache.sql").read_text()
WEB = [p for p in (ROOT / "web").rglob("*.ts*") if "node_modules" not in p.parts and ".next" not in p.parts]


def _chain(text, start):
    """The query chain that begins at `start`: up to the end of its statement."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                break
        elif c in ";," and depth == 0:
            break
        i += 1
    return text[start:i]


def test_every_page_read_of_the_ladder_starts_no_earlier_than_yesterday():
    reads = []
    for path in WEB:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'\.from\("v_prediction_ladder"\)', text):
            reads.append((path, _chain(text, m.start())))
    assert len(reads) == 2, f"the ladder's readers changed: {[str(p.relative_to(ROOT)) for p, _ in reads]}"
    for path, chain in reads:
        bound = re.search(r'\.gte\("for_date",\s*(new Date\(\)\.toISOString\(\)\.slice\(0, 10\)|since)\)', chain)
        assert bound, (f"{path.relative_to(ROOT)} reads v_prediction_ladder from a day other than today or "
                       "yesterday (UTC): the cache keeps yesterday on")
    cards = (ROOT / "web/components/CityCards.tsx").read_text()
    # since is the UTC date before today: never earlier than the cache's
    # current_date - 1, which is UTC (the database's TimeZone, 7 Oct).
    assert "const since = isoDay(-1);" in cards
    assert re.search(r"function isoDay\(offsetDays: number\): string \{\s*return new Date\(Date\.now\(\) \+ offsetDays \* 86400000\)\.toISOString\(\)\.slice\(0, 10\);", cards)


def test_the_funnel_planes_read_the_edges_not_the_ladder():
    page = (ROOT / "web/app/predictive/page.tsx").read_text()
    assert 'supabase.from("v_city_ladder_edges").select("edge")' in page
    assert 'supabase.from("v_prediction_ladder").select("band_lo,band_hi")' not in page
    # A failed edge read is said, not drawn as a funnel without its buckets
    # (Codex on #334).
    funnel = page[page.index('relation="v_forecast_convergence"'):]
    funnel = funnel[:funnel.index("</DataState>")]
    assert "cityEdgesQ.loading" in funnel and "cityEdgesQ.error" in funnel and "cityEdgesQ.refresh()" in funnel


def test_the_confidence_view_reads_the_ladder_from_today_on():
    mig = (ROOT / "supabase/migrations/20260927170000_the_card_shows_what_was_priced.sql").read_text()
    body = mig[mig.index("create or replace view public.v_city_prediction_confidence"):]
    body = body[:body.index("$v$")]
    reads = len(re.findall(r"from v_prediction_ladder\b", body))
    assert reads == 3
    assert body.count("for_date >= current_date") == reads


def test_the_cache_keeps_yesterday_on_in_both_installs():
    assert "where v.for_date >= (current_date - 1)" in MIG
    assert "' where v.for_date >= (current_date - 1)'" in SQL
    assert not re.search(r"drop [^;]*\bcascade\b", MIG, re.I), "nothing that depends on the cache may be dropped with it"
    assert "create or replace view public.v_prediction_ladder as select %s from public.mv_prediction_ladder'" in MIG


def test_the_edges_take_the_ladders_own_window():
    """mv_city_ladder_edges repeats v_prediction_ladder_bands' window rather
    than reading it (100 ms against 2.5 s, 7 Oct); the two must not drift."""
    bands = (ROOT / "sql/ad4_68_prediction_ladder_outcomes.sql").read_text()
    newest = (ROOT / "supabase/migrations/20260927170000_the_card_shows_what_was_priced.sql").read_text()
    for text in (bands, newest):
        assert "m.resolution_date >= (current_date - 45)" in text
        assert "m.resolution_date <= (current_date + 16)" in text
    for text in (MIG, SQL):
        edges = text[text.index("create materialized view public.mv_city_ladder_edges"):]
        edges = edges[:edges.index("$v$")]
        assert "m.resolution_date >= (current_date - 45)" in edges
        assert "m.resolution_date <= (current_date + 16)" in edges
        assert "coalesce(ct.status, 'active') = 'active'" in edges
        assert "v_canonical_markets" in edges and "v_canonical_bands" in edges


def test_the_refresh_keeps_the_edges_fresh():
    for text in (MIG, SQL):
        body = text[text.index("create or replace function public.refresh_page_cache()"):]
        assert "'mv_prediction_ladder', 'mv_city_ladder_edges'" in body
        assert "refresh materialized view concurrently" in body


def test_no_script_or_workflow_reads_the_stored_ladder():
    for folder in ("scripts", "n8n", "tools"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json", ".js"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert not re.search(r"\b(mv_prediction_ladder|v_prediction_ladder)\b", text), path
