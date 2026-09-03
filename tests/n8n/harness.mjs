// Run a workflow's Code nodes with mocked n8n globals and print every node's
// output as JSON, so pytest can assert on real behaviour.
//
// The Code nodes are where AD4's n8n workflows do their thinking: unit
// conversion, okta mapping, daily-max derivation, alert change-detection. None
// of that is exercised by importing the JSON into n8n and eyeballing it - and
// two genuine bugs (an embedded PostgREST array compared against a string, so
// every run re-raised the same alert; a partial forecast day written as if it
// were a full one) were caught here rather than in production.
//
// Usage: node harness.mjs <workflow.json> <plan.json>
//   plan.seed  node name -> the output that node would have produced
//   plan.run   [{node, input}] executed in order
import fs from 'fs';
import vm from 'vm';

const [, , file, plan] = process.argv;
const wf = JSON.parse(fs.readFileSync(file, 'utf8'));
const nodes = Object.fromEntries(wf.nodes.map((n) => [n.name, n]));
const PLAN = JSON.parse(fs.readFileSync(plan, 'utf8'));

const outputs = {};
const wrap = (v) => (Array.isArray(v) ? v.map((j) => ({ json: j })) : [{ json: v }]);
for (const [name, seed] of Object.entries(PLAN.seed)) outputs[name] = wrap(seed);

for (const step of PLAN.run) {
  const node = nodes[step.node];
  if (!node) {
    console.log(JSON.stringify({ ok: false, node: step.node, error: 'no such node in workflow' }));
    process.exit(0);
  }
  const inputItems = outputs[step.input] || [];
  const ctx = {
    $: (n) => ({
      all: () => outputs[n] || [],
      first: () => (outputs[n] || [{ json: {} }])[0],
    }),
    $input: { all: () => inputItems, first: () => inputItems[0] },
    $json: (inputItems[0] || { json: {} }).json,
    $execution: { mode: 'manual', id: 'test-exec-1' },
    console: { log() {}, error() {}, warn() {} },
  };
  try {
    const out = new vm.Script(`(function(){${node.parameters.jsCode}})()`)
      .runInNewContext(ctx, { timeout: 5000 });
    outputs[step.node] = out;
  } catch (e) {
    console.log(JSON.stringify({ ok: false, node: step.node, error: String(e.message || e) }));
    process.exit(0);
  }
}

const flat = {};
for (const [k, v] of Object.entries(outputs)) flat[k] = v.map((i) => i.json);
console.log(JSON.stringify({ ok: true, outputs: flat }));
