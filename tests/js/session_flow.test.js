// Regression tests for the sign-in session in the page (2026-11-20, Phase 1.3 of the SA20 readiness plan):
//   * LAUNCH: the saved sign-in is checked with the server, and only a real "no" (401) signs the member out. No network, a slow server or a failing
//     one used to sign them out too (and the check gave up after 8 s), so a valid member could land on the login card during a cold start.
//   * MID-SESSION: a request the server refuses with 401 while the member is signed in ends the session in the open: a blocking "You've been signed
//     out" sheet, the picks kept, and signing in again carries on where they were (it used to be a silent toast on a screen that still looked signed in).
// Loads the REAL Static/ipl_glue.js and the REAL page functions out of templates/index.html (so a change there is what gets tested).
//   node tests/js/session_flow.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const GLUE = fs.readFileSync(path.join(ROOT, 'Static', 'ipl_glue.js'), 'utf8');
const UI = fs.readFileSync(path.join(ROOT, 'Static', 'ui.js'), 'utf8');
const CSS = fs.readFileSync(path.join(ROOT, 'Static', 'ui.css'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'templates', 'index.html'), 'utf8');

// Source of one top-level `function name(...) {...}` in the page script, found by brace matching (quotes always open strings; the functions loaded
// here keep their comments free of lone apostrophes for that reason).
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
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

const tests = [];
const t = (name, fn) => tests.push({ name, fn });

// ====================================================================== the glue: a refused token tells the page
function glue(response, token) {
  const c = { console, Promise, JSON, Date, Math, URL, encodeURIComponent, Object, Error, RegExp, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, events: [], store: new Map() };
  if (token) c.store.set('ipl_session_token', token);
  c.window = c;
  c.document = { readyState: 'loading', addEventListener() {}, hidden: false, getElementById: () => null };
  c.navigator = { onLine: true }; c.addEventListener = () => {};
  c.dispatchEvent = (e) => { c.events.push(e); }; c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.sent = [];
  c.fetch = (url, opts) => { c.sent.push({ url, opts }); return response instanceof Error ? Promise.reject(response) : Promise.resolve({ status: response.status, ok: response.status >= 200 && response.status < 300, headers: { get: () => null }, json: () => Promise.resolve(response.body || {}) }); };
  vm.createContext(c);
  vm.runInContext(GLUE, c, { filename: 'ipl_glue.js' });
  return c;
}
const ended = (c) => c.events.filter((e) => e.type === 'ipl:session-ended');

t('a 401 for a signed-in member (a token was sent) tells the page their session is over', async () => {
  const c = glue({ status: 401, body: { error: 'invalid or expired token', code: 401, reason: 'session_ended' } }, 'tok-1');
  let err; await c.IplApi.whoami().catch((e) => { err = e; });
  assert.strictEqual(err.status, 401); assert.strictEqual(err.serverData.reason, 'session_ended');
  assert.strictEqual(ended(c).length, 1, 'the page was not told');
  assert.strictEqual(c.store.has('ipl_session_token'), false, 'the dead token should be forgotten');
});

t('a wrong passcode at login is a 401 too, but it is not a session ending', async () => {
  const c = glue({ status: 401, body: { error: 'wrong name or passcode', code: 401, reason: 'bad_credentials' } }, 'a-stale-token');
  await c.IplApi.login('Sai', '0000').catch(() => {});
  assert.strictEqual(ended(c).length, 0);
});

t('a refused registration is not a session ending either', async () => {
  const c = glue({ status: 401, body: {} }, 'a-stale-token');
  await c.IplApi.register('Sam', '1234').catch(() => {});
  assert.strictEqual(ended(c).length, 0);
});

t('a 401 when nobody was signed in (no token was sent) is not a session ending', async () => {
  const c = glue({ status: 401, body: { error: 'auth required', reason: 'auth_required' } }, null);
  await c.IplApi.whoami().catch(() => {});
  assert.strictEqual(ended(c).length, 0);
});

