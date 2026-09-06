"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { Freshness } from "@/components/Provenance";
import { SQL_OWNER } from "@/lib/sqlOwner";
import { fmtInt, fmtUsd, pnlColor } from "@/lib/format";

/**
 * CAMPAIGNS — a strategy, pointed at part of the map, for a window.
 *
 * This page used to list rows: a name, a strategy id, a target kind, five
 * status buttons and a P&L that read $0 until something settled — which, for
 * a desk whose strategies all ship disabled, is never. Nothing on it said
 * what a campaign covered, whether the strategy behind it was even switched
 * on, whether anything it targets is trading today, or what it would do in
 * the next hour. There was nothing to look at and no reason to come back.
 *
 * Every number below is now read live from v_campaign_state
 * (sql/ad4_41_campaigns.sql) rather than echoed back from what was typed when
 * the campaign was created: the cities the target actually resolves to, what
 * is on the board inside that scope, what this campaign's own strategy would
 * take right now, and the FIRST reason it cannot — one reason, in the order
 * that actually stops it, because a campaign whose strategy is switched off
 * does not also need to be told its window has not opened.
 */

interface CampaignRow {
  deployment_id: string;
  name: string | null;
  strategy_id: string;
  strategy_name: string | null;
  strategy_enabled: boolean;
  strategy_verdict: string | null;
  status: string;
  target_kind: string;
  condition: string | null;
  starts_at: string | null;
  ends_at: string | null;
  created_at: string;
  cities_targeted: number;
  targets_unresolved: number;
  unresolved_values: string[] | null;
  cities: string[] | null;
  cities_live: number;
  bands_in_scope: number;
  bands_tradeable: number;
  best_edge_pp: number | null;
  fillable_usd: number | null;
  book_age_min: number | null;
  would_fire_now: number;
  firing_best_edge_pp: number | null;
  firing_top_city: string | null;
  firing_top_band: string | null;
  soonest_peak_min: number | null;
  trades_settled: number;
  trades_won: number;
  net_pnl: number;
  days_left: number | null;
  /**
   * The FIRST reason it cannot trade, chosen server-side in the order that
   * actually stops it - a campaign whose strategy is switched off does not
   * also need to be told its window has not opened. Null when nothing blocks.
   */
  blocked_because: string | null;
  doing_now: string | null;
}

interface StrategyOption {
  strategy_id: string;
  name: string | null;
  enabled: boolean;
}

const STATUSES = ["draft", "armed", "running", "paused", "closed"] as const;
const REGIONS = ["Americas", "Europe/Africa", "West Asia", "East Asia", "Oceania"];

