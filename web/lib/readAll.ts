/**
 * EVERY ROW, NOT THE FIRST THOUSAND.
 *
 * PostgREST returns at most 1,000 rows per request however large the
 * `.limit()`, so a view bigger than that has to be read a page at a time. The
 * forward ladder on the Predictive page is 2,112 rows on 22 Sep - a YES and a
 * NO row for every band still open - and it was read with `.limit(1000)`, so
 * the table showed about half the city-days, and any city-day the cut fell
 * across chose its "best band" from part of its own ladder.
 *
 * `page(from, to)` must apply a TOTAL order (ending in a unique key) before
 * `.range(from, to)`, or rows can repeat or vanish across page boundaries.
 * Stops at the first short page, or at `maxRows` - pass the same number to
 * useQuery's `limit` so hitting the ceiling still shows as truncated.
 */
export async function readAllRows<T>(
  page: (from: number, to: number) => PromiseLike<{ data: T[] | null; error: unknown }>,
  maxRows: number,
  pageSize = 1000
): Promise<{ data: T[] | null; error: unknown }> {
  const out: T[] = [];
  for (let from = 0; from < maxRows; from += pageSize) {
    const { data, error } = await page(from, Math.min(from + pageSize, maxRows) - 1);
    if (error) return { data: null, error };
    const rows = data ?? [];
    out.push(...rows);
    if (rows.length < pageSize) break;
  }
  return { data: out, error: null };
}
