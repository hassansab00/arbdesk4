"use client";

import { useEffect, useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { fmtUsd, fmtPrice, fmtPct, pnlColor } from "@/lib/format";

/**
 * EVERY TRADE THE DESK HAS EVER MADE, from two places that must not double
 * count.
 *
 *   web/public/paper-trades/*.jsonl   the repository archive. Every CLOSED
 *                                     trade, permanently, written by
 *                                     scripts/export_paper_trades.py and
 *                                     committed daily. Free, versioned,
 *                                     outlives the database.
 *   paper_trades in Postgres          open positions, plus the tail of recent
 *                                     closes that the daily export has not
 *                                     picked up yet.
 *
 * The overlap is real - a trade that closed this morning is in Postgres and
 * not yet in the archive - so the merge is by trade_id, which is a uuid, and
 * the archive wins. A closed trade never changes, so preferring the archive
 * costs nothing and means the page shows the same numbers a reviewer reading
 * the repo sees.
 *
 * The archive is fetched, not imported, so a page already open picks up a new
 * deploy's trades on refresh rather than at build time.
 */

export type Trade = {
  trade_id: string;
  strategy_id: string | null;
  city_key: string | null;
  band_id: string | null;
  band_label: string | null;
  resolution_date: string | null;
  side: string;
  action: string | null;
  opened_at: string;
  shares: number | string;
  avg_fill_price: number | string;
  fee_paid: number | string | null;
  closed_at: string | null;
  close_price: number | string | null;
  close_reason: string | null;
  gross_pnl: number | string | null;
  net_pnl: number | string | null;
  partial_fill?: boolean;
};

const num = (v: unknown): number => {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : 0;
};

/** Archive first, then any live row the archive has not caught up with. */
export function mergeTrades(archived: Trade[], live: Trade[]): Trade[] {
  const byId = new Map<string, Trade>();
  for (const t of live) byId.set(t.trade_id, t);
  for (const t of archived) byId.set(t.trade_id, t);
  return [...byId.values()].sort((a, b) =>
    (b.closed_at ?? b.opened_at).localeCompare(a.closed_at ?? a.opened_at));
}

export function summarise(trades: Trade[]) {
  const closed = trades.filter(t => t.closed_at);
  const open = trades.filter(t => !t.closed_at);
  const net = closed.reduce((s, t) => s + num(t.net_pnl), 0);
  const gross = closed.reduce((s, t) => s + num(t.gross_pnl), 0);
  const fees = trades.reduce((s, t) => s + num(t.fee_paid), 0);
  const wins = closed.filter(t => num(t.net_pnl) > 0).length;
  const losses = closed.filter(t => num(t.net_pnl) < 0).length;
  return {
    closed: closed.length,
    open: open.length,
    net, gross, fees, wins, losses,
    // Undefined rather than 0 with no closed trades: a win rate of "0%" reads
    // as "it loses every time", which is a different statement from "it has
    // not finished a trade yet".
    winRate: closed.length ? wins / closed.length : undefined,
    atRisk: open.reduce((s, t) => s + num(t.shares) * num(t.avg_fill_price), 0),
  };
}

async function fetchArchive(): Promise<Trade[]> {
  const index = await fetch("/paper-trades/index.json", { cache: "no-store" });
  if (!index.ok) return [];               // nothing exported yet is not an error
  const { months } = await index.json() as { months: string[] };
  const files = await Promise.all(months.map(m =>
    fetch(`/paper-trades/${m}.jsonl`, { cache: "no-store" })
      .then(r => r.ok ? r.text() : "")));
  return files.flatMap(text =>
    text.split("\n").filter(Boolean).map(l => JSON.parse(l) as Trade));
}

const card = "rounded border border-border bg-panel p-4";

function Tile({ label, value, tone, hint }: {
  label: string; value: string; tone?: string; hint?: string;
}) {
  return <div className="rounded border border-border p-3" title={hint}>
    <div className="text-xs text-muted">{label}</div>
    <div className={`font-mono text-xl ${tone ?? ""}`}>{value}</div>
  </div>;
}

export default function PaperTradeHistory({ account, bandName }: {
  account: string;
  bandName: (id: string) => string;
}) {
  const [archive, setArchive] = useState<Trade[] | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [city, setCity] = useState("");
  const [strategy, setStrategy] = useState("");
  const [outcome, setOutcome] = useState("");

  useEffect(() => {
    let live = true;
    fetchArchive()
      .then(rows => { if (live) setArchive(rows); })
      .catch(e => { if (live) { setArchive([]); setArchiveError(String(e)); } });
    return () => { live = false; };
  }, []);

  const db = useQuery<Trade[]>(async () => {
    let q = supabase.from("paper_trades")
      .select("trade_id,strategy_id,city_key,band_id,resolution_date,side,action," +
              "opened_at,shares,avg_fill_price,fee_paid,closed_at,close_price," +
              "close_reason,gross_pnl,net_pnl,partial_fill")
      .order("opened_at", { ascending: false })
      .limit(500);
    if (account) q = q.eq("account_id", account);
    const r = await q;
    return { data: r.data as Trade[] | null, error: r.error };
  }, [account], 20000, 500);

  const all = useMemo(
    () => mergeTrades(archive ?? [], db.data ?? []),
    [archive, db.data]);

  const cities = useMemo(
    () => [...new Set(all.map(t => t.city_key).filter(Boolean))].sort() as string[], [all]);
  const strategies = useMemo(
    () => [...new Set(all.map(t => t.strategy_id).filter(Boolean))].sort() as string[], [all]);

  const shown = all.filter(t =>
    (!city || t.city_key === city) &&
    (!strategy || t.strategy_id === strategy) &&
    (!outcome
      || (outcome === "open" && !t.closed_at)
      || (outcome === "won" && num(t.net_pnl) > 0)
      || (outcome === "lost" && num(t.net_pnl) < 0)));

  const s = summarise(shown);

  return <div className="space-y-3">
    <div className={`${card} space-y-3`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Outcomes</h2>
        <div className="text-xs text-muted">
          {archive === null ? "loading archive…"
            : `${archive.length} archived in the repository · ${db.data?.length ?? 0} live in Postgres`}
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Net P&L" value={fmtUsd(s.net, { signed: true })} tone={pnlColor(s.net)}
              hint="Closed trades only, after both the entry fee and the exit fee." />
        <Tile label="Gross P&L" value={fmtUsd(s.gross, { signed: true })} tone={pnlColor(s.gross)}
              hint="Before any fee. The gap between this and Net is what the venue took." />
        <Tile label="Fees paid" value={fmtUsd(s.fees)} tone="text-muted"
              hint="Entry fees on every trade, open or closed." />
        <Tile label="Win rate"
              value={s.winRate === undefined ? "—" : fmtPct(s.winRate, 0)}
              tone={s.winRate === undefined ? "text-muted" : pnlColor(s.winRate - 0.5)}
              hint="Of closed trades. Dashed until one has actually finished." />
        <Tile label="Closed" value={`${s.wins}W / ${s.losses}L`} tone="text-muted" />
        <Tile label="Open" value={`${s.open}`} tone={s.open ? "text-accent" : "text-muted"}
              hint={`${fmtUsd(s.atRisk)} of entry cost still at risk.`} />
      </div>

      {archiveError && <p className="text-xs text-warn">
        The repository archive could not be read ({archiveError}); showing only what Postgres holds.
      </p>}
      {db.error && <p className="text-sm text-bad" role="alert">{db.error}</p>}
    </div>

    <div className={`${card} space-y-3`}>
      <div className="flex flex-wrap gap-2">
        <select aria-label="City" className="input max-w-[12rem]" value={city}
                onChange={e => setCity(e.target.value)}>
          <option value="">All cities</option>
          {cities.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select aria-label="Strategy" className="input max-w-[16rem]" value={strategy}
                onChange={e => setStrategy(e.target.value)}>
          <option value="">All strategies</option>
          {strategies.map(x => <option key={x} value={x}>{x}</option>)}
        </select>
        <select aria-label="Outcome" className="input max-w-[10rem]" value={outcome}
                onChange={e => setOutcome(e.target.value)}>
          <option value="">All outcomes</option>
          <option value="open">Open</option>
          <option value="won">Won</option>
          <option value="lost">Lost</option>
        </select>
        {shown.length !== all.length &&
          <span className="self-center text-xs text-muted">{shown.length} of {all.length}</span>}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-muted"><tr>
            {["Opened", "City", "Contract", "Resolves", "Strategy", "Side",
              "Shares", "Entry", "Exit", "Net", "Outcome"]
              .map(h => <th key={h} className="p-2 font-normal">{h}</th>)}
          </tr></thead>
          <tbody>
            {shown.map(t => <tr key={t.trade_id} className="border-t border-border">
              <td className="p-2 whitespace-nowrap">{t.opened_at.slice(0, 16).replace("T", " ")}</td>
              <td className="p-2">{t.city_key ?? "—"}</td>
              <td className="p-2">{t.band_label ?? (t.band_id ? bandName(t.band_id) : "—")}</td>
              <td className="p-2 whitespace-nowrap">{t.resolution_date ?? "—"}</td>
              <td className="p-2 text-xs">{t.strategy_id ?? "—"}</td>
              <td className="p-2">{t.side}</td>
              <td className="p-2 font-mono">
                {num(t.shares).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                {t.partial_fill && <span className="ml-1 text-xs text-warn" title="Filled less than requested">partial</span>}
              </td>
              <td className="p-2 font-mono">{fmtPrice(num(t.avg_fill_price))}</td>
              <td className="p-2 font-mono">{t.closed_at ? fmtPrice(num(t.close_price)) : "—"}</td>
              <td className={`p-2 font-mono ${t.closed_at ? pnlColor(num(t.net_pnl)) : "text-muted"}`}>
                {t.closed_at ? fmtUsd(num(t.net_pnl), { signed: true }) : "—"}
              </td>
              <td className="p-2 text-xs">
                {t.closed_at
                  ? (t.close_reason ?? "closed").replaceAll("_", " ")
                  : <span className="text-accent">open</span>}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>

      {!shown.length && <p className="py-4 text-sm text-muted">
        {all.length
          ? "No trade matches these filters."
          : "No paper trade yet. A trade appears here the moment an order fills — "
            + "it does not wait for the market to resolve."}
      </p>}
      {db.truncated && <p className="text-xs text-warn">
        Showing the latest 500 live rows. Older trades are in the repository archive above.
      </p>}
    </div>
  </div>;
}
