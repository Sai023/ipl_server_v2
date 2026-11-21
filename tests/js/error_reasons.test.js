// Regression tests for how a failed request becomes a sentence for the member (Ui.errorMessage / Ui.errorFor / Ui.errorReason in Static/ui.js).
// The server tags many errors with a machine `reason`; the page owns the wording for the ones it has a sentence for and otherwise shows the
// server's own readable text, then falls back to the HTTP status. Loads the REAL Static/ui.js.
//   node tests/js/error_reasons.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const UI_SRC = fs.readFileSync(process.env.UI_JS_PATH || path.join(ROOT, 'Static', 'ui.js'), 'utf8');

const c = { console, setTimeout, clearTimeout, navigator: { onLine: true }, addEventListener() {}, removeEventListener() {} };
c.window = c;
c.document = { readyState: 'complete', addEventListener() {}, getElementById: () => null, createElement: () => ({ style: {}, appendChild() {}, setAttribute() {} }), body: { contains: () => false, appendChild() {} } };
vm.createContext(c);
vm.runInContext(UI_SRC, c, { filename: 'ui.js' });
const Ui = c.Ui;

let failed = 0;
function t(name, fn) { try { fn(); console.log('PASS  ' + name); } catch (e) { failed++; console.log('FAIL  ' + name + '\n      ' + String(e.message).split('\n')[0]); } }
const fail = (status, serverData, extra) => { const e = new Error('HTTP ' + status); e.status = status; if (serverData) { e.serverData = serverData; e.serverMessage = serverData.error || ''; } return Object.assign(e, extra || {}); };

t('a reason we have a sentence for beats the server text', () => {
  assert.strictEqual(Ui.errorMessage(fail(429, { error: 'Too many requests', code: 429, reason: 'rate_limited' })), 'Too many tries. Wait a minute and try again.');
  assert.strictEqual(Ui.errorMessage(fail(401, { error: 'invalid or expired token', code: 401, reason: 'session_ended' })), "You've been signed out. Sign in again to continue.");
  assert.strictEqual(Ui.errorMessage(fail(401, { error: 'auth required', code: 401, reason: 'auth_required' })), "You're signed out. Sign in to continue.");
  assert.strictEqual(Ui.errorMessage(fail(403, { error: 'admin only', code: 403, reason: 'admin_only' })), 'Only an admin can do that.');
  assert.strictEqual(Ui.errorMessage(fail(403, { error: 'you can only modify your own team', reason: 'not_your_team' })), 'You can only change your own team.');
  assert.strictEqual(Ui.errorMessage(fail(409, { error: 'name already registered', reason: 'name_taken' })), 'Someone already has that name. Try another.');
  assert.strictEqual(Ui.errorMessage(fail(401, { error: 'wrong name or passcode', reason: 'bad_credentials' })), 'Wrong name or passcode.');
  assert.ok(/locked/i.test(Ui.errorMessage(fail(423, { error: 'The weekly deadline has passed', locked: true, reason: 'locked' }))));
  assert.ok(/server had a problem/i.test(Ui.errorMessage(fail(500, { error: 'Internal error', reason: 'server_error' }))));
});

t('a reason with no sentence of ours shows the server text as sent', () => {
  for (const r of ['invalid_team', 'name_invalid', 'passcode_invalid', 'credentials_required', 'target_required', 'member_not_found']) {
    assert.strictEqual(Ui.errorMessage(fail(422, { error: 'Choose a captain and a vice-captain before saving', reason: r })), 'Choose a captain and a vice-captain before saving', r);
  }
});

t('an unknown future reason is harmless: the server text is shown', () => {
  assert.strictEqual(Ui.errorMessage(fail(400, { error: 'Something specific', reason: 'brand_new_reason' })), 'Something specific');
  assert.strictEqual(Ui.errorMessage(fail(400, { error: 'x', reason: '__proto__' })), 'x');
  assert.strictEqual(Ui.errorMessage(fail(400, { error: 'x', reason: 'constructor' })), 'x');
});

t('without a reason everything works as before (server text, then the status)', () => {
  assert.strictEqual(Ui.errorMessage(fail(423, { error: 'The weekly deadline has passed' })), 'The weekly deadline has passed');
  const e401 = fail(401); assert.ok(/session has expired/i.test(Ui.errorMessage(e401)));
  assert.ok(/too many requests/i.test(Ui.errorMessage(fail(429))));
  assert.ok(/server had a problem/i.test(Ui.errorMessage(fail(503))));
  assert.ok(/can't reach the server/i.test(Ui.errorMessage(new TypeError('Failed to fetch'))));
  assert.strictEqual(Ui.errorMessage('plain text'), 'plain text');
  assert.strictEqual(Ui.errorMessage(null, 'fallback'), 'fallback');
});

t('the HTTP errors the API now answers as JSON (hardening release) have their own sentences', () => {
  assert.strictEqual(Ui.errorMessage(fail(404, { error: 'Not found', code: 404, reason: 'not_found' })), 'That could not be found.');
  assert.strictEqual(Ui.errorMessage(fail(413, { error: 'That is too large to send', code: 413, reason: 'too_large' })), 'That is too large to send.');
  assert.ok(/not understood/i.test(Ui.errorMessage(fail(400, { error: 'That request was not understood', code: 400, reason: 'bad_request' }))));
  assert.strictEqual(Ui.errorMessage(fail(405, { error: 'Method not allowed', code: 405, reason: 'method_not_allowed' })), 'Method not allowed');   // only code can cause it: shown as sent
});

t('an unexpected server failure shows its short request id, and only a plain id', () => {
  const base = 'The server had a problem. Please try again in a moment.';
  assert.strictEqual(Ui.errorMessage(fail(500, { error: base, ok: false, code: 500, reason: 'server_error', request_id: '3fa9c2d1' })), base + ' (ref 3fa9c2d1)');
  for (const bad of ['', 'abc', 'x'.repeat(17), '<b>hi</b>', '3fa9 c2d1', 7, null, undefined, {}, ['3fa9c2d1']]) {
    assert.strictEqual(Ui.errorMessage(fail(500, { error: base, reason: 'server_error', request_id: bad })), base, 'a request id like ' + JSON.stringify(bad) + ' is never shown');
  }
  assert.strictEqual(Ui.errorMessage(fail(500, { error: 'Something odd', reason: 'brand_new', request_id: 'aabbccdd' })), 'Something odd (ref aabbccdd)');   // it also rides on the server's own text
  assert.strictEqual(Ui.errorRef(null), ''); assert.strictEqual(Ui.errorRef({}), ''); assert.strictEqual(Ui.errorRef({ serverData: { request_id: 'ABCD1234' } }), ' (ref ABCD1234)');
  assert.strictEqual(Ui.errorMessage(fail(500, { error: base, reason: 'server_error' })), base, 'no id sent: nothing added');
});

t('Ui.errorFor and Ui.errorReason', () => {
  assert.strictEqual(Ui.errorFor('locked'), Ui.errorFor('locked', 'x'));
  assert.strictEqual(Ui.errorFor('nope', 'fallback'), 'fallback');
  assert.strictEqual(Ui.errorFor('nope'), '');
  assert.strictEqual(Ui.errorFor('toString'), '');                    // not inherited from Object.prototype
  assert.strictEqual(Ui.errorReason(fail(401, { reason: 'session_ended' })), 'session_ended');
  for (const bad of [null, undefined, 'text', 5, {}, { serverData: null }, { serverData: { reason: 7 } }]) assert.strictEqual(Ui.errorReason(bad), '', String(bad));
});

if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
console.log('\nall passed');
