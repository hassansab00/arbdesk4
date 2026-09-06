// P0.2, both modes.
//
// Three versions of this workflow listed events and filtered client-side, and
// all three were wrong because Gamma IGNORES query parameters it does not
// recognise: ?limit=40&tag_slug=weather returned 2160 events and 26 MB, and
// n8n went offline holding it. The dependency is gone - a temperature market
// has a known slug, one per city per day, so each is asked for by name.
import fs from 'fs'; import vm from 'vm';
const wf = JSON.parse(fs.readFileSync(new URL('../../n8n/P0.2_market_discovery.template.json', import.meta.url), 'utf8'));
const code = (n) => wf.nodes.find((x) => x.name === n).parameters.jsCode;
const out = [];
const t = (n, f) => { try { f(); out.push(['ok', n]); } catch (e) { out.push(['FAIL', n + ' :: ' + e.message]); } };
const ok = (c, m) => { if (!c) throw new Error(m); };

const CITIES = [{ city_key: 'nyc', unit: 'F', display_name: 'New York' },
                { city_key: 'chi', unit: 'F', display_name: 'Chicago' }];
const cfg = (v) => [{ json: {
  supabase_url: 'u', service_key: 'k', slug_template: '',
  event_url_template: 'https://gamma-api.polymarket.com/events?slug={slug}',
  discovery_url: 'https://gamma-api.polymarket.com/events?closed=false',
  city_slug_overrides: '{}', days_ahead: '2', max_requests: '120',
  max_events: '200', max_bands: '4000', ...v } }];
function run(name, outputs) {
  const ctx = { $: (n) => ({ all: () => outputs[n] || [], first: () => (outputs[n] || [{ json: {} }])[0] }),
                $execution: { mode: 'manual', id: 't' }, console: { log() {}, error() {}, warn() {} } };
  try { return { ok: true, out: new vm.Script(`(function(){${code(name)}})()`).runInNewContext(ctx, { timeout: 60000 }) }; }
  catch (e) { return { ok: false, error: String(e.message || e) }; }
}

t('it ships in discovery mode - slug_template is empty', () => {
  const cfgNode = wf.nodes.find((n) => n.name === 'Config');
  const v = cfgNode.parameters.assignments.assignments.find((a) => a.name === 'slug_template').value;
  ok(v === '', `slug_template ships as ${JSON.stringify(v)} - the first run must discover, not guess`);
});

t('nothing asks Gamma for a parameter it ignores', () => {
  const cfgNode = wf.nodes.find((n) => n.name === 'Config');
  const all = cfgNode.parameters.assignments.assignments.map((a) => String(a.value)).join(' ');
  ok(!/tag_slug/.test(all), 'tag_slug is silently ignored by Gamma');
  ok(!/[?&]limit=/.test(all), 'limit was ignored too - 40 returned 2160 events');
});

t('discovery survives a 287 MB feed and reports the real slugs', () => {
  const flood = Array.from({ length: 2160 }, (_, i) => i % 400 === 0
    ? { id: String(i), slug: `highest-temperature-in-${['nyc', 'chicago'][i % 2]}-on-september-6`,
        endDate: '2026-09-07T04:00:00Z',
        markets: Array.from({ length: 11 }, (_, k) => ({ id: `${i}-${k}`, groupItemTitle: `${70 + k}-${71 + k}°F` })) }
    : { id: String(i), slug: `election-outcome-${i}`, description: 'D'.repeat(4000),
        markets: Array.from({ length: 60 }, (_, k) => ({ id: `${i}-${k}`, description: 'D'.repeat(2000), groupItemTitle: `C${k}` })) });
  ok(JSON.stringify(flood).length / 1024 / 1024 > 100, 'fixture must be large');
  const o = { Config: cfg({}), 'Load cities': CITIES.map((j) => ({ json: j })),
              'Build requests': [{ json: { mode: 'discovery', url: 'x' } }], 'Fetch markets': [{ json: flood }] };
  const before = process.memoryUsage().heapUsed;
  const r = run('Parse markets + bands', o);
  ok(r.ok, r.error);
  ok((process.memoryUsage().heapUsed - before) / 1024 / 1024 < 40, 'discovery must not hold the feed');
  ok(r.out[0].json.n_temperature_events > 0, 'it must find the temperature slugs');
  o['Parse markets + bands'] = r.out;
  const g = run('Guard: did anything parse?', o);
  ok(!g.ok, 'discovery must stop, having written nothing');
  ok(/DISCOVERY COMPLETE/.test(g.error), g.error);
  ok(/highest-temperature-in-/.test(g.error), 'it must print the real slugs');
  ok(/nyc, chi/.test(g.error), 'and the city keys, so a mismatch is visible');
});

