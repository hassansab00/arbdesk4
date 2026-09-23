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

  // 1. Every write route: a forged same-origin request with no session is 401.
  for (const [p, url] of [['operator', '/api/operator'], ['paper-desk', '/api/paper-desk'],
                          ['paper-run', '/api/paper-run'], ['paper-cycle', '/api/paper-cycle']]) {
    const res = await route(p).POST(forged(url));
    assert.equal(res.status, 401, `${url}: a forged Origin with no session got ${res.status}, not 401`);
  }

  // A cross-origin request is still refused before anything else.
  const cross = new Request(`${SITE}/api/operator`, { method: 'POST', headers: { origin: 'https://evil.example' }, body: '{}' });
  assert.equal((await route('operator').POST(cross)).status, 403);

  // A token with no server to check it against is not a pass: 503, not 200.
  const tokenNoServer = await route('operator').POST(forged('/api/operator', { authorization: 'Bearer abc' }));
  assert.equal(tokenNoServer.status, 503, 'a token was accepted with nothing configured to verify it');

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

  console.log('PASS: operator gate - forged Origin without a session is 401 on every write route');
})().catch((e) => { console.error(e); process.exit(1); });
