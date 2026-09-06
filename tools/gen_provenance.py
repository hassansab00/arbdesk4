#!/usr/bin/env python3
"""Regenerate web/lib/provenance.ts - WHAT FILLS EACH TABLE.

sqlOwner.ts answers "which SQL file CREATES this relation", which is what a
missing-relation error needs. It says nothing about the far more common case:
the table exists, has zero rows, and the page shows an empty panel with no
way to tell whether that is a bug, a job that has never run, or simply a
market with nothing in it yet.

Every one of those cases looked identical in the UI, so "the data is
outdated" was impossible to act on. This derives the answer from the repo
rather than restating it in prose that goes stale:

  .github/workflows/*.yml   name, schedule, and which scripts it runs
  scripts/*.py              upsert()/insert() targets, and rpc() calls
  sql/*.sql                 what those RPCs insert into
  n8n/*.template.json       POST/PATCH targets under /rest/v1/

Output: web/lib/provenance.ts, consumed by <Provenance> and <EmptyBox> so an
empty panel names the job that fills it and how often that job runs.

tests/test_ui_contracts.py asserts the file is current.
"""
import glob
import json
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# Tables a job reads to decide what to write are not tables it fills. Without
# this, ingest_log (every job logs to it) claims to be filled by everything.
NEVER_A_PRODUCT = {"ingest_log"}


def _yaml_workflows():
    """name, cron, and the scripts each GitHub Action runs.

    Deliberately regex, not a YAML parser: the only fields needed are three
    lines the file states plainly, and adding a dependency to read them would
    have to be installed everywhere this generator runs.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))):
        src = open(path).read()
        base = os.path.basename(path)
        m = re.search(r"^name:\s*(.+)$", src, re.M)
        name = (m.group(1).strip().strip("'\"") if m else base.replace(".yml", ""))
        crons = re.findall(r"cron:\s*['\"]([^'\"]+)['\"]", src)
        manual = "workflow_dispatch" in src
        scripts = set(re.findall(r"scripts/([a-z0-9_/]+)\.py", src))
        out[base] = {"name": name, "crons": crons, "manual": manual, "scripts": sorted(scripts)}
    return out


def _script_writes():
    """script stem -> {"tables": [...], "rpcs": [...]}"""
    out = {}
    paths = (glob.glob(os.path.join(ROOT, "scripts", "*.py"))
             + glob.glob(os.path.join(ROOT, "scripts", "*", "*.py")))
    for path in sorted(paths):
        src = open(path).read()
        stem = os.path.relpath(path, os.path.join(ROOT, "scripts"))[:-3]
        tables = set()
        # write_rows() is weather_model.py's own POST helper, and capacity.py
        # calls its RPCs through _call_rpc - neither is reached by a pattern
        # anchored on a word boundary before the name.
        for pat in (r"upsert\(\s*[\"']([a-z0-9_]+)[\"']",
                    r"(?<!_)insert\(\s*[\"']([a-z0-9_]+)[\"']",
                    r"write_rows\(\s*[\"']([a-z0-9_]+)[\"']",
                    r"rest/v1/([a-z0-9_]+)[\"'?]"):
            tables.update(m.lower() for m in re.findall(pat, src))
        rpcs = set(m.lower() for m in re.findall(r"rpc\(\s*[\"']([a-z0-9_]+)[\"']", src))
        # A helper in common.py that fans out to a per-city RPC still fills the
        # cache; the call site names the helper, not the function it runs.
        for helper, fn in (("refresh_feature_cache", "refresh_feature_cache"),):
            if re.search(r"\b" + helper + r"\s*\(", src):
                rpcs.add(fn)
        rpcs.update(re.findall(r"rest/v1/rpc/([a-z0-9_]+)", src))
        tables -= {"rpc"} | NEVER_A_PRODUCT
        rpcs -= {"log_ingest"}
        if tables or rpcs:
            out[stem] = {"tables": sorted(tables), "rpcs": sorted(rpcs)}
    return out


def _rpc_writes():
    """SQL function name -> tables it inserts into or refreshes.

    An RPC is how a script asks the database to recompute something, so the
    table it fills is named inside the function body, not at the call site.
    """
    out = {}
    fn_re = re.compile(
        r"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?([a-z0-9_]+)\s*\(", re.I)
    for path in sorted(glob.glob(os.path.join(ROOT, "sql", "*.sql"))):
        src = open(path).read()
        marks = [(m.start(), m.group(1).lower()) for m in fn_re.finditer(src)]
        for i, (pos, fn) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
            body = src[pos:end]
            tables = set(m.lower() for m in re.findall(
                r"insert\s+into\s+(?:public\.)?([a-z0-9_]+)", body, re.I))
            tables |= set(m.lower() for m in re.findall(
                r"refresh\s+materialized\s+view\s+(?:concurrently\s+)?(?:public\.)?([a-z0-9_]+)",
                body, re.I))
            tables -= NEVER_A_PRODUCT
            if tables:
                out.setdefault(fn, set()).update(tables)
    return {k: sorted(v) for k, v in out.items()}


def _n8n_writes(rpcs):
    """workflow file -> tables it POSTs or PATCHes.

    A workflow that calls an RPC to roll up volume fills the rollup table just
    as much as one that POSTs rows into it, so the RPC targets are resolved
    here rather than being invisible.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "n8n", "*.json"))):
        d = json.load(open(path))
        base = os.path.basename(path).replace(".template.json", "").replace(".json", "")
        tables = set()
        for node in d.get("nodes", []):
            p = node.get("parameters", {}) or {}
            if str(p.get("method", "GET")).upper() not in ("POST", "PATCH", "PUT"):
                continue
            url = str(p.get("url", ""))
            m = re.search(r"/rest/v1/rpc/([a-z0-9_]+)", url)
            if m:
                tables.update(rpcs.get(m.group(1).lower(), []))
                continue
            m = re.search(r"/rest/v1/([a-z0-9_]+)", url)
            if m:
                tables.add(m.group(1).lower())
        tables -= NEVER_A_PRODUCT
        if tables:
            out[base] = {"name": d.get("name") or base, "tables": sorted(tables)}
    return out


