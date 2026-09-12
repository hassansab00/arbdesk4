// Credential-free Paper Trades gateway. The Edge gateway verifies the caller's
// project JWT; this function then exposes only the paper-only operations below.
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')?.replace(/\/$/, '');
const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
});

async function database(path: string, init: RequestInit = {}) {
  if (!SUPABASE_URL || !SERVICE_KEY) return { data: null, error: { message: 'Paper gateway is not configured.' }, status: 503 };
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: SERVICE_KEY,
      Authorization: `Bearer ${SERVICE_KEY}`,
      'Content-Type': 'application/json',
      Prefer: 'return=representation',
      ...(init.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({ message: 'Invalid database response.' }));
  return response.ok
    ? { data: payload, error: null, status: response.status }
    : { data: null, error: payload, status: response.status };
}

function validUuid(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

Deno.serve(async (request) => {
  if (request.method !== 'POST') return json({ data: null, error: { message: 'Method not allowed.' } }, 405);
  const size = Number(request.headers.get('content-length') || 0);
  if (size > 16_384) return json({ data: null, error: { message: 'Paper request is too large.' } }, 413);

  try {
    const body = await request.json() as {
      method?: 'read' | 'action'; resource?: string; account?: string;
      action?: string; payload?: Record<string, unknown>;
    };

    if (body.method === 'read' && body.resource === 'accounts') {
      const result = await database('paper_accounts?select=*&access_mode=eq.single_desk&owner_id=is.null&order=created_at.asc&limit=1');
      return json({ data: result.data, error: result.error }, result.error ? 400 : 200);
    }

    if (body.method === 'read') {
      if (!validUuid(body.account)) return json({ data: null, error: { message: 'Single paper desk unavailable.' } }, 404);
      const account = encodeURIComponent(body.account);
      const shared = await database(`paper_accounts?select=account_id&account_id=eq.${account}&access_mode=eq.single_desk&owner_id=is.null&limit=1`);
      if (shared.error || !Array.isArray(shared.data) || shared.data.length !== 1) {
        return json({ data: null, error: { message: 'Single paper desk unavailable.' } }, 404);
      }
      const resources: Record<string, string> = {
        orders: `paper_orders?select=*&account_id=eq.${account}&order=requested_at.desc&limit=100`,
        positions: `paper_positions?select=*&account_id=eq.${account}&order=band_id.asc&limit=500`,
        activity: `paper_activity?select=*&account_id=eq.${account}&order=event_id.desc&limit=100`,
        plans: `paper_trade_plans?select=*&account_id=eq.${account}&order=created_at.desc&limit=100`,
      };
      const path = body.resource && resources[body.resource];
      if (!path) return json({ data: null, error: { message: 'Unknown paper resource.' } }, 400);
      const result = await database(path);
      return json({ data: result.data, error: result.error }, result.error ? 400 : 200);
    }

    if (body.method === 'action') {
      const commands: Record<string, string> = {
        create_account: 'create_single_paper_account',
        submit_order: 'submit_single_paper_order',
        cancel_order: 'cancel_single_paper_order',
        set_policy: 'set_single_paper_policy',
        approve_plan: 'approve_single_paper_plan',
        submit_exit: 'submit_single_paper_exit',
        set_exit_policy: 'set_single_paper_exit_policy',
      };
      const rpc = body.action && commands[body.action];
      if (!rpc) return json({ data: null, error: { message: 'Unknown paper command.' } }, 400);
      const result = await database(`rpc/${rpc}`, { method: 'POST', body: JSON.stringify(body.payload || {}) });
      return json({ data: result.data, error: result.error }, result.error ? 400 : 200);
    }

    return json({ data: null, error: { message: 'Unknown paper request.' } }, 400);
  } catch {
    return json({ data: null, error: { message: 'Paper request failed.' } }, 400);
  }
});
