"use client";

/**
 * One prominent, honest banner when the frontend has no Supabase
 * credentials baked in. Without this the user gets ten identical "Query
 * failed" boxes and has to infer the cause; with it, the cause is the
 * first thing on the page.
 *
 * Reads process.env directly (not the lazy client) so it can never itself
 * throw - these are inlined at build time by Next, so an unset var is
 * literally `undefined` in the bundle.
 */
export default function ConfigBanner() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (url && key) return null;
  const missing = [!url && "NEXT_PUBLIC_SUPABASE_URL", !key && "NEXT_PUBLIC_SUPABASE_ANON_KEY"].filter(Boolean);
  return (
    <div className="border-b border-warn/50 bg-warn/10 px-4 py-2 text-xs text-warn">
      <b>Supabase is not configured.</b> {missing.join(" and ")} {missing.length > 1 ? "are" : "is"}{" "}
      not set in this build, so every page below will show a query error. Set{" "}
      {missing.length > 1 ? "them" : "it"} in Vercel → Settings → Environment Variables and redeploy
      (they are inlined at build time, so changing them needs a rebuild, not just a restart). See{" "}
      <code>web/README.md</code> and <code>docs/GO_LIVE.md</code> step 4.
    </div>
  );
}
