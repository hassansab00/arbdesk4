#!/usr/bin/env python3
"""Verify a streamed ArbDesk NDJSON export without loading it into memory."""
import argparse
import hashlib
import json


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def verify(path):
    digest = hashlib.sha256()
    row_count = 0
    header = None
    footer = None
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            kind = record.get("type") if isinstance(record, dict) else None
            if kind == "arbdesk_export_header":
                if header is not None or row_count or footer is not None:
                    raise ValueError(f"line {line_number}: header is not first")
                header = record
            elif kind == "row":
                if header is None or footer is not None:
                    raise ValueError(f"line {line_number}: row outside header/manifest")
                encoded = canonical_bytes(record)
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
                row_count += 1
            elif kind == "arbdesk_export_manifest":
                if header is None or footer is not None:
                    raise ValueError(f"line {line_number}: invalid manifest position")
                footer = record
            else:
                raise ValueError(f"line {line_number}: unknown record type")

    if header is None or footer is None:
        raise ValueError("export must contain one header and one manifest")
    if footer.get("complete") is not True:
        raise ValueError("export manifest is not complete")
    if footer.get("row_count") != row_count or footer.get("expected_rows") != row_count:
        raise ValueError("export row counts do not match")
    actual = digest.hexdigest()
    if footer.get("sha256") != actual:
        raise ValueError("export SHA-256 does not match its rows")
    if header.get("dataset") != footer.get("dataset"):
        raise ValueError("header and manifest datasets do not match")
    return {"dataset": header["dataset"], "row_count": row_count, "sha256": actual}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    args = parser.parse_args()
    result = verify(args.file)
    print(f"VERIFIED {result['dataset']}: {result['row_count']} rows {result['sha256']}")


if __name__ == "__main__":
    main()
