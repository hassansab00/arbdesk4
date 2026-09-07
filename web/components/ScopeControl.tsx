"use client";

import { useMemo, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DataState } from "@/components/DataState";

/**
 * Which cities a job covers.
 *
 * WHAT THIS IS WORTH, per job, because it is not the same everywhere and a
 * control that implies otherwise is a control that gets misused:
 *
 *   One request per item - P0.3 book (~800 a run), P0.4 trades, P1.2 NWS
 *   (three per city, 111 a run) - scoping cuts the run proportionally, and is
 *   the difference between a run that finishes and one that hits the timeout
 *   having written nothing.
 *
 *   Batched - P1.5 sends ONE request carrying every city - scoping changes the
 *   length of a URL. The row says so instead of promising a saving.
 *
 * The other use, and the one the desk actually reaches for: refresh THIS city
 * now, before trading it, rather than waiting for the next full cycle.
 *
 * n8n bills per execution, so none of this reduces the execution count. It
 * reduces runtime and upstream load. Saying that plainly is the difference
 * between a budget control and a placebo.
 */

interface ScopeRow {
  job: string;
  mode: string;
  cities_covered: number | null;
  cities_total: number | null;
  cities: string[] | null;
  effect: string;
}
interface CityRow { city_key: string; display_name: string | null; status: string | null }

export default function ScopeControl({ jobs }: { jobs: Array<{ job: string; label: string }> }) {
  const scopeQ = useQuery<ScopeRow[]>(() => supabase.from("v_run_scope").select("*"), []);
  const citiesQ = useQuery<CityRow[]>(
    () => supabase.from("cities").select("city_key,display_name,status").order("city_key"), []
  );
  const [openJob, setOpenJob] = useState<string | null>(null);
  const [draft, setDraft] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const byJob = useMemo(() => {
    const m = new Map<string, ScopeRow>();
    for (const r of scopeQ.data ?? []) m.set(r.job, r);
    return m;
  }, [scopeQ.data]);

  const cities = (citiesQ.data ?? []).filter((c) => (c.status ?? "active") === "active");

  function edit(job: string) {
    setMsg(null);
    setOpenJob(job);
    setDraft(new Set(byJob.get(job)?.cities ?? []));
  }

  async function save(job: string, all: boolean) {
    setSaving(true);
    setMsg(null);
    const { error } = await supabase.rpc("set_run_scope", {
      p_job: job,
      p_cities: all ? null : Array.from(draft),
    });
    setSaving(false);
    if (error) {
      setMsg(error.message);
      return;
    }
    setOpenJob(null);
    scopeQ.refresh();
  }

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold">Which cities each job covers</h2>
      <p className="max-w-3xl text-xs leading-relaxed text-muted">
        Default is every active city. Narrowing it does <strong className="text-text">not</strong>{" "}
        reduce your n8n execution count — n8n bills per execution — it reduces how long a run takes
        and how hard it leans on the upstream API. That matters where a job makes one request per
        item, and not at all where it batches; each row says which it is.
      </p>

      <DataState
          relation="v_run_scope"
        loading={scopeQ.loading} error={scopeQ.error} isEmpty={(scopeQ.data ?? []).length === 0}
        emptyTitle="No jobs registered"
        emptyBody={<>Run <code className="rounded bg-panel2 px-1">sql/ad4_32_run_scope.sql</code>.</>}
        onRetry={scopeQ.refresh}
      >
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-xs">
            <thead className="bg-panel2 text-muted">
              <tr>
                <th className="px-2 py-1.5 text-left">Job</th>
                <th className="px-2 py-1.5 text-left">Covers</th>
                <th className="px-2 py-1.5 text-left">What scoping does here</th>
                <th className="px-2 py-1.5" />
              </tr>
            </thead>
            <tbody>
              {jobs.map(({ job, label }) => {
                const r = byJob.get(job);
                const editing = openJob === job;
                return (
                  <tr key={job} className="border-t border-border align-top">
                    <td className="px-2 py-1.5">
                      <div>{label}</div>
                      <div className="text-[10px] text-muted">{job}</div>
                    </td>
                    <td className="px-2 py-1.5">
                      {!r ? (
                        <span className="text-muted">—</span>
                      ) : r.mode === "all" ? (
                        <span>all {r.cities_total ?? ""}</span>
                      ) : (
                        <span className="text-accent">
                          {r.cities_covered} of {r.cities_total}
                          <span className="ml-1 text-muted">
                            {(r.cities ?? []).slice(0, 4).join(", ")}
                            {(r.cities ?? []).length > 4 ? ` +${(r.cities ?? []).length - 4}` : ""}
                          </span>
                        </span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-[11px] leading-relaxed text-muted">
                      {r?.effect ?? ""}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      <button
                        className="rounded border border-border px-2 py-0.5 text-muted hover:text-text"
                        onClick={() => (editing ? setOpenJob(null) : edit(job))}
                      >
                        {editing ? "cancel" : "choose"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {openJob && (
          <div className="rounded border border-accent/40 bg-panel2 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="font-semibold">{openJob}</span>
              <span className="text-muted">
                {draft.size === 0 ? "nothing selected — saving now means all cities" : `${draft.size} selected`}
              </span>
              <span className="flex-1" />
              <button className="text-muted hover:text-text" onClick={() => setDraft(new Set(cities.map((c) => c.city_key)))}>
                select all
              </button>
              <button className="text-muted hover:text-text" onClick={() => setDraft(new Set())}>
                clear
              </button>
            </div>

            <div className="mb-3 flex max-h-56 flex-wrap gap-1 overflow-y-auto">
              {cities.map((c) => {
                const on = draft.has(c.city_key);
                return (
                  <button
                    key={c.city_key}
                    onClick={() =>
                      setDraft((d) => {
                        const n = new Set(d);
                        if (n.has(c.city_key)) n.delete(c.city_key);
                        else n.add(c.city_key);
                        return n;
                      })
                    }
                    className={`rounded border px-1.5 py-0.5 text-[11px] ${
                      on ? "border-accent bg-accent/20 text-text" : "border-border text-muted hover:text-text"
                    }`}
                  >
                    {c.display_name ?? c.city_key}
                  </button>
                );
              })}
            </div>

            <div className="flex items-center gap-2 text-xs">
              <button
                disabled={saving}
                onClick={() => save(openJob, false)}
                className="rounded bg-accent/20 px-2 py-1 text-accent hover:bg-accent/30 disabled:opacity-50"
              >
                {saving ? "saving…" : draft.size ? `Cover these ${draft.size}` : "Cover all cities"}
              </button>
              <button
                disabled={saving}
                onClick={() => save(openJob, true)}
                className="rounded border border-border px-2 py-1 text-muted hover:text-text disabled:opacity-50"
              >
                Back to all
              </button>
              {msg && <span className="text-bad">{msg}</span>}
            </div>

            <p className="mt-2 text-[11px] leading-relaxed text-muted">
              A selection that matches no active city falls back to covering everything. Running
              zero cities looks exactly like a broken job, so it is never what a scope means.
            </p>
          </div>
        )}
      </DataState>
    </section>
  );
}
