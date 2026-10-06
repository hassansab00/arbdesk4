/**
 * THE PAPER DESKS, COUNTED, AND WHAT EACH ONE IS DOING (WXPredict build, wave
 * P, steps P.3 and P.4; docs/WXPREDICT_BUILD.md R41 and R42).
 *
 * Pure functions only: the route reads the rows with the service key and hands
 * them here, the page and PaperDeskControl render what comes back, and
 * web/tests/paper-desks.test.cjs runs every one of them without a database.
 *
 * WHY THE PAGE NEEDED THIS (5 Oct, measured). The page picked the first "live"
 * desk - automatic and not paused - and all 15 shadow desks were live, so the
 * counts that would have broken the tie decided nothing. The first desk in the
 * route's answer was "Shadow: s8_two_bucket_cover": 0 trades, 0 orders, 0
 * plans. Nine desks shared one created_at, so which empty desk opened was not
 * even fixed. And every switched-on shadow desk read "Running on its own"
 * while making ~250 decisions a day and buying nothing.
 */

export type AccountRow = {
  account_id: string;
  name: string;
  mode?: string | null;
  entries_paused?: boolean | null;
  status?: string | null;
  archived_at?: string | null;
  retired_at?: string | null;
  strategy_id?: string | null;
};
export type TradeRow = {
  trade_id?: string;
  account_id: string | null;
  opened_at: string;
  closed_at: string | null;
  net_pnl: number | string | null;
};
export type PositionRow = { account_id: string; shares: number | string };
export type StrategyRow = { strategy_id: string; name?: string | null; enabled: boolean };
export type DecisionRow = { strategy_id: string; action: string; reason_code: string | null };

/** What a desk's strategy is doing. `since` is when a desk was retired or
    archived; a strategy switched on or off carries no date (strategies has
    no column for when it was switched). */
export type StrategyState = "on" | "off" | "retired" | "archived" | "none";

export type Decisions24h = {
  n: number;
  buys: number;
  /** Most frequent first, then by code, so the order is fixed. */
  reasons: { reason_code: string; n: number }[];
};

export type DeskActivity = {
  account_id: string;
  name: string;
  status: string | null;
  trade_count: number;
  closed_count: number;
  /** Closed trades only, after fees: the same sum PaperTradeHistory's tile shows. */
  net_pnl: number;
  /** The newest trade's opened_at. */
  last_trade_at: string | null;
  open_positions: number;
  strategy_id: string | null;
  strategy_state: StrategyState;
  strategy_state_since: string | null;
  /** The strategy's decisions in the last 24 h; null for a desk with no strategy. */
  decisions_24h: Decisions24h | null;
};

const num = (v: unknown) => {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : 0;
};
const ms = (t: string | null | undefined) => {
  const v = t ? Date.parse(t) : NaN;
  return Number.isFinite(v) ? v : -Infinity;
};

export function strategyState(desk: AccountRow, strategies: Map<string, StrategyRow>):
    { state: StrategyState; since: string | null } {
  if (desk.status === "retired") return { state: "retired", since: desk.retired_at ?? null };
  if (desk.archived_at) return { state: "archived", since: desk.archived_at };
  const s = desk.strategy_id ? strategies.get(desk.strategy_id) : undefined;
  if (!s) return { state: "none", since: null };
  return { state: s.enabled ? "on" : "off", since: null };
}

export function decisionCounts(rows: DecisionRow[]): Map<string, Decisions24h> {
  const out = new Map<string, { n: number; buys: number; by: Map<string, number> }>();
  for (const d of rows) {
    const e = out.get(d.strategy_id) ?? { n: 0, buys: 0, by: new Map<string, number>() };
    e.n += 1;
    if (d.action === "BUY") e.buys += 1;
    const code = d.reason_code ?? "none";
    e.by.set(code, (e.by.get(code) ?? 0) + 1);
    out.set(d.strategy_id, e);
  }
  return new Map([...out].map(([k, e]) => [k, {
    n: e.n, buys: e.buys,
    reasons: [...e.by].map(([reason_code, n]) => ({ reason_code, n }))
      .sort((a, b) => b.n - a.n || a.reason_code.localeCompare(b.reason_code)),
  }]));
}

/**
 * EVERY TRADE ONCE: the repository archive merged with Postgres by trade_id,
 * the archive winning - the same rule as PaperTradeHistory's mergeTrades.
 * paper_trade_log.yml prunes a closed trade from Postgres 30 days after it is
 * committed to web/public/paper-trades (Codex on #318), so counts read from
 * the table alone would lose history from mid-October.
 */