t('a server error or a network failure is not a session ending', async () => {
  const c1 = glue({ status: 503, body: {} }, 'tok-1'); await c1.IplApi.whoami().catch(() => {});
  assert.strictEqual(ended(c1).length, 0); assert.strictEqual(c1.store.get('ipl_session_token'), 'tok-1', 'a 503 must not throw the token away');
  const c2 = glue(new TypeError('Failed to fetch'), 'tok-1'); await c2.IplApi.whoami().catch(() => {});
  assert.strictEqual(ended(c2).length, 0); assert.strictEqual(c2.store.get('ipl_session_token'), 'tok-1');
});

// ====================================================================== the page
const FUNCS = ['_rememberAdmin', '_cachedAdmin', '_timeoutAfter', '_whoamiWithin', '_checkSavedSession', '_scheduleVerify', '_recheckSession', '_sessionEnded', '_showSessionEndedSheet', '_reauthDone', '_closePasscodePrompt', '_signinTrouble'];

function page(o = {}) {
  const c = { console, Promise, Error, Math, JSON, events: [], renders: 0, reasons: [], calls: [], sheets: [], closed: 0, toasts: [], timers: [], store: new Map() };
  c.window = c;
  c.setTimeout = (fn, ms) => { const id = c.timers.length + 1; c.timers.push({ id, fn, ms, cleared: false }); return id; };
  c.clearTimeout = (id) => { const x = c.timers.find((y) => y.id === id); if (x) x.cleared = true; };
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.document = { getElementById: () => null };
  Object.assign(c, { _username: null, _isAdmin: false, _mustChange: false, _authPending: false, _sessionLost: false, _reauth: false, _unverified: false, _verifyTimer: null, _verifyTries: 0, _sessionSheet: null, _state: { members: { Sai: {} } } });
  c.render = () => { c.renders++; };
  c.clearUsername = () => { c.calls.push(['clearUsername']); }; c.saveUsername = (n) => { c.calls.push(['saveUsername', n]); };
  c._establishSession = (n, a, m) => { c.calls.push(['establish', n, !!a, !!m]); c._username = n; c._isAdmin = !!a; };
  c._showPasscodePromptModal = (n, note, reauth) => { c.calls.push(['prompt', n, note, !!reauth]); };
  c._showResetPasscodeModal = (f) => { c.calls.push(['resetModal', f]); };
  c.Ui = { sheetBlocking: (spec) => { const s = { spec, close() { c.closed++; } }; c.sheets.push(s); return s; }, success: (m) => { c.toasts.push(m); }, connection: { reason: (name, msg) => { c.reasons.push([name, msg]); } } };
  c.IplApi = { whoami: o.whoami || (() => new Promise(() => {})) };
  vm.createContext(c);
  vm.runInContext(HTML.match(/var _VERIFY_DELAYS=\[[^\]]*\];/)[0], c);
  for (const n of FUNCS) vm.runInContext(fnSource(n), c, { filename: n });
  c.fire = (ms) => { const x = c.timers.find((y) => y.ms === ms && !y.cleared); assert.ok(x, 'no timer set for ' + ms + ' ms (have ' + c.timers.map((y) => y.ms + (y.cleared ? 'x' : '')).join(', ') + ')'); x.cleared = true; x.fn(); };
  return c;
}
const fail = (status, extra) => Object.assign(new Error('HTTP ' + status), status ? { status } : {}, extra || {});
const prompts = (c) => c.calls.filter((x) => x[0] === 'prompt');

// ---- launch ----
t('LAUNCH: a valid saved sign-in is established', async () => {
  const c = page({ whoami: () => Promise.resolve({ ok: true, name: 'Sai', is_admin: true, must_change: false }) });
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.deepStrictEqual(c.calls, [['establish', 'Sai', true, false]]); assert.strictEqual(c._authPending, false);
});

t('LAUNCH: an answer that is not for this member goes to the login card', async () => {
  const c = page({ whoami: () => Promise.resolve({ ok: true, name: 'Moe', is_admin: false }) });
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._username, null); assert.ok(c.calls.some((x) => x[0] === 'clearUsername')); assert.strictEqual(c.renders, 1);
});

