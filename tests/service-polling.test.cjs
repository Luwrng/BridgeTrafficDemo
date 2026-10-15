const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../wwwroot/js/service-polling.js'), 'utf8'), context);
const Polling = context.window.ServicePolling;

test('unavailable service retries after 5/10/20/30 seconds and stays capped', () => {
  let now = 0;
  const policy = new Polling(2000, () => now);
  for (const delay of [5000, 10000, 20000, 30000, 30000]) {
    assert.equal(policy.begin(), true);
    policy.finish(true);
    now += delay - 1;
    assert.equal(policy.begin(), false);
    now++;
  }
  assert.equal(policy.begin(), true);
});
test('prevents concurrent requests even for manual refresh', () => {
  const policy = new Polling(2000, () => 0);
  assert.equal(policy.begin(), true);
  assert.equal(policy.begin(), false);
  assert.equal(policy.begin(true), false);
});
test('manual refresh bypasses retry delay without affecting normal timers', () => {
  const policy = new Polling(8000, () => 0);
  policy.begin(); policy.finish(true);
  assert.equal(policy.begin(), false);
  assert.equal(policy.begin(true), true);
});
test('recovery resets failure count and resumes the configured interval', () => {
  let now = 0;
  const policy = new Polling(8000, () => now);
  policy.begin(); policy.finish(true);
  now = 5000; policy.begin(); policy.finish(false);
  now = 12999; assert.equal(policy.begin(), false);
  now++; assert.equal(policy.begin(), true);
  policy.finish(true);
  assert.equal(policy.nextAt, now + 5000);
});
