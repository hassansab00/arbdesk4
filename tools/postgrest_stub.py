#!/usr/bin/env python3
"""A PostgREST stand-in, so the web app can be looked at without Supabase.

NOT a PostgREST implementation and not meant to be one. It speaks the subset
the AD4 frontend actually uses - select, order, limit, the eq/gte/lte/is/in
filters, and RPC POSTs - against a local Postgres. Enough to render every page
with real data and catch a component that throws on a real row shape, which is
the class of bug no unit test here can reach.

Usage:  python3 tools/postgrest_stub.py [--port 54321] [--db ad4full]
"""
import argparse
import datetime as dt
import decimal
import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg2
import psycopg2.extras

ARGS = None

# name -> (sql operator, wraps value)
OPS = {
    "eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    "like": "like", "ilike": "ilike",
}


def jsonable(v):
    """Match PostgREST's own JSON, which is what the app is written against.

    numeric arrives as a JSON NUMBER from PostgREST; psycopg hands back a
    Decimal, and serialising that with str() gives a string. Every `.toFixed()`
    in the app then throws - a bug that exists only in the stub, and one that
    would send someone hunting through chart code for it.
    """
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return bytes(v).decode("utf8", "replace")
    return str(v)


def connect():
    return psycopg2.connect(host="127.0.0.1", port=ARGS.pg_port, user="postgres",
                            password="postgres", dbname=ARGS.db)


def build_select(relation, qs):
    """Translate one PostgREST query string into SQL plus parameters."""
    cols = qs.get("select", ["*"])[0]
    # Embedded resources (`bands(...)`) are not supported; take the plain
    # columns and let the page show nulls rather than 500.
    cols = re.sub(r"\w+\([^)]*\)", "", cols).strip(",") or "*"
    cols = ",".join(f'"{c.strip()}"' for c in cols.split(",") if c.strip() and c.strip() != "*") or "*"

    where, params = [], []
    for key, values in qs.items():
        if key in ("select", "order", "limit", "offset"):
            continue
        for raw in values:
            if "." not in raw:
                continue
            op, _, val = raw.partition(".")
            if op == "is":
                where.append(f'"{key}" is {"null" if val == "null" else "not null"}')
            elif op == "in":
                items = [v for v in val.strip("()").split(",") if v]
                if not items:
                    where.append("false")
                else:
                    # ::text on both sides: PostgREST infers the column type
                    # and casts each value, which is more than this needs to
                    # do - a uuid column against a text[] is otherwise a hard
                    # error rather than a filter.
                    where.append(f'"{key}"::text = any(%s)')
                    params.append(items)
            elif op == "not":
                # not.is.null
                sub, _, subval = val.partition(".")
                if sub == "is":
                    where.append(f'"{key}" is {"not null" if subval == "null" else "null"}')
            elif op in OPS:
                where.append(f'"{key}" {OPS[op]} %s')
                params.append(val)

    sql = f'select {cols} from public."{relation}"'
    if where:
        sql += " where " + " and ".join(where)
    if "order" in qs:
        parts = []
        for term in qs["order"][0].split(","):
            col, _, rest = term.partition(".")
            direction = "desc" if "desc" in rest else "asc"
            nulls = " nulls last" if "nullslast" in rest else ""
            parts.append(f'"{col}" {direction}{nulls}')
        sql += " order by " + ", ".join(parts)
    sql += f' limit {int(qs.get("limit", ["1000"])[0])}'
    return sql, params


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, extra=None):
        payload = json.dumps(body, default=jsonable).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,HEAD,POST,PATCH,OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Range")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_HEAD(self):
        """supabase-js asks for counts with HEAD, and a HEAD with no CORS
        header fails the whole request in the browser."""
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"/rest/v1/([a-zA-Z0-9_]+)$", parsed.path)
        n = 0
        if m:
            qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            sql, params = build_select(m.group(1), qs)
            try:
                with connect() as conn, conn.cursor() as cur:
                    cur.execute(f"select count(*) from ({sql}) t", params)
                    n = cur.fetchone()[0]
            except psycopg2.Error:
                n = 0
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "0")
        self.send_header("Content-Range", f"0-{max(0, n - 1)}/{n}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Range")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"/rest/v1/([a-zA-Z0-9_]+)$", parsed.path)
        if not m:
            return self._send(404, {"message": f"no route {parsed.path}"})
        relation = m.group(1)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        sql, params = build_select(relation, qs)
        try:
            with connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            return self._send(200, rows)
        except psycopg2.Error as e:
            # Same shape PostgREST returns, so the app's error handling - which
            # reads code/message/hint - is exercised rather than bypassed.
            print(f"400 {relation}?{parsed.query}\n    {e}", flush=True)
            return self._send(400 if e.pgcode else 500, {
                "code": e.pgcode, "message": str(e.pgerror or e).strip(),
                "details": None, "hint": None,
            })

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or "{}") if length else {}
        m = re.match(r"/rest/v1/rpc/([a-zA-Z0-9_]+)$", parsed.path)
        if not m:
            return self._send(201, [])          # writes are accepted and dropped
        fn = m.group(1)
        names = list(body.keys())
        args = ", ".join(f"{n} => %s" for n in names)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in body.values()]
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(f"select public.{fn}({args})", vals)
                out = cur.fetchone()
            return self._send(200, out[0] if out else None)
        except psycopg2.Error as e:
            return self._send(400, {"code": e.pgcode, "message": str(e.pgerror or e).strip(),
                                    "details": None, "hint": None})

    do_PATCH = do_POST


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=54321)
    ap.add_argument("--pg-port", type=int, default=5442)
    ap.add_argument("--db", default="ad4full")
    ARGS = ap.parse_args()
    print(f"stub on :{ARGS.port} -> postgres:{ARGS.pg_port}/{ARGS.db}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()
