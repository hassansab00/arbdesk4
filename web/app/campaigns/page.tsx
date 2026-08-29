"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { fmtUsd, pnlColor } from "@/lib/format";

interface Deployment {
  deployment_id: string; name: string; strategy_id: string; target_kind: string;
  target: any; status: string; starts_at: string | null; ends_at: string | null; condition: string | null;
}

const STATUSES = ["draft", "armed", "running", "paused", "closed"] as const;

export default function CampaignsPage() {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [pnlByDeployment, setPnlByDeployment] = useState<Record<string, number>>({});
  const [strategies, setStrategies] = useState<string[]>([]);
  const [form, setForm] = useState({ name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "" });

  async function load() {
    const { data } = await supabase.from("deployments").select("*").order("created_at", { ascending: false });
    setDeployments((data as Deployment[]) ?? []);

    const { data: strategyRows } = await supabase.from("strategies").select("strategy_id");
    setStrategies((strategyRows ?? []).map((s: any) => s.strategy_id));

    const { data: ledgerRows } = await supabase.from("ledger").select("deployment_id,detail").eq("stage", "settlement");
    const grouped: Record<string, number> = {};
    for (const r of (ledgerRows as any[]) ?? []) {
      if (!r.deployment_id) continue;
      grouped[r.deployment_id] = (grouped[r.deployment_id] ?? 0) + (r.detail?.net_pnl ?? 0);
    }
    setPnlByDeployment(grouped);
  }

  useEffect(() => { load(); }, []);

  async function create() {
    await supabase.rpc("upsert_deployment", {
      p_deployment: {
        name: form.name, strategy_id: form.strategy_id, target_kind: form.target_kind,
        target: { values: form.target.split(",").map((s) => s.trim()).filter(Boolean) },
        condition: form.condition || null, ends_at: form.ends_at || null, status: "draft",
      },
    });
    setForm({ name: "", strategy_id: "", target_kind: "city", target: "", condition: "", ends_at: "" });
    load();
  }

  async function setStatus(id: string, status: string) {
    await supabase.rpc("set_deployment_status", { p_deployment_id: id, p_status: status });
    load();
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Campaigns / Deployments</h1>
      <p className="text-xs text-muted">Deploy a strategy to a city, list, or cluster, for a duration or until a condition. Separate P&amp;L per deployment (from the ledger&apos;s settlement stage).</p>

      <div className="grid gap-2 rounded border border-border bg-panel p-4 sm:grid-cols-3">
        <input placeholder="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="input" />
        <select value={form.strategy_id} onChange={(e) => setForm({ ...form, strategy_id: e.target.value })} className="input">
          <option value="">select strategy</option>
          {strategies.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={form.target_kind} onChange={(e) => setForm({ ...form, target_kind: e.target.value })} className="input">
          <option value="city">single city</option>
          <option value="list">list of cities</option>
          <option value="cluster">region/cluster</option>
        </select>
        <input placeholder="target (comma-separated)" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} className="input" />
        <input placeholder="condition (optional, free text)" value={form.condition} onChange={(e) => setForm({ ...form, condition: e.target.value })} className="input" />
        <input type="datetime-local" value={form.ends_at} onChange={(e) => setForm({ ...form, ends_at: e.target.value })} className="input" />
        <button onClick={create} className="rounded bg-accent py-1.5 text-white hover:opacity-90 sm:col-span-3">Create draft deployment</button>
      </div>

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
        {deployments.length === 0 && <div className="p-6 text-center text-muted">No deployments yet.</div>}
      </div>
    </div>
  );
}
