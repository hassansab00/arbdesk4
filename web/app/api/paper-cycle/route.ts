import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const origin = request.headers.get('origin');
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ error: 'Cross-origin worker request rejected.' }, { status: 403 });
  }
  const workerUrl = process.env.PAPER_WORKER_URL?.replace(/\/$/, '');
  const workerToken = process.env.PAPER_WORKER_TOKEN;
  if (!workerUrl || !workerToken) {
    return NextResponse.json({ error: 'Paper worker is not configured. The order remains queued.' }, { status: 503 });
  }

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