export function mergeTradeRows(archived: TradeRow[], live: TradeRow[]): TradeRow[] {
  const byId = new Map<string, TradeRow>();
  const loose: TradeRow[] = [];
  for (const t of live) if (t.trade_id) byId.set(t.trade_id, t); else loose.push(t);
  for (const t of archived) if (t.trade_id) byId.set(t.trade_id, t); else loose.push(t);
  return [...byId.values(), ...loose];
}

/** One row per desk, every desk the accounts list holds, archived included. */
export function deskActivity(accounts: AccountRow[], trades: TradeRow[], positions: PositionRow[],
                             strategies: StrategyRow[], decisions: DecisionRow[]): DeskActivity[] {
  const strat = new Map(strategies.map(s => [s.strategy_id, s]));
  const dec = decisionCounts(decisions);
  const t = new Map<string, { n: number; closed: number; net: number; last: string | null }>();
  for (const r of trades) {
    if (!r.account_id) continue;          // pre-multi-desk rows belong to no desk
    const e = t.get(r.account_id) ?? { n: 0, closed: 0, net: 0, last: null };
    e.n += 1;
    if (r.closed_at) { e.closed += 1; e.net += num(r.net_pnl); }
    if (ms(r.opened_at) > ms(e.last)) e.last = r.opened_at;
    t.set(r.account_id, e);
  }
  const open = new Map<string, number>();
  for (const p of positions) if (num(p.shares) > 0) open.set(p.account_id, (open.get(p.account_id) ?? 0) + 1);
  return accounts.map(a => {
    const e = t.get(a.account_id);
    const st = strategyState(a, strat);
    return {
      account_id: a.account_id, name: a.name, status: a.status ?? null,
      trade_count: e?.n ?? 0, closed_count: e?.closed ?? 0,
      net_pnl: Math.round((e?.net ?? 0) * 1e5) / 1e5,
      last_trade_at: e?.last ?? null,
      open_positions: open.get(a.account_id) ?? 0,
      strategy_id: a.strategy_id ?? null,
      strategy_state: st.state, strategy_state_since: st.since,
      decisions_24h: a.strategy_id ? (dec.get(a.strategy_id) ?? { n: 0, buys: 0, reasons: [] }) : null,
    };
  });
}

/**
 * WHICH DESK THE PAGE OPENS ON (P.3): the desk with the most recent trade;
 * ties by trade count, then by name, so the choice is fixed. Without counts
 * (the edge-gateway path sends plain paper_accounts rows) a desk that can act
 * comes first, then the name.
 */
export function pickDesk<T extends { account_id: string; name: string; mode?: string | null;
    entries_paused?: boolean | null; trade_count?: number; last_trade_at?: string | null }>(
    accounts: T[]): string | null {
  if (!accounts.length) return null;
  const counted = accounts.some(a => a.trade_count !== undefined);
  const live = (a: T) => (a.mode === "automatic" || a.mode === "assisted") && !a.entries_paused;
  const sorted = [...accounts].sort((a, b) => counted
    ? (ms(b.last_trade_at) - ms(a.last_trade_at) || 0)
      || (b.trade_count ?? 0) - (a.trade_count ?? 0)
      || a.name.localeCompare(b.name)
    : Number(live(b)) - Number(live(a)) || a.name.localeCompare(b.name));
  return sorted[0].account_id;
}

/**
 * EACH REASON CODE IN PLAIN WORDS (P.4), taken from the code that emits it,
 * not written from memory. A code not listed here is shown as the code.
 */
export const REASON_WORDS: Record<string, string> = {
  // scripts/strategies/s10_max_temp_winner.py:188-201 says NONE when the
  // target's YES does not grow the ledger at its ask, no book that cannot lose
  // holds the top bucket (s10_lock), or the target is not tradeable;
  // engine_views.py:97 passes it as "s10 NONE: ..." and engine_shadow.py
  // reason_code() codes it own_rule_none.
  own_rule_none: "S10's own rule found no target worth buying at its ask",
  // scripts/engine_shadow.py:283: the city-day had no ladder to price.
  no_ladder: "no ladder was priced for the city-day",
  // scripts/decision_engine.py:218 and :257: no book raises expected growth
  // by more than the no-trade band h.
  no_trade_band: "no purchase cleared the no-trade band",
  // scripts/decision_engine.py:183: nothing on the ladder can be bought
  // within the strategy's constraints.
  nothing_tradeable: "nothing on the ladder could be bought within the strategy's limits",
  // scripts/decision_engine.py:174-183: every buyable bucket bets against the
  // market, which is blocked until edge_engine.against_market_gate proves it.
  against_market: "every buyable bucket bet against the market, which stays blocked until proven",
  // scripts/decision_log.py:57: no signal fired for the strategy and city-day.
  no_signal: "no signal fired",
};

export function reasonWords(code: string): string {
  return REASON_WORDS[code] ?? `reason code ${code}`;
}

