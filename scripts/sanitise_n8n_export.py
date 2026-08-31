#!/usr/bin/env python3
"""
Strip secrets out of an n8n workflow export so it can be committed.

The four P0.x workflows already run in Hassan's n8n and were never captured
in this repo. The scaffolds in n8n/*.scaffold.json are reconstructions - the
REAL ones are better. This makes capturing them safe.

    n8n -> open the workflow -> ... menu -> Download
    python scripts/sanitise_n8n_export.py ~/Downloads/My_Workflow.json n8n/

What it removes:
  * every value in a `Config` Set node (service_key, supabase_url, ...)
  * any bound credential reference (`credentials` on any node)
  * anything anywhere that looks like a Supabase key, a project URL, a
    bearer token or an email address - including inside Code nodes, which
    is where a hardcoded key is easiest to miss

It fails loudly rather than writing a file it is not confident about: if a
secret-shaped string survives the scrub, you get a non-zero exit and the
node it is in, not a quietly-committed key.
"""
import json
import os
import re
import sys

# Deliberately broad. A false positive costs you one manual re-check; a
# false negative commits a credential.
PATTERNS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), "JWT (anon/service_role key)"),
    (re.compile(r"sb_secret_[A-Za-z0-9_-]+"),                                  "Supabase secret key"),
    (re.compile(r"sb_publishable_[A-Za-z0-9_-]+"),                             "Supabase publishable key"),
    (re.compile(r"https://[a-z0-9]{12,}\.supabase\.co"),                        "Supabase project URL"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),             "email address"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}"),                             "bearer token"),
]

# Keys whose value is always config, never structure.
CONFIG_KEYS = {"supabase_url", "service_key", "alert_email", "email_recipient",
               "webhook_url", "api_key", "token", "password", "n8n_base_url"}


def scrub_config_node(node):
    """Blank every assignment value in a Config-style Set node."""
    n = 0
    params = node.get("parameters", {})
    for a in params.get("assignments", {}).get("assignments", []):
        if a.get("value"):
            a["value"] = ""
            n += 1
    # older n8n Set node shape
    for a in params.get("values", {}).get("string", []):
        if a.get("value"):
            a["value"] = ""
            n += 1
    return n


def redact_strings(obj, path="", found=None):
    """Recursively replace secret-shaped strings anywhere in the tree."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        return {k: redact_strings(v, f"{path}.{k}", found) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_strings(v, f"{path}[{i}]", found) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        out = obj
        for rx, label in PATTERNS:
            if rx.search(out):
                # Keep n8n expressions intact - they reference Config, they
                # are not secrets themselves.
                if "$('Config')" in out or "$json" in out:
                    continue
                out = rx.sub(f"<REDACTED {label}>", out)
                found.append((path, label))
        return out
    return obj


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    dest_dir = sys.argv[2] if len(sys.argv) > 2 else "n8n"

    with open(src, encoding="utf-8") as f:
        wf = json.load(f)

    name = wf.get("name", os.path.basename(src))
    print(f"workflow : {name}")
    print(f"nodes    : {len(wf.get('nodes', []))}")

    blanked, creds = 0, 0
    for node in wf.get("nodes", []):
        if node.get("name", "").strip().lower() == "config" or node.get("type", "").endswith(".set"):
            blanked += scrub_config_node(node)
        if node.pop("credentials", None) is not None:
            creds += 1
            print(f"  removed bound credential from node: {node.get('name')}")

    found = []
    wf = redact_strings(wf, found=found)
    for path, label in found:
        print(f"  redacted {label} at {path}")

    # n8n export carries per-instance ids and run metadata; none of it is
    # useful in the repo and some of it is instance-identifying.
    for k in ("id", "versionId", "meta", "pinData", "tags", "staticData",
              "createdAt", "updatedAt", "triggerCount", "shared"):
        wf.pop(k, None)
    wf["active"] = False   # never import straight into an active state

    print(f"\nblanked {blanked} config value(s), removed {creds} bound credential(s), "
          f"redacted {len(found)} secret-shaped string(s)")

    # Final sweep over the serialised output - belt and braces.
    blob = json.dumps(wf, indent=2)
    leaks = []
    for rx, label in PATTERNS:
        for m in rx.finditer(blob):
            if m.group(0).startswith("<REDACTED"):
                continue
            leaks.append((label, m.group(0)[:40]))
    if leaks:
        print("\nREFUSING TO WRITE - secret-shaped strings survived:")
        for label, sample in leaks[:10]:
            print(f"  {label}: {sample}...")
        print("\nRemove these by hand and re-run. Nothing was written.")
        sys.exit(1)

    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    out = os.path.join(dest_dir, f"{slug}.template.json")
    os.makedirs(dest_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(blob + "\n")
    print(f"\nwrote {out}")
    print("Safe to commit. Open it once and confirm the Config node is empty "
          "before you push - this script is a net, not a guarantee.")


if __name__ == "__main__":
    main()
