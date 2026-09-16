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

function config() {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const repo = process.env.GITHUB_REPOSITORY;      // "owner/name"
  const ref = process.env.GITHUB_DEFAULT_BRANCH || 'main';
  return { token, repo, ref };
}

export async function GET() {
  const { token, repo, ref } = config();
  return NextResponse.json({
    configured: !!token && !!repo,
    workflow: WORKFLOW,
    ref,
    // Named exactly, so setting it up is a copy and paste rather than a search.
    missing: [
      !token && 'GITHUB_DISPATCH_TOKEN (a fine-grained token with Actions: read and write on this repo)',
      !repo && 'GITHUB_REPOSITORY (owner/name)',
    ].filter(Boolean),
  }, { headers: { 'Cache-Control': 'no-store' } });
}

export async function POST(request: Request) {
  const origin = request.headers.get('origin');
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: 'Cross-origin run request rejected.' }, { status: 403 });
  }
  const { token, repo, ref } = config();
  if (!token || !repo) {
    return NextResponse.json({
      error: 'Running the full cycle from here is not configured. Set '
        + [!token && 'GITHUB_DISPATCH_TOKEN', !repo && 'GITHUB_REPOSITORY'].filter(Boolean).join(' and ')
        + ' in the site environment, or start it from the repository’s Actions tab.',
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
