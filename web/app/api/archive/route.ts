import { NextResponse } from 'next/server';
import { gunzipSync } from 'node:zlib';
import { decodePayload } from '@/lib/archiveFormat';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * READ THE ARCHIVE. The half that was missing.
 *
 * scripts/archive_observations.py moves cold rows out of Postgres and into
 * data/archive/ in this repo (a GitHub Release until #87). That part worked. What did not exist was any way to get
 * them back, so from the platform's side an archive was indistinguishable
 * from a deletion: the rows stopped being in Supabase, every page that read
 * them went empty, and nothing said where they had gone.
 *
 * This is where they went. The manifest at /archive/index.json is public and
 * needs no token - a page can fetch it directly to learn WHICH ranges are
 * archived and offer them. The rows themselves live in a file of a private
 * repo (older ones also on a Release), so fetching one needs a token, which is why it happens here
 * and not in the browser.
 *
 * TWO CALLS:
 *
 *   GET /api/archive
 *     the manifest, plus whether this deployment can actually fetch rows.
 *
 *   GET /api/archive?dataset=research&from=2026-09-12&to=2026-09-14&limit=500
 *     rows, decompressed and parsed out of the matching assets. from/to are
 *     inclusive dates on the archive's own time column.
 *
 * WHY IT PAGES RATHER THAN STREAMS. An asset is ~12 MB gzipped and ~78,000
 * rows. Handing that to a browser would freeze the tab and blow the function's
 * memory, so the row route takes a limit (default 500, hard cap 5,000) and
 * reports how many matched. A page shows a window; an export takes the file.
 *
 * THE FILE ITSELF IS ALWAYS AVAILABLE. ?asset=<name>&redirect=1 returns the
 * repo's own download URL so a person can take the whole thing, which is
 * the honest answer for anything bigger than a window.
 */

const DEFAULT_REPO = 'hassansab00/arbdesk4';
const MAX_LIMIT = 5000;
const CACHE_TTL_MS = 10 * 60 * 1000;

// One decompressed asset, kept between requests on a warm function. An asset
// never changes once written - the archive rewrites the whole file under a new
// range rather than appending - so a cached copy cannot go stale, only cold.
const cache = new Map<string, { at: number; rows: Record<string, string>[] }>();

function repo() {
  const owner = process.env.VERCEL_GIT_REPO_OWNER;
  const slug = process.env.VERCEL_GIT_REPO_SLUG;
  return process.env.GITHUB_REPOSITORY || (owner && slug ? `${owner}/${slug}` : DEFAULT_REPO);
}

function token() {
  return process.env.GITHUB_DISPATCH_TOKEN || process.env.GITHUB_TOKEN;
}

type Manifest = {
  updated_at?: string;
  datasets: Record<string, {
    table: string;
    release_tag: string;
    rows_archived?: number;
    assets: { asset: string; rows: number; gzip_bytes: number; from: string; to: string;
              archived_through: string; archived_at: string }[];
  }>;
};

async function manifest(origin: string): Promise<Manifest> {
  // Served as a static file from web/public, so this is the same fetch the
  // browser would make - no token, no database.
  const r = await fetch(`${origin}/archive/index.json`, { cache: 'no-store' });
  if (!r.ok) return { datasets: {} };
  return await r.json() as Manifest;
}

/** Minimal CSV reader. The archive writes with Python's csv module: RFC 4180,
 *  doubled quotes, newlines legal inside a quoted field - which JSON payloads
 *  contain constantly, so a split(',') would corrupt most rows. */
function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else quoted = false;
      } else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (c !== '\r') field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  if (!rows.length) return [];
  const head = rows[0];
  return rows.slice(1)
    .filter(r => r.length === head.length)
    .map(r => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}

/** Where an archive file lives in the repo. Every export since #87 (21 Sep)
 *  is committed here and nowhere else, and the archive job pulls every older
 *  Release asset into the same folder, so the repo is the complete copy. On
 *  23 Sep the repo held all 29 files in the index; the observations Release
 *  held 6 of its 9. */
const ARCHIVE_REF = process.env.ARCHIVE_REF || 'main';
function repoPath(dataset: string, assetName: string) {
  return `data/archive/${dataset}/${assetName}`;
}

async function fromRepo(dataset: string, assetName: string, headers: Record<string, string>) {
  // The contents API serves files up to 100 MB, but above 1 MB only as the raw
  // media type - which is also the only form that skips base64.
  const r = await fetch(
    `https://api.github.com/repos/${repo()}/contents/${repoPath(dataset, assetName)}?ref=${ARCHIVE_REF}`,
    { headers: { ...headers, Accept: 'application/vnd.github.raw' }, cache: 'no-store' });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`repo ${repoPath(dataset, assetName)}: ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

async function fromRelease(assetName: string, tag: string, headers: Record<string, string>) {
  const rel = await fetch(`https://api.github.com/repos/${repo()}/releases/tags/${tag}`,
                          { headers, cache: 'no-store' });
  if (!rel.ok) throw new Error(`release ${tag}: ${rel.status}`);
  const found = ((await rel.json()).assets ?? [])
    .find((a: { name: string }) => a.name === assetName);
  if (!found) throw new Error(`asset ${assetName} is neither in the repo nor on release ${tag}`);
  const bin = await fetch(found.url, {
    headers: { ...headers, Accept: 'application/octet-stream' },
    cache: 'no-store',
  });
  if (!bin.ok) throw new Error(`download: ${bin.status}`);
  return Buffer.from(await bin.arrayBuffer());
}

