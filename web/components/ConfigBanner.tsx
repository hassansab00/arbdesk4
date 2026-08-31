"use client";

import { classifyKey } from "@/lib/keyGuard";

/**
 * One prominent, honest banner about how this build is configured.
 *
 * Two cases, in order of severity:
 *
 * 1. A SECRET key is set as the public key. This is a live credential
 *    exposure - NEXT_PUBLIC_ variables are inlined into the bundle every
 *    visitor downloads - so it gets a red, unmissable banner that says
 *    ROTATE, not "replace". Supabase's API refuses such requests, but the
 *    key is public the moment the page is served, which is why this is
 *    stated as an incident rather than a misconfiguration.
 *
 * 2. The variables are missing entirely, which just means every query below
 *    will fail. Amber, informational.
 *
 * Reads process.env directly rather than the lazy client so it can never
 * itself throw - these are inlined at build time, so an unset var is
 * literally `undefined` in the bundle.
 */
export default function ConfigBanner() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (key && classifyKey(key) === "secret") {
    return (
      <div className="border-b-2 border-bad bg-bad/20 px-4 py-3 text-sm text-bad">
        <div className="font-bold uppercase tracking-wide">
          Security: a secret Supabase key is exposed in this build
        </div>
        <p className="mt-1 max-w-4xl leading-relaxed">
          <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> holds a{" "}
          <b>secret / service_role</b> key. Every <code>NEXT_PUBLIC_</code> variable is compiled
          into the JavaScript that every visitor downloads, so this key is <b>already public</b>.
          AD4 has refused to open a connection with it, and Supabase would reject it anyway — but
          neither of those un-publishes it.
        </p>
        <ol className="mt-2 max-w-4xl list-inside list-decimal space-y-0.5 leading-relaxed">
          <li>
            <b>Rotate it now</b> — Supabase → Project Settings → API Keys → revoke this key.
            Replacing it in Vercel is not enough.
          </li>
          <li>
            Set the <b>publishable</b> key here instead: <code>sb_publishable_…</code>, or the
            legacy <code>anon</code> JWT.
          </li>
          <li>
            Put the new secret key only in the GitHub Actions secret{" "}
            <code>SUPABASE_SERVICE_KEY</code> and your n8n Config nodes.
          </li>
          <li>Redeploy — these are inlined at build time, so a restart will not pick it up.</li>
        </ol>
      </div>
    );
  }

  if (url && key) return null;

  const missing = [!url && "NEXT_PUBLIC_SUPABASE_URL", !key && "NEXT_PUBLIC_SUPABASE_ANON_KEY"].filter(Boolean);
  return (
    <div className="border-b border-warn/50 bg-warn/10 px-4 py-2 text-xs text-warn">
      <b>Supabase is not configured.</b> {missing.join(" and ")} {missing.length > 1 ? "are" : "is"}{" "}
      not set in this build, so every page below will show a query error. Set{" "}
      {missing.length > 1 ? "them" : "it"} in Vercel → Settings → Environment Variables and redeploy
      (they are inlined at build time, so changing them needs a rebuild, not just a restart). Use the{" "}
      <b>publishable</b> key — <code>sb_publishable_…</code> or the legacy <code>anon</code> JWT —
      never the secret / service_role one. See <code>web/README.md</code> and{" "}
      <code>docs/GO_LIVE.md</code> step 4.
    </div>
  );
}