# The tables the freshness view actually tracks (sql/ad4_39_freshness.sql).
# A view resolves to these and stops; resolving to every table it touches
# would name half the schema for one panel.
def _tracked_tables():
    path = os.path.join(ROOT, "sql", "ad4_39_freshness.sql")
    if not os.path.exists(path):
        return set()
    src = open(path).read()
    body = src[src.find("insert into data_freshness_spec"):]
    body = body[: body.find("on conflict")]
    return set(re.findall(r"\(\s*'([a-z0-9_]+)'\s*,", body))


def _view_sources():
    """view name -> every relation named in its body.

    A panel reads a view; the thing that goes stale is a table underneath it.
    Without this the operator is told a view is empty and left to work out
    which of the nine tables under it stopped being written.
    """
    out = {}
    hdr = re.compile(
        r"create\s+(?:or\s+replace\s+)?(?:materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?"
        r"(?:public\.)?([a-z0-9_]+)\s+as", re.I)
    ref = re.compile(r"\b(?:from|join)\s+(?:public\.)?([a-z_][a-z0-9_]*)", re.I)
    noise = {"select", "lateral", "unnest", "generate_series", "jsonb_array_elements",
             "jsonb_each", "jsonb_to_recordset", "values", "only", "rows"}
    for path in sorted(glob.glob(os.path.join(ROOT, "sql", "*.sql"))):
        src = re.sub(r"--[^\n]*", " ", open(path).read())
        marks = [(m.start(), m.group(1).lower()) for m in hdr.finditer(src)]
        for i, (pos, name) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(src)
            refs = {r.lower() for r in ref.findall(src[pos:end])} - noise - {name}
            if refs:
                out.setdefault(name, set()).update(refs)
    return {k: sorted(v) for k, v in out.items()}


def _resolve(name, views, tracked, seen=None):
    """Follow a view down to the tracked tables it ultimately reads."""
    seen = seen or set()
    if name in seen:
        return []
    seen.add(name)
    if name in tracked:
        return [name]
    out = []
    for src in views.get(name, []):
        for t in _resolve(src, views, tracked, seen):
            if t not in out:
                out.append(t)
    return out


CADENCE_WORDS = [
    (r"^\S+ \S+ \* \* \*$", "daily"),
    (r"^\S+ \S+ \* \* [0-6]", "weekly"),
    (r"^\S+ \*/\d+ ", "several times a day"),
    (r"^\*/\d+ ", "several times an hour"),
    (r"^\S+ \* \* \* \*$", "hourly"),
]


def _cadence(crons, manual):
    if not crons:
        return "only when you run it" if manual else "never - it has no trigger"
    for c in crons:
        for pat, word in CADENCE_WORDS:
            if re.match(pat, c.strip()):
                return word if len(crons) == 1 else f"{word} ({len(crons)} schedules)"
    return f"on a schedule ({crons[0]})"


