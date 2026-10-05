// Record the documents a child Node process reads (WXPredict build A.2).
// tests/conftest.py adds `--require` of this file to the NODE_OPTIONS its
// tests' children inherit. Reads and listings under docs/ (or a root *.md) of
// AD4_CI_ROOT are appended to AD4_CI_CHILD_READS when the process exits.
'use strict';
const OUT = process.env.AD4_CI_CHILD_READS;
const ROOT = process.env.AD4_CI_ROOT;
if (OUT && ROOT) {
  const fs = require('fs');
  const path = require('path');
  const seen = new Set();
  const rel = (p) => {
    try {
      const a = path.resolve(String(p));
      if (!a.startsWith(ROOT + path.sep)) return null;
      const r = path.relative(ROOT, a).split(path.sep).join('/');
      return (r === 'docs' || r.startsWith('docs/') || (!r.includes('/') && r.endsWith('.md'))) ? r : null;
    } catch (e) { return null; }
  };
  const wrap = (obj, name, kind) => {
    const orig = obj[name];
    if (typeof orig !== 'function') return;
    obj[name] = function (p, ...rest) { const r = rel(p); if (r) seen.add(`${kind}\t${r}`); return orig.call(this, p, ...rest); };
  };
  for (const n of ['readFileSync', 'openSync', 'createReadStream', 'readFile', 'open']) wrap(fs, n, 'read');
  for (const n of ['readdirSync', 'readdir', 'opendirSync']) wrap(fs, n, 'list');
  for (const n of ['readFile', 'open']) wrap(fs.promises, n, 'read');
  wrap(fs.promises, 'readdir', 'list');
  process.on('exit', () => {
    if (seen.size) fs.appendFileSync(OUT, [...seen].sort().map((s) => s + '\n').join(''));
  });
}
