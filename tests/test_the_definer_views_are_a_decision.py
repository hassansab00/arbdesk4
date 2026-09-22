"""83 advisor ERRORs: one was a defect, eighty-two are the design.

Supabase's linter reported on 2026-09-22:

    ERROR  82  security_definer_view
    ERROR   1  rls_disabled_in_public          derived_model_promotion
    WARN   32  function_search_path_mutable
    WARN   24  anon can execute a SECURITY DEFINER function
    WARN   24  authenticated can execute one
    WARN    1  materialized_view_in_api        mv_venue_band_resolution
    INFO   16  rls_enabled_no_policy

THE EIGHTY-TWO ARE LOAD-BEARING. Eight tables carry per-user RLS -
desk_members, paper_accounts, paper_activity, paper_orders,
paper_position_settlements, paper_positions, paper_trade_plans,
research_captures - and every one of their policies is granted to
`authenticated` alone. The web app never signs in: web/lib builds one client
from NEXT_PUBLIC_SUPABASE_ANON_KEY and there is no signIn, signUp, getSession
or setSession anywhere in web/. Every read is `anon`.

So setting security_invoker on v_paper_desks, v_strategy_desk_board,
v_paper_desk_integrity, v_data_freshness or v_prunable_resolution_evidence
would apply an authenticated-only policy to an anonymous caller and return
nothing. The desk pages would go blank. The definer view IS how a
single-operator, anon-key app reads its own per-user tables, and the access
boundary is Vercel's deployment protection rather than Postgres RLS.

THAT IS A PRECONDITION, NOT A PERMANENT TRUTH, and it is the whole reason
this file exists. It holds while there is one operator and no sign-in. The
moment the app gains an auth flow, those five views start serving one user
another user's desk, and the lint stops being noise. The test below fails
when that day comes, which is the only honest way to hold a "we accept this"
decision: make it notice when its own premise changes.

WHAT WAS ACTUALLY FIXED, because it was free or genuinely wrong:
derived_model_promotion had RLS off while every sibling has it on with an
anon_read policy; thirty-two functions ran with a mutable search_path, four
of them SECURITY DEFINER; and two helpers added the same morning granted
EXECUTE to anon for no reason - Postgres grants it to PUBLIC by default, so
revoking from anon alone left it reachable.
"""

import pathlib
import re

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MIG = (ROOT / "supabase/migrations"
            / "20260922220000_the_advisors_that_are_real_and_the_one_that_is_not.sql")

# The five definer views that read a per-user table AND are readable by anon.
EXPOSED_BY_DESIGN = [
    "v_paper_desks",
    "v_strategy_desk_board",
    "v_paper_desk_integrity",
    "v_data_freshness",
    "v_prunable_resolution_evidence",
]


def _web_sources():
    for p in WEB.rglob("*.ts*"):
        if "node_modules" in p.parts or ".next" in p.parts:
            continue
        yield p


# --------------------------------------------------------------------------
# 1. The precondition. This is the test that matters.
# --------------------------------------------------------------------------

def test_the_app_still_has_no_sign_in():
    """The moment this fails, the definer views must be revisited.

    They are accepted because every caller is the same single operator
    arriving as `anon`. An auth flow makes `authenticated` real, and then a
    definer view over paper_accounts serves one signed-in user another's
    desk. Failing here is the alarm, not a nuisance - read
    supabase/migrations/20260922220000_*.sql before changing this test.
    """
    hits = []
    for p in _web_sources():
        src = p.read_text(errors="ignore")
        for token in ("signInWith", "signUp(", "auth.getSession",
                      "auth.setSession", "auth.getUser"):
            if token in src:
                hits.append(f"{p.relative_to(ROOT)}: {token}")
    assert not hits, (
        "the web app now authenticates: " + "; ".join(hits) +
        " - the security-definer views listed in EXPOSED_BY_DESIGN were "
        "accepted ONLY because every caller is anon. With real sessions they "
        "leak one user's desk to another. Switch them to security_invoker and "
        "confirm the per-user RLS policies cover the pages."
    )


def test_the_accepted_views_are_named_not_waved_away():
    sql = MIG.read_text()
    for v in EXPOSED_BY_DESIGN:
        assert v in sql, (
            f"{v} reads a per-user table through a definer view and anon can "
            "read it. An accepted risk that is not written down is an "
            "unnoticed one."
        )


def test_the_migration_states_the_precondition():
    sql = MIG.read_text().lower()
    assert "never signs in" in sql
    assert "precondition" in sql


# --------------------------------------------------------------------------
# 2. What was fixed.
# --------------------------------------------------------------------------

def test_the_one_table_with_rls_off_gets_it_on():
    sql = MIG.read_text()
    assert "alter table public.derived_model_promotion enable row level security" in sql
    assert "create policy anon_read on public.derived_model_promotion" in sql, (
        "enabling RLS with no policy turns an ERROR into an outage - the "
        "Predictive page reads this table"
    )


def test_the_search_path_fix_covers_whatever_is_unpinned():
    sql = MIG.read_text()
    assert "set search_path = public, extensions" in sql
    assert "not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c" in sql, (
        "a hand-written list of function names goes stale the next time one "
        "is added; loop over what is actually unpinned"
    )
    assert "pg_temp" not in sql.split("$ad4$")[-3], (
        "pg_temp in a definer's search_path is the hole this closes"
    )


def test_a_server_side_helper_is_not_reachable_by_anon():
    sql = MIG.read_text()
    assert "from public, anon, authenticated" in sql, (
        "Postgres grants EXECUTE to PUBLIC on every new function and anon "
        "inherits it - revoking from anon alone leaves it reachable, which "
        "was measured here"
    )
    for fn in ("book_as_of", "storage_pressure"):
        assert fn in sql


@pytest.mark.parametrize("path,fn", [
    ("supabase/migrations/20260922200000_one_request_per_ladder_not_one_per_band.sql",
     "book_as_of"),
    ("supabase/migrations/20260922210000_retention_that_answers_to_the_tier.sql",
     "storage_pressure"),
])
def test_the_helpers_never_grant_anon_on_a_fresh_install(path, fn):
    sql = (ROOT / path).read_text()
    assert "array['service_role']" in sql, (
        f"{fn} still grants anon on a fresh install - never granting beats "
        "granting and revoking"
    )
    assert "'anon','authenticated','service_role'" not in sql
