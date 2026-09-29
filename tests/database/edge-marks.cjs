// ===========================================================================
// THE EDGES KEEP THE PRICES THE RECORD READS (plan v2 P1.6 phase 3, step 3.1,
// 29 Sep, 20260929220000_the_edges_keep_the_prices_the_record_reads.sql).
//
// Three cities - New York, Wellington, and one with no timezone (UTC) - with
// three settled market days and one still ahead, three bands each, a YES and
// a NO edge every three hours from three days before each day to the next
// noon, and the prices the engine made at the same moments. The views as they
// stood before 3.1 are built first (the files' own text with the step's three
// edits taken back out) and read; then the real migration, freeze_edge_marks
// and prune_edge_history at two days:
//
//   * v_hit_ladders, v_city_hit_history_live and the materialized view over
//     it return exactly what they returned before - before the first freeze,
//     after it, and after the prune has taken the marks from edges;
//   * the marks are the right rows: none for a band with no YES edge by the
//     cutoff, a NULL price kept as NULL, an edge exactly at 18:00 local on
//     the eve in (<=), one exactly at local midnight out (<), UTC for a city
//     with no timezone, and nothing for a day whose cutoff has not passed;
//   * until a mark is frozen, v_prunable_edge_history offers everything it
//     offered before except the marks; once frozen, exactly what it offered;
//   * a second freeze writes nothing, and an edge landing later before a
//     frozen cutoff does not rewrite it;
//   * the table, the live view and the freeze are the service role's alone;
//     the migration re-runs;
//   * 3.1b (20260929230000): with the marks a pre-3.1 prune took gone from
//     the table, restore_edge_marks brings back exactly those marks from the
//     rows the archive exported, in calls of whole bands - a dry run writes
//     nothing, a second pass writes nothing, a mark edges still holds is left
//     to freeze_edge_marks, an unknown band is counted - and both readers
//     read again what they read before the prune.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.join(__dirname, '..', '..');
const src = (rel) => fs.readFileSync(path.join(ROOT, rel), 'utf-8');
const MIG = src('supabase/migrations/20260929220000_the_edges_keep_the_prices_the_record_reads.sql');
const MIG2 = src('supabase/migrations/20260929230000_the_marks_the_prune_took_come_back.sql');
const between = (text, opener, closer) => {
  const i = text.indexOf(opener);
  assert.ok(i >= 0, `not found: ${opener}`);
  const j = text.indexOf(closer, i + opener.length);
  assert.ok(j >= 0, `not found after ${opener}: ${closer}`);
  return text.slice(i, j + closer.length);
};
// Take one of the step's edits back out; the fragment must be there verbatim.
const undo = (text, now, before) => {
  assert.ok(text.includes(now), `the step's edit is not in the file as written:\n${now}`);
  return text.replace(now, before);
};

// THE VIEWS AS THEY STOOD BEFORE 3.1, from the files as they stand now.
const E80 = src('sql/ad4_80_prune_edge_history.sql');
const OLD_PRUNABLE = undo(undo(
  between(E80, 'create or replace view v_prunable_edge_history as', 'and u.edge_id is null;'),
  `,
-- A mark derived_edge_marks does not hold yet, at the same cutoff: the
-- readers match on it, so a frozen price for another cutoff serves nobody.
unfrozen_marks as (
  select m.edge_id
    from public.v_edge_marks_live m
   where not exists (select 1 from public.derived_edge_marks d
                      where d.band_id = m.band_id and d.mark = m.mark
                        and d.cutoff_at = m.cutoff_at)
)`, ''),
  `  left join unfrozen_marks u on u.edge_id = s.edge_id
 where r.rn > 1
   and u.edge_id is null;`, ` where r.rn > 1;`);
const PRUNE_FN = between(E80, 'create or replace function public.prune_edge_history(', '$fn$;');