t('LAUNCH: a real 401 signs the member out and asks for the passcode of the member who was signed in', async () => {
  const c = page({ whoami: () => Promise.reject(fail(401, { serverData: { reason: 'session_ended' } })) });
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._username, null); assert.strictEqual(c._unverified, false); assert.strictEqual(c._authPending, false);
  assert.ok(c.calls.some((x) => x[0] === 'clearUsername')); assert.strictEqual(c.renders, 1);
  const p = prompts(c); assert.strictEqual(p.length, 1); assert.strictEqual(p[0][1], 'Sai'); assert.match(p[0][2], /session ended/i); assert.strictEqual(p[0][3], false);
});

t('LAUNCH: no network does NOT sign the member out: they stay signed in, the league shows, the check repeats', async () => {
  const c = page({ whoami: () => Promise.reject(new TypeError('Failed to fetch')) });
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._username, 'Sai'); assert.strictEqual(c._unverified, true); assert.strictEqual(c._authPending, false);
  assert.ok(!c.calls.some((x) => x[0] === 'clearUsername'), 'the saved name must be kept'); assert.strictEqual(prompts(c).length, 0, 'no passcode prompt');
  assert.strictEqual(c.renders, 1); assert.ok(c.timers.some((x) => x.ms === 3000 && !x.cleared), 'the first quiet re-check is due in 3 s');
});

t('LAUNCH: a failing server (503) is not an answer either', async () => {
  const c = page({ whoami: () => Promise.reject(fail(503)) });
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._username, 'Sai'); assert.strictEqual(c._unverified, true);
});

t('LAUNCH: a server that never answers is waited for 25 s, then the member stays signed in (it used to give up on the login card at 8 s)', async () => {
  const c = page();                                                 // whoami never settles
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._authPending, true, 'still checking'); assert.strictEqual(c.renders, 0, 'the loader stays up, no login card');
  assert.ok(!c.timers.some((x) => x.ms === 8000), 'the old 8 s give-up is gone');
  c.fire(25000); await flush();
  assert.strictEqual(c._username, 'Sai'); assert.strictEqual(c._unverified, true); assert.strictEqual(c.renders, 1);
});

t('LAUNCH: while unverified the Admin tab follows what this phone last knew', async () => {
  const c = page({ whoami: () => Promise.reject(new TypeError('Failed to fetch')) });
  c.store.set('ipl_is_admin', '1');
  c._authPending = true; c._checkSavedSession('Sai', c._state); await flush();
  assert.strictEqual(c._isAdmin, true);
  const d = page({ whoami: () => Promise.reject(new TypeError('Failed to fetch')) });
  d._authPending = true; d._checkSavedSession('Sai', d._state); await flush();
  assert.strictEqual(d._isAdmin, false, 'nothing known: not shown');
});

// ---- the quiet re-check ----
t('RE-CHECK: the delays are 3 s, 8 s, 20 s, then every minute', () => {
  const c = page(); const seen = [];
  for (let i = 0; i < 6; i++) { c._verifyTries = i; c._scheduleVerify(); seen.push(c.timers[c.timers.length - 1].ms); }
  assert.deepStrictEqual(seen, [3000, 8000, 20000, 60000, 60000, 60000]);
});

t('RE-CHECK: when the server answers the member is confirmed and the banner goes', async () => {
  const c = page({ whoami: () => Promise.resolve({ ok: true, name: 'Sai', is_admin: true, must_change: false }) });
  Object.assign(c, { _username: 'Sai', _unverified: true, _verifyTries: 2 });
  c._recheckSession(); await flush();
  assert.strictEqual(c._unverified, false); assert.strictEqual(c._verifyTries, 0); assert.strictEqual(c._isAdmin, true); assert.strictEqual(c.renders, 1);
  assert.strictEqual(c._sessionLost, false);
});

