const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../wwwroot/js/camera-polling.js'), 'utf8'), context);
const Polling = context.window.CameraStatusPolling;
const cameras = ['A', 'B', 'C', 'E'];
const targets = policy => Array.from(policy.targets());

test('empty cameras are discovered once without repeated idle requests', () => {
  let now = 0;
  const policy = new Polling(cameras, () => now);
  assert.deepEqual(targets(policy), cameras);
  for (const id of cameras) policy.update(id, { configured: false });
  policy.finish(false);
  for (now = 2500; now <= 60_000; now += 2500) assert.deepEqual(targets(policy), []);
});

test('only a configured camera is polled; disconnecting stops its requests', () => {
  let now = 0;
  const policy = new Polling(cameras, () => now);
  for (const id of cameras) policy.update(id, { configured: id === 'A' });
  policy.finish(false);
  assert.deepEqual(targets(policy), []);
  now = 2500;
  assert.deepEqual(targets(policy), ['A']);
  policy.update('A', { configured: false });
  policy.finish(false);
  now = 10_000;
  assert.deepEqual(targets(policy), []);
});

test('opening settings or connecting B can request a fresh check immediately', () => {
  const policy = new Polling(cameras, () => 0);
  policy.finish(false);
  assert.deepEqual(Array.from(policy.targets(['B'])), ['B']);
  assert.deepEqual(Array.from(policy.targets(cameras)), cameras);
  policy.update('B', { configured: true });
  assert.deepEqual(targets(policy), []);
});

test('an offline service without configured cameras does not produce a retry loop', () => {
  let now = 0;
  const policy = new Polling(cameras, () => now);
  policy.finish(true);
  now = 120_000;
  assert.deepEqual(targets(policy), []);
  assert.deepEqual(Array.from(policy.targets(['A'])), ['A']);
});

test('active cameras survive network failures and retries back off then recover', () => {
  let now = 0;
  const policy = new Polling(cameras, () => now);
  policy.update('A', { configured: true });
  for (const delay of [5000, 10_000, 20_000, 30_000, 30_000]) {
    policy.finish(true);
    now += delay - 1;
    assert.deepEqual(targets(policy), []);
    now++;
    assert.deepEqual(targets(policy), ['A']);
  }
  policy.finish(false);
  now += 2500;
  assert.deepEqual(targets(policy), ['A']);
});
