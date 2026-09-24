// The Predictive page's city cards (web/lib/cityCards.ts): which market a card
// shows, which bucket it calls, which trade it offers. Run with: npm run test:routes
const assert = require('node:assert/strict');
const path = require('node:path');
const { buildCards, disagreementRecord } = require(path.join(__dirname, '..', '.route-test', 'lib', 'cityCards.js'));

const cities = [
  { city_key: 'london', display_name: 'London', unit: 'C' },
  { city_key: 'nyc', display_name: 'New York', unit: 'F' },
  { city_key: 'empty', display_name: 'No market', unit: 'C' },
];
const row = (o) => ({ band_id: `${o.city_key}-${o.band_label}`, band_index: 1, sigma_c: 1.2,
  confidence: 0.6, regime_label: 'normal', depth_5c: 40, block_reason: null, edge_at: '2026-09-24T13:00:00Z',
  forecast_max_c: 24.1, ...o });
const ladder = [
  // London tomorrow: 24C is the favourite. On the NO row model_prob is still the YES probability,
  // and its price is the NO price - the card must quote the YES price (0.39).
  row({ city_key: 'london', for_date: '2026-09-25', band_label: '24C', side: 'NO', model_prob: 0.41, market_price: 0.63, edge_net_pp: -0.05, tradeable: true }),
  row({ city_key: 'london', for_date: '2026-09-25', band_label: '24C', side: 'YES', model_prob: 0.41, market_price: 0.39, edge_net_pp: 0.01, tradeable: true }),
  // The biggest edge on the ladder is BLOCKED; the best tradeable one is 8pp.
  row({ city_key: 'london', for_date: '2026-09-25', band_label: '23C', side: 'YES', model_prob: 0.26, market_price: 0.17, edge_net_pp: 0.08, tradeable: true }),
  row({ city_key: 'london', for_date: '2026-09-25', band_label: '22C', side: 'YES', model_prob: 0.06, market_price: 0.02, edge_net_pp: 0.30, tradeable: false, block_reason: 'below_7c_yes' }),
  // London the day after: not the soonest, so not the default card.
  row({ city_key: 'london', for_date: '2026-09-26', band_label: '25C', side: 'YES', model_prob: 0.5, market_price: 0.4, edge_net_pp: 0.02, tradeable: true }),
  // New York: nothing tradeable with a positive edge.
  row({ city_key: 'nyc', for_date: '2026-09-24', band_label: '70-71F', side: 'YES', model_prob: 0.3, market_price: 0.35, edge_net_pp: -0.06, tradeable: true }),
  row({ city_key: 'nyc', for_date: '2026-09-24', band_label: '72-73F', side: 'YES', model_prob: 0.2, market_price: 0.01, edge_net_pp: 0.1, tradeable: false, block_reason: 'dead_band' }),
];
const forecasts = [
  { city_key: 'london', for_date: '2026-09-25', model: 'open_meteo_forecast', lead_days: 2, forecast_max_c: 23.0, run_at: 'a' },
  { city_key: 'london', for_date: '2026-09-25', model: 'open_meteo_forecast', lead_days: 1, forecast_max_c: 24.0, run_at: 'b' },
];
const own = [{ city_key: 'london', for_date: '2026-09-25', lead_days: 1, predicted_max_c: 23.6, nws_max_c: null, promotion_state: 'shadow' }];
const live = [
  { city_key: 'london', local_date: '2026-09-24', temp_c: 20, running_max_c: 22, peak_window_state: 'past', day_decided: true, observed_at: 'x', source_kind: 'station' },
  { city_key: 'nyc', local_date: '2026-09-24', temp_c: 21, running_max_c: 22.5, peak_window_state: 'in_window', day_decided: false, observed_at: 'x', source_kind: 'station' },
];

const cards = buildCards(cities, ladder, forecasts, own, live, 'soonest');
assert.deepEqual(cards.map((c) => c.city_key), ['london', 'nyc'],
  'a city with no open market gets no card; the card with a real edge comes first');
const [ldn, ny] = cards;
assert.equal(ldn.for_date, '2026-09-25', 'the soonest OPEN trade date');
assert.equal(ldn.top_band, '24C');
assert.equal(ldn.top_yes_price, 0.39, 'the favourite is quoted at the YES price, never the NO row');
assert.deepEqual([ldn.best.band, ldn.best.side, ldn.best.edge], ['23C', 'YES', 0.08],
  'the best trade is the best TRADEABLE edge, never a blocked one');
