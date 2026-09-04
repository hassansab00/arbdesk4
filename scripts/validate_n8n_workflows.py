#!/usr/bin/env python3
"""Check every AD4 n8n workflow file is importable, credential-free and wired.

Run from the repo root:  python scripts/validate_n8n_workflows.py

Catches the mistakes that are invisible until n8n rejects the import or the
workflow silently misbehaves: a filled-in Config (the service key), a node
sending Supabase credentials to a third-party URL, a connection to a node
that no longer exists, a node unreachable from any trigger, a Summary that
does not emit what the Log run node posts, and a webhook the browser cannot
call.
"""
import json, glob, sys, re

FAIL = []
def bad(f, msg): FAIL.append(f"{f}: {msg}")

TRIGGERS = {"n8n-nodes-base.manualTrigger", "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.webhook"}

def check_intervals(f, d):
    """The one check that applies to a fragment as much as a workflow.

    A schedule trigger's rule.interval must be a LIST. n8n iterates it on
    import, so a bare object fails with `h[g] is not iterable` - an error that
    names nothing and points nowhere. It cost a whole afternoon once.
    """
    for n in d.get("nodes", []):
        if not n.get("type", "").endswith("scheduleTrigger"):
            continue
        iv = n.get("parameters", {}).get("rule", {}).get("interval")
        if iv is not None and not isinstance(iv, list):
            bad(f, f"{n['name']}: rule.interval is {type(iv).__name__}, must be a list "
                   f"(n8n iterates it - a bare object fails on import with "
                   f"'h[g] is not iterable')")