t('RE-CHECK: still no network means another try later, still signed in', async () => {
  const c = page({ whoami: () => Promise.reject(new TypeError('Failed to fetch')) });
  Object.assign(c, { _username: 'Sai', _unverified: true });
  c._recheckSession(); await flush();
  assert.strictEqual(c._unverified, true); assert.strictEqual(c._sessionLost, false); assert.ok(c.timers.some((x) => x.ms === 3000 && !x.cleared));
});

t('RE-CHECK: a 401 on the re-check ends the session in the open', async () => {
  const c = page({ whoami: () => Promise.reject(fail(401)) });
  Object.assign(c, { _username: 'Sai', _unverified: true });
  c._recheckSession(); await flush();
  assert.strictEqual(c._sessionLost, true); assert.strictEqual(c._unverified, false); assert.strictEqual(c.sheets.length, 1);
});

t('RE-CHECK: it does nothing when the member is not waiting to be confirmed', async () => {
  let asked = 0;
  const c = page({ whoami: () => { asked++; return Promise.resolve({ ok: true, name: 'Sai' }); } });
  Object.assign(c, { _username: 'Sai', _unverified: false });
  c._recheckSession(); await flush();
  assert.strictEqual(asked, 0);
});

// ---- mid-session ----
t('SESSION ENDED: a signed-in member gets the blocking sheet, once, with their picks promised safe', () => {
  const c = page(); c._username = 'Sai';
  c._sessionEnded(); c._sessionEnded();
  assert.strictEqual(c._sessionLost, true); assert.strictEqual(c.sheets.length, 1, 'shown once');
  const s = c.sheets[0].spec; assert.strictEqual(s.title, "You've been signed out"); assert.match(s.message, /picks on this phone are safe/i); assert.strictEqual(s.primary.label, 'Sign in');
  assert.deepStrictEqual(c.reasons, [['signin', '']], 'the "can\'t confirm your sign-in" message is cleared');
});

t('SESSION ENDED: nobody signed in, or the launch check still deciding, shows nothing', () => {
  const a = page(); a._sessionEnded(); assert.strictEqual(a.sheets.length, 0); assert.strictEqual(a._sessionLost, false);
  const b = page(); b._username = 'Sai'; b._authPending = true; b._sessionEnded(); assert.strictEqual(b.sheets.length, 0);
});

t('SESSION ENDED: "Sign in" opens the passcode prompt for the same member, as the follow-up of the sheet', () => {
  const c = page(); c._username = 'Sai'; c._sessionEnded();
  c.sheets[0].spec.primary.onClick();
  const p = prompts(c); assert.strictEqual(p.length, 1); assert.strictEqual(p[0][1], 'Sai'); assert.match(p[0][2], /session ended/i); assert.strictEqual(p[0][3], true, 'reauth');
});

t('SESSION ENDED: dismissing that prompt brings the sheet back (the member is still signed out)', () => {
  const c = page(); c._username = 'Sai'; c._sessionEnded();
  Object.assign(c, { _reauth: true });
  c._closePasscodePrompt();
  assert.strictEqual(c.sheets.length, 2); assert.strictEqual(c._reauth, false);
});

t('SESSION ENDED: closing the prompt after a successful sign-in, or an ordinary prompt, does not bring the sheet back', () => {
  const a = page(); a._username = 'Sai'; a._sessionLost = true; a._reauth = true; a._closePasscodePrompt(true); assert.strictEqual(a.sheets.length, 0);
  const b = page(); b._username = 'Sai'; b._closePasscodePrompt(); assert.strictEqual(b.sheets.length, 0);
});

t('SIGNED IN AGAIN: they carry on where they were, the sheet closes, the picks are untouched', () => {
  const c = page(); Object.assign(c, { _username: 'Sai', _sessionLost: true, _reauth: true, _sessionSheet: { close() { c.closed++; } } });
  c._reauthDone({ ok: true, name: 'Sai', is_admin: true, must_change: false });
  assert.strictEqual(c._sessionLost, false); assert.strictEqual(c._reauth, false); assert.strictEqual(c.closed, 1); assert.strictEqual(c._sessionSheet, null);
  assert.strictEqual(c._isAdmin, true); assert.strictEqual(c.renders, 1); assert.deepStrictEqual(c.calls.filter((x) => x[0] === 'saveUsername'), [['saveUsername', 'Sai']]);
  assert.strictEqual(c.store.get('ipl_is_admin'), '1'); assert.match(c.toasts[0], /picks are as you left them/i);
  assert.ok(!c.calls.some((x) => x[0] === 'establish'), 'this is not a fresh sign-in: the tab and the draft are not reset');
});