const OLD_LADDERS = undo(undo(
  between(src('sql/ad4_88_hit_tournament.sql'), 'create or replace view public.v_hit_ladders as',
          'and k.cutoff_at = d.cutoff_at;'),
  `       -- The eve's mark, frozen once its cutoff passed (derived_edge_marks,
       -- sql/ad4_80), else the edge itself: the edges prune keeps two days
       -- and never takes a mark it has not copied (plan v2 P1.6 phase 3).
       case when k.band_id is not null then k.market_price
            else (select e.market_price
                    from public.edges e
                   where e.band_id = o.band_id and e.side = 'YES' and e.computed_at <= d.cutoff_at
                   order by e.computed_at desc limit 1) end               as market_price`,
  `       (select e.market_price
          from public.edges e
         where e.band_id = o.band_id and e.side = 'YES' and e.computed_at <= d.cutoff_at
         order by e.computed_at desc limit 1)                            as market_price`),
  `
  left join public.derived_edge_marks k
         on k.band_id = o.band_id and k.mark = 'eve' and k.cutoff_at = d.cutoff_at;`, ';');

// The view before 3.1 had no priced centre either: audit repair 3 part 2
// (20260930004500) appended it later, so its four edits come out first.
const beforeCentre = (text) => undo(undo(undo(undo(text,
  `         p.forecast_max_c, p.sigma_c, p.confidence, p.regime_label, p.centre_c,\n`,
  `         p.forecast_max_c, p.sigma_c, p.confidence, p.regime_label,\n`),
  `bp.regime_label, bp.computed_at, bp.centre_c\n`,
  `bp.regime_label, bp.computed_at\n`),
  `         -- THE CENTRE THE CALL WAS PRICED ON (audit repair 3): from the newest
         -- row before the day, the same run as called_at - never the raw
         -- forecast_max_c the engine started from.
         (array_agg(m.centre_c order by m.priced_at desc nulls last, m.band_id))[1] as centre_c,\n`,
  ``),
  `  round(h.brier_uniform_common::numeric, 4)                                 as brier_uniform_common,
  round(m.centre_c::numeric, 2)                                             as centre_c,
  round((m.centre_c - m.observed_max_c)::numeric, 2)                        as centre_error_c\n`,
  `  round(h.brier_uniform_common::numeric, 4)                                 as brier_uniform_common\n`);

const OLD_HISTORY = undo(undo(beforeCentre(
  between(src('sql/ad4_85_city_hit_history.sql'), 'create view v_city_hit_history as',
          'order by m.for_date desc, m.city_key;')
    .replace('create view v_city_hit_history as', 'create view public.v_city_hit_history_live as')),
  `         case when k.band_id is not null then k.market_price
              else e.market_price end as market_pre`,
  `         e.market_price  as market_pre`),
  `    -- The market's YES price at the same cutoff: the frozen mark, else the
    -- edge itself.
    left join derived_edge_marks k
           on k.band_id = l.band_id and k.mark = 'day' and k.cutoff_at = l.day_starts_at
    left join lateral (
      select x.market_price
        from edges x
       where x.band_id = l.band_id
         and x.side = 'YES'
         and x.computed_at < l.day_starts_at
         and k.band_id is null`,
  `    -- The market's YES price at the same cutoff.
    left join lateral (
      select x.market_price
        from edges x
       where x.band_id = l.band_id
         and x.side = 'YES'
         and x.computed_at < l.day_starts_at`);

// A local wall-clock time on a day relative to today, in a city's zone.
const at = (day, hhmm, tz) => `(((current_date + ${day})::timestamp + interval '${hhmm}') at time zone '${tz}')`;

