// The payload that took an n8n instance offline, rebuilt in memory.
//
// P0.2 shipped with `tag_slug=weather` on the Gamma URL. Gamma IGNORES query
// parameters it does not recognise, so the request quietly became "give me 200
// open events, any topic" - elections and sports, each with hundreds of nested
// markets and multi-kilobyte descriptions. Eighty megabytes of JSON, which n8n
// holds as one node's output, again as the next node's input, and serialises
// into its execution store. On a small instance that is an out-of-memory, and
// it reaches the operator as "workspace offline, 503".
//
// Built here rather than committed, because the point is the SIZE and a
// megabyte fixture in git is its own problem.
import fs from 'fs';
import vm from 'vm';

const wf = JSON.parse(fs.readFileSync(new URL('../../n8n/P0.2_market_discovery.template.json', import.meta.url), 'utf8'));
const node = (name) => wf.nodes.find((n) => n.name === name).parameters.jsCode;

function fatEvent(i) {
  return {
    id: String(i),
    slug: `presidential-election-outcome-number-${i}`,
    title: 'Who wins '.repeat(12),
    description: 'D'.repeat(4000),
    image: 'https://' + 'i'.repeat(200),
    endDate: '2028-11-08T04:00:00Z',
    closed: false,
    markets: Array.from({ length: 120 }, (_, k) => ({
      id: `${i}-${k}`,
      question: 'Q'.repeat(180),
      description: 'D'.repeat(3000),
      conditionId: '0x' + 'a'.repeat(64),
      groupItemTitle: `Candidate ${k}`,
    })),
  };
}

function run(code, outputs) {
  const ctx = {
    $: (n) => ({ all: () => outputs[n] || [], first: () => (outputs[n] || [{ json: {} }])[0] }),
    $execution: { mode: 'manual', id: 't' },
    console: { log() {}, error() {}, warn() {} },
  };
  try {
    return { ok: true, out: new vm.Script(`(function(){${code}})()`).runInNewContext(ctx, { timeout: 120000 }) };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
}

const out = [];
const t = (name, fn) => { try { fn(); out.push(['ok', name]); } catch (e) { out.push(['FAIL', name + ' :: ' + e.message]); } };
const assert = (c, m) => { if (!c) throw new Error(m); };

const cities = [{ json: { city_key: 'nyc', unit: 'F', icao: 'KNYC', display_name: 'New York' } }];

t('an unfiltered 80 MB feed is REFUSED before anything is written', () => {
  const payload = Array.from({ length: 200 }, (_, i) => fatEvent(i));
  const mb = JSON.stringify(payload).length / 1024 / 1024;
  assert(mb > 60, `fixture should be large, got ${mb.toFixed(0)} MB`);
  const r = run(node('Guard: is the response sane?'), {
    Config: [{ json: { max_response_mb: '12' } }],
    'Load cities': cities,
    'Fetch markets': [{ json: payload }],
  });
  assert(!r.ok, 'the guard must throw on an 80 MB unmatched feed');
  assert(/NOT ONE matched a city/.test(r.error), r.error);
  assert(/503/.test(r.error), 'it must connect the size to the symptom the operator saw');
  assert(/nothing was written/i.test(r.error), 'it must say nothing was written');
});

t('a large feed that DOES match cities gets a different, accurate message', () => {
  const payload = Array.from({ length: 200 }, (_, i) => fatEvent(i));
  payload[0].slug = 'highest-temperature-in-nyc-on-september-6';
  const r = run(node('Guard: is the response sane?'), {
    Config: [{ json: { max_response_mb: '12' } }],
    'Load cities': cities,
    'Fetch markets': [{ json: payload }],
  });
  assert(!r.ok, 'still over the cap, so still refused');
  assert(/over the 12 MB cap/.test(r.error), r.error);
  assert(!/NOT ONE matched/.test(r.error), 'that message would be wrong here');
});

t('a normal weather response passes straight through', () => {
  const payload = [{
    id: '1', slug: 'highest-temperature-in-nyc-on-september-6',
    endDate: '2026-09-07T04:00:00Z', closed: false,
    markets: [{ id: 'a', conditionId: '0x1', groupItemTitle: '70-71°F', clobTokenIds: '["y","n"]' }],
  }];
  const r = run(node('Guard: is the response sane?'), {
    Config: [{ json: { max_response_mb: '12' } }],
    'Load cities': cities,
    'Fetch markets': [{ json: payload }],
  });
  assert(r.ok, 'a small weather response must not be refused: ' + r.error);
  assert(r.out[0].json.matched_a_city === 1, JSON.stringify(r.out[0].json));
});

t('the parse node stops at max_events instead of walking 200', () => {
  const payload = Array.from({ length: 200 }, (_, i) => {
    const e = fatEvent(i);
    e.slug = `highest-temperature-in-nyc-on-day-${i}`;
    e.markets = [{ id: 'a' + i, conditionId: '0x' + i, groupItemTitle: '70-71°F', clobTokenIds: '["y","n"]' }];
    return e;
  });
  const r = run(node('Parse markets + bands'), {
    Config: [{ json: { max_events: '40', max_bands: '800' } }],
    'Load cities': cities,
    'Fetch markets': [{ json: payload }],
  });
  assert(r.ok, r.error);
  const j = r.out[0].json;
  assert(j.n_markets === 40, `expected the cap to hold at 40, got ${j.n_markets}`);
  assert(j.capped === true, 'it must report that it stopped early');
});

const failed = out.filter(([s]) => s === 'FAIL');
for (const [s, n] of out) if (s === 'FAIL') console.log('  FAIL', n);
console.log(JSON.stringify({ ok: failed.length === 0, passed: out.length - failed.length, failed: failed.length }));