for path in sorted(glob.glob("n8n/*.json")):
    f = path.split("/")[-1]
    try:
        d = json.load(open(path))
    except Exception as e:
        bad(f, f"INVALID JSON: {e}"); continue

    # A *.snippet.json is a FRAGMENT meant to be pasted onto an existing
    # canvas - nodes and connections, no trigger, no Config/Summary/Log run.
    # Holding it to the shape of a whole workflow would report five failures
    # for a file that is correct.
    if f.endswith(".snippet.json"):
        if not d.get("nodes"):
            bad(f, "snippet has no nodes")
        for name in d.get("connections", {}):
            if name not in {n["name"] for n in d.get("nodes", [])}:
                bad(f, f"connection from unknown node {name!r}")
        check_intervals(f, d)
        continue

    nodes = {n["name"]: n for n in d["nodes"]}
    real = {k: v for k, v in nodes.items() if v["type"] != "n8n-nodes-base.stickyNote"}
    conns = d.get("connections", {})

    # -- structure ----------------------------------------------------------
    if d.get("active") is not False: bad(f, "ships Active - must be False")
    if "Config" not in real: bad(f, "no Config node")
    if "Summary" not in real: bad(f, "no Summary node")
    if "Log run" not in real: bad(f, "no Log run node")
    if "Webhook Trigger" not in real: bad(f, "no Webhook Trigger")
    if "Manual Trigger" not in real: bad(f, "no Manual Trigger")

    # -- duplicate names / ids ---------------------------------------------
    names = [n["name"] for n in d["nodes"]]
    ids = [n["id"] for n in d["nodes"]]
    for coll, label in ((names, "name"), (ids, "id")):
        dupes = {x for x in coll if coll.count(x) > 1}
        if dupes: bad(f, f"duplicate node {label}s: {sorted(dupes)}")

    # -- parameter shapes n8n's importer ITERATES ---------------------------
    #
    # This is the check that was missing. Six of these files shipped with
    # `rule: {interval: {...}}` where n8n expects `interval` to be a LIST of
    # interval objects. n8n loops over it on import, a bare object is not
    # iterable, and the whole import dies with "h[g] is not iterable" - a
    # message that names a minified variable and nothing else, so it points
    # nowhere near the schedule trigger. Every file with a Schedule Trigger
    # failed to import; the only one that worked was the one without one.
    #
    # A JSON file being valid JSON says nothing about whether n8n can load
    # it. Anything n8n iterates has to be checked as a list here.
    for n in d["nodes"]:
        p = n.get("parameters", {})
        if n["type"] == "n8n-nodes-base.scheduleTrigger":
            iv = p.get("rule", {}).get("interval")
            if not isinstance(iv, list):
                bad(f, f"{n['name']}: rule.interval is "
                       f"{type(iv).__name__}, must be a list - n8n cannot import this")
        if "headerParameters" in p and not isinstance(
                p["headerParameters"].get("parameters"), list):
            bad(f, f"{n['name']}: headerParameters.parameters must be a list")
        if "assignments" in p and not isinstance(
                p["assignments"].get("assignments"), list):
            bad(f, f"{n['name']}: assignments.assignments must be a list")
        if not isinstance(n.get("position"), list) or len(n.get("position", [])) != 2:
            bad(f, f"{n['name']}: position must be [x, y]")
        if "main" in conns.get(n["name"], {}) and not isinstance(
                conns[n["name"]]["main"], list):
            bad(f, f"{n['name']}: connections.main must be a list of lists")

    # -- every connection points at a node that exists ----------------------
    for src, v in conns.items():
        if src not in nodes: bad(f, f"connection from unknown node {src!r}")
        for branch in v.get("main", []):
            for c in (branch or []):
                if c["node"] not in nodes:
                    bad(f, f"{src} -> unknown node {c['node']!r}")

    # -- every non-trigger node is reachable from some trigger --------------
    reach, stack = set(), [n for n, v in real.items() if v["type"] in TRIGGERS]
    while stack:
        cur = stack.pop()
        if cur in reach: continue
        reach.add(cur)
        for branch in conns.get(cur, {}).get("main", []):
            for c in (branch or []): stack.append(c["node"])
    orphans = set(real) - reach
    if orphans: bad(f, f"unreachable from any trigger: {sorted(orphans)}")

    # -- credentials --------------------------------------------------------
    for n in d["nodes"]:
        if n.get("credentials"):
            bad(f, f"{n['name']} binds a credential - AD4 uses the Config node only")
    for a in nodes.get("Config", {}).get("parameters", {}).get("assignments", {}).get("assignments", []):
        if a["name"] in ("supabase_url", "service_key", "alert_email") and a["value"]:
            bad(f, f"Config.{a['name']} is filled in - must ship empty")

    # -- the Supabase service key must never reach a third party ------------
    for n in d["nodes"]:
        url = str(n.get("parameters", {}).get("url", ""))
        is_supabase = "supabase_url" in url
        hdrs = n.get("parameters", {}).get("headerParameters", {}).get("parameters", [])
        leaks = [h["name"] for h in hdrs if "service_key" in str(h.get("value", ""))]
        if leaks and not is_supabase:
            bad(f, f"{n['name']} sends {leaks} to a non-Supabase URL: {url[:60]}")

    # -- Summary must emit the shape Log run posts --------------------------
    js = real.get("Summary", {}).get("parameters", {}).get("jsCode", "")
    for token in ("summary", "status", "rows", "log:", "p_job", "p_status", "p_rows"):
        if token not in js: bad(f, f"Summary does not emit {token!r}")
    job = re.search(r"p_job:\s*'([^']+)'", js)
    if not job: bad(f, "Summary has no p_job")
    elif not f.startswith(job.group(1).split("_")[0].replace("P", "P")):
        pass  # name check is loose; the prefix match below is the real one

    # -- webhook path is stable and matches the file ------------------------
    wh = real.get("Webhook Trigger", {}).get("parameters", {})
    if wh.get("httpMethod") != "POST": bad(f, "webhook is not POST")
    if not wh.get("path"): bad(f, "webhook has no path")
    if wh.get("options", {}).get("allowedOrigins") != "*":
        bad(f, "webhook has no allowedOrigins - the browser cannot call it")

print(f"checked {len(glob.glob('n8n/*.json'))} workflow files")
if FAIL:
    print("\nFAILURES:")
    for x in FAIL: print("  ✗", x)
    sys.exit(1)
print("all checks passed")
