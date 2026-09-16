import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * RUN THE WHOLE CYCLE NOW.
 *
 * The desk's chain - signals, proposals, fills, settlement, exits - runs in
 * GitHub Actions on a four-hour schedule that GitHub delays by anything from
 * thirty minutes to three hours. Waiting for it is the difference between
 * changing a policy and finding out whether the change did anything.
 *
 * Only GitHub can start that workflow, so this asks it to, using a token that
 * stays on the server. The browser never sees it and cannot be made to leak
 * it: this route takes no parameters at all, so there is nothing to inject -
 * the workflow, the ref and the repository are all fixed here.
 *
 * WHEN IT IS NOT CONFIGURED IT SAYS SO, with the name of the variable to set.
 * A run button that silently fails is worse than a disabled one, and "it
 * didn't work" with no reason is how an afternoon disappears.
 *
 * GET reports whether it COULD run, so the page can disable the button and
 * explain itself before anyone clicks.
 */

const WORKFLOW = 'pipeline_intraday.yml';

// This repository. Written down rather than discovered, and that is the point.
//
// It first required GITHUB_REPOSITORY beside the token, so the button stayed
// disabled after the token was set. The second attempt inferred it from
// Vercel's VERCEL_GIT_REPO_OWNER/SLUG - which are only present when the
// project is Git-connected AND "Automatically expose System Environment
// Variables" is on, so it stayed disabled again, now for a reason nobody could
// see from the outside.
//
// Two rounds of a button that would not turn on, to avoid writing down a
// constant that has never changed and is already in every git remote in this
// repository. The environment variables still override it, for a fork or a
// deployment that should dispatch elsewhere. Nothing needs configuring for the
// ordinary case, and the ONLY thing that can now disable the button is a
// missing token - which is one thing to check instead of three.
const DEFAULT_REPO = 'hassansab00/arbdesk4';

function config() {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const owner = process.env.VERCEL_GIT_REPO_OWNER;
  const slug = process.env.VERCEL_GIT_REPO_SLUG;
  const repo = process.env.GITHUB_REPOSITORY
    || (owner && slug ? `${owner}/${slug}` : DEFAULT_REPO);
  const ref = process.env.GITHUB_DEFAULT_BRANCH || 'main';
  return { token, repo, ref };
}

export async function GET() {
  const { token, repo, ref } = config();
  return NextResponse.json({
    // repo always resolves now, so this is the token and nothing else.
    configured: !!token,
    workflow: WORKFLOW,
    ref,
    repo,
    missing: token ? [] : ['GITHUB_DISPATCH_TOKEN'],
  }, { headers: { 'Cache-Control': 'no-store' } });
}

export async function POST(request: Request) {
  const origin = request.headers.get('origin');
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: 'Cross-origin run request rejected.' }, { status: 403 });
  }
  const { token, repo, ref } = config();
  if (!token) {
    return NextResponse.json({
      error: 'GITHUB_DISPATCH_TOKEN is not set on this deployment. Add it in the site '
        + 'environment and redeploy - Vercel bakes environment variables in at build time, '
        + 'so one added after the last deploy is not in the running build.',
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

    // 204 is success here; GitHub returns no body for a dispatch.
    if (response.status === 204) {
      return NextResponse.json({
        started: true,
        // The run takes minutes and this call returns immediately, so the page
        // must not imply the numbers have already moved.
        note: 'Cycle requested. It takes a few minutes; the step tiles update as each stage finishes.',
        watch: `https://github.com/${repo}/actions/workflows/${WORKFLOW}`,
      });
    }
    const detail = await response.text().catch(() => '');
    if (response.status === 401 || response.status === 403) {
      return NextResponse.json({
        error: 'GitHub refused the token. It needs Actions: read and write on this repository, and must not be expired.',
      }, { status: 502 });
    }
    if (response.status === 404) {
      return NextResponse.json({
        error: `GitHub could not find ${WORKFLOW} on ${repo}@${ref}. Check GITHUB_REPOSITORY and the branch.`,
      }, { status: 502 });
    }
    return NextResponse.json({
      error: `GitHub rejected the run (${response.status}). ${detail.slice(0, 200)}`,
    }, { status: 502 });
  } catch {
    return NextResponse.json({ error: 'GitHub could not be reached. Nothing was started.' }, { status: 502 });
  }
}
