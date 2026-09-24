// ===========================================================================
// THE VENUE'S VERDICTS ARE KEPT; ONLY THE PAYLOADS ARE ARCHIVED (plan v2 P4.5).
// Every proof leaves its verdict in resolution_verdicts, v_venue_band_resolution
// reads the ledger, and a proof pruned from the evidence table no longer takes
// the venue's answer with it. The browser reads the verdict columns only.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const MIGRATION = path.join(__dirname, '..', '..', 'supabase', 'migrations',
  '20260924030000_the_verdicts_are_kept.sql');

(async () => {
  const db = new PGlite();
  await db.exec(`
    create role anon; create role authenticated; create role service_role;
    create schema arbdesk_private;
    create function arbdesk_private.immutable_record() returns trigger
    language plpgsql set search_path='' as $$
    begin raise exception 'Append-only record; write a linked correction instead'; end $$;
    create table public.bands (band_id uuid primary key, market_id uuid, condition_id text,
      token_yes text, token_no text);
    -- the evidence table as 20260912083740 made it, without its immutability,
    -- so this test can play the prune
    create table public.paper_resolution_evidence (proof_id text primary key, condition_id text not null,
      token_yes text not null, token_no text not null,
      winning_token text not null check (winning_token in (token_yes, token_no)),
      captured_at timestamptz not null default clock_timestamp(),
      gamma jsonb not null, clob jsonb not null, source_urls jsonb not null);
    insert into public.bands values
      ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-0000000000aa', 'c1', 'y1', 'n1'),
      ('00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-0000000000aa', 'c2', 'y2', 'n2');
    -- one proof captured before the ledger existed
    insert into public.paper_resolution_evidence values
      ('p1', 'c1', 'y1', 'n1', 'y1', '2026-09-17 10:00+00', '{}', '{}', '[]');
  `);
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));
  await db.exec(fs.readFileSync(MIGRATION, 'utf-8'));   // every migration runs every time

  const ledger = async () => Number((await db.query('select count(*) n from public.resolution_verdicts')).rows[0].n);
  assert.equal(await ledger(), 1, 'the proof already in the table is copied once');

  await db.exec(`insert into public.paper_resolution_evidence values
      ('p2', 'c2', 'y2', 'n2', 'n2', '2026-09-18 10:00+00', '{"big":"payload"}', '{}', '[]')`);
  assert.equal(await ledger(), 2, 'a new proof leaves its verdict by trigger');

  // THE PRUNE: both proofs archived and deleted.
  await db.exec('delete from public.paper_resolution_evidence');
  const vbr = (await db.query(`select band_id, settled_yes, resolution_state, evidence_count
                                 from public.v_venue_band_resolution order by band_id`)).rows;
  assert.deepEqual(vbr.map((r) => [r.settled_yes, r.resolution_state, r.evidence_count]),
    [[true, 'confirmed', 1], [false, 'confirmed', 1]],
    "the venue's answer outlives the pruned proof");

  // A verdict restored from the archive is ignored when the ledger has it.
  await db.exec(`insert into public.resolution_verdicts (condition_id, token_yes, token_no, winning_token, captured_at, source)
                 values ('c1', 'y1', 'n1', 'y1', '2026-09-17 10:00+00', 'archive:x') on conflict do nothing`);
  assert.equal(await ledger(), 2);

  for (const sql of ['update public.resolution_verdicts set winning_token = token_no',
                     'delete from public.resolution_verdicts',
                     'truncate public.resolution_verdicts']) {
    let refused = false;
    try { await db.query(sql); } catch (e) { refused = true; }
    assert.ok(refused, `accepted: ${sql}`);
  }
  let bad = false;
  try {
    await db.query(`insert into public.resolution_verdicts (condition_id, token_yes, token_no, winning_token, captured_at, source)
                    values ('c3', 'y3', 'n3', 'someone_else', now(), 'x')`);
  } catch (e) { bad = true; }
  assert.ok(bad, 'a winning token that is neither side is not a verdict');

  const g = (await db.query(`select
      has_column_privilege('anon','public.resolution_verdicts','winning_token','select') a_verdict,
      has_column_privilege('anon','public.resolution_verdicts','source','select') a_source,
      has_table_privilege('anon','public.resolution_verdicts','insert') a_insert,
      has_table_privilege('service_role','public.resolution_verdicts','insert') s_insert`)).rows[0];
  assert.deepEqual([g.a_verdict, g.a_source, g.a_insert, g.s_insert], [true, false, false, true]);
  console.log('verdicts-are-kept: every proof leaves its verdict, a prune no longer erases the venue\'s answer, append-only, verdict columns only for the browser');
})().catch((e) => { console.error(e); process.exit(1); });