async function probeRepo(): Promise<{ ok: boolean; status: number | null; files?: number; reason?: string }> {
  const tok = token();
  if (!tok) return { ok: false, status: null, reason: 'no GitHub token in this deployment' };
  try {
    const r = await fetch(
      `https://api.github.com/repos/${repo()}/contents/data/archive?ref=${ARCHIVE_REF}`,
      { headers: { Authorization: `Bearer ${tok}`, Accept: 'application/vnd.github+json' },
        cache: 'no-store' });
    if (!r.ok) {
      return { ok: false, status: r.status,
               reason: r.status === 404 || r.status === 403
                 ? 'the token cannot read this repository (needs contents: read)'
                 : 'GitHub refused the request' };
    }
    const list = await r.json();
    return { ok: true, status: r.status, files: Array.isArray(list) ? list.length : undefined };
  } catch (e) {
    return { ok: false, status: null, reason: e instanceof Error ? e.message : String(e) };
  }
}

async function assetRows(dataset: string, assetName: string, tag: string): Promise<Record<string, string>[]> {
  const hit = cache.get(assetName);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) return hit.rows;

  const tok = token();
  if (!tok) throw new Error('no token');
  const headers = { Authorization: `Bearer ${tok}`, Accept: 'application/vnd.github+json' };

  // The repo first: it holds every file. A Release only as the fallback, for a
  // deployment whose token cannot read contents.
  let gz: Buffer | null = null;
  try {
    gz = await fromRepo(dataset, assetName, headers);
  } catch {
    gz = null;
  }
  if (!gz) gz = await fromRelease(assetName, tag, headers);
  const rows = parseCsv(gunzipSync(gz).toString('utf-8'));
  cache.set(assetName, { at: Date.now(), rows });
  return rows;
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const origin = url.origin;
  const dataset = url.searchParams.get('dataset');
  const idx = await manifest(origin);

  if (!dataset) {
    return NextResponse.json({
      ...idx,
      // So a page can say "archived, but this deployment cannot reach it"
      // rather than showing an empty table and implying the data is gone.
      can_fetch_rows: !!token(),
      // PROVEN, NOT ASSUMED (plan v2.1 P1.8). Having a token is not the same
      // as the token reading this repo: one small listing of data/archive
      // says whether every archived file is reachable from here, so a single
      // request to this URL answers the question for the whole archive.
      repo_read: await probeRepo(),
      repo: repo(),
    }, { headers: { 'Cache-Control': 'no-store' } });
  }

  const ds = idx.datasets?.[dataset];
  if (!ds) {
    return NextResponse.json({
      error: `Nothing archived under "${dataset}".`,
      available: Object.keys(idx.datasets ?? {}),
    }, { status: 404 });
  }

  const asset = url.searchParams.get('asset');
  if (asset && url.searchParams.get('redirect') === '1') {
    return NextResponse.json({
      download: `https://github.com/${repo()}/raw/${ARCHIVE_REF}/${repoPath(dataset, asset)}`,
      note: 'The whole file, from the repo. A private repo will ask you to sign in.',
    });
  }

  if (!token()) {
    return NextResponse.json({
      error: 'This deployment has no GitHub token, so archived rows cannot be fetched. '
        + 'The data is not lost - it is in the repo folder below - but the site cannot read it. '
        + 'Set GITHUB_DISPATCH_TOKEN and redeploy.',
      folder: `https://github.com/${repo()}/tree/${ARCHIVE_REF}/data/archive/${dataset}`,
      assets: ds.assets,
    }, { status: 503 });
  }

  const from = url.searchParams.get('from') ?? '';
  const to = url.searchParams.get('to') ?? '';
  const limit = Math.min(MAX_LIMIT,
    Math.max(1, parseInt(url.searchParams.get('limit') ?? '500', 10) || 500));
  const offset = Math.max(0, parseInt(url.searchParams.get('offset') ?? '0', 10) || 0);

  // Only the assets whose own range can contain the window, so asking for one
  // day does not download six weeks.
  const wanted = ds.assets.filter(a =>
    (!from || a.to >= from) && (!to || a.from <= to) &&
    (!asset || a.asset === asset));

  try {
    const collected: Record<string, string>[] = [];
    for (const a of wanted) {
      for (const row of await assetRows(dataset, a.asset, ds.release_tag)) {
        const stamp = (row.captured_at ?? row.valid_at ?? row.for_date
                       ?? row.traded_at ?? '').slice(0, 10);
        if (from && stamp && stamp < from) continue;
        if (to && stamp && stamp > to) continue;
        // Decoded here rather than left to the caller: the browser cannot
        // read the legacy encoding, and handing it a Python repr is how an
        // archive looks corrupt when it is merely old.
        collected.push(row.payload ? { ...row, payload: decodePayload(row.payload) } as never : row);
      }
    }
    return NextResponse.json({
      dataset,
      table: ds.table,
      matched: collected.length,
      returned: Math.min(limit, Math.max(0, collected.length - offset)),
      offset,
      limit,
      assets_read: wanted.map(a => a.asset),
      rows: collected.slice(offset, offset + limit),
    }, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return NextResponse.json({
      error: `The archive could not be read: ${e instanceof Error ? e.message : String(e)}. `
        + 'The rows are still in the repo; this is a fetch failure, not a loss.',
      folder: `https://github.com/${repo()}/tree/${ARCHIVE_REF}/data/archive/${dataset}`,
    }, { status: 502 });
  }
}
