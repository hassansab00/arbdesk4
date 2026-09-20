"use client";

import { useEffect, useState } from "react";
import { fmtInt } from "@/lib/format";

/**
 * WHERE THE PRUNED ROWS WENT, on a page rather than in a URL only I knew.
 *
 * /api/archive has worked for days and NOTHING CALLED IT. Every page, every
 * component, every lib - the only references anywhere were the route file and
 * comments about it. So 623,768 rows sat in four GitHub Releases, retrievable
 * by anyone who knew the endpoint and invisible to everyone else, which is the
 * same condition the archive exists to prevent: a range leaves Postgres and
 * the platform can no longer show it.
 *
 * WHAT IT HAS TO SAY, and the order matters:
 *
 *   1 that the range EXISTS at all, with its row count - before any question
 *     of fetching it, "2026-03-18 to 2026-06-18, 114,591 rows" is the answer
 *     to "where did my observations go"
 *   2 whether THIS DEPLOYMENT can fetch rows. The assets live on a private
 *     repo, so the server needs a token; without one the honest message is
 *     "archived, and this deployment cannot reach it", never an empty table
 *     that reads as "gone"
 *   3 a window of actual rows, because a row count is a claim and a hundred
 *     rows on screen is evidence
 *   4 the whole file, for anything bigger than a window - a page shows a
 *     window, an export takes the file
 */
type Asset = {
  asset: string; rows: number; gzip_bytes: number;
  from: string; to: string; archived_at?: string;
};
type Manifest = {
  datasets: Record<string, { table: string; release_tag: string; rows_archived: number; assets: Asset[] }>;
  updated_at?: string;
  can_fetch_rows?: boolean;
  repo?: string;
};
type Window = { dataset: string; table: string; matched: number; returned: number;
                rows: Record<string, unknown>[]; assets_read: string[]; error?: string };

const card = "rounded border border-border bg-panel p-4";
const button = "rounded border border-border px-3 py-1 text-xs hover:bg-panel2 disabled:opacity-40";

export default function ArchiveBrowser() {
  const [idx, setIdx] = useState<Manifest | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [win, setWin] = useState<Window | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/archive")
      .then(r => r.json())
      .then(setIdx)
      .catch(e => setErr(String(e)));
  }, []);

  if (err) return <div className={`${card} text-sm text-bad`}>Archive index unavailable: {err}</div>;
  if (!idx) return null;

  const datasets = Object.entries(idx.datasets ?? {});
  if (!datasets.length) {
    return (
      <div className={`${card} text-sm text-muted`}>
        Nothing has been archived yet. Rows move here when
        <code className="mx-1 rounded bg-panel2 px-1">archive_observations.yml</code>
        prunes them out of Postgres.
      </div>
    );
  }

  const total = datasets.reduce((t, [, d]) => t + d.rows_archived, 0);

  async function show(dataset: string, a: Asset) {
    setBusy(true); setWin(null);
    try {
      const r = await fetch(
        `/api/archive?dataset=${encodeURIComponent(dataset)}&from=${a.from}&to=${a.to}&limit=100`);
      setWin(await r.json());
    } catch (e) {
      setWin({ dataset, table: "", matched: 0, returned: 0, rows: [], assets_read: [],
               error: String(e) });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      <div className={`${card} space-y-1`}>
        <div className="flex flex-wrap items-baseline gap-x-3 text-sm">
          <span className="font-semibold">Archive</span>
          <span className="font-mono">{fmtInt(total)} rows</span>
          <span className="text-muted">
            across {datasets.length} dataset{datasets.length === 1 ? "" : "s"}, kept in GitHub
            Releases on {idx.repo ?? "this repo"}
          </span>
        </div>
        <p className="text-xs text-muted">
          Rows pruned out of Postgres to stay inside the database tier. Nothing is deleted —
          every range below is still readable, and the whole file can be downloaded.
        </p>
        {/* The distinction that stops an unreachable archive reading as a
            deleted one. */}
        {idx.can_fetch_rows === false && (
          <p className="text-xs text-warn">
            This deployment has no repository token, so it can list the ranges but cannot fetch
            rows. The data is not lost — set GITHUB_TOKEN to read it here.
          </p>
        )}
      </div>

      {datasets.map(([name, d]) => (
        <div key={name} className={card}>
          <button
            type="button"
            className="flex w-full flex-wrap items-baseline justify-between gap-2 text-left"
            onClick={() => { setOpen(open === name ? null : name); setWin(null); }}
          >
            <span className="text-sm font-semibold">{name}</span>
            <span className="font-mono text-xs">{fmtInt(d.rows_archived)} rows</span>
            <span className="text-xs text-muted">
              {d.table} · {d.assets.length} file{d.assets.length === 1 ? "" : "s"}
            </span>
            <span className="text-[10px] text-muted">{open === name ? "hide" : "show"}</span>
          </button>

          {open === name && (
            <div className="mt-2 space-y-1 border-t border-border pt-2">
              {d.assets.map(a => (
                <div key={a.asset} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                  <span className="font-mono">{a.from} → {a.to}</span>
                  <span className="font-mono text-muted">{fmtInt(a.rows)} rows</span>
                  <span className="text-muted">{(a.gzip_bytes / 1e6).toFixed(1)} MB</span>
                  <button className={button} disabled={busy || idx.can_fetch_rows === false}
                          onClick={() => show(name, a)}>
                    Show rows
                  </button>
                  <a className="text-accent hover:underline"
                     href={`/api/archive?asset=${encodeURIComponent(a.asset)}&redirect=1`}>
                    download file →
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {/* A row count is a claim; rows on screen are evidence. */}
      {win && (
        <div className={`${card} overflow-x-auto`}>
          {win.error ? (
            <p className="text-sm text-bad">{win.error}</p>
          ) : (
            <>
              <p className="mb-2 text-xs text-muted">
                {fmtInt(win.matched)} rows matched in {win.table}, showing {win.returned}
                {win.assets_read?.length ? ` from ${win.assets_read.join(", ")}` : ""}.
              </p>
              {win.rows?.length > 0 && (
                <table className="w-full text-left text-[11px]">
                  <thead className="text-muted">
                    <tr>{Object.keys(win.rows[0]).slice(0, 8).map(k => (
                      <th key={k} className="p-1 font-normal">{k}</th>))}</tr>
                  </thead>
                  <tbody>
                    {win.rows.slice(0, 25).map((r, i) => (
                      <tr key={i} className="border-t border-border">
                        {Object.keys(win.rows[0]).slice(0, 8).map(k => (
                          <td key={k} className="p-1 font-mono">{String(r[k] ?? "").slice(0, 40)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
