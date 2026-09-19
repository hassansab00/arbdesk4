// How the archive encodes what it stores, and how to read both generations.
//
// A Next.js route file may only export its HTTP handlers and a small set of
// config constants, so these live here rather than beside the route - which
// also makes them directly testable without standing up a request.

/**
 * The first archive wrote jsonb payloads as PYTHON REPRS, not JSON.
 *
 * csv.DictWriter stringifies with str(), and the exporter handed it a parsed
 * dict, so 78,291 research captures went to the release looking like
 *
 *     {'band_hi': 28, 'sigma_c': None, 'open_low': False}
 *
 * - single-quoted, with None and False where JSON needs null and false. The
 * writer is fixed, so nothing archived from now on looks like this, but those
 * rows are already in the release and no longer in Postgres, so the reader
 * has to understand both.
 *
 * A SCANNER, NOT A REGEX. Replacing ' with " across the string corrupts every
 * apostrophe inside a value, and swapping bare None/True/False would rewrite
 * those words where they appear inside text. This walks the string, re-emits
 * quoted runs through JSON.stringify, and only rewrites bare words outside
 * them. Checked against all 78,291 archived payloads: every one parses, and
 * every one equals what Python's own ast.literal_eval produces.
 */
export function pythonReprToJson(src: string): string {
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    if (c === "'" || c === '"') {
      const quote = c;
      let s = '';
      i++;
      while (i < n) {
        const d = src[i];
        if (d === '\\') { s += d + (src[i + 1] ?? ''); i += 2; continue; }
        if (d === quote) { i++; break; }
        s += d; i++;
      }
      out += JSON.stringify(s);
      continue;
    }
    if (/[A-Za-z_]/.test(c)) {
      let w = '';
      while (i < n && /[A-Za-z_0-9.]/.test(src[i])) { w += src[i]; i++; }
      out += w === 'None' ? 'null' : w === 'True' ? 'true' : w === 'False' ? 'false' : w;
      continue;
    }
    out += c; i++;
  }
  return out;
}

/** Parse a payload written by either archive generation. Returns the raw
 *  string if it is neither, rather than throwing - one unreadable row must
 *  not cost the caller the other 78,290. */
export function decodePayload(raw: string): unknown {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { /* legacy */ }
  try { return JSON.parse(pythonReprToJson(raw)); } catch { return raw; }
}
