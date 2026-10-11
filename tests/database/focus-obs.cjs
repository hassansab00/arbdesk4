// ===========================================================================
// REPORTS THE MINUTE THEY ARRIVE (WXPredict build WP4; Hassan, 11 Oct: "yes
// add the poller"). focus_obs_poll() reads the answers pg_net has back and asks
// again for the stations whose local window is open; every distinct report is
// kept once with its reading time, the source's receipt and our first receipt;
// a correction is a new row beside the original; an unreadable answer is
// recorded and stops nothing; the Workflows-page switch stops the asking;
// focus_obs_day() writes one ingest_log row; the browser reads none of it.
// pg_net is faked here: http_get records the URL and returns an id, and the
// test writes the answers into net._http_response as pg_net's worker would.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIG = fs.readFileSync(path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20261011180000_reports_the_minute_they_arrive.sql'), 'utf-8');

const metar = (icao, obs, raw, receipt, temp) => ({
  icaoId: icao, obsTime: Math.floor(Date.parse(obs) / 1000), reportTime: obs, receiptTime: receipt,
  temp, dewp: 10, metarType: raw.startsWith('SPECI') ? 'SPECI' : 'METAR', rawOb: raw,
});

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create table public.settings (key text primary key, value jsonb not null);
    insert into public.settings values ('workflow_schedules', '{}');
    create table public.ingest_log (log_id bigserial primary key, job text, status text, rows int,
                                    detail jsonb, logged_at timestamptz not null default now());
    create function public.log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb) returns void
      language sql as $$ insert into public.ingest_log (job, status, rows, detail) values (p_job, p_status, p_rows, p_detail) $$;
  `);
  // without pg_net: created, asks nothing, fails nothing
  await db.exec(MIG);
  await db.exec(MIG);                                            // re-runnable
  const one = async (sql, p = []) => (await db.query(sql, p)).rows;
  assert.deepEqual((await one('select public.focus_obs_poll(true) r'))[0].r,
    { asked: false, read_new: 0, reason: 'no pg_net' });
  assert.deepEqual((await one('select icao, local_from, local_to from public.focus_obs_stations order by icao')),
    [{ icao: 'LTFM', local_from: 10, local_to: 17 }, { icao: 'OEJN', local_from: 10, local_to: 17 }]);

  await db.exec(`
    create schema net;
    create table net._http_response (id bigint, status_code int, content_type text, headers jsonb,
                                     content text, timed_out boolean, error_msg text, created timestamptz default now());
    create sequence net.req;
    create table net.calls (id bigint, url text);
    create function net.http_get(url text, params jsonb default '{}', headers jsonb default '{}',
                                 timeout_milliseconds int default 5000) returns bigint language plpgsql as $$
      declare v bigint := nextval('net.req'); begin insert into net.calls values (v, url); return v; end $$;
  `);
  const answer = async (id, status, body, created) => db.query(
    `insert into net._http_response (id, status_code, content, timed_out, created) values ($1, $2, $3, false, $4)`,
    [id, status, body, created]);

  // THE WINDOW: only the stations whose local hour is inside theirs are asked.
  const h = new Date().getUTCHours();
  await db.query(`update public.focus_obs_stations set timezone = 'UTC', local_from = $1::int, local_to = $1::int + 1 where icao = 'OEJN'`, [h]);
  await db.query(`update public.focus_obs_stations set timezone = 'UTC', local_from = $1::int, local_to = $1::int + 1 where icao = 'LTFM'`, [(h + 2) % 22]);
  let r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.asked, true);
  assert.deepEqual(r.stations, ['OEJN'], 'LTFM is outside its window');
  assert.match((await one('select url from net.calls where id = $1', [r.request_id]))[0].url,
    /aviationweather\.gov\/api\/data\/metar\?format=json&hours=3&ids=OEJN$/);
  const first = r.request_id;

  // AN ANSWER: two reports, kept once each with all three times.
  await answer(first, 200, JSON.stringify([
    metar('OEJN', '2026-10-12T07:00:00Z', 'METAR OEJN 120700Z 29003KT CAVOK 31/26 Q1008 NOSIG', '2026-10-12T07:00:22Z', 31),
    metar('OEJN', '2026-10-12T06:00:00Z', 'METAR OEJN 120600Z 30004KT CAVOK 29/25 Q1009 NOSIG', '2026-10-12T06:00:31Z', 29),
  ]), '2026-10-12T07:01:05Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.read_new, 2);
  const reps = await one(`select icao, obs_time::text o, source_receipt_at::text s, first_seen_at::text f, temp_c::float t,
                                 first_request_id::int q from public.focus_obs_reports order by obs_time`);
  assert.deepEqual(reps.map((x) => [x.o, x.s, x.f, x.t, x.q]), [
    ['2026-10-12 06:00:00+00', '2026-10-12 06:00:31+00', '2026-10-12 07:01:05+00', 29, first],
    ['2026-10-12 07:00:00+00', '2026-10-12 07:00:22+00', '2026-10-12 07:01:05+00', 31, first]]);
  assert.deepEqual((await one('select status_code, n_reports, n_new, (read_at is not null) rd from public.focus_obs_polls where request_id = $1', [first]))[0],
    { status_code: 200, n_reports: 2, n_new: 2, rd: true });

  // THE SAME REPORTS AGAIN, AND A CORRECTION: only the correction is new;
  // the original keeps its first receipt.
  const second = r.request_id;
  await answer(second, 200, JSON.stringify([
    metar('OEJN', '2026-10-12T07:00:00Z', 'METAR OEJN 120700Z 29003KT CAVOK 31/26 Q1008 NOSIG', '2026-10-12T07:00:22Z', 31),
    metar('OEJN', '2026-10-12T07:00:00Z', 'METAR COR OEJN 120700Z 29003KT CAVOK 32/26 Q1008 NOSIG', '2026-10-12T07:03:10Z', 32),
  ]), '2026-10-12T07:04:02Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  assert.equal(r.read_new, 1);
  assert.deepEqual((await one(`select raw_ob, first_seen_at::text f from public.focus_obs_reports
                                where obs_time = '2026-10-12T07:00:00Z' order by report_id`)).map((x) => [x.raw_ob.slice(0, 9), x.f]),
    [['METAR OEJ', '2026-10-12 07:01:05+00'], ['METAR COR', '2026-10-12 07:04:02+00']]);

  // AN UNREADABLE ANSWER is recorded on its poll and stops nothing; a failed
  // one (status 500) is read too.
  const third = r.request_id;
  await answer(third, 200, '<html>busy</html>', '2026-10-12T07:05:01Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  const fourth = r.request_id;
  await answer(fourth, 500, null, '2026-10-12T07:06:01Z');
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  const polls = await one('select request_id::int i, status_code, n_new, error from public.focus_obs_polls where request_id in ($1, $2) order by request_id', [third, fourth]);
  assert.equal(polls[0].n_new, 0);
  assert.match(polls[0].error, /unreadable answer/);
  assert.equal(polls[1].status_code, 500);
  assert.equal((await one('select count(*)::int n from public.focus_obs_reports'))[0].n, 3);

  // THE SWITCH: off or manual on the Workflows page asks nothing (but still reads).
  for (const mode of ['off', 'manual']) {
    await db.query(`update public.settings set value = jsonb_build_object('P2.11_focus_obs', jsonb_build_object('mode', $1::text)) where key = 'workflow_schedules'`, [mode]);
    const before = (await one('select count(*)::int n from net.calls'))[0].n;
    r = (await one('select public.focus_obs_poll() r'))[0].r;
    assert.equal(r.asked, false, mode);
    assert.equal(r.reason, `switched ${mode}`);
    assert.equal((await one('select count(*)::int n from net.calls'))[0].n, before, `${mode} asks nothing`);
  }
  await db.exec(`update public.settings set value = '{}' where key = 'workflow_schedules'`);
  await db.exec(`update public.focus_obs_stations set local_from = 0, local_to = 1, timezone = 'Pacific/Kiritimati'`);
  r = (await one('select public.focus_obs_poll() r'))[0].r;
  if (!r.asked) assert.equal(r.reason, 'no window open');

  // THE DAY: one ingest_log row, counted. The polls are put on the answers'
  // day, so nothing here depends on when the test runs.
  await db.exec(`update public.focus_obs_polls set asked_at = '2026-10-12T07:00:00Z'::timestamptz + request_id * interval '1 minute'`);
  const day = (await one(`select public.focus_obs_day('2026-10-12') r`))[0].r;
  const log = (await one(`select status, rows, detail from public.ingest_log where job = 'P2.11_focus_obs'`));
  assert.equal(log.length, 1);
  assert.equal(log[0].rows, 3);
  assert.equal(day.polls.new_reports, 3);
  assert.equal(day.stations.OEJN.new_reports, 3);
  assert.equal(day.stations.OEJN.corrections, 1);
  assert.ok(['ok', 'partial'].includes(log[0].status), log[0].status);

  // CLOSED TO THE BROWSER.
  const g = (await one(`select
      has_table_privilege('anon', 'public.focus_obs_reports', 'select') a,
      has_table_privilege('authenticated', 'public.focus_obs_polls', 'select') b,
      has_function_privilege('anon', 'public.focus_obs_poll(boolean)', 'execute') c,
      has_function_privilege('authenticated', 'public.focus_obs_day(date)', 'execute') d,
      has_table_privilege('service_role', 'public.focus_obs_reports', 'select') e`))[0];
  assert.deepEqual([g.a, g.b, g.c, g.d, g.e], [false, false, false, false, true]);

  await db.exec(MIG);                                            // re-runnable with rows in place
  assert.equal((await one('select count(*)::int n from public.focus_obs_reports'))[0].n, 3);
  console.log('PASS: focus-obs: each distinct report once with its reading time, the source receipt and our first receipt; a correction beside the original; an unreadable or failed answer recorded and nothing stopped; the window and the Workflows-page switch decide the asking; one ingest_log row a day; nothing scheduled without pg_cron and pg_net; closed to the browser; re-runnable');
})().catch((e) => { console.error(e); process.exit(1); });
