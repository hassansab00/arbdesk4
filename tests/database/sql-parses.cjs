// ===========================================================================
// EVERY FILE WE TELL PEOPLE TO INSTALL HAS TO PARSE.
//
// sql/ad4_44_indexes.sql did not, from 7 Sep to 22 Sep 2026. A block creating
// the freshness indexes was pasted INTO the body of the next DO block, between
// its `begin` and its loop, so the second `$ad4$` closed the first one early:
//
//     syntax error at or near "declare"
//
// Anyone installing it in the order sql/INSTALL_ORDER.txt gives got neither
// the freshness indexes nor the ANALYZE after them, and nothing said so -
// tests/test_sql_order.py checks that the file is LISTED, and every other
// test reads SQL as text. Text assertions pass on a file Postgres rejects.
//
// WHAT THIS PROVES, AND WHAT IT DOES NOT. Postgres parses a whole
// multi-statement string before it executes any of it, so a syntax error
// anywhere in a file is reported first. Each file is sent on its own to an
// empty database, which means most of them then stop on a table another file
// creates - that is expected and ignored. What fails here is only SQLSTATE
// 42601, a syntax error: the file could not have run anywhere. A PL/pgSQL
// body is compiled when it executes, so an error INSIDE a DO block that sits
// after the first missing table is beyond this check.
// ===========================================================================
const { PGlite } = require('@electric-sql/pglite');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const SQL = path.join(__dirname, '..', '..', 'sql');

function installOrder() {
  return fs.readFileSync(path.join(SQL, 'INSTALL_ORDER.txt'), 'utf-8')
    .split('\n')
    .map((l) => l.replace(/#.*/, '').trim())
    .filter((l) => l.endsWith('.sql'));
}

(async () => {
  const files = installOrder();
  assert.ok(files.length > 50, `INSTALL_ORDER.txt lists only ${files.length} files - is it being read?`);

  const db = new PGlite();
  const broken = [];
  for (const f of files) {
    const sql = fs.readFileSync(path.join(SQL, f), 'utf-8');
    try {
      await db.exec(sql);
    } catch (e) {
      if (e.code === '42601') broken.push(`${f}: ${String(e.message).split('\n')[0]}`);
    }
  }
  await db.close();

  assert.deepEqual(broken, [],
    'these files do not parse, so installing them in order stops at them:\n  ' + broken.join('\n  '));
  console.log(`sql-parses: all ${files.length} files in INSTALL_ORDER.txt parse`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
