import { createClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function bearer(request: Request) {
  const header = request.headers.get('authorization') || '';
  return header.startsWith('Bearer ') ? header.slice(7).trim() : '';
}

export async function POST(request: Request) {
  const accessToken = bearer(request);
  if (!accessToken) return NextResponse.json({ error: 'Desk owner sign-in required.' }, { status: 401 });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publicKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const workerUrl = process.env.PAPER_WORKER_URL?.replace(/\/$/, '');
  const workerToken = process.env.PAPER_WORKER_TOKEN;
  if (!supabaseUrl || !publicKey || !workerUrl || !workerToken) {
    return NextResponse.json({ error: 'Paper worker is not configured. The order remains queued.' }, { status: 503 });
  }

  const auth = createClient(supabaseUrl, publicKey, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { headers: { Authorization: `Bearer ${accessToken}` } },
  });
  const { data: identity, error: identityError } = await auth.auth.getUser(accessToken);
  if (identityError || !identity.user) {
    return NextResponse.json({ error: 'Desk owner session is invalid or expired.' }, { status: 401 });
  }
  const { data: member, error: memberError } = await auth.from('desk_members')
    .select('user_id').eq('user_id', identity.user.id).maybeSingle();
  if (memberError || !member) {
    return NextResponse.json({ error: 'This account is not authorized for the paper desk.' }, { status: 403 });
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
