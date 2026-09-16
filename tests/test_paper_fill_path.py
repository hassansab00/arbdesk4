"""A manual paper ticket had never once filled, and nothing said so.

The chain: the ticket form calls submit_single_paper_order, which queues a
paper_orders row with expires_at = now() + 5 minutes and reserves the cash
against it. The page then calls /api/paper-cycle to wake the worker. That
route forwarded to a standalone service at PAPER_WORKER_URL, which was never
deployed, so it answered 503 "Paper worker is not configured. The order
remains queued." - and five minutes later expire_paper_commands() killed the
order and gave the cash back. Every manual ticket, every time. "Fill queued"
on the desk header was the same call and therefore equally dead.

The fill cannot be moved into Postgres or the browser: paper_worker.py fetches
a live book from clob.polymarket.com, reads the fee schedule from gamma-api,
verifies the token identity against both, and simulates against real depth. It
needs outbound HTTP and the service key. GitHub Actions already runs exactly
that code every cycle, so the route dispatches it instead of a service that
does not exist - one implementation of the fill, not two, which matters most
in the code that decides what a position cost.

These pin the wiring that is silent when it breaks: the workflow exists and is
dispatch-only, the route names the file that is actually there, and the page
never claims a fill it cannot know about yet.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "web" / "app" / "api" / "paper-cycle" / "route.ts"
WORKFLOW = ROOT / ".github" / "workflows" / "paper_fill.yml"
HELPER = ROOT / "web" / "lib" / "paperSupabase.ts"
PAGE = ROOT / "web" / "app" / "paper-trades" / "page.tsx"


def test_the_worker_workflow_exists_and_runs_the_worker():
    assert WORKFLOW.exists(), "the route dispatches this file; it has to be in the repo"
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [s for job in doc["jobs"].values() for s in job["steps"]]
    assert any("scripts/paper_worker.py" in str(s.get("run", "")) for s in steps), \
        "a fill workflow that does not run the worker fills nothing"


def test_the_worker_workflow_costs_nothing_per_month():
    """No schedule. The scheduled fill already runs inside pipeline_intraday,
    immediately after the proposals that create the orders - the only ordering
    that beats a five-minute expiry. This one is on demand."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)          # yaml reads a bare `on:` as True
    assert "workflow_dispatch" in on, "the button dispatches it; it must be dispatchable"
    assert "schedule" not in on, (
        "a schedule here would be billed every month for work pipeline_intraday "
        "already does in the right order")


def test_the_worker_workflow_does_not_queue_behind_the_intraday_cycle():
    """Sharing pipeline-intraday's concurrency group, which does not cancel in
    progress, would make a fill wait several minutes for a cycle to finish -
    against an order with five minutes to live. That is the bug, restored."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    group = (doc.get("concurrency") or {}).get("group")
    assert group and group != "pipeline-intraday", \
        "the fill needs its own concurrency group; leases make two workers safe"


def test_the_route_dispatches_the_workflow_that_exists():
    """Renaming one and not the other is a 404 nobody sees until they click."""
    src = ROUTE.read_text(encoding="utf-8")
    assert f"const WORKFLOW = '{WORKFLOW.name}'" in src, \
        f"the route must name {WORKFLOW.name}, the file actually in .github/workflows"


def test_the_route_needs_no_secret_that_is_not_already_set():
    """GITHUB_DISPATCH_TOKEN is already set and proven - the Run cycle button
    uses it. Requiring a second one would put this back where it started."""
    src = ROUTE.read_text(encoding="utf-8")
    assert "GITHUB_DISPATCH_TOKEN" in src
    assert "PAPER_WORKER_URL" in src, (
        "a real deployed worker should still win when someone sets it: it answers "
        "in milliseconds with a true count")


def test_a_dispatch_is_never_reported_as_a_finished_fill():
    """GitHub queues the run and returns immediately, so orders_completed
    cannot exist yet. Reporting 'nothing was claimable' for a fill that has not
    run is how the old version taught you to distrust the page."""
    src = HELPER.read_text(encoding="utf-8")
    start = src.index("export function describeWorker")
    body = src[start:]
    started = body.index("r.started")
    claimable = body.index("Nothing was claimable")
    assert started < claimable, \
        "the started branch must be taken before any claim about what was filled"


def test_the_manual_ticket_tells_the_truth_about_the_wait():
    """The ticket reserves cash for five minutes. Saying 'refresh shortly' with
    no worker behind it is what made the feature look merely slow."""
    src = PAGE.read_text(encoding="utf-8")
    assert "describeWorker" in src, \
        "the ticket must report what the worker route actually answered"
    assert "Refresh shortly." not in src
