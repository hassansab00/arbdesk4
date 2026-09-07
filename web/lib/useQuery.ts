"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PostgrestError } from "@supabase/supabase-js";

/**
 * Every data fetch in AD4 has exactly three failure-adjacent states and
 * they are all shown, never swallowed:
 *
 *   loading  - the request is in flight and nothing is known yet
 *   error    - it failed; the REAL Postgres/Supabase message is surfaced
 *              verbatim, so `relation "v_opportunities" does not exist`
 *              reaches the screen instead of a blank page
 *   empty    - it succeeded and returned nothing, which is the normal
 *              state of most tables until the engines run for the first
 *              time - the page explains WHY and WHAT will populate it
 *
 * A page that silently renders nothing is the failure mode this hook
 * exists to make impossible.
 */
export interface QueryState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /**
   * The query came back with EXACTLY as many rows as it asked for, so there
   * are almost certainly more it did not get.
   *
   * This is the failure mode that is worse than an error, because it looks
   * like an answer. The Predictive page asked v_forecast_convergence for
   * 20,000 rows and filtered by city in the browser; the view holds 55,000 at
   * three weeks of history and is ordered by city_key, so PostgREST returned
   * the alphabetically first cities and silently dropped the rest. Every
   * other city's chart said "no data for this city" - which is what the page
   * genuinely saw, and was completely wrong.
   *
   * Pass the same number to `limit` here that you passed to `.limit()`, and
   * DataState will say the view is truncated rather than pretending the
   * missing rows do not exist.
   *
   * AND THE SERVER HAS ITS OWN CEILING. Supabase sets PostgREST's max-rows to
   * 1,000 by default, so `.limit(4000)` never returns more than 1,000 rows no
   * matter what the client asks for. A guard that only compared against the
   * CLIENT's limit would therefore never fire on the exact case it exists to
   * catch: a query that asked for 4,000, was cut to 1,000 by the platform,
   * and looks complete because 1,000 is less than 4,000. Exactly 1,000 rows
   * back is treated as truncated for that reason.
   */
  truncated: boolean;
}

function messageOf(e: unknown): string {
  if (!e) return "Unknown error";
  const pg = e as Partial<PostgrestError> & { message?: string };
  const parts = [pg.message, pg.details, pg.hint].filter(Boolean);
  if (pg.code) parts.push(`(code ${pg.code})`);
  return parts.length ? parts.join(" — ") : String(e);
}

/**
 * `fn` must return `{ data, error }` (the Supabase shape) or throw.
 * PromiseLike, not Promise: a PostgREST query builder is a thenable that
 * only issues the request when awaited, so it satisfies this directly and
 * callers can `return supabase.from(...).select(...)` with no `.then()`.
 * `deps` re-runs the query; `intervalMs` optionally re-polls; `limit` is the
 * number passed to `.limit()`, used only to detect silent truncation.
 */
export function useQuery<T>(
  fn: () => PromiseLike<{ data: T | null; error: unknown }>,
  deps: unknown[] = [],
  intervalMs?: number,
  limit?: number
): QueryState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);

  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const res = await fnRef.current();
        if (cancelled || !alive.current) return;
        if (res.error) {
          setError(messageOf(res.error));
          setData(null);
        } else {
          setError(null);
          setData(res.data);
        }
      } catch (e) {
        // Thrown, not returned: an unconfigured Supabase client (missing
        // NEXT_PUBLIC_SUPABASE_* env vars) lands here, and the user needs
        // to see that message rather than an empty screen.
        if (cancelled || !alive.current) return;
        setError(messageOf(e));
        setData(null);
      } finally {
        if (!cancelled && alive.current) setLoading(false);
      }
    }
    setLoading(true);
    run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  useEffect(() => {
    if (!intervalMs) return;
    const t = setInterval(() => setTick((n) => n + 1), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);

  const refresh = useCallback(() => setTick((n) => n + 1), []);
  const SUPABASE_MAX_ROWS = 1000;
  const n = Array.isArray(data) ? (data as unknown[]).length : -1;
  const truncated =
    n >= 0 && ((limit !== undefined && n >= limit) || n === SUPABASE_MAX_ROWS);
  return { data, loading, error, refresh, truncated };
}
