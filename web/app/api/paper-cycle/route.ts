import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * WAKE THE WORKER THAT FILLS QUEUED ORDERS.
 *
 * This route used to forward to a standalone worker service at
 * PAPER_WORKER_URL. That service was never deployed, so "Fill queued" and
 * every manual paper ticket answered "Paper worker is not configured. The
 * order remains queued." - and a manual order expires five minutes after it
 * is submitted, so "remains queued" meant "expires unfilled and gives the
 * reserved cash back". The manual ticket had never once filled.
 *
 * The fill cannot move into Postgres or the browser: paper_worker.py pulls a
 * live book from clob.polymarket.com, reads the market's fee schedule from
 * gamma-api, checks the token identity against both, and only then simulates
 * against real depth. That needs outbound HTTP and the service key.
 *
 * So it dispatches .github/workflows/paper_fill.yml with the same token the
 * Run cycle button already uses. No new service, no new secret, and one
 * implementation of the fill instead of two - which matters more here than
 * anywhere else in the desk, because this is the code that decides what a
 * position cost.
 *
 * A DISPATCH IS NOT A FILL, and this route no longer pretends otherwise. It
 * returns started, not orders_completed: GitHub queues the run and answers
 * immediately, so the honest report is "asked for, ~a minute" and the page
 * says so. The old shape claimed a count it could not have.
 *
 * PAPER_WORKER_URL still wins if it is set. A real worker answers in
 * milliseconds with a true count, and nothing here should stop someone
 * deploying one later.
 */

const WORKFLOW = 'paper_fill.yml';
const DEFAULT_REPO = 'hassansab00/arbdesk4';

export async function POST(request: Request) {
  const origin = request.headers.get('origin');
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: 'Cross-origin worker request rejected.' }, { status: 403 });
  }

  const workerUrl = process.env.PAPER_WORKER_URL?.replace(/\/$/, '');
  const workerToken = process.env.PAPER_WORKER_TOKEN;
  if (workerUrl && workerToken) {
    try {
      const response = await fetch(`${workerUrl}/paper-cycle`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${workerToken}` },
        cache: 'no-store',
        signal: AbortSignal.timeout(95_000),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        return NextResponse.json({ error: 'Paper worker did not complete. The order remains queued.' }, { status: 502 });
      }
      return NextResponse.json(payload, { status: 200 });
    } catch {
      return NextResponse.json({ error: 'Paper worker could not be reached. The order remains queued.' }, { status: 502 });
    }
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const owner = process.env.VERCEL_GIT_REPO_OWNER;
  const slug = process.env.VERCEL_GIT_REPO_SLUG;
  const repo = process.env.GITHUB_REPOSITORY || (owner && slug ? `${owner}/${slug}` : DEFAULT_REPO);
  const ref = process.env.GITHUB_DEFAULT_BRANCH || 'main';

  if (!token) {
    return NextResponse.json({
      error: 'GITHUB_DISPATCH_TOKEN is not set on this deployment, so the worker cannot be woken. '
        + 'The order stays queued and expires in five minutes. Add the variable and redeploy - '
        + 'Vercel bakes environment variables in at build time.',
    }, { status: 503 });
  }

  try {
    const response = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref }),
        cache: 'no-store',
        signal: AbortSignal.timeout(20_000),
      });

    if (response.status === 204) {
      return NextResponse.json({
        started: true,
        note: 'Worker started in GitHub Actions. It takes about a minute; refresh Orders then.',
        watch: `https://github.com/${repo}/actions/workflows/${WORKFLOW}`,
      });
    }
    if (response.status === 401 || response.status === 403) {
      return NextResponse.json({
        error: 'GitHub refused the token. It needs Actions: read and write on this repository.',
      }, { status: 502 });
    }
    if (response.status === 404) {
      return NextResponse.json({
        error: `GitHub could not find ${WORKFLOW} on ${repo}@${ref}. It ships in .github/workflows - `
          + 'merge the branch that adds it to the default branch.',
      }, { status: 502 });
    }
    const detail = await response.text().catch(() => '');
    return NextResponse.json({
      error: `GitHub rejected the worker run (${response.status}). ${detail.slice(0, 200)}`,
    }, { status: 502 });
  } catch {
    return NextResponse.json({
      error: 'GitHub could not be reached, so the worker was not woken. The order remains queued.',
    }, { status: 502 });
  }
}