(async () => {
  const db = new PGlite();
  // What the views read, at the columns they use. The canonical and verified
  // views stand in as tables; paper-contracts builds the migration over the
  // real ones.
  await db.exec(`
    set timezone = 'UTC';
    create role anon; create role authenticated; create role service_role;
    grant usage on schema public to anon, authenticated, service_role;
    create table public.cities(city_key text primary key, display_name text, unit text, timezone text);
    create table public.markets(market_id uuid primary key, city_key text, resolution_date date);
    create table public.bands(band_id uuid primary key, market_id uuid, band_index int);
    create table public.edges(edge_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null default now(), side text not null, market_price numeric,
      tradeable boolean not null default false, book_snapshot_id bigint, prob_id bigint);
    create view public.v_latest_edge as
      select distinct on (band_id, side) * from public.edges order by band_id, side, computed_at desc;
    create table public.band_probabilities(prob_id bigserial primary key, band_id uuid not null,
      computed_at timestamptz not null, raw_prob numeric, calibrated_prob numeric, forecast_max_c numeric,
      sigma_c numeric, confidence numeric, regime_label text, centre_c numeric);
    create table public.v_fact_band_outcome_clean(city_key text, for_date date, band_id uuid, band_lo numeric,
      band_hi numeric, open_low boolean, open_high boolean, settled_yes boolean, observed_max_c numeric);
    create table public.v_canonical_bands(band_id uuid, market_id uuid, band_lo numeric, band_hi numeric,
      open_low boolean, open_high boolean, band_label text);
    create table public.v_canonical_markets(market_id uuid, unit text);
    create table public.v_verified_weather_outcomes(city_key text, for_date date, observed_max_c numeric);

    insert into public.cities values ('nyc', 'New York', 'F', 'America/New_York'),
                                     ('wlg', 'Wellington', 'C', 'Pacific/Auckland'),
                                     ('nul', 'No zone', 'C', null);
    -- Three settled days and one ahead, three bands a day.
    insert into public.markets
    select gen_random_uuid(), c.city_key, current_date + d
      from public.cities c cross join (values (-6), (-5), (-4), (2)) v(d);
    insert into public.bands
    select gen_random_uuid(), m.market_id, i from public.markets m cross join generate_series(1, 3) i;
    insert into public.v_canonical_markets select market_id, 'C' from public.markets;
    insert into public.v_canonical_bands
    select b.band_id, b.market_id, 19 + b.band_index, 20 + b.band_index, false, false, 'b' || b.band_index
      from public.bands b;
    -- Band 2 pays on every settled day.
    insert into public.v_fact_band_outcome_clean
    select m.city_key, m.resolution_date, b.band_id, 19 + b.band_index, 20 + b.band_index, false, false,
           b.band_index = 2, 21
      from public.bands b join public.markets m using (market_id)
     where m.resolution_date < current_date;
    insert into public.v_verified_weather_outcomes
    select distinct city_key, for_date, 21 from public.v_fact_band_outcome_clean;
    -- A YES and a NO edge every three hours, from three days before each day
    -- to the next noon (never in the future), and a price at each moment.
    insert into public.edges(band_id, computed_at, side)
    select b.band_id, t, s.side
      from public.bands b join public.markets m using (market_id)
      cross join lateral generate_series(((m.resolution_date - 3)::timestamp at time zone 'UTC'),
                                         least(now() - interval '1 minute',
                                               ((m.resolution_date + 1)::timestamp + interval '12 hours') at time zone 'UTC'),
                                         interval '3 hours') t
      cross join (values ('YES'), ('NO')) s(side);
    update public.edges set market_price = edge_id / 100000.0 where side = 'YES';
    insert into public.band_probabilities(band_id, computed_at, raw_prob, calibrated_prob, forecast_max_c, sigma_c,
                                          confidence, regime_label, centre_c)
    select e.band_id, e.computed_at, case b.band_index when 2 then 0.6 else 0.2 end,
           case b.band_index when 2 then 0.6 else 0.2 end, 21, 1, 0.5, 'normal', 21.4
      from public.edges e join public.bands b using (band_id) where e.side = 'YES';
  `);
  const q = async (sql) => (await db.query(sql)).rows;
  const one = async (sql) => (await q(sql))[0];
  const snap = async (sql) => JSON.stringify(await q(sql));
  const band = async (city, day, i) => (await one(`select b.band_id from public.bands b join public.markets m using (market_id)
    where m.city_key = '${city}' and m.resolution_date = current_date + ${day} and b.band_index = ${i}`)).band_id;

  // THE CASES. X: no YES edge by the eve (the eve's mark is nothing, the
  // day's is not). Y: the eve's mark carries no price. Z: one edge exactly at
  // 18:00 local on the eve (in) and one exactly at local midnight (out).
  // W: no YES edge at all.
  const X = await band('nyc', -5, 3), Y = await band('wlg', -5, 2), Z = await band('nyc', -4, 1), W = await band('nul', -4, 1);
  await db.exec(`
    delete from public.edges where band_id = '${X}' and side = 'YES' and computed_at <= ${at(-6, '18:00', 'America/New_York')};
    update public.edges set market_price = null
     where edge_id = (select edge_id from public.edges where band_id = '${Y}' and side = 'YES'
                        and computed_at <= ${at(-6, '18:00', 'Pacific/Auckland')} order by computed_at desc limit 1);
    insert into public.edges(band_id, computed_at, side, market_price) values
      ('${Z}', ${at(-5, '18:00', 'America/New_York')}, 'YES', 0.777),
      ('${Z}', ${at(-4, '00:00', 'America/New_York')}, 'YES', 0.888);
    delete from public.edges where band_id = '${W}' and side = 'YES';
  `);

  await db.exec(OLD_PRUNABLE);
  await db.exec(PRUNE_FN);
  await db.exec(OLD_LADDERS);
  await db.exec(OLD_HISTORY);
  await db.exec(`create materialized view public.mv_city_hit_history as select * from public.v_city_hit_history_live`);

  const HL = `select * from public.v_hit_ladders order by city_key, for_date, band_id`;
  const HH = `select * from public.v_city_hit_history_live order by city_key, for_date`;
  const MV = `select * from public.mv_city_hit_history order by city_key, for_date`;
  const OFFERED = `select edge_id from public.v_prunable_edge_history order by edge_id`;
  const hl0 = await snap(HL), hh0 = await snap(HH), offered0 = await q(OFFERED);
  const hlRows = JSON.parse(hl0), hhRows = JSON.parse(hh0);
  assert.equal(hlRows.length, 3 * 3 * 3, 'three cities, three settled days, three bands');
  // New York's and Wellington's 18 bands, less X (no edge by the eve) and Y
  // (no price at it); the city with no timezone has no cutoff here at all.
  assert.equal(hlRows.filter((r) => r.market_price !== null).length, 18 - 2, 'the ladders carry prices to lose');
  assert.equal(hlRows.find((r) => r.band_id === X).market_price, null);
  assert.equal(Number(hlRows.find((r) => r.band_id === Z).market_price), 0.777, 'the edge at 18:00 local is the eve\'s');
  assert.equal(hlRows.filter((r) => r.city_key === 'nul' && r.market_price !== null).length, 0,
    'v_hit_ladders puts no cutoff on a city with no timezone');
  assert.ok(hhRows.filter((r) => r.head_to_head).length >= 6, 'head-to-head days to lose');
  assert.ok(hhRows.some((r) => r.city_key === 'nul' && r.head_to_head), 'v_city_hit_history reads a city with no timezone in UTC');

  // ---- the migration ----------------------------------------------------
  await db.exec(MIG);
  assert.equal(await snap(HL), hl0, 'v_hit_ladders changed before the first freeze');
  assert.equal(await snap(HH), hh0, 'v_city_hit_history_live changed before the first freeze');

  // Until a mark is frozen it is never offered; nothing else changes.
  const marks = (await q(`select edge_id from public.v_edge_marks_live`)).map((r) => Number(r.edge_id));
  const markSet = new Set(marks);
  assert.ok(marks.length > 0);
  assert.deepEqual((await q(OFFERED)).map((r) => Number(r.edge_id)),
    offered0.map((r) => Number(r.edge_id)).filter((id) => !markSet.has(id)),
    'before the freeze the view must offer what it offered, less the marks');
  assert.ok(offered0.some((r) => markSet.has(Number(r.edge_id))), 'the old view offered marks - the bug');

  // ---- the freeze -------------------------------------------------------
  const expected = (await one(`select count(*)::int as n from public.v_edge_marks_live where cutoff_at < now() - interval '6 hours'`)).n;
  const f1 = (await one(`select public.freeze_edge_marks() as r`)).r;
  assert.equal(f1.ok, true, JSON.stringify(f1));
  assert.equal(f1.rows_written, expected, JSON.stringify(f1));
  // Every band of the nine settled days has both marks, less X's eve and W's two.
  assert.equal(expected, 3 * 3 * 3 * 2 - 1 - 2);
  assert.equal((await one(`select count(*)::int as n from public.derived_edge_marks k join public.bands b using (band_id)
    join public.markets m using (market_id) where m.resolution_date >= current_date`)).n, 0,
    'a day whose cutoff has not passed is not frozen');
  const mark = async (b, m) => one(`select * from public.derived_edge_marks where band_id = '${b}' and mark = '${m}'`);
  assert.equal(await mark(X, 'eve'), undefined);
  assert.ok(await mark(X, 'day'));
  assert.equal((await mark(Y, 'eve')).market_price, null, 'a NULL price is the mark, not a reason to look further back');
  assert.ok((await mark(Y, 'eve')).edge_id, 'the edge it came from');
  assert.equal(Number((await mark(Z, 'eve')).market_price), 0.777);
  assert.equal((await one(`select (select computed_at from public.derived_edge_marks where band_id = '${Z}' and mark = 'day')
    < ${at(-4, '00:00', 'America/New_York')} as before_midnight`)).before_midnight, true, 'the edge at local midnight is not before the day');
  assert.equal((await one(`select (cutoff_at = (current_date - 4)::timestamp at time zone 'UTC') as utc from public.derived_edge_marks
    where band_id = (select band_id from public.bands b join public.markets m using (market_id)
                      where m.city_key = 'nul' and m.resolution_date = current_date - 4 and b.band_index = 2) and mark = 'day'`)).utc, true);
  assert.equal(await mark(W, 'eve'), undefined);
  assert.equal((await one(`select count(*)::int as n from public.derived_edge_marks where source <> 'edges'`)).n, 0);

  assert.equal(await snap(HL), hl0, 'the freeze changed v_hit_ladders');
  assert.equal(await snap(HH), hh0, 'the freeze changed v_city_hit_history_live');
  assert.deepEqual(await q(OFFERED), offered0, 'every mark frozen: the view offers exactly what it offered');
  assert.equal((await one(`select public.freeze_edge_marks() as r`)).r.rows_written, 0, 'a second freeze wrote again');

  // ---- the prune at two days -------------------------------------------
  const dry = (await one(`select public.prune_edge_history(2, true) as r`)).r;
  assert.equal(dry.ok, true, JSON.stringify(dry));
  const n = Number(dry.would_delete);
  assert.equal(n, (await one(`select count(*)::int as n from public.v_prunable_edge_history where computed_at < now() - interval '2 days'`)).n);
  // What the archive exports before the prune deletes it: data/archive/edges.
  await db.exec(`create table public.archived_edges as
                 select * from public.v_prunable_edge_history where computed_at < now() - interval '2 days'`);
  assert.equal((await one(`select count(*)::int as n from public.archived_edges`)).n, n);
  const done = (await one(`select public.prune_edge_history(2, false, null, ${n}) as r`)).r;
  assert.equal(done.ok, true, JSON.stringify(done));
  assert.equal(Number(done.deleted), n);
  assert.equal(done.band_sides_priced_before, done.band_sides_priced_after, 'a band lost its newest price');
  const gone = (await one(`select count(*)::int as n from public.derived_edge_marks d
    where not exists (select 1 from public.edges e where e.edge_id = d.edge_id)`)).n;
  assert.ok(gone > 20, `the prune took the marks from edges (${gone}) - otherwise this proves nothing`);
  assert.equal(await snap(HL), hl0, 'v_hit_ladders changed with the prune');
  assert.equal(await snap(HH), hh0, 'v_city_hit_history_live changed with the prune');
  await db.exec(`refresh materialized view public.mv_city_hit_history`);
  assert.equal(await snap(MV), hh0, 'the page cache changed with the prune');

  // A frozen mark is never rewritten: an edge that lands later stamped just
  // before local midnight - the newest before the day, had it been read from
  // edges - does not move it.
  await db.exec(`insert into public.edges(band_id, computed_at, side, market_price)
                 values ('${Z}', ${at(-5, '23:59', 'America/New_York')} + interval '30 seconds', 'YES', 0.555)`);
  assert.equal((await one(`select public.freeze_edge_marks() as r`)).r.rows_written, 0);
  assert.equal(await snap(HL), hl0, 'a late edge rewrote a frozen mark');
  assert.equal(await snap(HH), hh0, 'a late edge rewrote a frozen pre-day mark');
  await db.exec(`delete from public.edges where band_id = '${Z}' and market_price = 0.555`);

  // ---- who may ----------------------------------------------------------
  for (const role of ['anon', 'authenticated']) {
    for (const t of ['public.derived_edge_marks', 'public.v_edge_marks_live']) {
      assert.equal((await one(`select has_table_privilege('${role}', '${t}', 'select') as ok`)).ok, false, `${role} reads ${t}`);
    }
    assert.equal((await one(`select has_function_privilege('${role}', 'public.freeze_edge_marks()', 'execute') as ok`)).ok,
      false, `${role} runs the freeze`);
  }
  assert.equal((await one(`select has_function_privilege('service_role', 'public.freeze_edge_marks()', 'execute') as ok`)).ok, true);
  assert.equal((await one(`select has_table_privilege('service_role', 'public.derived_edge_marks', 'select') as ok`)).ok, true);

  // ---- re-runnable ------------------------------------------------------
  const before = (await one(`select count(*)::int as n from public.derived_edge_marks`)).n;
  await db.exec(MIG);
  assert.equal((await one(`select count(*)::int as n from public.derived_edge_marks`)).n, before);
  assert.equal(await snap(HL), hl0, 'v_hit_ladders changed on a re-run');
  assert.equal(await snap(HH), hh0, 'v_city_hit_history_live changed on a re-run');

  // ---- 3.1b: the marks the prune had already taken come back ------------
  // (20260929230000). Before 3.1 the prune took the marks with no copy: the
  // table emptied is the state it left. The archive's rows bring them back.
  await db.exec(MIG2);
  await db.exec(`create table public.lost_marks as select * from public.derived_edge_marks;
                 delete from public.derived_edge_marks;`);
  assert.notEqual(await snap(HL), hl0, 'the prune took nothing v_hit_ladders reads - the restore would prove nothing');
  assert.notEqual(await snap(HH), hh0, 'the prune took nothing v_city_hit_history reads');
  const lost = (await one(`select count(*)::int as n from public.lost_marks`)).n;
  const arch = await q(`select band_id, computed_at, market_price, 'archive:edges-test.csv.gz' as source
                          from public.archived_edges where side = 'YES' order by band_id, computed_at`);
  const bandIds = [...new Set(arch.map((r) => r.band_id))];
  const half = new Set(bandIds.slice(0, Math.floor(bandIds.length / 2)));
  const parts = [arch.filter((r) => half.has(r.band_id)), arch.filter((r) => !half.has(r.band_id))];
  const restore = async (rows, dryRun) => (await db.query('select public.restore_edge_marks($1::jsonb, $2) as r',
    [JSON.stringify(rows), dryRun])).rows[0].r;
  const sum = (rs, k) => rs.reduce((t, r) => t + Number(r[k]), 0);
  const plan = [await restore(parts[0], true), await restore(parts[1], true)];
  assert.equal(sum(plan, 'marks_found'), lost, JSON.stringify(plan));
  assert.equal(sum(plan, 'to_write'), lost);
  assert.equal(sum(plan, 'written'), 0, 'a dry run wrote');
  assert.equal((await one(`select count(*)::int as n from public.derived_edge_marks`)).n, 0);
  const wrote = [await restore(parts[0], false), await restore(parts[1], false)];
  assert.equal(sum(wrote, 'written'), lost, JSON.stringify(wrote));
  assert.equal(await snap(HL), hl0, 'v_hit_ladders is not back to what it read before the prune');
  assert.equal(await snap(HH), hh0, 'v_city_hit_history_live is not back to what it read before the prune');
  const cols = 'band_id, mark, cutoff_at, computed_at, market_price';
  assert.equal((await one(`select count(*)::int as n from (select ${cols} from public.derived_edge_marks
    except all select ${cols} from public.lost_marks) x`)).n, 0);
  assert.equal((await one(`select count(*)::int as n from (select ${cols} from public.lost_marks
    except all select ${cols} from public.derived_edge_marks) x`)).n, 0);
  assert.equal((await one(`select count(*)::int as n from public.derived_edge_marks
    where edge_id is null and source = 'archive:edges-test.csv.gz'`)).n, lost, 'a restored mark names its file and no edge');
  const again = [await restore(parts[0], true), await restore(parts[1], true)];
  assert.equal(sum(again, 'to_write'), 0, 'a second pass would write again');
  assert.equal(sum(again, 'already_frozen'), lost);
  // A mark edges still holds is freeze_edge_marks' to copy, with its edge_id.
  const back = await one(`select * from public.lost_marks where mark = 'day' order by band_id limit 1`);
  await db.exec(`delete from public.derived_edge_marks where band_id = '${back.band_id}' and mark = 'day';
                 insert into public.edges(band_id, computed_at, side, market_price)
                 select band_id, computed_at, 'YES', market_price from public.lost_marks
                  where band_id = '${back.band_id}' and mark = 'day';`);
  const holds = await restore(arch.filter((r) => r.band_id === back.band_id), true);
  assert.equal(holds.edges_holds_it, 1, JSON.stringify(holds));
  assert.equal(holds.to_write, 0, JSON.stringify(holds));
  assert.equal((await restore(arch.filter((r) => r.band_id === back.band_id), false)).written, 0,
    'the restore wrote a mark edges still holds');
  assert.equal((await one(`select public.freeze_edge_marks() as r`)).r.rows_written, 1);
  assert.ok((await one(`select edge_id from public.derived_edge_marks where band_id = '${back.band_id}' and mark = 'day'`)).edge_id);
  assert.equal(await snap(HH), hh0);
  // A day whose cutoff has not passed is not a mark yet, whatever is sent.
  const ahead = await q(`select e.band_id, e.computed_at, e.market_price, 'archive:x' as source
                           from public.edges e join public.bands b using (band_id) join public.markets m using (market_id)
                          where m.resolution_date > current_date and e.side = 'YES'`);
  assert.ok(ahead.length > 0);
  const future = await restore(ahead, false);
  assert.equal(future.marks_found, 0, JSON.stringify(future));
  assert.equal(future.written, 0);
  const stranger = await restore([{ band_id: '00000000-0000-0000-0000-000000000001', computed_at: new Date().toISOString(),
    market_price: '0.5', source: 'archive:x' }], true);
  assert.equal(stranger.bands_unknown, 1, JSON.stringify(stranger));
  assert.equal(stranger.marks_found, 0);
  for (const role of ['anon', 'authenticated']) {
    assert.equal((await one(`select has_function_privilege('${role}', 'public.restore_edge_marks(jsonb, boolean)', 'execute') as ok`)).ok,
      false, `${role} runs the restore`);
  }
  await db.exec(MIG2);

  console.log(`PASS: edges keep the prices the record reads - ${hlRows.length} ladder bands and ${hhRows.length} city-days `
    + `unchanged before the freeze, after it (${f1.rows_written} marks) and after the prune (${n} edges, ${gone} marks only `
    + 'the table holds); the right rows at each cutoff; never offered unfrozen; never rewritten; service role only; re-runnable; '
    + `and the ${lost} marks a pre-3.1 prune took come back from the archive's rows, exactly, once, never over edges`);
})().catch((e) => { console.error(e); process.exit(1); });
