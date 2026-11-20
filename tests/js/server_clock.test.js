// Regression tests for the SERVER CLOCK (2026-11-20, Phase 1.4 of the SA20 readiness plan): a lock is decided by the server's clock, so the
// countdown, the "locked" state and the red last hour must follow it and not the phone's (a phone set 2 hours wrong used to show the wrong lock).
// Loads the REAL IplClock out of Static/ipl_glue.js and the REAL lock functions out of templates/index.html.
//   node tests/js/server_clock.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const GLUE = fs.readFileSync(path.join(ROOT, 'Static', 'ipl_glue.js'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'templates', 'index.html'), 'utf8');

// Source of one top-level `function name(...) {...}` in the page script, found by brace matching.
function fnSource(name) {
  const at = HTML.indexOf('function ' + name + '(');
  assert.ok(at >= 0, 'function ' + name + ' not found in templates/index.html');
  let i = HTML.indexOf('{', at), depth = 0, q = null;
  for (; i < HTML.length; i++) {
    const c = HTML[i];
    if (q) { if (c === '\\') i++; else if (c === q) q = null; continue; }
    if (c === '"' || c === "'") { q = c; continue; }
    if (c === '{') depth++; else if (c === '}' && --depth === 0) return HTML.slice(at, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
}

// The glue script in a sandbox that never touches the network (document.readyState "loading" keeps its _init from running).
function loadGlue(skewMs = 0, perf) {
  // skewMs: how far the sandbox's "phone" clock is from the true time (positive = the phone is ahead); perf: a fake performance clock (ms) to age readings
  class PhoneDate extends Date { static now() { return Date.now() + skewMs; } }
  const RealDate = Date;
  PhoneDate.now = () => RealDate.now() + skewMs;
  const c = { console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, Promise, JSON, Date: PhoneDate, Math, URL, encodeURIComponent };
  if (perf) c.performance = perf;
  c.window = c;
  c.document = { readyState: 'loading', addEventListener() {}, hidden: false, getElementById: () => null };
  c.navigator = { onLine: true }; c.addEventListener = () => {}; c.dispatchEvent = () => {}; c.CustomEvent = function () {};
  c.fetch = () => new Promise(() => {}); c.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  vm.createContext(c);
  vm.runInContext(GLUE, c, { filename: 'ipl_glue.js' });
  return c;
}

let failed = 0;
function t(name, fn) { try { fn(); console.log('PASS  ' + name); } catch (e) { failed++; console.log('FAIL  ' + name + '\n      ' + String(e.message).split('\n')[0]); } }
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, (msg || '') + ' expected ' + b + ' +- ' + tol + ', got ' + a);
const iso = (ms) => new Date(ms).toISOString();

// ---- IplClock ----
t('before the server has answered, the clock is simply the phone clock', () => {
  const { IplClock } = loadGlue();
  assert.strictEqual(IplClock.isSynced(), false); assert.strictEqual(IplClock.offsetMs(), 0);
  near(IplClock.now(), Date.now(), 50);
});

t('a phone that is two hours AHEAD of the server is corrected', () => {
  const { IplClock } = loadGlue(2 * 3600e3);                       // the sandbox phone thinks it is 2 h later than it is
  near(IplClock.now(), Date.now() + 2 * 3600e3, 60, 'uncorrected, the clock is 2 h out');
  const phone = Date.now() + 2 * 3600e3;
  assert.ok(IplClock.sync(iso(Date.now()), phone - 200, phone + 200));   // the server stamped its (true) time half-way through a 400 ms round trip
  near(IplClock.offsetMs(), -(2 * 3600e3), 5, 'offset');
  near(IplClock.now(), Date.now(), 60, 'corrected time');
  assert.strictEqual(IplClock.isSynced(), true);
});

t('a phone that is ten minutes BEHIND the server is corrected', () => {
  const { IplClock } = loadGlue(-10 * 60e3);
  const phone = Date.now() - 10 * 60e3;
  IplClock.sync(iso(Date.now()), phone - 100, phone + 100);
  near(IplClock.offsetMs(), 10 * 60e3, 5, 'offset');
  near(IplClock.now(), Date.now(), 60, 'corrected time');
});

t('the half-way point of the round trip is used', () => {
  const { IplClock } = loadGlue();
  IplClock.sync('2027-01-18T14:00:00.000Z', 1000, 3000);            // sent at 1000, answered at 3000: the server stamped about 2000
  assert.strictEqual(IplClock.offsetMs(), Date.parse('2027-01-18T14:00:00.000Z') - 2000);
});

t('a slow round trip (a sleeping server waking up) says nothing about the time and is ignored', () => {
  const { IplClock } = loadGlue();
  IplClock.sync(iso(Date.now()), Date.now() - 100, Date.now() + 100);   // a good sync first
  const before = IplClock.offsetMs();
  assert.strictEqual(IplClock.sync(iso(Date.now() - 3600e3), 0, 40000), false);   // a 40 s round trip with a very different time
  assert.strictEqual(IplClock.offsetMs(), before);
});

t('nonsense input changes nothing', () => {
  const { IplClock } = loadGlue();
  for (const bad of [undefined, null, '', 'not a date', {}, NaN]) assert.strictEqual(IplClock.sync(bad, 1, 2), false, String(bad));
  assert.strictEqual(IplClock.isSynced(), false); assert.strictEqual(IplClock.offsetMs(), 0);
  assert.strictEqual(IplClock.sync(iso(Date.now()), 5000, 1000), false, 'a reply that arrived before the request left');
});

t('without timings the server time is taken as of now', () => {
  const { IplClock } = loadGlue();
  assert.ok(IplClock.sync(iso(Date.now() + 90e3)));
  near(IplClock.now(), Date.now() + 90e3, 80);
});

// With both server stamps (when the request ARRIVED and when the reply LEFT) the time the server spent on the request cancels out, as in NTP.
// True time axis: the request leaves at A, reaches the server at A+up, the server works for P, the reply reaches the phone at A+up+P+down.
// The phone's clock reads true time + skew.
function trip(skew, up, P, down, A = Date.now()) {
  return { sentAt: A + skew, recvIso: iso(A + up), nowIso: iso(A + up + P), receivedAt: A + up + P + down + skew };
}

t('with both server stamps, the time the server spends on a request does not matter', () => {
  const { IplClock } = loadGlue(2 * 3600e3);
  const x = trip(2 * 3600e3, 100, 3000, 100);                         // the phone is 2 h fast; the network is quick; the server takes 3 s (a heavy /api/state)
  assert.ok(IplClock.sync(x.nowIso, x.sentAt, x.receivedAt, x.recvIso));
  near(IplClock.offsetMs(), -2 * 3600e3, 2, 'offset with both stamps');
  const { IplClock: old } = loadGlue(2 * 3600e3);
  old.sync(x.nowIso, x.sentAt, x.receivedAt);                          // the one-stamp half-way guess for the same trip
  assert.ok(Math.abs(old.offsetMs() + 2 * 3600e3) > 1000, 'the half-way guess is out by about half the server time: ' + (old.offsetMs() + 2 * 3600e3) + ' ms');
});

t('an uneven network is wrong by at most half the unevenness', () => {
  const { IplClock } = loadGlue();
  const x = trip(0, 300, 500, 100);                                    // 300 ms up, 100 ms down
  IplClock.sync(x.nowIso, x.sentAt, x.receivedAt, x.recvIso);
  near(IplClock.offsetMs(), 0, 101, 'offset');
});

t('a slow NETWORK is ignored, a slow SERVER is not', () => {
  const { IplClock } = loadGlue();
  const slowNet = trip(0, 3000, 200, 3000);
  assert.strictEqual(IplClock.sync(slowNet.nowIso, slowNet.sentAt, slowNet.receivedAt, slowNet.recvIso), false, 'a 6 s network trip says nothing');
  const slowServer = trip(0, 80, 30000, 80);                           // the server was busy for 30 s but the network was fast
  assert.strictEqual(IplClock.sync(slowServer.nowIso, slowServer.sentAt, slowServer.receivedAt, slowServer.recvIso), true);
  near(IplClock.offsetMs(), 0, 5);
});

t('a reading from a noisy trip does not replace a better one (the page loads several things at once)', () => {
  const { IplClock } = loadGlue();
  const good = trip(0, 10, 50, 10); assert.ok(IplClock.sync(good.nowIso, good.sentAt, good.receivedAt, good.recvIso));
  const noisy = trip(0, 1900, 50, 100);                                // held back 1.9 s before it was sent: the reading would be about 900 ms out
  assert.strictEqual(IplClock.sync(noisy.nowIso, noisy.sentAt, noisy.receivedAt, noisy.recvIso), false);
  near(IplClock.offsetMs(), 0, 15, 'the good reading stays');
});

t('a quick reading always replaces the previous one (the phone clock was corrected, or its time zone changed)', () => {
  const { IplClock } = loadGlue();
  const first = trip(0, 10, 50, 10); IplClock.sync(first.nowIso, first.sentAt, first.receivedAt, first.recvIso);
  const later = trip(5000, 10, 50, 10);                                // now the phone is 5 s ahead
  assert.ok(IplClock.sync(later.nowIso, later.sentAt, later.receivedAt, later.recvIso));
  near(IplClock.offsetMs(), -5000, 15, 'the newer quick reading wins');
});

t('with nothing better on record a noisy reading is accepted', () => {
  const { IplClock } = loadGlue();
  const noisy = trip(0, 1900, 50, 100);
  assert.ok(IplClock.sync(noisy.nowIso, noisy.sentAt, noisy.receivedAt, noisy.recvIso));
  assert.strictEqual(IplClock.isSynced(), true);
});

t('a worse reading replaces an old better one, but not a recent one', () => {
  const perf = { v: 0, now() { return this.v; } };
  const { IplClock } = loadGlue(0, perf);
  const good = trip(0, 10, 50, 10); IplClock.sync(good.nowIso, good.sentAt, good.receivedAt, good.recvIso);
  const noisy = () => trip(0, 1900, 50, 100);
  perf.v = 5 * 60e3; let n = noisy(); assert.strictEqual(IplClock.sync(n.nowIso, n.sentAt, n.receivedAt, n.recvIso), false, 'after 5 minutes the good reading still stands');
  perf.v = 11 * 60e3; n = noisy(); assert.strictEqual(IplClock.sync(n.nowIso, n.sentAt, n.receivedAt, n.recvIso), true, 'after 11 minutes it is stale');
});

t('an arrival stamp that makes no sense falls back to the half-way guess', () => {
  const { IplClock } = loadGlue();
  const x = trip(0, 100, 100, 100);
  assert.ok(IplClock.sync(x.nowIso, x.sentAt, x.receivedAt, 'garbage'));
  assert.ok(IplClock.sync(x.nowIso, x.sentAt, x.receivedAt, iso(Date.parse(x.nowIso) + 5000)), 'an arrival stamp after the reply stamp is ignored');
  near(IplClock.offsetMs(), 0, 120);
});

t('the glue puts the clock on window and feeds it, with both server stamps, from /api/poll and /api/state', () => {
  assert.ok(/window\.IplClock\s*=\s*IplClock/.test(GLUE), 'IplClock is not exported');
  assert.ok(/IplClock\.sync\(poll\.server_now_utc,\s*t0,\s*Date\.now\(\),\s*poll\.server_recv_utc\)/.test(GLUE), '/api/poll does not feed the clock');
  assert.ok(/IplClock\.sync\(j\.server_now_utc,\s*t0,\s*Date\.now\(\),\s*j\.server_recv_utc\)/.test(GLUE), '/api/state (getState) does not feed the clock');
  assert.ok(/IplClock\.sync\(j\.server_now_utc,t0,Date\.now\(\),j\.server_recv_utc\)/.test(HTML), 'the first /api/state load in the page does not feed the clock');
});

// ---- the lock functions in the page ----
function page(offsetMs) {
  const c = { console, Date, Math, Number, isFinite, parseInt };
  c.window = c; c._lockedWeek = null; c._currentWeek = 3; c._state = { deadline: { status: 'active', next_lock_utc: null } };
  if (offsetMs !== undefined) c.IplClock = { now: () => Date.now() + offsetMs };
  vm.createContext(c);
  for (const n of ['_nowMs', '_deadlineLocked', '_lockUrgent', '_deadlineText', '_lockIn']) vm.runInContext(fnSource(n), c, { filename: n });
  return c;
}

t('with no server time yet the page uses the phone clock (nothing breaks)', () => {
  const c = page();
  c._state.deadline.next_lock_utc = iso(Date.now() + 90 * 60e3);
  assert.strictEqual(c._deadlineLocked(), false); assert.strictEqual(c._lockUrgent(), false); assert.match(c._deadlineText(), /locks in 1h/);
  c._state.deadline.next_lock_utc = iso(Date.now() - 1000);
  assert.strictEqual(c._deadlineLocked(), true);
});

t('a phone that is 2 h FAST: a lock 90 minutes away on the server is NOT locked and not yet urgent', () => {
  const c = page(-2 * 3600e3);                                       // the phone is 2 h ahead, so the true time is 2 h behind the phone clock
  c._state.deadline.next_lock_utc = iso(Date.now() - 2 * 3600e3 + 90 * 60e3 + 30e3);   // 90.5 min away on the server's clock = 29.5 min ago by the phone's (the extra 30 s keeps the minutes stable while the test runs)
  assert.strictEqual(c._deadlineLocked(), false, 'the server says it is still open');
  assert.strictEqual(c._lockUrgent(), false);
  assert.match(c._deadlineText(), /locks in 1h 30m/);
  assert.strictEqual(c._lockIn(), c._lockIn());                     // same text both ways
  assert.match(c._lockIn(), /^1h 30m/);
});

t('a phone that is 2 h FAST: 20 minutes before the lock on the server it is urgent, not locked', () => {
  const c = page(-2 * 3600e3);
  c._state.deadline.next_lock_utc = iso(Date.now() - 2 * 3600e3 + 20 * 60e3);
  assert.strictEqual(c._lockUrgent(), true); assert.strictEqual(c._deadlineLocked(), false);
});

t('a phone that is 2 h SLOW: a lock that has passed on the server is locked, not "locks in 1h 59m"', () => {
  const c = page(2 * 3600e3);                                        // the phone is 2 h behind
  c._state.deadline.next_lock_utc = iso(Date.now() + 2 * 3600e3 - 60e3);   // 1 minute ago on the server's clock
  assert.strictEqual(c._deadlineLocked(), true);
  assert.match(c._deadlineText(), /Locked/);
  assert.strictEqual(c._lockIn(), '');
});

t('the lock functions never read the phone clock directly any more', () => {
  for (const n of ['_deadlineLocked', '_lockUrgent', '_deadlineText', '_lockIn']) assert.ok(!/Date\.now\(\)/.test(fnSource(n)), n + ' still uses Date.now()');
  assert.ok(/IplClock\.now\(\)/.test(fnSource('_nowMs')));
});

if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
console.log('\nall passed');
