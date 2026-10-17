const assert = require('node:assert/strict');
const { cityInstant, cityDate, fmtCityHour } = require('../.route-test/lib/time.js');

assert.equal(cityInstant('2026-10-17', 12, 'Asia/Riyadh').toISOString(), '2026-10-17T09:00:00.000Z');
assert.equal(cityInstant('2026-10-17', 17, 'Europe/Madrid').toISOString(), '2026-10-17T15:00:00.000Z');
assert.equal(cityInstant('2026-10-17', 18, 'America/Los_Angeles').toISOString(), '2026-10-18T01:00:00.000Z');
assert.equal(cityInstant('2026-10-17', 9, 'Asia/Tokyo').toISOString(), '2026-10-17T00:00:00.000Z');
assert.equal(cityInstant('2026-10-17', 13.5, 'Asia/Kolkata').toISOString(), '2026-10-17T08:00:00.000Z');
assert.equal(cityInstant('2026-10-31', 9, 'America/New_York').toISOString(), '2026-10-31T13:00:00.000Z');
assert.equal(cityInstant('2026-11-01', 9, 'America/New_York').toISOString(), '2026-11-01T14:00:00.000Z');
assert.equal(cityDate(new Date('2026-10-17T22:00Z'), 'Asia/Riyadh'), '2026-10-18');
assert.match(fmtCityHour(18, 'America/Los_Angeles', 'Asia/Beirut', '2026-10-17'), /18 Oct,? 04:00 your time/);
assert.match(fmtCityHour(12, 'Asia/Riyadh', 'Asia/Beirut', '2026-10-17'), /12:00 your time/);
assert.equal(cityInstant('2026-03-08', 2.5, 'America/New_York'), null, 'nonexistent DST hour is not shifted silently');
console.log('City-local dates, half-hour zones, DST and viewer-day rollover passed');
