// ===========================================================================
// EVERY PRICE A READER USES STAYS (plan v2 P1.6 phase 2, step 6, 29 Sep,
// 20260929140000_the_prices_readers_use_stay.sql).
//
// v_prunable_band_probabilities offers the band_probabilities rows no reader
// selects, and prune_band_probabilities deletes them for markets at least 30
// days past their date. What must never be offered, one row each here: the
// newest of a band, the newest before the city's local day, the newest by
// 18:00 local on the eve (UTC for a city with no timezone, at the boundary
// itself), the row fact_band_outcome was priced at, every row at the newest
// published instant of the city-day, and a row an edge cites. The readers'
// own selections are identical before and after the prune. The prune takes
// the exact verified count or nothing, never a row the repo mirror has not
// had, rolls back if the delete takes a different set from the count, keeps
// every band priced, and releases an edge's price once the edge is gone.
// request_reclaim and the weekly backstop take the table, the view and the
// function are the service role's alone, and the migration re-runs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATIONS = path.join(__dirname, '..', '..', 'supabase', 'migrations');
const MIG = fs.readFileSync(path.join(MIGRATIONS, '20260929140000_the_prices_readers_use_stay.sql'), 'utf-8');

// A local wall-clock time on a day relative to today, in a city's zone.
const at = (day, hhmm, tz) => `(((current_date + ${day})::timestamp + interval '${hhmm}') at time zone '${tz}')`;

