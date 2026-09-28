// ===========================================================================
// A DAY'S VOLUME NEVER GOES DOWN (plan v2 P1.6, 28 Sep,
// 20260928190000_a_days_volume_never_goes_down.sql). The archive's prune cuts
// trades_observed at a timestamp, so the oldest day left is part of a day;
// refresh_derived recounted it and overwrote the whole day's volume with the
// part left (live on 28 Sep: 25 Aug and 11-14 Sep short by 330-634 trades a
// day against the repository's mirror). A recount may raise a stored day,
// never lower it - also when a trade arrives late for a pruned day. And the
// trades floor is two days, not fourteen. The migration re-runs.
//
// THEN ONE DAY (plan v2 P1.6 phase 1, 28 Sep evening,
// 20260928210000_trades_keep_one_day.sql, applied after it here as in
// production): never shorter than the window the volume views read, and
// never a cutoff past the trade ingest's own mark.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260928190000_a_days_volume_never_goes_down.sql'), 'utf-8');
const MIG_ONE_DAY = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260928210000_trades_keep_one_day.sql'), 'utf-8');
const RECONCILE = fs.readFileSync(path.join(ROOT, 'sql', 'ad4_13_reconcile.sql'), 'utf-8');
const TS_EXPR = RECONCILE.slice(RECONCILE.indexOf('create or replace function ad4_trades_ts_expr'),
  RECONCILE.indexOf('$ad4$;', RECONCILE.indexOf('create or replace function ad4_trades_ts_expr')) + 6);

const BAND = '00000000-0000-0000-0000-000000000001';