t('SIGNED IN AGAIN: a forced passcode change is still asked for', () => {
  const c = page(); Object.assign(c, { _username: 'Sai', _sessionLost: true });
  c._reauthDone({ ok: true, name: 'Sai', is_admin: false, must_change: true });
  assert.ok(c.calls.some((x) => x[0] === 'resetModal' && x[1] === true));
});

// ====================================================================== wiring (the page source)
t('WIRING: the launch check no longer gives up at 8 s or signs the member out on any failure', () => {
  const src = fnSource('_bootstrap');
  assert.ok(/_checkSavedSession\(savedUser,state\)/.test(src), 'the launch uses _checkSavedSession');
  assert.ok(!/_authGuard|8000/.test(src), 'the 8 s give-up is back');
  assert.ok(!/whoami\(\)\.then/.test(src), 'the old inline whoami handling is back');
});

t('WIRING: the page listens for a session ending and for the network coming back, and the banner stays while unverified', () => {
  assert.ok(/addEventListener\("ipl:session-ended",_sessionEnded\)/.test(HTML));
  assert.ok(/addEventListener\("online",function\(\)\{if\(_unverified\)_recheckSession\(\);\}\)/.test(HTML));
  assert.ok(/addEventListener\("ipl:retry",function\(\)\{[^}]*_recheckSession\(\)/.test(HTML), '"Retry now" re-checks the sign-in too');
  const r = fnSource('render'); assert.ok(/_signinTrouble\(_unverified\)/.test(r), 'every redraw restates whether the sign-in could be checked');
});

t('WIRING: signing in establishes a fresh session, which clears every flag of the old one', () => {
  const e = fnSource('_establishSession'); for (const f of ['_sessionLost=false', '_reauth=false', '_unverified=false', 'clearTimeout(_verifyTimer)', '_rememberAdmin(']) assert.ok(e.includes(f), '_establishSession lost ' + f);
  const l = fnSource('logout'); for (const f of ['_sessionLost=false', '_unverified=false', 'ipl_is_admin']) assert.ok(l.includes(f), 'logout lost ' + f);
  const s = fnSource('_submitPasscode'); assert.ok(/_closePasscodePrompt\(true\)/.test(s) && /if\(_sessionLost\)_reauthDone\(d\)/.test(s));
});

t('WIRING: the blocking sheet is an alert dialog that cannot be dismissed, and has its styles', () => {
  const i = UI.indexOf('Ui.sheetBlocking = function'); assert.ok(i > 0, 'Ui.sheetBlocking is missing');
  const body = UI.slice(i, UI.indexOf('/* ---------- accordion state', i));
  assert.ok(body.includes('role="alertdialog"') && body.includes('aria-modal="true"') && body.includes('aria-labelledby'), 'announced as an alert dialog');
  const esc = body.match(/if \(e\.key === "Escape"\)[^\n]*/)[0]; assert.ok(/preventDefault/.test(esc) && !/close\(\)/.test(esc), 'Escape must not close it');
  assert.ok(!/mousedown/.test(body), 'a tap outside must not close it');
  assert.ok(/\.ui-sheet-blocking/.test(CSS) && /\.ui-sheet-actions/.test(CSS), 'styles');
});

(async () => {
  let failed = 0;
  for (const { name, fn } of tests) {
    try { await fn(); console.log('PASS  ' + name); } catch (e) { failed++; console.log('FAIL  ' + name + '\n      ' + String(e.message).split('\n').slice(0, 2).join(' | ')); }
  }
  if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
  console.log('\nall passed');
})();