(async () => {
  const db = new PGlite();
  // The tables the view reads, at their live shapes (29 Sep): band_probabilities
  // as tests/database/paper-contracts.cjs builds it, edges.prob_id with its
  // live foreign key (no ON DELETE). cron as a recorder.
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    create schema cron;
    create table cron.calls (jobname text, schedule text, command text);
    create function cron.schedule(p_job text, p_expr text, p_cmd text) returns bigint
      language sql as $$ insert into cron.calls values (p_job, p_expr, p_cmd); select 1::bigint $$;
    create table public.cities(city_key text primary key, timezone text);
    create table public.markets(market_id uuid primary key, city_key text, resolution_date date);
    create table public.bands(band_id uuid primary key, market_id uuid);
    create table public.band_probabilities(prob_id bigserial primary key,band_id uuid not null,
      computed_at timestamptz not null default now(),forecast_version uuid,calibration_version uuid,
      raw_prob numeric,calibrated_prob numeric,input_forecast_run timestamptz,input_book_snapshot bigint,
      forecast_max_c numeric,bias_applied_c numeric,sigma_c numeric,lead_days integer,
      lattice_applied boolean default false,confidence numeric,regime_label text,skill_lead_days integer,
      skill_proxy boolean not null default false,skill_source text not null default 'legacy',
      pricing_eligible boolean not null default true,pricing_block_reason text,observed_floor_c numeric,
      centre_c numeric,forecast_sigma_c numeric,station_width_c numeric);
    create table public.edges(edge_id bigint primary key, band_id uuid, computed_at timestamptz,
      side text, prob_id bigint references public.band_probabilities(prob_id));
    create table public.fact_band_outcome(band_id uuid primary key, city_key text, for_date date,
      priced_at timestamptz);
    grant all on all tables in schema public to service_role;
  `);
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable

  // London's market 40 days back (bands A and B), a city with no timezone on
  // the same day (band E), and London 10 days back (band C) - inside the keep.
  const A = '00000000-0000-0000-0000-00000000000a', B = '00000000-0000-0000-0000-00000000000b';
  const C = '00000000-0000-0000-0000-00000000000c', E = '00000000-0000-0000-0000-00000000000e';
  const L = 'Europe/London', U = 'UTC';
  await db.exec(`
    insert into public.cities values ('london', '${L}'), ('nowhere', null);
    insert into public.markets values
      ('10000000-0000-0000-0000-000000000001', 'london',  current_date - 40),
      ('10000000-0000-0000-0000-000000000003', 'nowhere', current_date - 40),
      ('10000000-0000-0000-0000-000000000002', 'london',  current_date - 10);
    insert into public.bands values
      ('${A}', '10000000-0000-0000-0000-000000000001'), ('${B}', '10000000-0000-0000-0000-000000000001'),
      ('${E}', '10000000-0000-0000-0000-000000000003'), ('${C}', '10000000-0000-0000-0000-000000000002');`);
  // published: the width and forecast v_trajectory_evidence needs.
  const row = (id, band, ts, pub = true) =>
    `(${id}, '${band}', ${ts}, ${pub ? '21.0' : 'null'}, ${pub ? '1.2' : 'null'}, ${pub ? '1.1' : 'null'}, 0.2)`;
  const D = -40;
  await db.exec(`insert into public.band_probabilities (prob_id, band_id, computed_at, forecast_max_c, sigma_c, forecast_sigma_c, raw_prob) values
    ${row(101, A, at(D - 3, '12:00', L))},          -- nobody's: offered
    ${row(102, A, at(D - 2, '12:00', L))},          -- an edge cites it
    ${row(103, A, at(D - 2, '13:00', L))},          -- the fact was priced at it
    ${row(104, A, at(D - 2, '14:00', L))},          -- nobody's: offered
    ${row(105, A, at(D - 1, '18:00', L))},          -- the eve, exactly 18:00 local
    ${row(106, A, at(D - 1, '20:00', L))},          -- nobody's: offered
    ${row(107, A, at(D - 1, '23:00', L))},          -- the newest before the local day
    ${row(108, A, at(D, '10:00', L))},              -- nobody's: offered
    ${row(109, A, at(D, '12:00', L))},              -- the city-day's newest published
    ${row(110, A, at(D + 1, '01:00', L), false)},   -- the newest (unpublished)
    ${row(201, B, at(D, '11:00', L))},              -- nobody's: offered
    ${row(202, B, at(D - 1, '23:00', L))},          -- B's newest before the day
    ${row(203, B, at(D, '12:00', L))},              -- the same published instant as 109
    ${row(204, B, at(D + 1, '02:00', L), false)},   -- B's newest
    ${row(301, E, at(D - 2, '12:00', U))},          -- nobody's: offered
    ${row(302, E, at(D - 1, '17:00', U))},          -- the eve, in UTC for want of a zone
    ${row(303, E, at(D - 1, '19:00', U))},          -- the newest before the UTC day
    ${row(304, E, at(D + 1, '00:00', U))},          -- E's newest
    ${row(401, C, at(-13, '12:00', L))},            -- nobody's, but inside the keep
    ${row(403, C, at(-12, '12:00', L))},            -- C's eve and newest before the day
    ${row(402, C, at(-9, '12:00', L))}`);
  await db.exec(`insert into public.edges values (1, '${A}', ${at(D - 2, '12:00', L)}, 'YES', 102);
                 insert into public.fact_band_outcome values ('${A}', 'london', current_date + ${D}, ${at(D - 2, '13:00', L)});`);

  const ids = async (sql) => (await db.query(sql)).rows.map((r) => Number(r.prob_id));
  const offered = (cut = 'current_date - 30') =>
    ids(`select prob_id from public.v_prunable_band_probabilities where resolution_date < ${cut} order by prob_id`);
  const prune = async (args) => (await db.query(`select public.prune_band_probabilities(${args}) as r`)).rows[0].r;
  const count = async () => (await db.query('select count(*)::int as n from public.band_probabilities')).rows[0].n;

  // THE MARKS: exactly the rows nobody reads are offered, and the recent
  // market's only by the view, never below the cut.
  assert.deepEqual(await offered(), [101, 104, 106, 108, 201, 301]);
  assert.deepEqual(await ids(`select prob_id from public.v_prunable_band_probabilities order by prob_id`),
    [101, 104, 106, 108, 201, 301, 401]);
  const one = (await db.query(`select * from public.v_prunable_band_probabilities where prob_id = 104`)).rows[0];
  assert.equal(one.resolution_date.toISOString().slice(0, 10),
    (await db.query(`select (current_date - 40)::text as d`)).rows[0].d, 'the market date rides along');
  assert.equal(Number(one.raw_prob), 0.2, 'every column of the table rides along');

  // What each reader selects, as the live views select it.
  const READERS = `select json_build_object(
    'newest', (select json_agg(prob_id order by band_id) from (select distinct on (band_id) band_id, prob_id
                 from public.band_probabilities order by band_id, computed_at desc, prob_id desc) x),
    'before_day', (select json_agg(p.prob_id order by b.band_id) from public.bands b
                     join public.markets m on m.market_id = b.market_id left join public.cities c on c.city_key = m.city_key
                     join lateral (select bp.prob_id from public.band_probabilities bp
                                    where bp.band_id = b.band_id
                                      and bp.computed_at < (m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC'))
                                    order by bp.computed_at desc, bp.prob_id desc limit 1) p on true),
    'by_eve', (select json_agg(p.prob_id order by b.band_id) from public.bands b
                 join public.markets m on m.market_id = b.market_id left join public.cities c on c.city_key = m.city_key
                 join lateral (select bp.prob_id from public.band_probabilities bp
                                where bp.band_id = b.band_id
                                  and bp.computed_at <= (((m.resolution_date - 1)::timestamp + interval '18 hours') at time zone coalesce(c.timezone, 'UTC'))
                                order by bp.computed_at desc, bp.prob_id desc limit 1) p on true),
    'priced_at', (select json_agg(p.prob_id) from public.fact_band_outcome f
                    join public.band_probabilities p on p.band_id = f.band_id and p.computed_at = f.priced_at),
    'city_day', (select json_agg(x order by x) from (select distinct m.city_key || ' ' || max(bp.computed_at) over (partition by m.city_key, m.resolution_date) as x
                   from public.band_probabilities bp join public.bands b on b.band_id = bp.band_id
                   join public.markets m on m.market_id = b.market_id
                  where bp.sigma_c > 0 and bp.forecast_sigma_c > 0 and bp.forecast_max_c is not null) y),
    'edges', (select json_agg(p.prob_id) from public.edges e join public.band_probabilities p on p.prob_id = e.prob_id)) as r`;
  // Each reader's row, named: the fixture tests what it says it tests.
  const r0 = (await db.query(READERS)).rows[0].r;
  assert.deepEqual(r0.newest, [110, 204, 402, 304]);
  assert.deepEqual(r0.before_day, [107, 202, 403, 303]);
  assert.deepEqual(r0.by_eve, [105, 403, 302], 'B has no price by its eve; E\'s eve is 18:00 UTC');
  assert.deepEqual(r0.priced_at, [103]);
  assert.deepEqual(r0.edges, [102]);
  assert.equal(r0.city_day.length, 3);

  // The floor is thirty days; a committed prune needs the verified count.
  const short = await prune('29');
  assert.equal(short.ok, false);
  assert.match(short.error, /at least 30/);
  const blind = await prune('30, false');
  assert.equal(blind.ok, false);
  assert.match(blind.error, /p_expected_rows is required/);

  // Dry run: the six offered rows of the two old markets, nothing deleted.
  const dry = await prune('30, true');
  assert.equal(dry.ok, true, JSON.stringify(dry));
  assert.equal(Number(dry.would_delete), 6);
  assert.equal(Number(dry.bands_priced), 4);
  assert.equal(await count(), 21);
  // A cutoff newer than the floor is moved back to it, never forward.
  assert.equal(Number((await prune(`30, true, current_date`)).would_delete), 6, 'a cutoff inside the window was honoured');
  const older = await prune(`30, true, current_date - 41`);
  assert.equal(older.ok, true);
  assert.equal(Number(older.deleted), 0, 'an older cutoff is honoured: no market is dated before it');

  // A count that differs from the file deletes nothing.
  const wrong = await prune('30, false, null, 5');
  assert.equal(wrong.ok, false);
  assert.match(wrong.error, /count mismatch/);
  assert.equal(await count(), 21, 'a mismatched prune deleted rows');

  // THE MIRROR. A late price for the old band, computed since yesterday's UTC
  // midnight and then superseded, is offered but not in the repo mirror yet:
  // nothing goes, dry run or not. (Both unpublished, so the city-day's newest
  // published instant does not move; 110 is no longer A's newest.)
  const midnight = `((date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC')`;
  await db.exec(`insert into public.band_probabilities (prob_id, band_id, computed_at) values
                   (501, '${A}', ${midnight}), (502, '${A}', ${midnight} + interval '1 microsecond')`);
  assert.deepEqual(await offered(), [101, 104, 106, 108, 110, 201, 301, 501]);
  const young = await prune('30, true');
  assert.equal(young.ok, false, JSON.stringify(young));
  assert.match(young.error, /not in the repo mirror yet/);
  assert.equal(Number(young.not_yet_mirrored), 1);
  assert.equal((await prune('30, false, null, 8')).ok, false);
  assert.equal(await count(), 23, 'a row the mirror has not had was deleted');
  // A microsecond earlier, the mirror had it last night.
  await db.exec(`update public.band_probabilities set computed_at = computed_at - interval '1 microsecond' where prob_id = 501`);
  assert.equal(Number((await prune('30, true')).would_delete), 8);

  // A delete that takes a different set from the count rolls back whole.
  await db.exec(`create function skip_one() returns trigger language plpgsql as
                   $$ begin if old.prob_id = 104 then return null; end if; return old; end $$;
                 create trigger skip_one before delete on public.band_probabilities for each row execute function skip_one();`);
  await assert.rejects(db.query(`select public.prune_band_probabilities(30, false, null, 8)`),
    /counted 8 rows but the delete took 7/);
  assert.equal(await count(), 23, 'a prune whose delete differed from its count kept a deletion');
  await db.exec('drop trigger skip_one on public.band_probabilities');

  // The exact count: the eight go, every band still priced, every reader's
  // selection unchanged.
  const readersBeforePrune = JSON.stringify((await db.query(READERS)).rows[0].r);
  const done = await prune('30, false, null, 8');
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), 8);
  assert.equal(Number(done.bands_priced_before), 4);
  assert.equal(Number(done.bands_priced_after), 4);
  assert.deepEqual(await ids('select prob_id from public.band_probabilities order by prob_id'),
    [102, 103, 105, 107, 109, 202, 203, 204, 302, 303, 304, 401, 402, 403, 502]);
  assert.equal(JSON.stringify((await db.query(READERS)).rows[0].r), readersBeforePrune,
    'a reader selects a different row after the prune');

  // Nothing left to take: a no-op, not an error.
  const none = await prune('30, false, null, 0');
  assert.equal(none.ok, true);
  assert.equal(Number(none.deleted), 0);

  // THE FOREIGN KEY. The edge's price stays while the edge does; once the
  // edges prune removes the edge, the price is offered the next night.
  await assert.rejects(db.query('delete from public.band_probabilities where prob_id = 102'), /foreign key/);
  await db.exec('delete from public.edges where edge_id = 1');
  assert.deepEqual(await offered(), [102]);

  // request_reclaim takes the table and schedules its VACUUM FULL; every
  // table before it keeps its slot; the weekly backstop is scheduled.
  const rr = (await db.query(`select public.request_reclaim('band_probabilities') as r`)).rows[0].r;
  assert.equal(rr.ok, true);
  assert.equal(rr.job, 'ad4_reclaim_after_archive_band_probabilities');
  await assert.rejects(db.query(`select public.request_reclaim('bands')`), /not a table the archive prunes/);
  for (const t of ['research_captures', 'signals', 'weather_forecast_models']) {
    assert.equal((await db.query(`select public.request_reclaim('${t}') as r`)).rows[0].r.ok, true, t);
  }
  const calls = (await db.query('select jobname, schedule, command from cron.calls')).rows;
  assert.ok(calls.some((c) => c.jobname === 'ad4_reclaim_band_probabilities' && c.schedule === '5 7 * * 1'
    && c.command === 'VACUUM (FULL, ANALYZE) public.band_probabilities'), JSON.stringify(calls));

  // The view is the service role's alone, and so is the prune.
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await db.query(`select has_table_privilege('${role}', 'public.v_prunable_band_probabilities', 'select') as ok`)).rows[0].ok,
      false, `${role} can read the prunable view`);
    assert.equal((await db.query(`select has_function_privilege('${role}', 'public.prune_band_probabilities(integer,boolean,date,bigint)', 'execute') as ok`)).rows[0].ok,
      false, `${role} can execute the prune`);
  }
  assert.equal((await db.query(`select has_table_privilege('service_role', 'public.v_prunable_band_probabilities', 'select') as ok`)).rows[0].ok, true);
  assert.equal((await db.query(`select has_function_privilege('service_role', 'public.prune_band_probabilities(integer,boolean,date,bigint)', 'execute') as ok`)).rows[0].ok, true);
  await db.exec('set role anon');
  await assert.rejects(db.query('select count(*) from public.v_prunable_band_probabilities'), /permission denied/);
  await db.exec('reset role');

  console.log('PASS: band-probabilities: the newest, pre-day, eve (UTC without a zone, at the boundary), priced-at, city-day and edge-cited prices are never offered and every reader selects the same rows after the prune; a thirty-day floor, the exact verified count or nothing, never a row the repo mirror has not had, a delete that differs from its count rolls back, every band stays priced, an edge\'s price is released with the edge, request_reclaim and the weekly backstop take the table, service role only, re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
