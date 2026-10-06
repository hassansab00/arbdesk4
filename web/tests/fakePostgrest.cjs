// A small PostgREST stand-in for the route and render tests (WXPredict build
// P.3). It serves rows from memory over real HTTP, so the route's own
// supabase-js client makes the requests it makes in production: the filters
// postgrest-js writes (col=eq.x, is.null, gt., gte., lt., lte., in.()),
// order=, and the offset/limit pair .range() sets. Unknown parameters are
// ignored. A table named in `fail` answers 500, the way a refused read does.
const http = require('node:http');

const date = (v) => (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v) ? Date.parse(v) : NaN);
function cmp(a, b) {
  const da = date(a), db = date(b);
  if (Number.isFinite(da) && Number.isFinite(db)) return da - db;
  const na = Number(a), nb = Number(b);
  if (a !== null && a !== '' && Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
  return String(a).localeCompare(String(b));
}

function keep(row, key, cond) {
  const dot = cond.indexOf('.');
  const op = cond.slice(0, dot), v = cond.slice(dot + 1), x = row[key];
  switch (op) {
    case 'eq': return x !== null && x !== undefined && String(x) === v;
    case 'neq': return String(x) !== v;
    case 'is': return v === 'null' ? x === null || x === undefined : String(x) === v;
    case 'gt': return x != null && cmp(x, v) > 0;
    case 'gte': return x != null && cmp(x, v) >= 0;
    case 'lt': return x != null && cmp(x, v) < 0;
    case 'lte': return x != null && cmp(x, v) <= 0;
    case 'in': return v.replace(/^\(|\)$/g, '').split(',').map((s) => s.replace(/^"|"$/g, '')).includes(String(x));
    default: return true;
  }
}

const RESERVED = new Set(['select', 'order', 'offset', 'limit', 'and', 'or', 'columns', 'on_conflict']);

function project(rows, select) {
  if (!select || select === '*' || select.includes('(')) return rows;
  const cols = select.split(',').map((c) => c.trim()).filter(Boolean)
    .map((c) => (c.includes(':') ? c.split(':') : [c, c]));
  if (cols.some(([, c]) => c === '*')) return rows;
  return rows.map((r) => Object.fromEntries(cols.map(([alias, c]) => [alias, r[c] === undefined ? null : r[c]])));
}

function query(rows, params) {
  let out = rows.filter((r) => [...params].every(([k, v]) => RESERVED.has(k) || k.includes('.') || keep(r, k, v)));
  const order = params.get('order');
  if (order) {
    const keys = order.split(',').map((o) => { const [c, dir] = o.split('.'); return [c, dir === 'desc' ? -1 : 1]; });
    out = [...out].sort((a, b) => { for (const [c, d] of keys) { const x = cmp(a[c], b[c]); if (x) return x * d; } return 0; });
  }
  const offset = Number(params.get('offset') || 0);
  const limit = params.has('limit') ? Number(params.get('limit')) : Infinity;
  out = out.slice(offset, offset + limit);
  return project(out, params.get('select'));
}

/**
 * fakePostgrest({ tables: {name: rows[]}, fail: Set<name>, rpc: {name: value}, edge: (body) => payload, port })
 * -> Promise<{ url, requests, close }>
 */
function fakePostgrest({ tables = {}, fail = new Set(), rpc = {}, edge = null, port = 0 } = {}) {
  const requests = [];
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      requests.push({ method: req.method, path: u.pathname, params: u.searchParams, body });
      const send = (status, payload) => {
        res.writeHead(status, { 'content-type': 'application/json', 'access-control-allow-origin': '*',
                                'access-control-allow-headers': '*', 'access-control-allow-methods': '*' });
        res.end(JSON.stringify(payload));
      };
      if (req.method === 'OPTIONS') return send(204, {});
      if (u.pathname.startsWith('/functions/v1/') && edge) return send(200, edge(JSON.parse(body || '{}')));
      const m = u.pathname.match(/^\/rest\/v1\/(rpc\/)?([\w]+)$/);
      if (!m) return send(404, { message: `no route ${u.pathname}` });
      const [, isRpc, name] = m;
      if (isRpc) return send(200, rpc[name] ?? null);
      if (fail.has(name)) return send(500, { message: `${name} failed`, code: 'XX000', details: null, hint: null });
      return send(200, query(tables[name] ?? [], u.searchParams));
    });
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => {
    const bound = server.address().port;
    resolve({ url: `http://127.0.0.1:${bound}`, requests, close: () => new Promise((r) => server.close(r)) });
  }));
}

module.exports = { fakePostgrest, query };
