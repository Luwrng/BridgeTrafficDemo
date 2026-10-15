const test = require('node:test');
const assert = require('node:assert/strict');
const timestamp = require('../wwwroot/js/record-editor-time.js');

test('editing notes retains original camera milliseconds even when native input omits zero seconds', () => {
  assert.equal(timestamp('2026-10-15T09:30','2026-10-15T02:30:00.123+00:00'),'2026-10-15T02:30:00.123+00:00');
});
test('an explicit time change uses UTC+7 regardless of machine timezone', () => {
  assert.equal(timestamp('2026-10-15T09:31:05','2026-10-15T02:30:00.123+00:00'),'2026-10-15T02:31:05.000Z');
});
test('clearing an unknown timestamp remains null and malformed input is rejected', () => {
  assert.equal(timestamp('',null),null);
  assert.throws(() => timestamp('not-a-date',null),RangeError);
});
