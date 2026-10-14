// Route tests for the operator gate (plan v2 P1.2).
//
// Runs the REAL route handlers, compiled by tests/tsconfig.routes.json into
// .route-test/, with no Supabase environment at all - the same condition as
// CI's web job. Run with:  npm run test:routes
//
// The plan's acceptance for the step: a request with a forged Origin header
// and no session gets 401. The Origin header is set by whoever sends the
// request, so a matching one is exactly what a script would forge.
const assert = require('node:assert/strict');
const path = require('node:path');

const OUT = path.join(__dirname, '..', '.route-test');
const auth = require(path.join(OUT, 'lib', 'operatorAuth.js'));
const route = (p) => require(path.join(OUT, 'app', 'api', p, 'route.js'));

const SITE = 'https://desk.example';
const forged = (url, extra = {}) => new Request(`${SITE}${url}`, {
  method: 'POST',
  headers: { origin: SITE, 'content-type': 'application/json', ...extra },
  body: JSON.stringify({ op: 'rpc', rpc: 'update_setting', params: { p_key: 'bankroll', p_value: {} } }),
});

(async () => {
  for (const k of Object.keys(process.env)) if (/SUPABASE/i.test(k)) delete process.env[k];

  // 1a. SIGN-IN OFF (the default; Hassan, 23 Sep). The gate lets a
  // same-origin request through to the service key, and with no service key
  // configured the route says so (503) rather than doing anything.
  delete process.env.OPERATOR_SIGN_IN;
  for (const [p, url] of [['operator', '/api/operator'], ['paper-desk', '/api/paper-desk'],
                          ['paper-run', '/api/paper-run'], ['paper-cycle', '/api/paper-cycle']]) {
    const res = await route(p).POST(forged(url));
    assert.notEqual(res.status, 401, `${url}: sign-in is off but the route still asked for a session`);
  }
  assert.equal((await route('operator').POST(forged('/api/operator'))).status, 503,
    'with sign-in off and no service key, a write must stop at 503, not pass');

  // A cross-origin request is refused in both modes, before anything else.
  const cross = new Request(`${SITE}/api/operator`, { method: 'POST', headers: { origin: 'https://evil.example' }, body: '{}' });
  assert.equal((await route('operator').POST(cross)).status, 403);

  // 1b. SIGN-IN ON (OPERATOR_SIGN_IN=required): the plan's acceptance - a
  // forged same-origin request with no session is 401 on every write route.
  process.env.OPERATOR_SIGN_IN = 'required';
  for (const [p, url] of [['operator', '/api/operator'], ['paper-desk', '/api/paper-desk'],
                          ['paper-run', '/api/paper-run'], ['paper-cycle', '/api/paper-cycle']]) {
    const res = await route(p).POST(forged(url));
    assert.equal(res.status, 401, `${url}: a forged Origin with no session got ${res.status}, not 401`);
  }
  assert.equal((await route('operator').POST(cross)).status, 403);
  // A token with no server to check it against is not a pass: 503, not 200.
  const tokenNoServer = await route('operator').POST(forged('/api/operator', { authorization: 'Bearer abc' }));
  assert.equal(tokenNoServer.status, 503, 'a token was accepted with nothing configured to verify it');
  delete process.env.OPERATOR_SIGN_IN;

  // 2. The decision itself, with its I/O stubbed.
  const deps = (email, ops) => ({ emailForToken: async () => email, operators: async () => ops });
  assert.deepEqual(await auth.authorize(null, deps('a@b.c', ['a@b.c'])),
    { ok: false, status: 401, message: 'Sign in to change anything. Reading needs no sign-in.' });
  assert.equal((await auth.authorize('t', deps(null, ['a@b.c']))).status, 401, 'an invalid token passed');
  assert.equal((await auth.authorize('t', deps('x@y.z', ['a@b.c']))).status, 403, 'a non-operator passed');
  assert.equal((await auth.authorize('t', deps('a@b.c', []))).status, 403, 'an empty operator list let someone in');
  assert.deepEqual(await auth.authorize('t', deps('A@B.c', [' a@b.C '])), { ok: true, email: 'A@B.c' },
    'emails must compare case- and space-insensitively');
  const throws = { emailForToken: async () => { throw new Error('network'); }, operators: async () => ['a@b.c'] };
  assert.equal((await auth.authorize('t', throws)).status, 401, 'a failed token check passed');

  assert.equal(auth.bearerToken(new Request(SITE, { headers: { authorization: 'Bearer abc.def' } })), 'abc.def');
  assert.equal(auth.bearerToken(new Request(SITE, { headers: { authorization: 'Basic abc' } })), null);
  assert.deepEqual(auth.normaliseOperators(['A@x.io', 3, '', null, ' b@y.io ']), ['a@x.io', 'b@y.io']);
  assert.deepEqual(auth.normaliseOperators({ emails: ['a@x.io'] }), [], 'only a JSON array is an operator list');

  // 3. The n8n webhook key (plan v2 P6.4): sent from the server when set,
  // absent when not, and never echoed back in a refusal.
  const hook = require(path.join(OUT, 'lib', 'n8nWebhook.js'));
  assert.deepEqual(hook.webhookHeaders({}), { 'Content-Type': 'application/json' });
  assert.equal(hook.webhookHeaders({ N8N_WEBHOOK_KEY: ' k3y ' })['X-AD4-Key'], 'k3y');
  assert.equal(hook.webhookHeaders({ NEXT_PUBLIC_N8N_WEBHOOK_KEY: 'k' })['X-AD4-Key'], undefined,
    'a NEXT_PUBLIC_ copy of the key must never be what the server sends');
  assert.equal(hook.webhookRefusal(200, { N8N_WEBHOOK_KEY: 'k' }), null);
  assert.match(hook.webhookRefusal(403, {}), /set N8N_WEBHOOK_KEY/);
  assert.ok(!hook.webhookRefusal(403, { N8N_WEBHOOK_KEY: 'k3y' }).includes('k3y'), 'a refusal echoed the key');

  // Saved URLs never leave GET, including when the single-user write gate
  // deliberately accepts requests without a session. Replacing one URL
  // keeps the other jobs and metadata without first disclosing them.
  const realClient = auth.serviceClient;
  const realGate = auth.requireOperator;
  const hooks = { P0_ignored: { url: '' },
    'P0.2_market_discovery': { url: 'https://n8n.example/webhook/old-secret', label: 'keep this label' },
    'P0.3_book_volume_snapshot': { url: 'https://n8n.example/webhook/other-secret' } };
  let readError = null, rpcError = null, rpcData = null;
  const writes = [];
  auth.serviceClient = () => ({
    from: () => ({ select: () => ({ eq: () => ({ maybeSingle: async () => ({ data: { value: hooks }, error: readError }) }) }) }),
    rpc: async (name, params) => { writes.push({ name, params }); return { data: rpcData ?? { ok: true, secret: hooks }, error: rpcError }; },
  });
  // Keep the real authenticated refusal, while the no-session mode uses the
  // fake configured server above rather than constructing a network client.
  auth.requireOperator = (request) => auth.signInRequired()
    ? realGate(request) : Promise.resolve({ ok: true, email: 'sign-in off' });
  try {
    for (const mode of [undefined, 'required']) {
      if (mode) process.env.OPERATOR_SIGN_IN = mode; else delete process.env.OPERATOR_SIGN_IN;
      const res = await route('operator').GET(new Request(`${SITE}/api/operator?what=workflows`));
      assert.equal(res.status, 200);
      assert.equal(res.headers.get('cache-control'), 'no-store');
      const json = await res.json();
      assert.deepEqual(json.data, { configured: { P0_ignored: false, 'P0.2_market_discovery': true,
        'P0.3_book_volume_snapshot': true }, webhooks: null });
      assert.ok(!JSON.stringify(json).includes('secret'), 'GET exposed saved credentials');
    }
    delete process.env.OPERATOR_SIGN_IN;
    const setUrl = (url, job = 'P0.2_market_discovery') => new Request(`${SITE}/api/operator`, {
      method: 'POST', headers: { origin: SITE, 'content-type': 'application/json' },
      body: JSON.stringify({ op: 'set_webhook', job, url }),
    });
    let res = await route('operator').POST(setUrl(' https://n8n.example/webhook/replacement '));
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { data: { ok: true }, error: null }, 'save echoed the RPC configuration');
    assert.deepEqual(writes[0], { name: 'update_setting', params: { p_key: 'n8n_webhooks', p_value: {
      ...hooks, 'P0.2_market_discovery': { label: 'keep this label', url: 'https://n8n.example/webhook/replacement' },
    } } });
    for (const url of ['http://n8n.example/x', 'not a URL', 'https://user:pass@n8n.example/x']) {
      assert.equal((await route('operator').POST(setUrl(url))).status, 400);
    }
    assert.equal((await route('operator').POST(setUrl('https://n8n.example/x', '__proto__'))).status, 400);
    assert.equal(writes.length, 1, 'an invalid URL or job reached the database');
    readError = { message: 'database unavailable' };
    assert.equal((await route('operator').GET(new Request(`${SITE}/api/operator?what=workflows`))).status, 503);
    assert.equal((await route('operator').POST(setUrl('https://n8n.example/x'))).status, 500);
    assert.equal(writes.length, 1, 'a failed configuration read overwrote the other jobs');
    readError = null;
    rpcError = { message: 'private database error' };
    res = await route('operator').POST(setUrl('https://n8n.example/x'));
    assert.equal(res.status, 400);
    assert.ok(!(await res.text()).includes('private database error'));
    rpcError = null;
    rpcData = { ok: false, error: 'private setting value' };
    assert.equal((await route('operator').POST(setUrl('https://n8n.example/x'))).status, 400);
    rpcData = null;
    assert.equal((await route('operator').POST(setUrl(''))).status, 200, 'clearing a URL must remain supported');
    process.env.OPERATOR_SIGN_IN = 'required';
    const count = writes.length;
    assert.equal((await route('operator').POST(setUrl('https://n8n.example/x'))).status, 401);
    assert.equal(writes.length, count, 'the URL replacement bypassed the operator gate');
  } finally {
    auth.serviceClient = realClient;
    auth.requireOperator = realGate;
    delete process.env.OPERATOR_SIGN_IN;
  }

  console.log('PASS: operator gate - sign-in off passes to the service key; sign-in on refuses a forged Origin with 401 on every write route');
})().catch((e) => { console.error(e); process.exit(1); });