def build():
    actions = _yaml_workflows()
    scripts = _script_writes()
    rpcs = _rpc_writes()
    n8n = _n8n_writes(rpcs)

    filled = {}

    def add(table, entry):
        if table in NEVER_A_PRODUCT:
            return
        for e in filled.setdefault(table, []):
            if e["file"] == entry["file"] and e["kind"] == entry["kind"]:
                return
        filled[table].append(entry)

    for base, wf in actions.items():
        cadence = _cadence(wf["crons"], wf["manual"])
        for stem in wf["scripts"]:
            w = scripts.get(stem)
            if not w:
                continue
            targets = set(w["tables"])
            for fn in w["rpcs"]:
                targets.update(rpcs.get(fn, []))
            for t in sorted(targets):
                add(t, {"kind": "action", "name": wf["name"], "file": base,
                        "cadence": cadence, "how": f"scripts/{stem}.py"})

    for base, wf in n8n.items():
        for t in wf["tables"]:
            add(t, {"kind": "n8n", "name": wf["name"], "file": f"{base}.template.json",
                    "cadence": "whenever its n8n schedule fires", "how": "n8n"})

    return {k: filled[k] for k in sorted(filled)}


def build_view_map():
    views = _view_sources()
    tracked = _tracked_tables()
    out = {}
    for name in sorted(views):
        base = _resolve(name, views, tracked)
        if base:
            out[name] = base
    return out


def render(filled, view_map):
    return (
        "// GENERATED by tools/gen_provenance.py - do not edit by hand.\n"
        "//\n"
        "// WHAT FILLS EACH TABLE. sqlOwner.ts says which SQL file CREATES a relation,\n"
        "// which is what a missing-relation error needs. This says which job PUTS ROWS\n"
        "// IN IT - so an empty panel can say \"the Databank action fills this, it runs\n"
        "// daily, and it has never run\" instead of showing nothing and leaving the\n"
        "// reader to guess whether that is a bug, a job that never ran, or a quiet day.\n"
        "export type Filler = {\n"
        "  /** action = GitHub Actions, n8n = an imported workflow */\n"
        "  kind: \"action\" | \"n8n\";\n"
        "  /** what it is called where you would go to run it */\n"
        "  name: string;\n"
        "  /** the file, so it can be found without knowing the name */\n"
        "  file: string;\n"
        "  /** in words, not cron */\n"
        "  cadence: string;\n"
        "  /** the script or step that does the writing */\n"
        "  how: string;\n"
        "};\n\n"
        "export const FILLED_BY: Record<string, Filler[]> = "
        + json.dumps(filled, indent=2, sort_keys=True) + ";\n\n"
        "/** Which TRACKED TABLES a view ultimately reads.\n"
        " *\n"
        " * A panel queries a view; the thing that stops being written is a table under\n"
        " * it. Without this map an empty panel could only say \"this view is empty\",\n"
        " * leaving the reader to work out which of the tables beneath it went quiet.\n"
        " */\n"
        "export const VIEW_TABLES: Record<string, string[]> = "
        + json.dumps(view_map, indent=2, sort_keys=True) + ";\n\n"
        "/** The tracked tables behind any relation, view or table alike. */\n"
        "export function tablesBehind(relation: string): string[] {\n"
        "  const r = relation.toLowerCase();\n"
        "  if (VIEW_TABLES[r]) return VIEW_TABLES[r];\n"
        "  return FILLED_BY[r] ? [r] : [];\n"
        "}\n\n"
        "/** Every job that fills this relation, or [] if nothing in the repo does. */\n"
        "export function fillersFor(relation: string): Filler[] {\n"
        "  const direct = FILLED_BY[relation.toLowerCase()];\n"
        "  if (direct) return direct;\n"
        "  const out: Filler[] = [];\n"
        "  for (const t of tablesBehind(relation)) {\n"
        "    for (const f of FILLED_BY[t] ?? []) {\n"
        "      if (!out.some((e) => e.file === f.file && e.kind === f.kind)) out.push(f);\n"
        "    }\n"
        "  }\n"
        "  return out;\n"
        "}\n\n"
        "/** One sentence naming who fills it and how often, or null if nothing does. */\n"
        "export function whoFills(relation: string): string | null {\n"
        "  const f = fillersFor(relation);\n"
        "  if (!f.length) return null;\n"
        "  const where = f[0].kind === \"action\" ? \"the GitHub Action\" : \"the n8n workflow\";\n"
        "  const rest = f.length > 1 ? ` (and ${f.length - 1} more)` : \"\";\n"
        "  return `Filled by ${where} \\u201c${f[0].name}\\u201d, ${f[0].cadence}${rest}.`;\n"
        "}\n")


if __name__ == "__main__":
    filled = build()
    view_map = build_view_map()
    out = os.path.join(ROOT, "web", "lib", "provenance.ts")
    open(out, "w").write(render(filled, view_map))
    print(f"wrote {len(filled)} table(s) and {len(view_map)} view(s) -> web/lib/provenance.ts")
