"use client";

import { useMemo, useSyncExternalStore } from "react";
import { supabase } from "@/lib/supabase";
import { messageOf } from "@/lib/useQuery";

/**
 * IS THIS PANEL EMPTY, STALE, OR FINE?
 *
 * Every panel had three ways of showing nothing and they all looked the same:
 * the table does not exist, the table exists and no job has ever filled it, or
 * a job used to fill it and stopped. Nothing on the page distinguished them,
 * which is what "the data is outdated" means and why it could not be acted on.
 *
 * sql/ad4_39_freshness.sql answers it in one view. This reads that view once
 * for the whole page - one request, refreshed every two minutes, shared by
 * every component that calls the hook, so a page with fifteen panels still
 * issues one request.
 *
 * IT DID NOT, UNTIL 2026-09-22. That sentence was the intent, but the hook was
 * a plain useQuery, and useQuery keeps its state per component: every
 * Freshness chip, every FreshnessRow and every empty panel's WhatFillsThis
 * sent its own request and ran its own two-minute timer. A FreshnessRow of
 * eight inputs was nine identical requests. pg_stat_statements had
 * v_data_freshness as the single most expensive thing the database did:
 * 19,219 calls, about 9,300 seconds, all of it the same answer - fired in a
 * burst at every page load on a shared-CPU instance, where it starved the
 * page's real reads into 57014. The store below is what the sentence said.
 */
export interface FreshRow {
  table_name: string;
  layer: string;
  plain_english: string;
  rows: number | null;
  /**
   * true when `rows` is the planner's estimate (pg_class.reltuples) rather
   * than a count. Tables over 5,000 rows are estimated so this view stays
   * cheap - see sql/ad4_39_freshness.sql. Print it with rowsText().
   */
  rows_estimated?: boolean | null;
  newest: string | null;
  age_hours: number | null;
  fresh_hours: number | null;
  /** absent = no such table | empty = no rows | stale = old rows | ok */
  state: "absent" | "empty" | "stale" | "ok";
}

/* ------------------------------------------------------------------ store
 * One snapshot for the whole browser tab. Replaced, never mutated, so
 * useSyncExternalStore sees a change exactly when there is one.
 */
interface Snapshot { data: FreshRow[] | null; loading: boolean; error: string | null }

const REFRESH_MS = 120000;
const SERVER_SNAPSHOT: Snapshot = { data: null, loading: true, error: null };
let snapshot: Snapshot = SERVER_SNAPSHOT;
let fetchedAt = 0;
let inflight: Promise<void> | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function publish(next: Snapshot) {
  snapshot = next;
  listeners.forEach((l) => l());
}

/** One request at a time: a caller arriving mid-flight shares it. */
function load(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await supabase.from("v_data_freshness").select("*");
      if (res.error) publish({ data: null, loading: false, error: messageOf(res.error) });
      else publish({ data: (res.data ?? []) as FreshRow[], loading: false, error: null });
    } catch (e) {
      // Thrown, not returned: an unconfigured client lands here.
      publish({ data: null, loading: false, error: messageOf(e) });
    } finally {
      fetchedAt = Date.now();
      inflight = null;
    }
  })();
  return inflight;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    // The first panel on a page starts the clock; a page navigated to within
    // the refresh window reuses what the last one read.
    if (Date.now() - fetchedAt >= REFRESH_MS) void load();
    timer = setInterval(() => void load(), REFRESH_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

export function useFreshness() {
  const q = useSyncExternalStore(subscribe, () => snapshot, () => SERVER_SNAPSHOT);

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
    refresh: () => void load(),
  };
}

/**
 * A row count as a person should read it: "~146,274" when the number is the
 * planner's estimate, "1,807" when it was counted. An estimate printed without
 * the tilde claims a precision nobody measured.
 */
export function rowsText(rows: number | null | undefined, estimated?: boolean | null): string {
  if (rows === null || rows === undefined) return "—";
  return `${estimated ? "~" : ""}${Number(rows).toLocaleString()}`;
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