export default function CampaignsPage() {
  const [form, setForm] = useState({
    name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "",
  });
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const campaignsQ = useQuery<CampaignRow[]>(
    () => supabase.from("v_campaign_state").select("*").order("created_at", { ascending: false }),
    [],
    60000
  );
  const strategiesQ = useQuery<StrategyOption[]>(
    () => supabase.from("strategies").select("strategy_id,name,enabled").order("strategy_id"),
    []
  );
  const citiesQ = useQuery<Array<{ city_key: string; display_name: string | null }>>(
    () => supabase.from("cities").select("city_key,display_name").order("city_key"),
    []
  );

  const campaigns = campaignsQ.data ?? [];
  const strategies = strategiesQ.data ?? [];
  const cities = citiesQ.data ?? [];

  // The page's own summary, so the state of the whole book is one line rather
  // than something you assemble by reading every card.
  const roll = useMemo(() => {
    const live = campaigns.filter((c) => c.would_fire_now > 0);
    return {
      total: campaigns.length,
      acting: live.length,
      blocked: campaigns.filter((c) => c.would_fire_now === 0).length,
      offStrategy: campaigns.filter((c) => !c.strategy_enabled).length,
      pnl: campaigns.reduce((a, c) => a + (c.net_pnl ?? 0), 0),
      settled: campaigns.reduce((a, c) => a + c.trades_settled, 0),
    };
  }, [campaigns]);

  function load() {
    campaignsQ.refresh();
  }

  async function create() {
    if (!form.name.trim()) return setActionError("Give it a name — you will have several.");
    if (!form.strategy_id) return setActionError("Pick the strategy this campaign runs.");
    if (!form.target.trim()) return setActionError("Say what it points at — a city, a list, or a region.");
    setBusy(true);
    const { error } = await supabase.rpc("upsert_deployment", {
      p_deployment: {
        name: form.name,
        strategy_id: form.strategy_id,
        target_kind: form.target_kind,
        target: { values: form.target.split(",").map((x) => x.trim()).filter(Boolean) },
        condition: form.condition || null,
        ends_at: form.ends_at || null,
        status: "draft",
      },
    });
    setBusy(false);
    if (error) {
      // NAME THE FILE. "Could not find the function upsert_deployment" told
      // you nothing about which of forty SQL files publishes it, and that is
      // the single most common reason creating a campaign fails.
      const missing = /Could not find the function|schema cache|does not exist/i.test(error.message);
      setActionError(
        missing
          ? `${error.message} — this page writes through the upsert_deployment RPC, which is created by sql/${SQL_OWNER["deployments"] ?? "ad4_rpc.sql"} and sql/ad4_rpc.sql. Run those in the Supabase SQL editor.`
          : `${error.message}${error.hint ? ` — ${error.hint}` : ""}`
      );
      return;
    }
    setActionError(null);
    setForm({ name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "" });
    load();
  }

  async function setStatus(id: string, status: string) {
    const { error } = await supabase.rpc("set_deployment_status", {
      p_deployment_id: id, p_status: status,
    });
    setActionError(error ? error.message : null);
    if (!error) load();
  }

  const targetHint =
    form.target_kind === "cluster"
      ? `region names, comma separated — ${REGIONS.join(", ")}`
      : form.target_kind === "list"
        ? "city keys, comma separated"
        : "one city key";

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-lg font-semibold">Campaigns</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted">
          A campaign is one strategy pointed at part of the map for a window, with its own P&amp;L.
          It is how you run <em>the tail-fade, on the Gulf cities, for August</em> without turning
          that strategy on everywhere. Everything below is read live: the cities a target actually
          resolves to, what is on the board inside that scope right now, and what this campaign&apos;s
          own strategy would take in the next hour.
        </p>
      </div>

      {actionError && (
        <div className="rounded border border-bad/60 bg-bad/10 p-2.5 font-mono text-xs leading-relaxed text-bad">
          {actionError}
        </div>
      )}
      <InlineError message={strategiesQ.error} />

      {/* ================================================== THE BOOK, ONE LINE */}
      {campaigns.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded border border-border bg-panel/60 px-3 py-2 text-xs">
          <span>
            <span className="text-muted">campaigns </span>
            <span className="tabular-nums">{roll.total}</span>
          </span>
          <span>
            <span className="text-muted">acting right now </span>
            <span className={`tabular-nums ${roll.acting ? "text-good" : "text-muted"}`}>{roll.acting}</span>
          </span>
          <span>
            <span className="text-muted">blocked </span>
            <span className={`tabular-nums ${roll.blocked ? "text-warn" : "text-muted"}`}>{roll.blocked}</span>
          </span>
          {roll.offStrategy > 0 && (
            <span className="text-warn">
              {roll.offStrategy} pointing at a strategy that is switched off
            </span>
          )}
          <span className="ml-auto">
            <span className="text-muted">settled </span>
            <span className="tabular-nums">{fmtInt(roll.settled)}</span>
            <span className="text-muted"> trades · </span>
            <span className={`tabular-nums ${pnlColor(roll.pnl)}`}>{fmtUsd(roll.pnl, { signed: true })}</span>
          </span>
        </div>
      )}

      {/* ========================================================= NEW CAMPAIGN */}
      <section className="space-y-2">
        <h2 className="text-sm font-semibold">Point a strategy at something</h2>
        <div className="grid gap-2 rounded border border-border bg-panel p-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted">
            name
            <input
              placeholder="Gulf tail-fade, August"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="input"
            />
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted">
            strategy
            <select
              value={form.strategy_id}
              onChange={(e) => setForm({ ...form, strategy_id: e.target.value })}
              className="input"
            >
              <option value="">
                {strategiesQ.loading ? "loading…" : strategies.length ? "select strategy" : "no strategies seeded"}
              </option>
              {strategies.map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name ?? s.strategy_id}
                  {s.enabled ? "" : " — currently off"}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted">
            scope
            <select
              value={form.target_kind}
              onChange={(e) => setForm({ ...form, target_kind: e.target.value, target: "" })}
              className="input"
            >
              <option value="city">one city</option>
              <option value="list">a list of cities</option>
              <option value="cluster">a whole region</option>
            </select>
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted sm:col-span-2">
            {targetHint}
            <input
              list="campaign-targets"
              placeholder={form.target_kind === "cluster" ? REGIONS[0] : cities[0]?.city_key ?? "city_key"}
              value={form.target}
              onChange={(e) => setForm({ ...form, target: e.target.value })}
              className="input"
            />
            <datalist id="campaign-targets">
              {(form.target_kind === "cluster" ? REGIONS : cities.map((c) => c.city_key)).map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted">
            ends (optional)
            <input
              type="datetime-local"
              value={form.ends_at}
              onChange={(e) => setForm({ ...form, ends_at: e.target.value })}
              className="input"
            />
          </label>

          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted sm:col-span-3">
            note to yourself (optional) — this is a label, not a rule the engine reads
            <input
              placeholder="while the heat dome holds"
              value={form.condition}
              onChange={(e) => setForm({ ...form, condition: e.target.value })}
              className="input"
            />
          </label>

          <button
            onClick={create}
            disabled={busy}
            className="rounded bg-accent py-1.5 text-white hover:opacity-90 disabled:opacity-50 sm:col-span-3"
          >
            {busy ? "Creating…" : "Create as a draft"}
          </button>
          <p className="text-[10px] leading-relaxed text-muted sm:col-span-3">
            It starts as a draft and trades nothing. Set it to <strong>running</strong> on its card
            once you have looked at what it covers — the card shows that before you commit to it.
          </p>
        </div>
      </section>

      {/* ============================================================ THE CARDS */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold">Running campaigns</h2>
          <Freshness relation="v_campaign_state" />
        </div>
        <DataState
          relation="v_campaign_state"
          loading={campaignsQ.loading}
          error={campaignsQ.error}
          isEmpty={campaigns.length === 0}
          emptyTitle="No campaigns yet"
          emptyBody="Create one above. A campaign is how you run a strategy on part of the map without turning it on everywhere — and its card shows what it would do before you set it running."
          onRetry={campaignsQ.refresh}
        >
          <div className="space-y-3">
            {campaigns.map((c) => (
              <CampaignCard key={c.deployment_id} c={c} onStatus={setStatus} />
            ))}
          </div>
        </DataState>
      </section>
    </div>
  );
}

