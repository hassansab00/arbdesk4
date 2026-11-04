const assert = require('node:assert/strict');
const { test } = require('node:test');
const { startVisiblePolling } = require('../.route-test/lib/visiblePolling.js');
const turn = () => new Promise(resolve => setImmediate(resolve));

function environment(t, visibility = 'visible') {
  const original = { document: global.document, setInterval, clearInterval, now: Date.now };
  const page = new EventTarget(); page.visibilityState = visibility;
  global.document = page;
  let time = 1000, timerId = 0;
  const timers = new Map();
  global.setInterval = callback => { timers.set(++timerId, callback); return timerId; };
  global.clearInterval = id => timers.delete(id);
  Date.now = () => time;
  t.after(() => {
    if (original.document === undefined) delete global.document; else global.document = original.document;
    global.setInterval = original.setInterval; global.clearInterval = original.clearInterval; Date.now = original.now;
  });
  return {
    tick(ms = 100) { time += ms; for (const callback of [...timers.values()]) callback(); },
    visible(state) { page.visibilityState = state; page.dispatchEvent(new Event('visibilitychange')); },
    timers,
  };
}

test('background polls send no requests; overdue visible page refreshes once', async t => {
  const env = environment(t); let requests = 0;
  const stop = startVisiblePolling(async () => { requests++; }, 100);
  await turn(); assert.equal(requests, 1);
  env.visible('hidden'); for (let i = 0; i < 50; i++) { env.tick(); await turn(); }
  assert.equal(requests, 1);
  env.visible('visible'); await turn(); assert.equal(requests, 2);
  env.visible('visible'); await turn(); assert.equal(requests, 2, 'visibility changes within the interval do not refetch');
  stop(); assert.equal(env.timers.size, 0);
  env.tick(); env.visible('visible'); await turn(); assert.equal(requests, 2);
});

test('a hidden initial mount waits; a one-shot read is never repeated on tab return', async t => {
  const env = environment(t, 'hidden'); let requests = 0;
  const stop = startVisiblePolling(async () => { requests++; });
  await turn(); assert.equal(requests, 0);
  env.visible('visible'); await turn(); assert.equal(requests, 1);
  env.visible('hidden'); env.tick(100000); env.visible('visible'); await turn();
  assert.equal(requests, 1); stop();
});

test('slow or rejected refreshes do not overlap or permanently stop polling', async t => {
  const env = environment(t); let requests = 0, reject;
  const stop = startVisiblePolling(() => { requests++; return new Promise((_, no) => { reject = no; }); }, 100);
  await turn(); env.tick(); env.tick(); await turn(); assert.equal(requests, 1);
  reject(new Error('Synthetic failure already reported by caller')); await turn();
  env.tick(); await turn(); assert.equal(requests, 2);
  stop(); reject(new Error('Synthetic cleanup')); await turn();
});

test('fresh shared snapshots are reused and unmount cancels queued initial work', async t => {
  const env = environment(t); let requests = 0;
  const stop = startVisiblePolling(async () => { requests++; }, 100, false);
  await turn(); env.visible('visible'); await turn(); assert.equal(requests, 0);
  env.tick(); await turn(); assert.equal(requests, 1); stop();
  const cancel = startVisiblePolling(async () => { requests++; }, 100); cancel();
  await turn(); assert.equal(requests, 1);
});
