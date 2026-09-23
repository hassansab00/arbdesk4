import { supabase } from "@/lib/supabase";
import type { BookRow } from "@/lib/execution";

/** The v_latest_book columns a page needs to walk a ladder. */
export const BOOK_COLUMNS =
  "band_id,ask_levels,bid_levels,ask_levels_source,bid_levels_source,ask_depth_usd,bid_depth_usd,best_ask,best_bid";

/** Band ids per request. A uuid is 36 characters, so 100 of them keep the URL
 *  near 4 KB - well inside what the API gateway accepts. */
const CHUNK = 100;

/**
 * THE BOOKS OF THE BANDS ON THE PAGE, AND NO OTHERS.
 *
 * Board, Opportunities and Goals each read `v_latest_book` with no filter, every
 * 30 seconds. That view holds one row per band that has EVER had a snapshot -
 * 13,545 on 22 Sep, of which 1,628 belong to a market still open - and
 * PostgREST returns at most 1,000 rows. So each page received an arbitrary
 * thousand books, mostly of long-settled bands, frequently not the ones it was
 * pricing, and paid for them: 8.6 MB and up to 4.9 s a read.
 *
 * Asked by band id it is one index probe per band (sql/ad4_13_reconcile.sql,
 * v_band_book): eleven bands cost 103 buffers. Resolves `{ data, error }` so it
 * drops straight into useQuery.
 */
export async function latestBooks(
  bandIds: Array<string | null | undefined>,
  columns: string = BOOK_COLUMNS
): Promise<{ data: BookRow[] | null; error: unknown }> {
  const ids = Array.from(new Set(bandIds.filter((b): b is string => Boolean(b)))).sort();
  if (ids.length === 0) return { data: [], error: null };
  const chunks: string[][] = [];
  for (let i = 0; i < ids.length; i += CHUNK) chunks.push(ids.slice(i, i + CHUNK));
  const results = await Promise.all(
    chunks.map((c) => supabase.from("v_latest_book").select(columns).in("band_id", c))
  );
  const failed = results.find((r) => r.error);
  if (failed) return { data: null, error: failed.error };
  return { data: results.flatMap((r) => (r.data ?? []) as unknown as BookRow[]), error: null };
}