t('targeted mode builds one URL per city per day', () => {
  const r = run('Build requests', {
    Config: cfg({ slug_template: 'highest-temperature-in-{city}-on-{month}-{day}',
                  city_slug_overrides: '{"chi":"chicago"}', days_ahead: '2' }),
    'Load cities': CITIES.map((j) => ({ json: j })) });
  ok(r.ok, r.error);
  ok(r.out.length === 4, `2 cities x 2 days = 4, got ${r.out.length}`);
  const slugs = r.out.map((i) => i.json.slug);
  ok(slugs.some((s) => s.includes('chicago')), 'the override must be applied: ' + slugs);
  ok(slugs.every((s) => /^highest-temperature-in-[a-z-]+-on-[a-z]+-\d+$/.test(s)), slugs.join(' '));
  ok(r.out.every((i) => i.json.url.includes(encodeURIComponent(i.json.slug))), 'the slug must be encoded into the URL');
});

t('max_requests is a hard cap', () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ city_key: 'c' + i, unit: 'C', display_name: 'City ' + i }));
  const r = run('Build requests', {
    Config: cfg({ slug_template: 'x-{city}-{month}-{day}', days_ahead: '14', max_requests: '25' }),
    'Load cities': many.map((j) => ({ json: j })) });
  ok(r.ok, r.error);
  ok(r.out.length === 25, `expected the cap to hold at 25, got ${r.out.length}`);
});

t('a response is matched back to the request that asked for it', () => {
  const build = run('Build requests', {
    Config: cfg({ slug_template: 'highest-temperature-in-{city}-on-{month}-{day}',
                  city_slug_overrides: '{"chi":"chicago"}', days_ahead: '1' }),
    'Load cities': CITIES.map((j) => ({ json: j })) }).out;
  const responses = build.map((b) => ({ json: [{
    id: 'e', slug: b.json.slug, endDate: b.json.for_date + 'T23:00:00Z', closed: false,
    markets: [{ id: '1', conditionId: '0x' + b.json.slug + 1, groupItemTitle: '72°F or below', clobTokenIds: '["a","b"]' },
              { id: '2', conditionId: '0x' + b.json.slug + 2, groupItemTitle: '75-76°F', clobTokenIds: '["c","d"]' },
              { id: '3', conditionId: '0x' + b.json.slug + 3, groupItemTitle: '73-74°F', clobTokenIds: '["e","f"]' },
              { id: '4', conditionId: '0x' + b.json.slug + 4, groupItemTitle: '77°F or above', clobTokenIds: '["g","h"]' }] }] }));
  const o = { Config: cfg({ slug_template: 'x' }), 'Load cities': CITIES.map((j) => ({ json: j })),
              'Build requests': build, 'Fetch markets': responses };
  const r = run('Parse markets + bands', o);
  ok(r.ok, r.error);
  const j = r.out[0].json;
  ok(j.n_markets === 2, `2 markets, got ${j.n_markets}`);
  ok(j.n_bands === 8, `8 bands, got ${j.n_bands}`);
  ok(j.not_found === 0, `nothing should be missing, got ${j.not_found}`);
  // the city comes from the REQUEST, so 'chi' survives even though the slug says 'chicago'
  ok(j.markets.map((m) => m.city_key).sort().join() === 'chi,nyc', JSON.stringify(j.markets.map((m) => m.city_key)));
  // ladder: contiguous, ordered by floor, open tails at the ends
  const one = j.bands.filter((b) => b.market_id === j.markets[0].market_id).sort((a, b) => a.band_index - b.band_index);
  ok(one[0].open_low === true && one[0].band_hi === 73, JSON.stringify(one[0]));
  ok(one[3].open_high === true && one[3].band_lo === 77, JSON.stringify(one[3]));
  for (let i = 0; i < one.length - 1; i++) {
    if (one[i].band_hi != null && one[i + 1].band_lo != null) {
      ok(one[i].band_hi === one[i + 1].band_lo, `gap at ${i}: ${JSON.stringify(one.map((b) => [b.band_lo, b.band_hi]))}`);
    }
  }
});

t('a wrong template stops with the slugs it tried and how to fix it', () => {
  const build = run('Build requests', {
    Config: cfg({ slug_template: 'wrong-{city}-{month}-{day}', days_ahead: '1' }),
    'Load cities': CITIES.map((j) => ({ json: j })) }).out;
  const o = { Config: cfg({ slug_template: 'x' }), 'Load cities': CITIES.map((j) => ({ json: j })),
              'Build requests': build, 'Fetch markets': [{ json: [] }] };
  o['Parse markets + bands'] = run('Parse markets + bands', o).out;
  const g = run('Guard: did anything parse?', o);
  ok(!g.ok, 'it must stop');
  ok(/slug_template in Config is almost certainly wrong/.test(g.error), g.error);
  ok(/wrong-new-york/.test(g.error), 'it must name the slugs it tried');
  ok(/Clear slug_template and run again/.test(g.error), 'and the way out');
});

const failed = out.filter(([s]) => s === 'FAIL');
for (const [s, n] of out) if (s === 'FAIL') console.log('  FAIL', n);
console.log(JSON.stringify({ ok: failed.length === 0, passed: out.length - failed.length, failed: failed.length }));
