#!/usr/bin/env python3
"""Bind the AD4 workflow templates to a real n8n instance's credentials.

WHY THIS EXISTS

The templates in this folder carry credential references shaped like

    "credentials": {"supabaseApi": {"id": "ad4-supabase", "name": "AD4 Supabase"}}

`ad4-supabase` is a placeholder. It is deliberately not a real id, because a
real one is instance-specific and committing it would tie this repo to one
n8n account. But n8n resolves a credential by ID FIRST and falls back to the
name only when the id is absent - so importing the templates as they stand
leaves 59 nodes across 12 workflows showing an unset credential, to be
re-selected by hand one node at a time.

This script rewrites the placeholder ids to the real ones, producing
*.import.json next to each template. The templates themselves are never
modified, so the repo stays portable and this stays repeatable.

NO SECRET EVER PASSES THROUGH HERE. A credential ID is a reference, not a
key: it names a credential that lives encrypted inside n8n. The Supabase
service key, the SMTP password and everything else stay where they belong.
Never add a field here that carries an actual secret value.

USAGE

    python bind_credentials.py --supabase-id <id> [--smtp-id <id>]

Get the ids from the n8n API or MCP (list_credentials), or from the URL when
you open the credential in the n8n UI:

    .../home/credentials/<this-part-is-the-id>
"""
import argparse
import glob
import json
import os
import re
import sys

PLACEHOLDERS = {
    "supabaseApi": "ad4-supabase",
    "smtp": "ad4-smtp",
    "worker": "ad4-paper-worker",
    "paper_webhook": "ad4-paper-webhook",
}

# A credential id is a reference. If something that looks like a KEY is passed
# in, refuse - it would end up written into a file, and files get committed.
SECRET_SHAPED = re.compile(r"^(eyJ|sbp_|sb_secret_|ghp_|github_pat_)")


def bind(paths, ids, out_suffix=".import.json"):
    total_nodes = 0
    written = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            wf = json.load(fh)

        touched = 0
        unbound = []
        for node in wf.get("nodes", []):
            for cred_type, ref in (node.get("credentials") or {}).items():
                if not isinstance(ref, dict):
                    continue
                reference = {'AD4 Paper Worker':'worker', 'AD4 Paper Webhook':'paper_webhook'}.get(ref.get('name'),cred_type)
                new_id = ids.get(reference)
                if not new_id:
                    # No id supplied for this type. Drop the placeholder rather
                    # than leaving it: a MISSING id makes n8n fall back to
                    # matching on name, which at least has a chance of
                    # resolving. A WRONG id resolves to nothing, silently.
                    if ref.get("id") in PLACEHOLDERS.values():
                        ref.pop("id", None)
                        unbound.append((node.get("name"), cred_type))
                    continue
                ref["id"] = new_id
                touched += 1

        out = path.replace(".template.json", out_suffix)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(wf, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

        total_nodes += touched
        written.append(out)
        note = ""
        if unbound:
            kinds = sorted({c for _, c in unbound})
            note = f"   ({len(unbound)} node(s) left to match by NAME: {', '.join(kinds)})"
        print(f"  {os.path.basename(out):<44} {touched:2} node(s) bound{note}")
    return total_nodes, written


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--supabase-id", help="id of the 'AD4 Supabase' credential in n8n")
    ap.add_argument("--smtp-id", help="id of the 'AD4 SMTP' credential in n8n")
    ap.add_argument("--worker-id", help="id of the 'AD4 Paper Worker' HTTP Header Auth credential")
    ap.add_argument("--paper-webhook-id", help="id of the separate 'AD4 Paper Webhook' HTTP Header Auth credential")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args()

    ids = {}
    for flag, cred_type in (("supabase_id", "supabaseApi"), ("smtp_id", "smtp"), ("worker_id","worker"), ("paper_webhook_id","paper_webhook")):
        val = getattr(args, flag)
        if not val:
            continue
        if SECRET_SHAPED.match(val):
            sys.exit(f"refusing --{flag.replace('_','-')}: that looks like a KEY, not an id. "
                     "This script takes credential IDs (references), never secret values.")
        ids[cred_type] = val

    if not ids:
        sys.exit("nothing to bind - pass at least --supabase-id "
                 "(get it from n8n: list_credentials, or the credential's URL)")

    paths = sorted(glob.glob(os.path.join(args.dir, "*.template.json")))
    if not paths:
        sys.exit(f"no *.template.json found in {args.dir}")

    print(f"binding {len(paths)} workflow(s):")
    n, written = bind(paths, ids)
    print(f"\n{n} credential reference(s) bound across {len(written)} file(s).")
    print("Import the *.import.json files. The *.template.json files are unchanged.")


if __name__ == "__main__":
    main()
