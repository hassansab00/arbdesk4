"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState, InlineError } from "@/components/DataState";
import { fmtUsd, pnlColor } from "@/lib/format";

interface Deployment {
  deployment_id: string; name: string; strategy_id: string; target_kind: string;
  target: any; status: string; starts_at: string | null; ends_at: string | null; condition: string | null;
}

const STATUSES = ["draft", "armed", "running", "paused", "closed"] as const;

export default function CampaignsPage() {
  const [form, setForm] = useState({ name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const deploymentsQ = useQuery<Deployment[]>(
    () => supabase.from("deployments").select("*").order("created_at", { ascending: false }),
    []
  );
  const strategiesQ = useQuery<Array<{ strategy_id: string; name: string | null; enabled: boolean }>>(
    () => supabase.from("strategies").select("strategy_id,name,enabled").order("strategy_id"),
    []
  );
  const ledgerQ = useQuery<Array<{ deployment_id: string | null; detail: any }>>(
    () => supabase.from("ledger").select("deployment_id,detail").eq("stage", "settlement"),
    []
  );

  const deployments = deploymentsQ.data ?? [];
  const strategies = strategiesQ.data ?? [];

  const pnlByDeployment = useMemo(() => {
    const grouped: Record<string, number> = {};
    for (const r of ledgerQ.data ?? []) {
      if (!r.deployment_id) continue;
      grouped[r.deployment_id] = (grouped[r.deployment_id] ?? 0) + (r.detail?.net_pnl ?? 0);
    }
    return grouped;
  }, [ledgerQ.data]);

  function load() {
    deploymentsQ.refresh();
    ledgerQ.refresh();
  }

  async function create() {
    if (!form.name.trim() || !form.strategy_id) {
      setActionError("A name and a strategy are required.");
      return;
    }
    setBusy(true);
    const { error } = await supabase.rpc("upsert_deployment", {
      p_deployment: {
        name: form.name, strategy_id: form.strategy_id, target_kind: form.target_kind,
        target: { values: form.target.split(",").map((x) => x.trim()).filter(Boolean) },
        condition: form.condition || null, ends_at: form.ends_at || null, status: "draft",
      },
    });
    setBusy(false);
    setActionError(error ? `${error.message}${error.hint ? ` — ${error.hint}` : ""}` : null);
    if (!error) {
      setForm({ name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "" });
      load();
    }
  }

  async function setStatus(id: string, status: string) {
    const { error } = await supabase.rpc("set_deployment_status", { p_deployment_id: id, p_status: status });
    setActionError(error ? error.message : null);
    if (!error) load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Campaigns / Deployments</h1>
      <p className="text-xs text-muted">Deploy a strategy to a city, list, or cluster, for a duration or until a condition. Separate P&amp;L per deployment (from the ledger&apos;s settlement stage).</p>

      {actionError && <div className="rounded border border-bad/60 bg-bad/10 p-2 font-mono text-xs text-bad">{actionError}</div>}
      <InlineError message={strategiesQ.error ?? ledgerQ.error} />

      <div className="grid gap-2 rounded border border-border bg-panel p-4 sm:grid-cols-3">
        <input placeholder="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="input" />
        <select value={form.strategy_id} onChange={(e) => setForm({ ...form, strategy_id: e.target.value })} className="input">
          <option value="">
            {strategiesQ.loading ? "loading strategies…" : strategies.length ? "select strategy" : "no strategies seeded"}
          </option>
          {strategies.map((s) => (
            <option key={s.strategy_id} value={s.strategy_id}>
              {s.name ?? s.strategy_id}{s.enabled ? "" : " (disabled)"}
            </option>
          ))}
        </select>
        <select value={form.target_kind} onChange={(e) => setForm({ ...form, target_kind: e.target.value })} className="input">
          <option value="city">single city</option>
          <option value="list">list of cities</option>
          <option value="cluster">region/cluster</option>
        </select>
        <input placeholder="target (comma-separated)" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} className="input" />
        <input placeholder="condition (optional, free text)" value={form.condition} onChange={(e) => setForm({ ...form, condition: e.target.value })} className="input" />
        <input type="datetime-local" value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} className="input" />
        <button onClick={create} disabled={busy} className="rounded bg-accent py-1.5 text-white hover:opacity-90 disabled:opacity-50 sm:col-span-3">
          {busy ? "Creating…" : "Create draft deployment"}
        </button>
        {strategies.length > 0 && strategies.every((s) => !s.enabled) && (
          <p className="text-[10px] text-warn sm:col-span-3">
            Every strategy is currently disabled, so a deployment created now will not trade until you
            enable its strategy. That is deliberate — all six ship <code>enabled = false</code>.
          </p>
        )}
      </div>

      <DataState
          relation="deployments"
        loading={deploymentsQ.loading}
        error={deploymentsQ.error}
        isEmpty={deployments.length === 0}
        emptyTitle="No deployments yet"
        emptyBody={<>Create one above to run a strategy against a city, list or cluster for a fixed window. Per-deployment P&amp;L is attributed from the ledger&apos;s settlement stage, so it only appears once trades under that deployment have settled.</>}
        onRetry={deploymentsQ.refresh}
      >
      <div className="space-y-2">
        {deployments.map((d) => (
          <div key={d.deployment_id} className="rounded border border-border bg-panel p-3 text-sm">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-semibold">{d.name || d.deployment_id.slice(0, 8)}</span>
                <span className="ml-2 text-muted">{d.strategy_id} · {d.target_kind}</span>
              </div>
              <span className={pnlColor(pnlByDeployment[d.deployment_id])}>{fmtUsd(pnlByDeployment[d.deployment_id] ?? 0, { signed: true })}</span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatus(d.deployment_id, s)}
                  className={`rounded px-2 py-0.5 ${d.status === s ? "bg-accent text-white" : "bg-panel2 text-muted hover:text-text"}`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      </DataState>
    </div>
  );
}