/** "5 Oct 2026", from an ISO time. */
export function dayOf(iso: string | null | undefined): string {
  const t = iso ? new Date(iso) : null;
  if (!t || !Number.isFinite(t.getTime())) return "an unknown date";
  return t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

// --------------------------------------------------------------------------
// THE DESK'S ONE-WORD STATE (moved here from PaperDeskControl so it can be
// tested without React).
//
//   ACTIVE    automation on, correctly configured, will trade
//   PAUSED    deliberately stopped; nothing new will open
//   MANUAL    no automation at all - it only acts on tickets you write
//   STALLED   switched ON and cannot possibly trade
//   RETIRED   its strategy is retired, or switched off (P.4): never "Running"
// --------------------------------------------------------------------------
type Policy = {
  strategies?: string[]; cities?: string[];
  max_plan_usd?: number; max_exposure_usd?: number; min_edge?: number;
};
export type Desk = {
  account_id: string; name: string; mode: string; entries_paused: boolean;
  cash: number | string; reserved_cash: number | string; policy: Policy;
  /** From the route's accounts answer (service key). Absent on the edge-gateway path. */
  activity?: DeskActivity | null;
};

export type DeskState = {
  key: "ACTIVE" | "PAUSED" | "MANUAL" | "STALLED" | "RETIRED";
  dot: string; text: string; line: string;
};

const DAY_MS = 24 * 3600 * 1000;

/** The one sentence that explains what this desk is doing and why. */
export function deskState(desk: Desk, available: number, now: number = Date.now()): DeskState {
  const act = desk.activity;
  // RETIRED OR SWITCHED OFF (P.4). A desk whose strategy will never act again,
  // or is switched off, is never shown as running, whatever its mode says.
  if (act?.strategy_state === "retired") return {
    key: "RETIRED", dot: "bg-muted", text: "text-muted",
    line: `Strategy retired on ${dayOf(act.strategy_state_since)}: this desk never trades again.`,
  };
  if (act?.strategy_state === "off") return {
    key: "RETIRED", dot: "bg-muted", text: "text-muted",
    line: "Strategy switched off: this desk does not trade until the strategy is switched back on.",
  };
  const automated = desk.mode === "automatic" || desk.mode === "assisted";
  if (!automated) return {
    key: "MANUAL", dot: "bg-muted", text: "text-muted",
    line: "No automation. It acts only on tickets you write by hand — switch it to automatic in Settings to have strategies trade it.",
  };
  if (desk.entries_paused) return {
    key: "PAUSED", dot: "bg-warn", text: "text-warn",
    line: "Stopped. Nothing new will open. Exits and settlement still run, and queued orders can still be cancelled.",
  };
  // Switched on but unable to act. Each of these leaves the desk looking
  // healthy and idle for ever, so each is named rather than left to deduce.
  if (!desk.policy?.strategies?.length) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No strategies chosen. It can only act on what a strategy proposes, so it will never trade until you pick one in Settings.",
  };
  if (!desk.policy?.cities?.length) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No cities chosen. Every proposal will be rejected as out of policy. Pick cities, or ALL, in Settings.",
  };
  if (available < 1) return {
    key: "STALLED", dot: "bg-bad", text: "text-bad",
    line: "No available cash. Everything is either spent or reserved against queued orders, so no new plan can be funded.",
  };
  // RUNNING, WITH NOTHING TO BUY (P.4): switched on, no trade in 24 h, and its
  // decisions in that time bought nothing. Says how many it made and why.
  const d = act?.decisions_24h;
  if (act && d && d.buys === 0 && !(ms(act.last_trade_at) > now - DAY_MS)) {
    const top = d.reasons[0];
    return {
      key: "ACTIVE", dot: "bg-good", text: "text-good",
      line: `Running. ${d.n.toLocaleString("en-US")} decision${d.n === 1 ? "" : "s"} in the last 24 h, 0 buys.`
        + (top ? ` Most often: ${reasonWords(top.reason_code)} (${top.n.toLocaleString("en-US")}).` : ""),
    };
  }
  return {
    key: "ACTIVE", dot: "bg-good", text: "text-good",
    line: desk.mode === "assisted"
      ? "Running. Strategies propose; each plan waits for your approval in Settings before it is queued."
      : "Running on its own. Eligible proposals are queued and filled within the limits below.",
  };
}

/** A desk's strategy state as one short phrase, for the All desks summary. */
export function strategyPhrase(a: DeskActivity): string {
  switch (a.strategy_state) {
    case "retired": return `retired ${dayOf(a.strategy_state_since)}`;
    case "archived": return `archived ${dayOf(a.strategy_state_since)}`;
    case "on": return "switched on";
    case "off": return "switched off";
    default: return a.status === "suspended" ? "no strategy · suspended" : "no strategy";
  }
}
