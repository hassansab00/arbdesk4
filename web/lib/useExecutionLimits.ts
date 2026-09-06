"use client";

import { useMemo } from "react";
import { supabase } from "@/lib/supabase";
import { useQuery } from "@/lib/useQuery";
import { DEFAULT_LIMITS, limitsFromRow, type Limits } from "@/lib/execution";

/**
 * What the venue will actually accept, from sql/ad4_36_execution_limits.sql.
 *
 * Every page that sizes a trade needs the same floor, and a page that hard-
 * codes it drifts the moment the real per-market minimums land on `markets`.
 * The fallback is the shipped default rather than "no limit": treating an
 * unreachable settings row as "anything goes" is exactly the failure this
 * whole layer exists to stop.
 */
export function useExecutionLimits(): { limits: Limits; error: string | null } {
  const q = useQuery<Array<Record<string, unknown>>>(
    () => supabase.from("v_execution_limits").select("*"),
    [],
    300000
  );
  const limits = useMemo(() => limitsFromRow((q.data ?? [])[0]), [q.data]);
  return { limits: limits ?? DEFAULT_LIMITS, error: q.error };
}
