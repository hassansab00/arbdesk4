"""The edge gateway and the Next.js route are the same API. They drifted.

A deployment WITHOUT SUPABASE_SERVICE_KEY never reaches
web/app/api/paper-desk/route.ts - it falls through to the Supabase edge
function, supabase/functions/paper-desk/index.ts. Two implementations of one
contract, and only one of them was ever maintained.

The route was fixed to .limit(50) with a comment naming ".limit(1)" as the bug.
The edge function kept `limit=1` with `order=created_at.asc` and was deployed
once, on 12 Sep, and never again. So on that deployment the desk switcher
received exactly one desk - the OLDEST - which was "Main paper account":
manual, paused, no trades. "Wide edge, all US", holding 10 trades and 9 open
positions, was never sent to the browser at all.

The page rendered that empty desk perfectly and looked broken. Every fix aimed
at the page - a better default selection, richer dropdown labels, a counts view
- sorted an array with one item in it and changed nothing.

These tests hold the two implementations to the same contract.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "supabase" / "functions" / "paper-desk" / "index.ts"
ROUTE = ROOT / "web" / "app" / "api" / "paper-desk" / "route.ts"


def code(path: Path) -> str:
    """Source with comments stripped - the edge function DOCUMENTS the bug it
    fixed by quoting `limit=1`, and matching that would pass while the query
    still used it."""
    return re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))


def test_the_gateway_returns_every_desk_not_the_first():
    src = code(EDGE)
    assert "limit=1&" not in src and not src.rstrip().endswith("limit=1")
    accounts = src.split("resource === 'accounts'")[1][:600]
    assert "limit=50" in accounts, (
        "limit=1 here returns the oldest desk only, which was a paused empty one")


def test_the_gateway_hides_archived_desks_like_the_route_does():
    """Absent here, and masked only by limit=1 happening to land on a live desk.
    Archived desks are kept for their history and cannot trade."""
    accounts = code(EDGE).split("resource === 'accounts'")[1][:600]
    assert "archived_at=is.null" in accounts


def test_both_paths_offer_the_same_commands():
    """A command missing from the gateway fails with "Unknown paper command",
    which reads as a broken button rather than a gap in a list. New desk,
    rename, reset and archive were all missing."""
    def commands(src):
        # The commands map only - scoping matters, or an unrelated `method:`
        # key elsewhere in the file counts as a command the gateway lacks.
        block = src.split("commands", 1)[1]
        block = block[block.index("{"): block.index("}")]
        return set(re.findall(r"(\w+)\s*:\s*'[\w_]+'", block))
    edge, route = commands(code(EDGE)), commands(code(ROUTE))
    missing = sorted(route - edge)
    assert not missing, f"the edge gateway cannot perform: {missing}"


def test_both_paths_read_the_same_resources():
    edge = set(re.findall(r"^\s*(\w+):\s*`paper_\w+\?", code(EDGE), re.M))
    route = set(re.findall(r"^\s*(\w+):\(\)=>client", code(ROUTE), re.M))
    missing = sorted(route - edge)
    assert not missing, f"the edge gateway cannot read: {missing}"


def test_the_route_still_reads_the_table_the_gateway_does():
    """Both must read paper_accounts. The route briefly read a view instead and
    the desk list went blank on a deployment nobody could observe."""
    assert "from('paper_accounts')" in code(ROUTE)