assert.deepEqual(ldn.forecasts, [{ model: 'open_meteo_forecast', max_c: 24.0, run_at: 'b' }],
  'each model shows its latest run for the day (the shortest lead)');
assert.equal(ldn.own.predicted_max_c, 23.6);
assert.equal(ldn.live, null, "today's live readings must not appear on tomorrow's card");
assert.equal(ldn.unit, 'C');

assert.equal(ny.best, null, 'no positive tradeable edge means no trade offered');
assert.equal(ny.blocked, 'dead_band');
assert.equal(ny.live.running_max_c, 22.5, 'same-day live readings are shown');
assert.equal(ny.unit, 'F');

const later = buildCards(cities, ladder, forecasts, own, live, '2026-09-26');
assert.deepEqual(later.map((c) => [c.city_key, c.for_date]), [['london', '2026-09-26']],
  'a chosen date shows only the cities with an open market that day');

// ---- the market beside the engine (Hassan, 24 Sep: "never favour losing bets")
// San Francisco on 24 Sep, as the live ladder had it: the engine centred at
// 81.7 F favoured 80-81 F (41%) that the market priced at 2c, while NWS said
// 73.4 F and the market favoured 74-75 F. The only "tradeable" edge was a NO
// on the market's favourite.
const sfc = [{ city_key: 'sf', display_name: 'San Francisco', unit: 'F' }];
const sfLadder = [
  row({ city_key: 'sf', for_date: '2026-09-24', band_label: '74-75F', side: 'YES', model_prob: 0.001, market_price: 0.298, edge_net_pp: -0.3, tradeable: true, forecast_max_c: 27.6 }),
  row({ city_key: 'sf', for_date: '2026-09-24', band_label: '74-75F', side: 'NO', model_prob: 0.001, market_price: 0.739, edge_net_pp: 0.251, tradeable: true, forecast_max_c: 27.6 }),
  row({ city_key: 'sf', for_date: '2026-09-24', band_label: '80-81F', side: 'YES', model_prob: 0.407, market_price: 0.022, edge_net_pp: 0.384, tradeable: false, block_reason: 'dead_band', forecast_max_c: 27.6 }),
];
const sfFc = [
  { city_key: 'sf', for_date: '2026-09-24', model: 'nws', lead_days: 0, forecast_max_c: 23.0, run_at: 'a' },
  { city_key: 'sf', for_date: '2026-09-24', model: 'open_meteo_forecast', lead_days: 0, forecast_max_c: 26.8, run_at: 'b' },
];
const [sf] = buildCards(sfc, sfLadder, sfFc, [], [], 'soonest');
assert.equal(sf.top_band, '80-81F');
assert.equal(sf.market_band, '74-75F', "the market's favourite is shown beside the engine's");
assert.equal(sf.disagrees, true);
assert.equal(sf.favourite_dead, true, 'a 2c favourite the edge engine calls dead is flagged');
assert.equal(sf.best.against_market, true, "a NO on the market's favourite is a bet against the market");
assert.ok(Math.abs(sf.forecast_spread_c - 3.8) < 1e-9, 'NWS and Open-Meteo 3.8 C apart');
assert.equal(sf.centre_outside_forecasts, true, 'a centre above every public forecast is flagged');

// ranking: a trade with the market sorts before any trade against it, whatever its edge
const both = buildCards([...cities, ...sfc], [...ladder, ...sfLadder], [...forecasts, ...sfFc], own, live, 'soonest');
assert.deepEqual(both.map((c) => c.city_key).slice(0, 2), ['london', 'sf'],
  'London (8pp, with the market) before San Francisco (25pp, against it)');

// the record the card quotes
const rec = disagreementRecord([
  { unit: 'C', model_call: 'a', market_call: 'b', model_hit: false, market_hit: true, head_to_head: true },
  { unit: 'C', model_call: 'a', market_call: 'b', model_hit: true, market_hit: false, head_to_head: true },
  { unit: 'C', model_call: 'a', market_call: 'a', model_hit: true, market_hit: true, head_to_head: true },
  { unit: 'F', model_call: 'x', market_call: 'y', model_hit: false, market_hit: false, head_to_head: false },
]);
assert.deepEqual(rec, { C: { days: 2, engine_won: 1, market_won: 1 } },
  'only head-to-head days where the favourites differed count');

console.log('PASS: city cards - soonest open market, YES-price favourite, best tradeable edge, same-day live only');
