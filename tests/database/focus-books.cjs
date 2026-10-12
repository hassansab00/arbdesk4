// ===========================================================================
// BOOKS THE MINUTE REPORTS ARRIVE (WXPredict WP5; the Codex action plan of
// 11 Oct, sections 4 and 5). Each minute of a focus station's window the poll
// asks, after the reports, for the books of that city's open markets of its
// own local today, in one request; every book is kept in the live record
// ('focus_book', keyed by its bucket) reduced - the touch found by price,
// sizes summed per price, ten levels each side, its own time - and only when
// it changed; an unreadable answer keeps nothing; a failure to ask stops
// nothing; the day's row reports books apart from reports; the browser reads
// none of it. pg_net is faked as in focus-obs.cjs.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const mig = (f) => fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations', f), 'utf-8');
const LIVE = mig('20261011210000_the_live_record.sql');
const LIVE2 = mig('20261012150000_the_live_record_keeps_the_evening_forecasts.sql');
const WP4 = mig('20261011180000_reports_the_minute_they_arrive.sql');
const MIG = mig('20261012170000_books_the_minute_reports_arrive.sql');

const level = (price, size) => ({ price: String(price), size: String(size) });

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values ('workflow_schedules', '{}');
    create table public.job_sla (job text primary key, cadence_minutes int not null, cadence text not null,
                                 scheduled_by text not null);
    create table public.ingest_log (log_id bigserial primary key, job text, status text, rows int, detail jsonb,
                                    logged_at timestamptz not null default now());
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log (job, status, rows, detail) values (p_job, p_status, p_rows, p_detail) $$;
    create schema cron;
    create table cron.calls (name text, expr text, cmd text);
    create function cron.schedule(n text, e text, c text) returns bigint language sql
      as $$ insert into cron.calls values (n, e, c); select 1::bigint $$;
    create table public.markets (market_id text primary key, city_key text, resolution_date date, closed boolean);
    create table public.bands (band_id text primary key, market_id text, band_index int, token_yes text);
  `);
  for (const m of [LIVE, LIVE2, WP4, MIG, MIG]) await db.exec(m);              // re-runnable
  const one = async (sql, p = []) => (await db.query(sql, p)).rows;

  // the live record takes the new kind, and only it besides the others
  const chk = (await one(`select pg_get_constraintdef(oid) d from pg_constraint
                           where conrelid = 'public.live_capture'::regclass and contype = 'c'`));
  assert.equal(chk.length, 1);
  for (const k of ['report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book']) {
    assert.ok(chk[0].d.includes(`'${k}'`), k);
  }
  assert.equal((chk[0].d.match(/'[a-z0-9_]+'::text/g) || []).length, 7);
  await assert.rejects(db.query(`select public.live_capture_put('focus_books', now(), '[]'::jsonb)`), /not a kind/);

  // pg_net, faked: the GET for reports and the POST for books
  await db.exec(`
    create schema net;
    create table net._http_response (id bigint, status_code int, content_type text, headers jsonb,
                                     content text, timed_out boolean, error_msg text, created timestamptz default now());
    create sequence net.req;
    create table net.calls (id bigint, url text, body jsonb);
    create function net.http_get(url text, params jsonb default '{}', headers jsonb default '{}',
                                 timeout_milliseconds int default 5000) returns bigint language plpgsql as $$
      declare v bigint := nextval('net.req'); begin insert into net.calls values (v, url, null); return v; end $$;
    create function net.http_post(url text, body jsonb default '{}', params jsonb default '{}',
                                  headers jsonb default '{}', timeout_milliseconds int default 5000)
      returns bigint language plpgsql as $$
      declare v bigint := nextval('net.req'); begin insert into net.calls values (v, url, body); return v; end $$;
  `);
  const answer = async (id, status, body, created) => db.query(
    `insert into net._http_response (id, status_code, content, timed_out, created) values ($1, $2, $3, false, $4)`,
    [id, status, body, created]);

  // Jeddah open today, Istanbul outside its window; a closed market, another
  // day's market and a band without a token are not asked.
  const h = new Date().getUTCHours();
  await db.query(`update public.focus_obs_stations set timezone = 'UTC', local_from = $1::int, local_to = $1::int + 1 where icao = 'OEJN'`, [h]);
  await db.query(`update public.focus_obs_stations set timezone = 'UTC', local_from = $1::int, local_to = $1::int + 1 where icao = 'LTFM'`, [(h + 2) % 22]);
  await db.exec(`
    insert into public.markets values ('mj', 'jeddah', current_date, false), ('mj-old', 'jeddah', current_date - 1, false),
                                      ('mj-closed', 'jeddah', current_date, true), ('mi', 'istanbul', current_date, false);
    insert into public.bands values ('bj0', 'mj', 0, 'tj0'), ('bj1', 'mj', 1, 'tj1'), ('bj2', 'mj', 2, null),
                                    ('bo0', 'mj-old', 0, 'to0'), ('bc0', 'mj-closed', 0, 'tc0'), ('bi0', 'mi', 0, 'ti0');
  `);
  let r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.deepEqual(r.stations, ['OEJN']);
  const booksReq = r.books_request_id;
  assert.ok(booksReq, 'the books are asked beside the reports');
  const call = (await one('select url, body from net.calls where id = $1', [booksReq]))[0];
  assert.equal(call.url, 'https://clob.polymarket.com/books');
  assert.deepEqual(call.body, [{ token_id: 'tj0' }, { token_id: 'tj1' }], "only Jeddah's open markets of today");
  assert.deepEqual((await one('select source, stations from public.focus_obs_polls where request_id = $1', [booksReq]))[0],
    { source: 'clob_books', stations: ['OEJN'] });

  // THE ANSWER: asks worst first as the venue lists them, a repeated price,
  // an empty level, more than ten levels; a second book with no bid; a token
  // the platform does not know.
  const asks = [level(0.99, 5), level(0.6, 0)];
  for (let i = 20; i >= 1; i--) asks.push(level((0.3 + i / 100).toFixed(2), 10));
  asks.push(level(0.31, 7));                                    // 0.31 again: 10 + 7
  const book0 = { asset_id: 'tj0', timestamp: '1791820365605', last_trade_price: '0.33', min_order_size: '5',
                  tick_size: '0.01', bids: [level(0.25, 40), level(0.29, 12), level(0.28, 0)], asks };
  const book1 = { asset_id: 'tj1', timestamp: '1791820300000', last_trade_price: '', min_order_size: '5',
                  tick_size: '0.001', bids: [], asks: [level(0.002, 100), level(0.001, 5000)] };
  const stranger = { asset_id: 'nobody', timestamp: '1791820300000', bids: [], asks: [level(0.5, 1)] };
  await answer(booksReq, 200, JSON.stringify([book0, book1, stranger]), '2026-10-12T07:01:05Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.read_new, 0, "read_new counts reports, as before");
  const rows = await one(`select key, captured_at::text c, payload from public.live_capture where kind = 'focus_book' order by key`);
  assert.deepEqual(rows.map((x) => [x.key, x.c]), [['bj0', '2026-10-12 07:01:05+00'], ['bj1', '2026-10-12 07:01:05+00']],
    'keyed by bucket, at the answer, the stranger left out');
  const p0 = rows[0].payload;
  assert.equal(Number(p0.ask), 0.31, 'the touch by price, not by position');
  assert.equal(Number(p0.bid), 0.29);
  assert.deepEqual(p0.asks[0].map(Number), [0.31, 17], 'sizes summed per price');
  assert.equal(p0.asks.length, 10, 'ten levels');
  assert.deepEqual(p0.asks.map((x) => Number(x[0])), [0.31, 0.32, 0.33, 0.34, 0.35, 0.36, 0.37, 0.38, 0.39, 0.4]);
  assert.deepEqual(p0.bids.map((x) => x.map(Number)), [[0.29, 12], [0.25, 40]], 'an empty level dropped');
  assert.deepEqual([p0.last, p0.ts, p0.min, p0.tick].map(Number), [0.33, 1791820365, 5, 0.01]);
  const p1 = rows[1].payload;
  assert.equal(p1.bid, null);
  assert.equal(Number(p1.ask), 0.001);
  assert.equal(p1.last, null);
  assert.deepEqual((await one('select n_reports, n_new, error from public.focus_obs_polls where request_id = $1', [booksReq]))[0],
    { n_reports: 2, n_new: 2, error: null });

  // THE NEXT MINUTE: the same books store nothing; a book that changed is
  // stored again.
  const second = r.books_request_id;
  await answer(second, 200, JSON.stringify([book0, { ...book1, asks: [level(0.003, 100)], timestamp: '1791820400000' }]),
    '2026-10-12T07:02:04Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.deepEqual((await one('select n_reports, n_new from public.focus_obs_polls where request_id = $1', [second]))[0],
    { n_reports: 2, n_new: 1 });
  assert.equal((await one(`select count(*)::int n from public.live_capture where kind = 'focus_book'`))[0].n, 3);

  // AN UNREADABLE ANSWER keeps nothing of itself and stops nothing.
  const third = r.books_request_id;
  await answer(third, 200, JSON.stringify([{ ...book0, asks: [level('n/a', 3)] }]), '2026-10-12T07:03:04Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  const bad = (await one('select n_reports, n_new, error from public.focus_obs_polls where request_id = $1', [third]))[0];
  assert.equal(bad.n_new, 0);
  assert.match(bad.error, /unreadable answer/);
  assert.equal((await one(`select count(*)::int n from public.live_capture where kind = 'focus_book'`))[0].n, 3);

  // NOTHING TO ASK: no open market today, the reports are still asked.
  await db.exec(`update public.markets set closed = true where market_id = 'mj'`);
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.asked, true);
  assert.equal(r.books_request_id, null);
  await db.exec(`update public.markets set closed = false where market_id = 'mj'`);

  // A FAILURE TO ASK THE BOOKS stops nothing.
  await db.exec(`alter table public.bands rename to bands_away`);
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.asked, true);
  assert.equal(r.books_request_id, null);
  await db.exec(`alter table public.bands_away rename to bands`);

  // THE DAY: books reported apart from the reports.
  await db.exec(`update public.focus_obs_polls set asked_at = '2026-10-12T07:00:00Z'::timestamptz + request_id * interval '1 minute'`);
  const day = (await one(`select public.focus_obs_day('2026-10-12') r`))[0].r;
  assert.equal(day.books.asked, 4, 'the minute after the unreadable one asked again');
  assert.equal(day.books.unanswered, 1);
  assert.equal(day.books.books_stored, 3);
  assert.equal(day.books.books_seen, 4);
  assert.equal(day.books.failed, 1, 'the unreadable one');
  assert.equal(day.polls.reports_seen, 0, 'books are not counted as reports');
  assert.ok(day.polls.asked >= 5);

  // CLOSED TO THE BROWSER.
  const g = (await one(`select
      has_function_privilege('anon', 'public.focus_books_ask(text[])', 'execute') a,
      has_function_privilege('authenticated', 'public.focus_book_top(jsonb)', 'execute') b,
      has_function_privilege('anon', 'public.focus_obs_poll(boolean)', 'execute') c,
      has_function_privilege('service_role', 'public.focus_obs_poll(boolean)', 'execute') d`))[0];
  assert.deepEqual([g.a, g.b, g.c, g.d], [false, false, false, true]);

  // RE-RUNNABLE with rows in place
  const n = (await one('select count(*)::int n from public.live_capture'))[0].n;
  await db.exec(MIG);
  assert.equal((await one('select count(*)::int n from public.live_capture'))[0].n, n);
  console.log('PASS: focus-books: each minute of the window the open same-day books of the window\'s cities, one request, kept in the live record by bucket - the touch by price, sizes summed per price, ten levels a side, the book\'s own time - and only when changed; an unreadable answer keeps nothing; no market or a failure to ask stops nothing; the day reports books apart; closed to the browser; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
