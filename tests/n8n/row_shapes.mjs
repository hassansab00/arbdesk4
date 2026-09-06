import fs from 'fs'; import vm from 'vm';
const [, , file, plan] = process.argv;
const wf = JSON.parse(fs.readFileSync(file, 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const PLAN = JSON.parse(fs.readFileSync(plan, 'utf8'));
const outputs = {};
const wrap = (v) => (Array.isArray(v) ? v.map((j) => ({ json: j })) : [{ json: v }]);
for (const [name, seed] of Object.entries(PLAN.seed)) outputs[name] = wrap(seed);
for (const step of PLAN.run) {
  const node = nodes[step.node]; if (!node) continue;
  const inputItems = outputs[step.input] || [];
  const ctx = { $: (n) => ({ all: () => outputs[n] || [], first: () => (outputs[n] || [{json:{}}])[0] }),
    $input: { all: () => inputItems, first: () => inputItems[0] }, $json: (inputItems[0]||{json:{}}).json,
    $execution: { mode:'manual', id:'s' }, $itemIndex: 0, console: { log(){}, error(){}, warn(){} } };
  try { outputs[step.node] = new vm.Script(`(function(){${node.parameters.jsCode}})()`).runInNewContext(ctx,{timeout:8000}); }
  catch (e) { console.log('{}'); process.exit(0); }
}
// every array of objects, with EVERY distinct key set it contains
const found = {};
for (const [name, items] of Object.entries(outputs)) {
  for (const it of items || []) {
    const j = it && it.json; if (!j || typeof j !== 'object') continue;
    for (const [k, v] of Object.entries(j)) {
      if (Array.isArray(v) && v.length && typeof v[0] === 'object' && v[0] !== null) {
        const shapes = new Map();
        for (const row of v) shapes.set(Object.keys(row).sort().join('|'), Object.keys(row));
        found[`${name}.${k}`] = [...shapes.values()];
      }
    }
  }
}
console.log(JSON.stringify(found));
