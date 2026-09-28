// ===========================================================================
// A TRADE LEAVES ONLY AFTER THE MIRROR HAS IT (plan v2 P1.6 phase 1, 28 Sep,
// 20260929000000_a_trade_leaves_after_the_mirror_has_it.sql). Hassan, 28 Sep:
// "yes keep trades in te mrror to, proceed."
//
// scripts/mirror_to_repo.py copies trades by ingested_at, one whole UTC day a
// night, after the prune. prune_trades now deletes a trade only if it traded
// before the cutoff AND was ingested before the midnight that opens the
// cutoff's UTC day; a trade ingested later than that waits a night. The count,
// the presence guard, the kept count and the delete agree on the pair, and
// the bound is the same midnight the archiver's export filters on. Every
// earlier guard still holds. The migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const MIG = fs.readFileSync(path.join(ROOT, 'supabase', 'migrations',
  '20260929000000_a_trade_leaves_after_the_mirror_has_it.sql'), 'utf-8');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.trades_observed (trade_id bigint primary key, band_id uuid, city_key text,
      traded_at timestamptz, observed_at timestamptz, ingested_at timestamptz not null default now(),
      price numeric, size numeric);
    create table public.archive_daily_city_presence (dataset text, day date, city_key text,
      primary key (dataset, day, city_key));
    create table public.settings (key text primary key, value jsonb);
    create table public.ingest_log (log_id bigserial primary key, job text not null,
      started_at timestamptz not null default now(), finished_at timestamptz, status text,
      rows_written int, detail jsonb, rows int, logged_at timestamptz default now());
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // UTC day arithmetic, as the archiver does it.
  const at = (daysAgo, hour) => `((current_date - ${daysAgo}) + time '${String(hour).padStart(2, '0')}:00') at time zone 'UTC'`;
  await db.exec(`set timezone = 'UTC'`);
  await db.exec(`insert into public.trades_observed (trade_id, city_key, traded_at, ingested_at) values
    (1, 'london', ${at(3, 10)}, ${at(3, 10)} + interval '5 minutes'),   -- ingested at once: mirrored
    (2, 'paris',  ${at(3, 11)}, ${at(1, 10)}),                          -- ingested yesterday: not yet
    (3, 'london', now() - interval '2 hours', now()),                   -- too recent to go
    (4, 'london', ${at(2, 12)}, ${at(2, 12)} + interval '1 minute')     -- ingested at once: mirrored
  `);
  // Presence for the city-days that go. Paris's is missing on purpose: its
  // one trade is held back, so the guard must not count it.
  await db.exec(`insert into public.archive_daily_city_presence values
    ('Trades seen', current_date - 3, 'london'), ('Trades seen', current_date - 2, 'london')`);
  const count = async () => (await db.query('select count(*)::int as n from public.trades_observed')).rows[0].n;
  const prune = async (args) => (await db.query(`select public.prune_trades(${args}) as r`)).rows[0].r;

  // The archiver's cutoff (its now() less a day) and the midnight its export
  // filters ingested_at on, computed the way the Python does.
  const cutoff = (await db.query(`select (now() - interval '1 day') as c`)).rows[0].c;
  const midnight = new Date(Date.UTC(cutoff.getUTCFullYear(), cutoff.getUTCMonth(), cutoff.getUTCDate()));
  const cut = `'${cutoff.toISOString()}'::timestamptz`;

  // Dry run: the two trades the mirror has; the late one and the recent one
  // stay; the missing presence for the held trade's city-day is not a refusal.
  const dry = await prune(`1, true, ${cut}`);
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 2);
  assert.equal(Number(dry.would_keep), 2);
  assert.equal(new Date(dry.ingested_before).getTime(), midnight.getTime(),
    'the prune and the export disagree on the mirror\'s midnight');

  // The count the old rule gave (every trade before the cutoff) is refused.
  const old = await prune(`1, false, ${cut}, 3`);
  assert.equal(old.ok, false);
  assert.match(old.error, /count mismatch/);
  assert.equal(await count(), 4);

  // The exact count: only what the mirror has goes.
  const done = await prune(`1, false, ${cut}, 2`);
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 2);
  const left = (await db.query(`select string_agg(trade_id::text, ',' order by trade_id) as ids from public.trades_observed`)).rows[0].ids;
  assert.equal(left, '2,3', 'the late-ingested trade left before the mirror had it');

  // A night later the mirror has it, and it goes - once its city-day is in
  // the presence rollup, as for every trade.
  await db.exec(`update public.trades_observed set ingested_at = ${at(2, 10)} where trade_id = 2`);
  const uncovered = await prune(`1, true, ${cut}`);
  assert.equal(uncovered.ok, false);
  assert.match(uncovered.error, /archive_daily_city_presence/);
  await db.exec(`insert into public.archive_daily_city_presence values ('Trades seen', current_date - 3, 'paris')`);
  const next = await prune(`1, false, ${cut}, 1`);
  assert.equal(next.ok, true, JSON.stringify(next));
  assert.equal(Number(next.deleted), 1);
  assert.equal(await count(), 1);

  // The earlier guards hold: a day's floor, the volume window, the ingest mark.
  const zero = await prune('0, true');
  assert.equal(zero.ok, false);
  assert.match(zero.error, /at least 1/);
  await db.exec(`insert into public.ingest_log (job, status, detail) values
    ('P0.4_trade_history', 'attention', jsonb_build_object('since', (now() - interval '30 hours')::text))`);
  const held = await prune(`1, true, ${cut}`);
  assert.equal(held.ok, false);
  assert.match(held.error, /trade ingest still reads from/);

  // Service role only.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_function_privilege('${role}', 'public.prune_trades(integer,boolean,timestamptz,bigint)', 'execute') as ok`)).rows[0].ok,
      false, `${role} can execute prune_trades`);
  }
  assert.equal((await db.query(`select has_function_privilege('service_role', 'public.prune_trades(integer,boolean,timestamptz,bigint)', 'execute') as ok`)).rows[0].ok, true);

  console.log("PASS: trades-mirror: a trade leaves only once ingested before the cutoff's UTC midnight (the mirror has it), the late one waits a night, the count, presence guard and delete agree, the export's midnight matches, the floor, window and ingest-mark guards hold, service role only, re-runnable");
})().catch((e) => { console.error(e); process.exit(1); });