function CampaignCard({
  c,
  onStatus,
}: {
  c: CampaignRow;
  onStatus: (id: string, status: string) => void;
}) {
  const acting = c.would_fire_now > 0;
  const won = c.trades_settled > 0 ? (c.trades_won / c.trades_settled) * 100 : null;

  return (
    <article className={`rounded border bg-panel p-3 ${acting ? "border-good/50" : "border-border"}`}>
      {/* ---- identity and P&L ------------------------------------------ */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <span className="text-sm font-semibold">{c.name || c.deployment_id.slice(0, 8)}</span>
          <span className="ml-2 text-xs text-muted">
            {c.strategy_name ?? c.strategy_id}
            {!c.strategy_enabled && <span className="text-warn"> · switched off</span>}
          </span>
          {c.condition && <span className="ml-2 text-[11px] italic text-muted">“{c.condition}”</span>}
        </div>
        <div className="text-right">
          <div className={`text-sm tabular-nums ${pnlColor(c.net_pnl)}`}>
            {fmtUsd(c.net_pnl ?? 0, { signed: true })}
          </div>
          <div className="text-[10px] text-muted">
            {c.trades_settled === 0
              ? "nothing settled yet"
              : `${fmtInt(c.trades_settled)} settled${won === null ? "" : `, ${won.toFixed(0)}% won`}`}
          </div>
        </div>
      </div>

      {/* ---- what it covers, live -------------------------------------- */}
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Cell
          label="Cities"
          value={fmtInt(c.cities_targeted)}
          sub={c.cities_live > 0 ? `${c.cities_live} trading today` : "none trading today"}
          tone={c.cities_targeted === 0 ? "bad" : c.cities_live === 0 ? "warn" : undefined}
        />
        <Cell
          label="Buckets in scope"
          value={fmtInt(c.bands_in_scope)}
          sub={`${fmtInt(c.bands_tradeable)} tradeable after fees`}
          tone={c.bands_in_scope > 0 && c.bands_tradeable === 0 ? "warn" : undefined}
        />
        <Cell
          label="Best edge in scope"
          value={c.best_edge_pp === null ? "—" : `${c.best_edge_pp.toFixed(1)} pp`}
          sub={c.fillable_usd ? `${fmtUsd(c.fillable_usd)} fillable at 5¢` : "nothing fillable"}
          tone={(c.best_edge_pp ?? 0) >= 5 ? "good" : undefined}
        />
        <Cell
          label="Would take now"
          value={fmtInt(c.would_fire_now)}
          sub={
            c.soonest_peak_min !== null && c.would_fire_now > 0
              ? `soonest peak in ${Math.round(c.soonest_peak_min)} min`
              : c.days_left !== null
                ? `${c.days_left} days left`
                : "no end date"
          }
          tone={acting ? "good" : undefined}
        />
      </div>

      {/* ---- the one sentence that matters ----------------------------- */}
      <div className="mt-2 rounded border border-border bg-panel2/40 px-2.5 py-1.5 text-xs leading-relaxed">
        {c.would_fire_now > 0 ? (
          <span className="text-good">
            {c.strategy_name ?? c.strategy_id} would take {fmtInt(c.would_fire_now)} bucket
            {c.would_fire_now === 1 ? "" : "s"} right now
            {c.firing_top_band && c.firing_top_city
              ? ` — best is ${c.firing_top_band} in ${c.firing_top_city} at ${(c.firing_best_edge_pp ?? 0).toFixed(1)} points.`
              : "."}
          </span>
        ) : (
          <span className="text-muted">{blockedText(c)}</span>
        )}
      </div>

      {/* ---- unresolved targets are a typo, and say so ------------------ */}
      {c.targets_unresolved > 0 && (
        <p className="mt-1.5 text-[11px] text-warn">
          {(c.unresolved_values ?? []).map((v) => `“${v}”`).join(", ")} matched no city — this
          campaign is smaller than it looks. Region names are exactly {REGIONS.join(", ")}; city
          targets are city keys, not display names.
        </p>
      )}

      {/* ---- status ----------------------------------------------------- */}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => onStatus(c.deployment_id, s)}
            title={STATUS_MEANING[s]}
            className={`rounded px-2 py-0.5 ${
              c.status === s ? "bg-accent text-white" : "bg-panel2 text-muted hover:text-text"
            }`}
          >
            {s}
          </button>
        ))}
        {c.cities && c.cities.length > 0 && (
          <span className="ml-auto max-w-[45%] truncate text-[10px] text-muted" title={c.cities.join(", ")}>
            {c.cities.slice(0, 6).join(", ")}
            {c.cities.length > 6 ? ` +${c.cities.length - 6}` : ""}
          </span>
        )}
      </div>

      {!c.strategy_enabled && (
        <p className="mt-1.5 text-[10px] text-muted">
          Turn the strategy on from{" "}
          <Link href="/strategies" className="text-accent hover:underline">
            Strategies
          </Link>{" "}
          — a campaign cannot trade a strategy that is switched off, whatever its own status says.
        </p>
      )}
    </article>
  );
}

const STATUS_MEANING: Record<string, string> = {
  draft: "Written down, trades nothing.",
  armed: "Ready, waiting for its window to open.",
  running: "Acting on its strategy's signals inside its scope.",
  paused: "Holds what it has, takes nothing new.",
  closed: "Finished. Kept for its record.",
};

/**
 * The view already picks the FIRST reason a campaign cannot trade, in the
 * order that actually stops it. This is only the fallback for a database that
 * has not run ad4_41 yet.
 */
function blockedText(c: CampaignRow): string {
  if (c.blocked_because) return c.blocked_because;
  if (!c.strategy_enabled) return `${c.strategy_name ?? c.strategy_id} is switched off.`;
  if (c.bands_in_scope === 0) return "Nothing live in scope today.";
  return "Live and armed, with no setup in scope right now.";
}

function Cell({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "good" | "warn" | "bad";
}) {
  const t = tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-text";
  return (
    <div className="rounded border border-border bg-panel2/30 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${t}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[10px] leading-snug text-muted">{sub}</div>}
    </div>
  );
}
