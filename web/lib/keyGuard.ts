/**
 * Refuse to let a SECRET Supabase key be used from the browser.
 *
 * Why this file exists: `NEXT_PUBLIC_*` variables are inlined into the
 * JavaScript bundle every visitor downloads. Putting the service_role /
 * secret key there publishes it. Supabase's own API now rejects such a
 * request ("Forbidden use of secret API key in browser"), which is a good
 * backstop - but by then the key has already been served to every visitor,
 * so the damage is done before the first request is even made.
 *
 * So AD4 refuses at the client-construction boundary instead: no request is
 * ever attempted with a secret key, and the UI says plainly what happened
 * and that the key must be ROTATED, not merely replaced.
 *
 * Supabase has two key generations and this handles both:
 *   legacy  - `anon` and `service_role`, both JWTs (`eyJ...`) with the role
 *             in the payload
 *   current - `sb_publishable_...` (browser-safe) and `sb_secret_...`
 *             (server only)
 */

export type KeyKind = "publishable" | "secret" | "unknown";

/** base64url decode that works in both the browser and Node prerender. */
function decodeSegment(seg: string): string | null {
  try {
    const b64 = seg.replace(/-/g, "+").replace(/_/g, "/");
    const pad = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    if (typeof atob === "function") return atob(pad);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const B = (globalThis as any).Buffer;
    if (B) return B.from(pad, "base64").toString("utf8");
    return null;
  } catch {
    return null;
  }
}

export function classifyKey(key: string | undefined | null): KeyKind {
  if (!key) return "unknown";
  const k = key.trim();

  // Current-generation keys announce themselves in the prefix.
  if (k.startsWith("sb_secret_")) return "secret";
  if (k.startsWith("sb_publishable_")) return "publishable";

  // Legacy keys are JWTs; the role lives in the payload.
  const parts = k.split(".");
  if (parts.length === 3) {
    const payload = decodeSegment(parts[1]);
    if (payload) {
      try {
        const role = (JSON.parse(payload) as { role?: string }).role;
        if (role === "service_role") return "secret";
        if (role === "anon") return "publishable";
      } catch {
        /* not JSON - fall through to unknown */
      }
    }
    // A JWT we cannot read. Do not guess it is safe.
    return "unknown";
  }

  return "unknown";
}

export const SECRET_KEY_MESSAGE =
  "A SECRET Supabase key is configured as NEXT_PUBLIC_SUPABASE_ANON_KEY. " +
  "NEXT_PUBLIC_ variables are compiled into the JavaScript every visitor " +
  "downloads, so this key is already public and must be ROTATED, not just " +
  "replaced: Supabase -> Project Settings -> API Keys -> revoke it. Then set " +
  "the PUBLISHABLE key here (sb_publishable_... or the legacy anon JWT), put " +
  "the new secret key only in the GitHub Actions secret SUPABASE_SERVICE_KEY " +
  "and your n8n Config nodes, and redeploy.";
