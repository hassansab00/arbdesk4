"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { FILLED_BY } from "@/lib/provenance";
import { useFreshness, ageWords } from "@/lib/useFreshness";

/**
 * DOCUMENTATION, in plain words.
 *
 * Written for someone who did not build this and does not want to read SQL:
 * what the desk is trying to do, what each page is for, what each job does,
 * and what to do when a page is empty.
 *
 * It is deliberately part of the app rather than a file in the repo, for one
 * reason: the parts that go stale are the parts about THIS database - which
 * tables are filled, when they last were, what is still empty. Those are read
 * live from v_data_freshness and from the derived provenance map, so the page
 * describes the desk as it is right now rather than as it was the day someone
 * wrote a README.
 */

const SECTIONS = [
  { id: "what", label: "What this is" },
  { id: "how", label: "How it works" },
  { id: "pages", label: "Every page" },
  { id: "jobs", label: "The jobs" },
  { id: "empty", label: "When something is empty" },
  { id: "setup", label: "Setting it up" },
  { id: "safety", label: "Keys and safety" },
  { id: "words", label: "Words used here" },
];

export default function DocsPage() {
  const { rows, byTable } = useFreshness();
  const [open, setOpen] = useState<string>("what");

  const health = useMemo(() => {
    const bad = rows.filter((r) => r.state !== "ok");
    return { total: rows.length, bad: bad.length, list: bad };
  }, [rows]);

  return (
    <div className="mx-auto max-w-4xl space-y-8 pb-16">
      <header>
        <h1 className="text-lg font-semibold">How ArbDesk works</h1>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          Written for someone who did not build it. No SQL, no jargon that is not explained. The
          parts about <em>your</em> database — what is filled, what is empty, when it last updated —
          are read live, so this page is never out of date about your own desk.
        </p>
      </header>

      <nav className="flex flex-wrap gap-1.5">
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            onClick={() => setOpen(s.id)}
            className="rounded border border-border bg-panel px-2 py-1 text-[11px] text-muted hover:text-text"
          >
            {s.label}
          </a>
        ))}
      </nav>

      {/* ================================================================ */}
      <Section id="what" title="What this is">
        <P>
          ArbDesk trades one kind of bet: <b>how hot will it get in a city today</b>. Polymarket
          runs a market for each city each day, split into temperature buckets — 70–71°F, 72–73°F,
          and so on. You buy the bucket you think will win. If the day&apos;s highest temperature
          lands in your bucket, it pays out; otherwise it is worth nothing.
        </P>
        <P>
          The whole desk exists to answer one question better than the market does:{" "}
          <b>which bucket will win, and is it priced too cheaply right now?</b>
        </P>
        <Callout tone="good" title="Where the edge is supposed to come from">
          Three places, in order of how much they matter. <b>One</b>: public weather forecasts have
          a consistent lean per city, and a lean you have measured is a correction you can apply.{" "}
          <b>Two</b>: the day&apos;s maximum is made in a few hours around local solar afternoon, so
          by mid-afternoon you know more than the morning price does. <b>Three</b>: these markets
          are thin, so a price can sit stale for hours after a forecast has moved.
        </Callout>
        <P>
          Nothing here trades real money on its own. It writes down what it <i>would</i> do, and
          every strategy ships switched off.
        </P>
      </Section>

      {/* ================================================================ */}
      <Section id="how" title="How it works, end to end">
        <P>Five steps, in order. Each one needs the one before it.</P>
        <ol className="space-y-2">
          <Step n={1} title="Collect">
            Jobs fetch the weather (what it is now, what it was, what the forecast says) and the
            market (which buckets exist, what they cost, what has traded). This is the only part
            that talks to the outside world.
          </Step>
          <Step n={2} title="Learn">
            From the days that have finished, work out how wrong each forecast usually is for each
            city, what hour each city peaks, and how fast it warms. This is the desk&apos;s
            advantage and it only exists once enough days have finished.
          </Step>
          <Step n={3} title="Predict">
            Take today&apos;s public forecast, apply the correction learned in step 2, and turn it
            into a probability for every bucket — not one number but a spread, because a forecast
            two days out is less certain than one made this morning.
          </Step>
          <Step n={4} title="Compare">
            Put that probability next to what the market charges. The gap is the <b>edge</b>. Then
            subtract what it would really cost to trade: fees, and the fact that buying moves the
            price against you.
          </Step>
          <Step n={5} title="Decide">
            A strategy is a rule about when an edge is worth taking. Each writes down what it would
            do. Nothing is automatic and nothing is real money.
          </Step>
        </ol>
        <Callout tone="warn" title="Why the order matters">
          You cannot skip a step. With no finished days there is nothing to learn from, so there is
          no correction, so a probability is just the public forecast repeated back, so an
          &ldquo;edge&rdquo; is only the market disagreeing with a forecast you have not checked.
          The <Link href="/synthesis" className="text-accent hover:underline">Synthesis</Link> page
          shows exactly where in this chain your desk currently is.
        </Callout>
      </Section>

      {/* ================================================================ */}
      <Section id="pages" title="Every page, and when to open it">
        <div className="space-y-2">
          {PAGES.map((p) => (
            <div key={p.href} className="rounded border border-border bg-panel p-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <Link href={p.href} className="text-sm font-semibold text-accent hover:underline">
                  {p.label}
                </Link>
                <span className="text-[11px] text-muted">{p.when}</span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">{p.what}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* ================================================================ */}
      <Section id="jobs" title="The jobs that fill it">
        <P>
          Nothing appears on its own. Every table is written by a named job, and there are two
          kinds. <b>GitHub Actions</b> run in this repository on a schedule and do the thinking —
          learning, scoring, backtesting. <b>n8n workflows</b> run wherever you host n8n and do the
          fetching — weather and market data. Both are listed on{" "}
          <Link href="/workflows" className="text-accent hover:underline">Workflows</Link>.
        </P>
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-panel2 text-[10px] uppercase tracking-wide text-muted">
              <tr>
                <th className="p-2">Job</th>
                <th className="p-2">Kind</th>
                <th className="p-2">How often</th>
                <th className="p-2">Fills</th>
              </tr>
            </thead>
            <tbody>
              {JOBS(byTable).map((j) => (
                <tr key={j.file} className="border-t border-border/50">
                  <td className="p-2 font-medium">{j.name}</td>
                  <td className="p-2 text-muted">{j.kind === "action" ? "GitHub Action" : "n8n"}</td>
                  <td className="p-2 text-muted">{j.cadence}</td>
                  <td className="p-2 text-muted">
                    {j.tables.join(", ")}
                    {j.oldest !== null && (
                      <span className="ml-1 text-[10px]">· last wrote {ageWords(j.oldest)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* ================================================================ */}
      <Section id="empty" title="When a page is empty">
        <P>
          An empty panel is not a bug by default. It means one of three things, and every panel now
          tells you which:
        </P>
        <ul className="space-y-1.5 text-xs leading-relaxed text-muted">
          <li>
            <b className="text-bad">Not installed</b> — the table does not exist. A SQL file has not
            been run. The panel names which one.
          </li>
          <li>
            <b className="text-warn">No data yet</b> — the table exists and nothing has filled it.
            The panel names the job and where its button is.
          </li>
          <li>
            <b className="text-warn">Stale</b> — it was filled and stopped. The job has failed or is
            switched off.
          </li>
        </ul>

        {health.total > 0 && (
          <div className="rounded border border-border bg-panel p-3">
            <div className="text-xs font-semibold">
              {health.bad === 0
                ? `All ${health.total} inputs on your desk are current.`
                : `${health.bad} of ${health.total} inputs on your desk need attention:`}
            </div>
            {health.bad > 0 && (
              <ul className="mt-1.5 space-y-1 text-[11px] text-muted">
                {health.list.slice(0, 12).map((r) => (
                  <li key={r.table_name}>
                    <span className="font-mono">{r.table_name}</span> —{" "}
                    <span className={r.state === "absent" ? "text-bad" : "text-warn"}>
                      {r.state === "absent" ? "not installed" : r.state === "empty" ? "never filled" : "stale"}
                    </span>
                    . {r.plain_english}{" "}
                    {(FILLED_BY[r.table_name] ?? []).length > 0 && (
                      <>Run <span className="text-text">{FILLED_BY[r.table_name][0].name}</span>.</>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Section>

      {/* ================================================================ */}
      <Section id="setup" title="Setting it up, in order">
        <ol className="space-y-2">
          <Step n={1} title="Run the SQL">
            In the Supabase SQL editor, run the files in <code>sql/</code> in the order listed in{" "}
            <code>sql/INSTALL_ORDER.txt</code>. They are all safe to run again, so if you lose your
            place, start over. If a page is red afterwards, run{" "}
            <code>sql/ad4_98_ui_health.sql</code> — it lists every missing table, the file that
            creates it, and the pages it breaks.
          </Step>
          <Step n={2} title="Set the keys">
            The web app needs <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>. GitHub Actions needs{" "}
            <code>SUPABASE_URL</code> and <code>SUPABASE_SERVICE_KEY</code> as repository secrets.
            See the safety section below — the two keys are not interchangeable.
          </Step>
          <Step n={3} title="Import the workflows">
            Import each file in <code>n8n/</code> into n8n. Open its Config node and fill in your
            Supabase URL and the <b>service</b> key. Activate it, copy its Production webhook URL,
            and paste it on the{" "}
            <Link href="/workflows" className="text-accent hover:underline">Workflows</Link> page so
            the app can run it.
          </Step>
          <Step n={4} title="Collect for a while">
            Nothing can be learned from a day that has not finished. Let the collectors run for a
            couple of weeks. The{" "}
            <Link href="/synthesis" className="text-accent hover:underline">Synthesis</Link> page
            shows each finding filling up toward the point where it is worth believing.
          </Step>
          <Step n={5} title="Then learn">
            Press <b>Learn from what has settled</b> on Synthesis, or run the four learning Actions
            in order yourself. Only after that do the probabilities mean anything.
          </Step>
        </ol>
        <Callout tone="warn" title="If a workflow says permission denied">
          <code>permission denied for table … (42501)</code> means the database has not granted
          write access to the key the workflow is using. Run{" "}
          <code>sql/ad4_38_grants.sql</code> — it grants the write path back and then prints one row
          per table saying whether the service key can write it.
        </Callout>
      </Section>

      {/* ================================================================ */}
      <Section id="safety" title="Keys, and the one rule about them">
        <P>
          Supabase gives you two keys and they are not interchangeable. Mixing them up is the single
          most expensive mistake available here.
        </P>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border border-good/40 bg-good/5 p-3">
            <div className="text-xs font-semibold text-good">The anon key — public</div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">
              Can read, cannot write. It is meant to be in the browser, which is why it goes in{" "}
              <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>. Anyone who opens the site can see it, and
              that is fine.
            </p>
          </div>
          <div className="rounded border border-bad/40 bg-bad/5 p-3">
            <div className="text-xs font-semibold text-bad">The service key — secret</div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">
              Can do anything, including delete everything. It belongs in exactly two places: the
              GitHub secret <code>SUPABASE_SERVICE_KEY</code>, and the Config node inside n8n.
            </p>
          </div>
        </div>
        <Callout tone="bad" title="Never put the service key in a NEXT_PUBLIC_ variable">
          Anything starting <code>NEXT_PUBLIC_</code> is compiled into the JavaScript the browser
          downloads. Putting the service key there publishes it to everyone who opens the site.
          There is no way to un-publish it — the key has to be rotated. The app refuses to send a
          request at all if it detects a secret key in the browser, and says so at the top of every
          page.
        </Callout>
        <P>
          The same rule covers the GitHub token used by the Relearn workflow: it lives in n8n, and
          the button on Synthesis calls n8n rather than GitHub. A page that could start a build
          would be a page carrying a credential.
        </P>
      </Section>

      {/* ================================================================ */}
      <Section id="words" title="Words used here">
        <dl className="grid gap-2 sm:grid-cols-2">
          {GLOSSARY.map((g) => (
            <div key={g.term} className="rounded border border-border bg-panel p-2.5">
              <dt className="text-xs font-semibold">{g.term}</dt>
              <dd className="mt-0.5 text-[11px] leading-relaxed text-muted">{g.meaning}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}

/* -------------------------------------------------------------- content -- */

const PAGES = [
  { href: "/", label: "Overview", when: "start here", what: "The desk in one screen: what is happening now, what needs attention, and whether the machinery is running." },
  { href: "/board", label: "Board", when: "the working view", what: "Every live bucket in every city, with the desk's probability next to the market's price. This is the trading screen." },
  { href: "/opportunities", label: "Opportunities", when: "when you want the short list", what: "Only the buckets worth looking at, ranked, with what a real order would actually fill at rather than the headline price." },
  { href: "/predictive", label: "Predictive", when: "before you trust a number", what: "Was the forecast right, and did being right pay? Those are different questions. Actual against predicted, error by how far ahead the call was, and whether edges scale." },
  { href: "/synthesis", label: "Synthesis", when: "to see what the desk has learned", what: "What the archive has established, in sentences, each with the evidence behind it — and whether the learning chain is up to date. The Learn button lives here." },
  { href: "/strategies", label: "Strategies", when: "to switch a rule on or off", what: "Every trading rule, what it is waiting for, its record, and a switch. All ship off." },
  { href: "/clusters", label: "City Clusters", when: "before sizing up", what: "Which cities move together. Ten positions across cities under one weather system is not ten bets." },
  { href: "/globe", label: "Globe", when: "for the shape of the day", what: "The cities under real daylight. Which are inside their peak window right now, and which whole regions are hot together." },
  { href: "/live", label: "Live Weather", when: "during the afternoon", what: "What each city is doing right now against what it normally does, and how much climb is usually left at this hour." },
  { href: "/monitor", label: "City Monitor", when: "when watching one city", what: "One city in detail: today's path, its forecast, its buckets, its history." },
  { href: "/analytics", label: "Analytics", when: "to check the model itself", what: "Is the model honest? When it says 70%, does it happen 70% of the time — and where does it go wrong." },
  { href: "/databank", label: "Data Bank", when: "to see what has been kept", what: "Everything the desk has collected and frozen, and how much of it there is." },
  { href: "/backtest", label: "Backtest", when: "to test a rule on the past", what: "Run a strategy over history. It now tells you whether the window has data in it before you spend twenty minutes finding out." },
  { href: "/campaigns", label: "Campaigns", when: "to run a rule on part of the map", what: "One strategy, pointed at some cities, for a window, with its own P&L — and a live view of what it would take right now." },
  { href: "/goals", label: "Goals", when: "when you have a target", what: "Work backwards from an amount you want to make to the trades that would get there, sized so the venue will actually accept them." },
  { href: "/workflows", label: "Workflows", when: "when something has stopped", what: "Every job, when it last ran, what it wrote, and a button to run it now." },
];

const GLOSSARY = [
  { term: "Bucket (band)", meaning: "One temperature range you can bet on, like 72–73°F. A market is a row of them and exactly one wins." },
  { term: "Edge", meaning: "The gap between what the desk thinks a bucket is worth and what it costs. 5 points means the desk says 45% and the market charges 40¢." },
  { term: "Net edge", meaning: "The same gap after fees and after the price moves against you as you buy. This is the only one that means anything." },
  { term: "The book", meaning: "The list of prices people are actually offering. A big order eats through it and pays worse than the headline price." },
  { term: "Depth", meaning: "How much money you can put in before the price moves against you by a given amount. Thin markets have almost none." },
  { term: "Peak window", meaning: "The hours around local solar afternoon when the day's maximum is made. Before it, the day is undecided; after it, it is done." },
  { term: "Sigma (σ)", meaning: "How unusual today is for that city, in its own terms. +2σ is a genuinely hot day for that city, whether that is Chicago or Beirut." },
  { term: "Bias", meaning: "A forecast being wrong in the same direction every time. Fixable — you subtract it. Different from error, which is being wrong in random directions." },
  { term: "Calibration", meaning: "Whether a stated probability is honest. If everything called 70% happens about 70% of the time, the model is calibrated." },
  { term: "Persistence", meaning: "Guessing today will be like yesterday. It is free and surprisingly good, so it is the bar any model has to beat." },
  { term: "Settled", meaning: "The day is over, the market has paid out, and the result is written down. Nothing can be learned from a day until it settles." },
  { term: "Paper trade", meaning: "A trade written down but not placed. Everything here is paper." },
];

function JOBS(byTable: Map<string, { age_hours: number | null }>) {
  const grouped = new Map<string, { name: string; kind: string; cadence: string; tables: string[] }>();
  for (const [table, fillers] of Object.entries(FILLED_BY)) {
    for (const f of fillers) {
      const cur = grouped.get(f.file) ?? { name: f.name, kind: f.kind, cadence: f.cadence, tables: [] };
      cur.tables.push(table);
      grouped.set(f.file, cur);
    }
  }
  return Array.from(grouped.entries())
    .map(([file, g]) => {
      // The oldest write across everything it fills: a job that fills five
      // tables and stopped writing one of them has stopped.
      const ages = g.tables
        .map((t) => byTable.get(t)?.age_hours)
        .filter((a): a is number => typeof a === "number");
      return { file, ...g, tables: g.tables.sort(), oldest: ages.length ? Math.max(...ages) : null };
    })
    .sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "action" ? -1 : 1));
}

/* ------------------------------------------------------------ building -- */

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-4 space-y-3">
      <h2 className="border-b border-border pb-1 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function P({ children }: { children: React.ReactNode }) {
  return <p className="text-xs leading-relaxed text-muted">{children}</p>;
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-[10px] tabular-nums text-muted">
        {n}
      </span>
      <span className="text-xs leading-relaxed text-muted">
        <b className="text-text">{title}. </b>
        {children}
      </span>
    </li>
  );
}

function Callout({
  tone,
  title,
  children,
}: {
  tone: "good" | "warn" | "bad";
  title: string;
  children: React.ReactNode;
}) {
  const border = tone === "good" ? "border-good/40" : tone === "warn" ? "border-warn/40" : "border-bad/40";
  const bg = tone === "good" ? "bg-good/5" : tone === "warn" ? "bg-warn/5" : "bg-bad/5";
  const text = tone === "good" ? "text-good" : tone === "warn" ? "text-warn" : "text-bad";
  return (
    <div className={`rounded border ${border} ${bg} p-3`}>
      <div className={`text-xs font-semibold ${text}`}>{title}</div>
      <div className="mt-1 text-[11px] leading-relaxed text-muted">{children}</div>
    </div>
  );
}
