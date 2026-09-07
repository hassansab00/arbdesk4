"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";

/**
 * IS THIS PANEL EMPTY, STALE, OR FINE?
 *
 * Every panel had three ways of showing nothing and they all looked the same:
 * the table does not exist, the table exists and no job has ever filled it, or
 * a job used to fill it and stopped. Nothing on the page distinguished them,
 * which is what "the data is outdated" means and why it could not be acted on.
 *
 * sql/ad4_39_freshness.sql answers it in one view. This reads that view once
 * for the whole page - it is cached for two minutes and shared by every
 * component through the same useQuery, so a page with fifteen panels still
 * issues one request.
 */
export interface FreshRow {
  table_name: string;
  layer: string;
  plain_english: string;
  rows: number | null;
  newest: string | null;
  age_hours: number | null;
  fresh_hours: number | null;
  /** absent = no such table | empty = no rows | stale = old rows | ok */
  state: "absent" | "empty" | "stale" | "ok";
}

export function useFreshness() {
  const q = useQuery<FreshRow[]>(
    () => supabase.from("v_data_freshness").select("*"),
    [],
    120000
  );

  const byTable = useMemo(() => {
    const m = new Map<string, FreshRow>();
    for (const r of q.data ?? []) m.set(r.table_name, r);
    return m;
  }, [q.data]);

  return {
    rows: q.data ?? [],
    byTable,
    loading: q.loading,
    /**
     * A missing freshness view is not an error worth showing on every page -
     * it means ad4_39 has not been run, and the panels degrade to what they
     * did before it existed. It is reported once, by the health strip.
     */
    error: q.error,
    refresh: q.refresh,
  };
}

/** How old, in words a person reads rather than a number they convert. */
export function ageWords(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return "age unknown";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min ago`;
  if (hours < 48) return `${Math.round(hours)} h ago`;
  const d = hours / 24;
  if (d < 60) return `${Math.round(d)} days ago`;
  return `${Math.round(d / 30)} months ago`;
}
