"use client";

import { fmtUsd, fmtAge, pnlColor } from "@/lib/format";
import { strategyPhrase, type DeskActivity } from "@/lib/paperDesks";

/**
 * EVERY DESK ON ONE LINE (WXPredict build P.3, R41).
 *
 * The 198 trades sat on five desks - s1 (43), s3 (41), s4 (43), s12 (1) and
 * the archived "Wide edge, all US" (70) - and no view showed them together.
 * Selecting "All desks" shows this table above the unfiltered trade history:
 * desk, its strategy's state, trades, net P&L and the last trade. Archived
 * and retired desks are included and say so.
 *
 * The counts come from the route (service key); on a deployment that reads
 * desks through the edge gateway they are absent, and this says why rather
 * than showing zeros.
 */
const card = "rounded border border-border bg-panel p-4";

export default function PaperDeskSummary({ desks, error, archiveError = null }: {
  desks: DeskActivity[] | null;
  error: string | null;
  /** The repository archive could not be read: the counts cover Postgres only,
      which loses trades pruned 30 days after export. Said, not hidden. */
  archiveError?: string | null;
}) {
  if (!desks) {
    return <div className={`${card} text-sm text-muted`}>
      Per-desk counts are not available{error ? `: ${error}` : " on this deployment (the desk list came through the edge gateway, which sends no counts)"}. The trades below are every desk&apos;s.
    </div>;
  }
  const rows = [...desks].sort((a, b) =>
    (Date.parse(b.last_trade_at ?? "") || 0) - (Date.parse(a.last_trade_at ?? "") || 0)
    || b.trade_count - a.trade_count || a.name.localeCompare(b.name));
  const trades = rows.reduce((t, d) => t + d.trade_count, 0);
  const net = rows.reduce((t, d) => t + d.net_pnl, 0);
  return <div className={`${card} overflow-x-auto`}>
    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="font-semibold">All desks</h2>
      <span className="text-xs text-muted">{rows.length} desks · {trades.toLocaleString("en-US")} trades · net {fmtUsd(net, { signed: true })} on closed trades</span>
    </div>
    {archiveError && <p className="mb-2 text-xs text-warn">These counts cover Postgres only - the trade archive could not be read ({archiveError}), so trades pruned after export are missing.</p>}
    <table className="w-full text-left text-sm">
      <thead className="text-muted"><tr>
        {["Desk", "Strategy", "Trades", "Net P&L", "Last trade"].map(h => <th key={h} className="p-2 font-normal">{h}</th>)}
      </tr></thead>
      <tbody>{rows.map(d => {
        const gone = d.strategy_state === "retired" || d.strategy_state === "archived";
        return <tr key={d.account_id} className={`border-t border-border ${gone ? "text-muted" : ""}`} data-desk={d.account_id}>
          <td className="p-2">{d.name}{gone && <span className="ml-2 rounded border border-border px-1 text-[10px] uppercase">{d.strategy_state}</span>}</td>
          <td className="p-2">{strategyPhrase(d)}</td>
          <td className="p-2 font-mono">{d.trade_count.toLocaleString("en-US")}{d.open_positions ? <span className="text-muted"> · {d.open_positions} open</span> : null}</td>
          <td className={`p-2 font-mono ${d.closed_count ? pnlColor(d.net_pnl) : "text-muted"}`}>{d.closed_count ? fmtUsd(d.net_pnl, { signed: true }) : "—"}</td>
          <td className="p-2 text-muted">{d.last_trade_at ? fmtAge(d.last_trade_at) : "never"}</td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}
