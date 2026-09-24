/**
 * THE KEY EVERY n8n WEBHOOK CHECKS (plan v2 P6.4).
 *
 * An n8n webhook with no auth starts its workflow for anyone who learns the
 * URL - and the URLs are shown on the Workflows page. Each AD4 webhook now
 * requires the header below (n8n credential "AD4 Webhook Key", Header Auth),
 * and only this server sends it: the value is N8N_WEBHOOK_KEY, a server-only
 * Vercel variable. Never a NEXT_PUBLIC_ variable, which would put it in the
 * browser bundle.
 *
 * With the variable unset the call goes out without the header, as it did
 * before; once n8n requires it, n8n answers 403 and the caller says why.
 */
export const WEBHOOK_KEY_HEADER = "X-AD4-Key";

export function webhookHeaders(env: Record<string, string | undefined> = process.env): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const key = (env.N8N_WEBHOOK_KEY || "").trim();
  if (key) headers[WEBHOOK_KEY_HEADER] = key;
  return headers;
}

/** What to tell the operator when n8n refuses the call. Never includes the key. */
export function webhookRefusal(status: number, env: Record<string, string | undefined> = process.env): string | null {
  if (status !== 401 && status !== 403) return null;
  return (env.N8N_WEBHOOK_KEY || "").trim()
    ? "n8n refused the key. N8N_WEBHOOK_KEY in Vercel and the n8n credential \"AD4 Webhook Key\" (header X-AD4-Key) must hold the same value."
    : "n8n asks for a key and this server has none: set N8N_WEBHOOK_KEY in Vercel (server-only, never NEXT_PUBLIC_) to the value in the n8n credential \"AD4 Webhook Key\", then redeploy.";
}