(async () => {
  const db = new PGlite();
  // The live columns (28 Sep) the two functions read; the tables come from
  // sql/ files in production, which this harness does not apply.
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.trades_observed (trade_id bigint primary key, band_id uuid, city_key text,
      traded_at timestamptz, observed_at timestamptz, ingested_at timestamptz, price numeric, size numeric);
    create table public.derived_city_day_volume (city_key text, trade_date date, volume_usd numeric,
      n_trades int, computed_at timestamptz, primary key (city_key, trade_date));
    create table public.derived_band_day_volume (band_id uuid, city_key text, trade_date date,
      volume_usd numeric, n_trades int, computed_at timestamptz, primary key (band_id, trade_date));
    create table public.bands (band_id uuid primary key, market_id uuid);
    create table public.markets (market_id uuid primary key, city_key text);
    create table public.archive_daily_city_presence (dataset text, day date, city_key text,
      primary key (dataset, day, city_key));
    create table public.settings (key text primary key, value jsonb);
    create table public.ingest_log (log_id bigserial primary key, job text not null,
      started_at timestamptz not null default now(), finished_at timestamptz, status text,
      rows_written int, detail jsonb, rows int, logged_at timestamptz default now());
  `);
  await db.exec(TS_EXPR);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  await db.exec(MIG_ONE_DAY);
  await db.exec(MIG_ONE_DAY);                                    // re-runnable

  const at = (daysAgo, hour) => `((current_date - ${daysAgo}) + time '${String(hour).padStart(2, '0')}:00') at time zone 'UTC'`;
  const trade = (id, daysAgo, hour, price, size) =>
    `(${id}, '${BAND}', 'london', ${at(daysAgo, hour)}, null, now(), ${price}, ${size})`;
  const day = (daysAgo) => `(current_date - ${daysAgo})`;
  const city = async (daysAgo) => (await db.query(
    `select n_trades, volume_usd::float as v from public.derived_city_day_volume where city_key = 'london' and trade_date = ${day(daysAgo)}`)).rows[0];
  const band = async (daysAgo) => (await db.query(
    `select n_trades, volume_usd::float as v from public.derived_band_day_volume where band_id = '${BAND}' and trade_date = ${day(daysAgo)}`)).rows[0];
  const refresh = async () => (await db.query('select public.refresh_derived() as r')).rows[0].r;

  // Three whole days: 6, 5 and 4 days ago (outside the two-day floor).
  await db.exec(`insert into public.trades_observed values
    ${trade(1, 6, 10, 0.5, 10)}, ${trade(2, 5, 1, 0.4, 10)}, ${trade(3, 5, 12, 0.6, 10)}, ${trade(4, 4, 9, 0.5, 2)};
    insert into public.archive_daily_city_presence
      select distinct 'Trades seen', (traded_at at time zone 'UTC')::date, city_key from public.trades_observed;`);
  assert.equal((await refresh()).ok, true);
  assert.deepEqual(await city(5), { n_trades: 2, v: 10 });
  assert.deepEqual(await band(5), { n_trades: 2, v: 10 });

  // The archive prunes part-way through day -5 (its 01:00 trade goes, the
  // 12:00 one stays) and the recount runs again. Before, day -5 became 1
  // trade and 6.0; it keeps its whole total now.
  const cut = (await db.query(`select (${at(5, 6)})::text as c`)).rows[0].c;
  const pruned = (await db.query(`select public.prune_trades(2, false, '${cut}', 2) as r`)).rows[0].r;
  assert.equal(pruned.ok, true, JSON.stringify(pruned));
  assert.equal(Number(pruned.deleted), 2);
  await refresh();
  assert.deepEqual(await city(5), { n_trades: 2, v: 10 }, 'a pruned-through day was lowered');
  assert.deepEqual(await band(5), { n_trades: 2, v: 10 }, 'a pruned-through band-day was lowered');
  assert.deepEqual(await city(6), { n_trades: 1, v: 5 }, 'a fully pruned day changed');

  // A trade arrives late for day -6, whose trades are gone: the recount
  // sees one small trade and must not replace the day's whole total.
  await db.exec(`insert into public.trades_observed values ${trade(5, 6, 11, 0.5, 2)}`);
  await refresh();
  assert.deepEqual(await city(6), { n_trades: 1, v: 5 }, 'a late trade replaced a pruned day');

  // A day that grows is still raised.
  await db.exec(`insert into public.trades_observed values ${trade(6, 4, 10, 0.5, 4)}`);
  await refresh();
  assert.deepEqual(await city(4), { n_trades: 2, v: 3 }, 'a growing day was not raised');
  assert.deepEqual(await band(4), { n_trades: 2, v: 3 });

  // A day stored without a count (older rows) is filled.
  await db.exec(`update public.derived_city_day_volume set n_trades = null where trade_date = ${day(4)}`);
  await refresh();
  assert.deepEqual(await city(4), { n_trades: 2, v: 3 });

  // The floor is one day: none is refused, one is accepted.
  const none = (await db.query('select public.prune_trades(0, true) as r')).rows[0].r;
  assert.equal(none.ok, false);
  assert.match(none.error, /at least 1/);
  assert.equal((await db.query('select public.prune_trades(1, true) as r')).rows[0].r.ok, true);

  // ...and never shorter than the window the volume views read: widen it to
  // 48 hours and one day is refused until keep_days follows.
  await db.exec(`insert into public.settings values ('volume_thresholds', '{"lookback_hours": 48}')`);
  const narrow = (await db.query('select public.prune_trades(1, true) as r')).rows[0].r;
  assert.equal(narrow.ok, false, 'a prune shorter than the volume window was accepted');
  assert.equal(narrow.lookback_hours, 48);
  assert.equal((await db.query('select public.prune_trades(2, true) as r')).rows[0].r.ok, true);
  await db.exec(`delete from public.settings where key = 'volume_thresholds'`);

  // NEVER A TRADE THE INGEST MAY READ AGAIN: with the ingest's mark 30 hours
  // back (a stalled cycle), a one-day cutoff is newer than the mark - the
  // ingest would fetch those trades again, insert them as new and have them
  // archived twice - so it is refused, and nothing is deleted...
  await db.exec(`insert into public.trades_observed values ${trade(7, 2, 10, 0.5, 2)};
    insert into public.archive_daily_city_presence values ('Trades seen', ${day(2)}, 'london')`);
  const before = (await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n;
  await db.exec(`insert into public.ingest_log (job, status, detail, logged_at)
    values ('P0.4_trade_history', 'attention', jsonb_build_object('since', (now() - interval '30 hours')::text, 'complete', false), now())`);
  // The archiver passes its own cutoff, now() less the window (with no
  // cutoff the function falls back to yesterday's midnight).
  const dayAgo = (await db.query(`select (now() - interval '24 hours')::text as c`)).rows[0].c;
  const held = (await db.query(`select public.prune_trades(1, false, '${dayAgo}', 1) as r`)).rows[0].r;
  assert.equal(held.ok, false, 'a cutoff past the ingest mark was accepted');
  assert.match(held.error, /trade ingest still reads from/);
  assert.equal((await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n, before);
  // ...while a cutoff at or before the mark still prunes, count-bound. A later
  // row with no mark (a switched-off run) does not hide the last real one.
  await db.exec(`insert into public.ingest_log (job, status, detail, logged_at)
    values ('P0.4_trade_history', 'skipped', '{"summary": "off"}', now() + interval '1 second')`);
  const cutAt = (await db.query(`select (now() - interval '31 hours')::text as c`)).rows[0].c;
  const older = (await db.query(`select count(*)::int as n from public.trades_observed where traded_at < '${cutAt}'`)).rows[0].n;
  const ok = (await db.query(`select public.prune_trades(1, false, '${cutAt}', ${older}) as r`)).rows[0].r;
  assert.equal(ok.ok, true, JSON.stringify(ok));
  assert.equal(Number(ok.deleted), older);
  // A caught-up ingest (mark an hour back) holds nothing back.
  await db.exec(`insert into public.ingest_log (job, status, detail, logged_at)
    values ('P0.4_trade_history', 'ok', jsonb_build_object('since', (now() - interval '1 hour')::text, 'complete', true), now() + interval '2 seconds')`);
  assert.equal((await db.query(`select public.prune_trades(1, true, '${dayAgo}') as r`)).rows[0].r.ok, true);

  // Both stay the service role's alone.
  for (const fn of ['public.refresh_derived()', 'public.prune_trades(integer,boolean,timestamptz,bigint)']) {
    for (const role of ['anon', 'authenticated']) {
      assert.equal((await db.query(`select has_function_privilege('${role}', '${fn}', 'execute') as ok`)).rows[0].ok,
        false, `${role} can execute ${fn}`);
    }
    assert.equal((await db.query(`select has_function_privilege('service_role', '${fn}', 'execute') as ok`)).rows[0].ok, true);
  }

  console.log("PASS: day-volume: a pruned-through day keeps its whole total, a late trade cannot replace one, a growing day is raised, trades keep one day and never less than the volume window, no cutoff past the ingest's mark, service role only, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
