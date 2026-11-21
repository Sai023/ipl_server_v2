// Regression tests for Google sign-in in the page (Phase 3 of the SA20 readiness plan, 2026-11-21). Loads the REAL Static/auth_google.js and Static/ipl_glue.js and the
// REAL page functions out of templates/index.html. Google itself is a fake (window.google.accounts.id records what the module asks of it) and the DOM part of the
// module is replaced by a view that records what it was asked to show.
//   * THE GLUE: the Google calls go to the right URLs; a Google refusal at /api/auth/link is NOT "your session ended" (it used to be: every 401 was)
//   * THE MODULE: nothing happens until the server says Google is set up; a built-in browser is never sent to Google; the nonce goes to Google AND back with the
//     credential and is never reused; a good Google account nobody has linked is a message, not a sign-in; errors re-arm; the switch is offered once, and never near a lock
//   * THE PAGE: the menu, the sign-in card, the lock window, what a sign-in teaches the module
//   node tests/js/google_signin.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const GLUE = fs.readFileSync(path.join(ROOT, 'Static', 'ipl_glue.js'), 'utf8');
const MODULE = fs.readFileSync(path.join(ROOT, 'Static', 'auth_google.js'), 'utf8');
const HTML = fs.readFileSync(path.join(ROOT, 'templates', 'index.html'), 'utf8');

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
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
const plain = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));   // objects made inside the sandbox are not the same realm as ours: compare them as plain data
const tests = [];
const t = (name, fn) => tests.push({ name, fn });

// ====================================================================== THE GLUE
function glue(response, token) {
  const c = { console, Promise, JSON, Date, Math, URL, encodeURIComponent, Object, Error, RegExp, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, events: [], store: new Map() };
  if (token) c.store.set('ipl_session_token', token);
  c.window = c;
  c.document = { readyState: 'loading', addEventListener() {}, hidden: false, getElementById: () => null };
  c.navigator = { onLine: true }; c.addEventListener = () => {};
  c.dispatchEvent = (e) => { c.events.push(e); }; c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.sent = [];
  c.fetch = (url, opts) => { c.sent.push({ url, opts }); return response instanceof Error ? Promise.reject(response) : Promise.resolve({ status: response.status, ok: response.status >= 200 && response.status < 300, headers: { get: () => null }, json: async () => response.body }); };
  vm.createContext(c);
  vm.runInContext(GLUE, c, { filename: 'ipl_glue.js' });
  return c;
}
const ended = (c) => c.events.filter((e) => e.type === 'ipl:session-ended');

t('GLUE: the Google calls go to the right addresses with the right bodies, and a session token goes along where there is one', async () => {
  const c = glue({ status: 200, body: { ok: true } }, 'tok-1');
  await c.IplApi.authConfig(); await c.IplApi.authNonce(); await c.IplApi.authGoogle('CRED', 'N1'); await c.IplApi.authLink('CRED2', 'N2'); await c.IplApi.authLogout(); await c.IplApi.adminUnlinkGoogle('Moe');
  const by = (u) => c.sent.find((s) => s.url.indexOf(u) === 0);
  assert.ok(by('/api/auth/config') && by('/api/auth/nonce'));
  assert.strictEqual(by('/api/auth/google').opts.method, 'POST'); assert.deepStrictEqual(JSON.parse(by('/api/auth/google').opts.body), { credential: 'CRED', nonce: 'N1' });
  assert.deepStrictEqual(JSON.parse(by('/api/auth/link').opts.body), { credential: 'CRED2', nonce: 'N2' });
  assert.strictEqual(by('/api/auth/link').opts.headers.Authorization, 'Bearer tok-1', 'the link is made by the signed-in member');
  assert.strictEqual(by('/api/auth/logout').opts.method, 'POST'); assert.strictEqual(by('/api/auth/logout').opts.headers.Authorization, 'Bearer tok-1', 'sign-out must say WHICH session to end');
  assert.deepStrictEqual(JSON.parse(by('/api/admin/auth/unlink').opts.body), { target_username: 'Moe' });
});

t('GLUE: a Google sign-in that works stores the session token, like a passcode login', async () => {
  const c = glue({ status: 200, body: { ok: true, linked: true, name: 'Moe', token: 'new-token' } }, null);
  const d = await c.IplApi.authGoogle('CRED', 'N1');
  assert.strictEqual(d.name, 'Moe'); assert.strictEqual(c.store.get('ipl_session_token'), 'new-token');
  const c2 = glue({ status: 200, body: { ok: true, linked: false, email_masked: 'n***@gmail.com' } }, null);
  await c2.IplApi.authGoogle('CRED', 'N1');
  assert.strictEqual(c2.store.has('ipl_session_token'), false, 'an account nobody linked gets no session');
});

t('GLUE: a refused Google check at /api/auth/link does NOT end the session of the member who is signed in', async () => {
  for (const reason of ['token_invalid', 'token_expired', 'nonce_invalid', 'email_unverified']) {
    const c = glue({ status: 401, body: { ok: false, error: 'x', code: 401, reason } }, 'tok-1');
    let err; await c.IplApi.authLink('CRED', 'N1').catch((e) => { err = e; });
    assert.strictEqual(err.serverData.reason, reason);
    assert.strictEqual(ended(c).length, 0, reason + ' was taken for a signed-out member');
    assert.strictEqual(c.store.get('ipl_session_token'), 'tok-1', reason + ' threw the token away');
  }
});

t('GLUE: but a link refused because the SESSION is gone does end it', async () => {
  for (const reason of ['session_ended', 'auth_required', undefined]) {
    const c = glue({ status: 401, body: reason ? { error: 'x', code: 401, reason } : {} }, 'tok-1');
    await c.IplApi.authLink('CRED', 'N1').catch(() => {});
    assert.strictEqual(ended(c).length, 1, String(reason) + ': the page was not told');
    assert.strictEqual(c.store.has('ipl_session_token'), false);
  }
});

t('GLUE: a refused Google SIGN-IN is never "your session ended" (and a stale token is cleared, as for a wrong passcode)', async () => {
  const c = glue({ status: 401, body: { error: 'x', code: 401, reason: 'token_invalid' } }, 'a-stale-token');
  await c.IplApi.authGoogle('CRED', 'N1').catch(() => {});
  assert.strictEqual(ended(c).length, 0); assert.strictEqual(c.store.has('ipl_session_token'), false);
  const c2 = glue({ status: 401, body: { error: 'x', code: 401, reason: 'session_ended' } }, 'a-stale-token');   // even if the server said so: a sign-in attempt has no session to end
  await c2.IplApi.authGoogle('CRED', 'N1').catch(() => {});
  assert.strictEqual(ended(c2).length, 0);
});

t('GLUE: the old rules still hold (any signed-in 401 ends the session; a wrong passcode does not)', async () => {
  const c = glue({ status: 401, body: { error: 'invalid or expired token', code: 401, reason: 'session_ended' } }, 'tok-1');
  await c.IplApi.whoami().catch(() => {});
  assert.strictEqual(ended(c).length, 1); assert.strictEqual(c.store.has('ipl_session_token'), false);
  const c2 = glue({ status: 401, body: { error: 'wrong name or passcode', code: 401, reason: 'bad_credentials' } }, 'a-stale-token');
  await c2.IplApi.login('Sai', '0000').catch(() => {});
  assert.strictEqual(ended(c2).length, 0);
  const c3 = glue({ status: 401, body: { error: 'This account signs in with Google.', code: 401, reason: 'pin_retired' } }, 'a-stale-token');
  let err; await c3.IplApi.login('Moe', '1234').catch((e) => { err = e; });
  assert.strictEqual(ended(c3).length, 0); assert.strictEqual(err.serverData.reason, 'pin_retired'); assert.strictEqual(err.serverMessage, 'This account signs in with Google.');
});

// ====================================================================== THE MODULE
const UA = {
  chromeAndroid: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36',
  webviewAndroid: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36',
  instagram: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 Instagram 350.0.0.38.109 Android',
  facebook: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 [FB_IAB/FB4A;FBAV/460.0.0.0.0]',
  desktop: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
  iphoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
};

function mod(o = {}) {
  const clock = { t: 1000000 };
  const c = { console, Promise, JSON, Math, Object, Error, RegExp, String, Array, store: new Map(), events: [], intervals: [], timeouts: [], scripts: [], sheets: [], calls: [], viewLog: [], clock };
  c.Date = { now: () => clock.t };
  c.window = c;
  c.setInterval = (fn, ms) => { const h = { fn, ms, cleared: false }; c.intervals.push(h); return h; };
  c.clearInterval = (h) => { if (h) h.cleared = true; };
  c.setTimeout = (fn, ms) => { const h = { fn, ms, cleared: false }; c.timeouts.push(h); return h; };
  c.clearTimeout = (h) => { if (h) h.cleared = true; };
  c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.dispatchEvent = (e) => { c.events.push(e); };
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.navigator = { userAgent: o.ua || UA.chromeAndroid };
  c.location = { origin: 'https://ipl.example' };
  c.document = {
    body: { contains: (el) => !(el && el.__gone) },
    head: { appendChild(s) { c.scripts.push(s); if (o.gis === 'blocked') setImmediate(() => s.onerror && s.onerror()); else if (o.gis === 'ok-later') setImmediate(() => { c.google = fakeGoogle(c); s.onload && s.onload(); }); } },
    createElement: (tag) => ({ tag, parentNode: null }),
    getElementById: (id) => (c.byId && c.byId[id]) || null,
  };
  let n = 0;
  c.IplApi = {
    authConfig: () => { c.calls.push('config'); return o.configError ? Promise.reject(o.configError) : Promise.resolve(o.config || { ok: true, google: { enabled: true, client_id: 'CID.apps.googleusercontent.com' }, pin_enabled: true }); },
    authNonce: () => { c.calls.push('nonce'); return o.nonceError ? Promise.reject(o.nonceError) : Promise.resolve({ ok: true, nonce: 'N' + (++n) }); },
    authGoogle: (cred, nn) => { c.calls.push('google:' + cred + ':' + nn); return o.googleReply ? o.googleReply(cred, nn) : Promise.resolve({ ok: true, linked: true, name: 'Moe', token: 'tok', is_admin: false, must_change: false, email_masked: 'm***@gmail.com' }); },
    authLink: (cred, nn) => { c.calls.push('link:' + cred + ':' + nn); return o.linkReply ? o.linkReply(cred, nn) : Promise.resolve({ ok: true, linked: true, name: 'Moe', email_masked: 'm***@gmail.com' }); },
  };
  c.Ui = { errorMessage: (e) => 'MSG:' + (((e && e.serverData) || {}).reason || (e && e.message)), success() {}, info() {} };
  if (o.gis !== 'blocked' && o.gis !== 'ok-later' && o.gis !== 'none') c.google = fakeGoogle(c);
  vm.createContext(c);
  vm.runInContext(MODULE, c, { filename: 'auth_google.js' });
  const rec = {
    loginBlockHtml: () => '<div id="google-signin"></div>',
    renderButton: (host, g) => { c.viewLog.push(['renderButton']); g.renderButton(host, { text: 'continue_with' }); },
    status: (h, text, kind) => c.viewLog.push(['status', text, kind]), busy: (h, on) => c.viewLog.push(['busy', on]),
    notLinked: (h, m) => c.viewLog.push(['notLinked', m]), blocked: (h, app) => c.viewLog.push(['blocked', app]),
    trouble: (h, msg, retry) => { c.viewLog.push(['trouble', msg]); c.retry = retry; },
    sheet: (opts) => { const s = { slot: { id: 'sheet-slot' }, opts, closed: false, finished: null, close() { this.closed = true; }, finish(x) { this.finished = x; }, dismiss() { this.closed = true; if (opts.onCancel) opts.onCancel(); } }; c.sheets.push(s); return s; },
    closeSheets: () => c.sheets.forEach((s) => s.close()),
  };
  c.realView = c.IplGoogle._test.getView();      // the view as shipped (kept for the tests of the markup it draws)
  c.IplGoogle._test.setView(rec);
  return c;
}
function fakeGoogle(c) {
  c.gis = { inits: [], renders: [] };
  return { accounts: { id: { initialize: (cfg) => c.gis.inits.push(cfg), renderButton: (host, opts) => c.gis.renders.push({ host, opts }) } } };
}
const ev = (c, type) => c.events.filter((e) => e.type === type);
const lastInit = (c) => c.gis.inits[c.gis.inits.length - 1];

t('MODULE: embeddedBrowser tells the apps Google refuses from the browsers it allows', () => {
  const c = mod();
  const eb = (ua) => plain(c.IplGoogle.embeddedBrowser(ua));
  assert.deepStrictEqual(eb(UA.instagram), { blocked: true, app: 'Instagram' });
  assert.deepStrictEqual(eb(UA.facebook), { blocked: true, app: 'Facebook' });
  assert.deepStrictEqual(eb(UA.webviewAndroid), { blocked: true, app: 'this app' });   // the old BCL Fantasy app is an Android WebView
  for (const [ua, app] of [['... Line/13.0 ...', 'LINE'], ['... MicroMessenger/8 ...', 'WeChat'], ['... Snapchat/12 ...', 'Snapchat'], ['... TikTok 33 ...', 'TikTok'], ['... Telegram ...', 'Telegram'], ['... LinkedInApp ...', 'LinkedIn'], ['... Pinterest/10 ...', 'Pinterest']]) assert.deepStrictEqual(eb(ua), { blocked: true, app }, app);
  for (const ua of [UA.chromeAndroid, UA.desktop, UA.iphoneSafari, '', undefined]) assert.strictEqual(eb(ua).blocked, false, String(ua));   // (undefined: this very browser, an ordinary Chrome)
  assert.strictEqual(eb(12345).blocked, false, 'not a string: nothing to refuse');
});

t('MODULE: the server config is asked once, shared while in flight, cached, and a failure is not cached', async () => {
  const c = mod();
  const [a, b] = await Promise.all([c.IplGoogle.config(), c.IplGoogle.config()]);
  assert.strictEqual(a, b); assert.deepStrictEqual(c.calls.filter((x) => x === 'config').length, 1, 'two callers at once, one request');
  await c.IplGoogle.config(); assert.strictEqual(c.calls.filter((x) => x === 'config').length, 1, 'cached');
  assert.deepStrictEqual({ enabled: a.enabled, clientId: a.clientId, pinEnabled: a.pinEnabled }, { enabled: true, clientId: 'CID.apps.googleusercontent.com', pinEnabled: true });
  assert.strictEqual(ev(c, 'ipl:google-config').length, 1, 'the page is told once that Google sign-in is available');
  const bad = mod({ configError: new Error('HTTP 500') });
  await bad.IplGoogle.config().catch(() => {}); await bad.IplGoogle.config().catch(() => {});
  assert.strictEqual(bad.calls.filter((x) => x === 'config').length, 2, 'a failed answer is asked again next time');
});

t('MODULE: enabled needs BOTH the flag and the client id; pin_enabled false is carried; a missing pin_enabled means true; disabled fires nothing', async () => {
  const off = mod({ config: { ok: true, google: { enabled: false, client_id: null }, pin_enabled: true } });
  const c1 = await off.IplGoogle.config(); assert.strictEqual(c1.enabled, false); assert.strictEqual(ev(off, 'ipl:google-config').length, 0); assert.strictEqual(off.IplGoogle.enabled(), false);
  const half = mod({ config: { ok: true, google: { enabled: true, client_id: null } } });
  assert.strictEqual((await half.IplGoogle.config()).enabled, false);
  const nopin = mod({ config: { ok: true, google: { enabled: true, client_id: 'X' }, pin_enabled: false } });
  const c3 = await nopin.IplGoogle.config(); assert.strictEqual(c3.pinEnabled, false); assert.strictEqual(nopin.IplGoogle.pinEnabled(), false);
  const none = mod({ config: { ok: true, google: { enabled: true, client_id: 'X' } } });
  assert.strictEqual((await none.IplGoogle.config()).pinEnabled, true);
  assert.strictEqual(mod().IplGoogle.pinEnabled(), true, 'before the answer is known nothing is hidden');
});

t('MODULE: nothing is loaded, fetched or drawn while Google sign-in is not set up', async () => {
  const c = mod({ config: { ok: true, google: { enabled: false, client_id: null }, pin_enabled: true }, gis: 'none' });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.deepStrictEqual(c.calls, ['config']); assert.strictEqual(c.scripts.length, 0); assert.strictEqual(c.viewLog.length, 0);
  assert.strictEqual(c.IplGoogle.loginBlockHtml(), '', 'no block in the sign-in card');
});

t('MODULE: the button is armed with the client id and the nonce the server issued, in that order (config, nonce, then Google)', async () => {
  const c = mod();
  const host = { id: 'host' };
  await c.IplGoogle.mount(host, 'signin');
  assert.deepStrictEqual(c.calls, ['config', 'nonce']);
  assert.strictEqual(c.gis.inits.length, 1);
  assert.strictEqual(lastInit(c).client_id, 'CID.apps.googleusercontent.com'); assert.strictEqual(lastInit(c).nonce, 'N1'); assert.strictEqual(lastInit(c).auto_select, false);
  assert.strictEqual(typeof lastInit(c).callback, 'function');
  assert.strictEqual(c.gis.renders.length, 1); assert.strictEqual(c.gis.renders[0].host, host);
  assert.ok(c.IplGoogle.loginBlockHtml().indexOf('id="google-signin"') >= 0, 'the sign-in card has its slot once Google is available');
});

t('MODULE: a built-in browser is never sent to Google: no library, no nonce, just the "open it in Chrome" notice', async () => {
  const c = mod({ ua: UA.instagram, gis: 'none' });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.deepStrictEqual(c.viewLog, [['blocked', 'Instagram']]); assert.strictEqual(c.scripts.length, 0); assert.deepStrictEqual(c.calls, ['config']);
});

t('MODULE: the library is loaded from Google when the page does not have it yet; a refusal to load says so and offers a retry that works', async () => {
  const c = mod({ gis: 'blocked' });
  const host = { id: 'host' };
  await c.IplGoogle.mount(host, 'signin'); await flush();
  assert.strictEqual(c.scripts.length, 1); assert.strictEqual(c.scripts[0].src, 'https://accounts.google.com/gsi/client');
  const trouble = c.viewLog.find((x) => x[0] === 'trouble');
  assert.ok(trouble && /could not load/i.test(trouble[1]), JSON.stringify(c.viewLog));
  assert.deepStrictEqual(c.calls.filter((x) => x === 'nonce'), [], 'no nonce is spent when Google cannot even load');
  c.google = fakeGoogle(c);                                   // the connection is back
  await c.retry(); await flush();
  assert.strictEqual(c.gis.inits.length, 1, 'the retry armed the button');
});

t('MODULE: a library that never answers times out after 12 s (it does not hang the sign-in card)', async () => {
  const c = mod({ gis: 'none' });
  const p = c.IplGoogle.mount({ id: 'host' }, 'signin');
  await flush();
  const to = c.timeouts.find((x) => x.ms === 12000); assert.ok(to, 'a 12 s timeout was set');
  to.fn(); await p; await flush();
  assert.ok(c.viewLog.some((x) => x[0] === 'trouble' && /could not load/i.test(x[1])));
});

t('MODULE: Google hands over a credential: it goes to the server WITH the very nonce Google was given, and a linked member is signed in', async () => {
  const c = mod();
  const done = []; const host = { id: 'host' };
  await c.IplGoogle.mount(host, 'signin', (d) => done.push(d));
  lastInit(c).callback({ credential: 'CRED-1' });
  await flush();
  assert.ok(c.calls.includes('google:CRED-1:N1'), c.calls.join(' | '));
  const e = ev(c, 'ipl:google-signed-in'); assert.strictEqual(e.length, 1); assert.strictEqual(e[0].detail.name, 'Moe'); assert.strictEqual(e[0].detail.token, 'tok');
  assert.strictEqual(done.length, 1);
  assert.deepStrictEqual(plain(c.IplGoogle.account()), { linked: true, emailMasked: 'm***@gmail.com', pinEnabled: false });
  assert.strictEqual(c.IplGoogle.wasLinked(), true); assert.strictEqual(c.store.get('ipl_linked'), '1');
  assert.deepStrictEqual(c.viewLog.filter((x) => x[0] === 'busy'), [['busy', true], ['busy', false]]);
});

t('MODULE: a nonce is used once: after an attempt the next button gets a NEW one, never the spent one', async () => {
  const c = mod();
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.strictEqual(lastInit(c).nonce, 'N1');
  lastInit(c).callback({ credential: 'CRED-1' }); await flush();                  // a linked sign-in ends the flow: the nonce is spent
  await c.IplGoogle.mount({ id: 'host-2' }, 'signin');
  assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 2, 'a second nonce was asked for');
  assert.strictEqual(lastInit(c).nonce, 'N2', 'the next button must not reuse the nonce the server has already spent');
});

t('MODULE: a good Google account nobody has linked is a message, not a sign-in, and the button is armed again with a new nonce', async () => {
  const c = mod({ googleReply: () => Promise.resolve({ ok: true, linked: false, email_masked: 'n***@gmail.com' }) });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  lastInit(c).callback({ credential: 'CRED-1' }); await flush();
  assert.deepStrictEqual(c.viewLog.filter((x) => x[0] === 'notLinked'), [['notLinked', 'n***@gmail.com']]);
  assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 0); assert.strictEqual(c.IplGoogle.account(), null);
  assert.strictEqual(c.gis.inits.length, 2, 'armed again'); assert.strictEqual(lastInit(c).nonce, 'N2', 'with a nonce that was never used');
  lastInit(c).callback({ credential: 'CRED-2' }); await flush();
  assert.ok(c.calls.includes('google:CRED-2:N2'));
});

t('MODULE: a refusal shows the page sentence for its reason and arms the button again; nothing is signed in', async () => {
  const err = Object.assign(new Error('HTTP 401'), { status: 401, serverData: { reason: 'nonce_invalid', error: 'x', code: 401 }, serverMessage: 'x' });
  const c = mod({ googleReply: () => Promise.reject(err) });
  const done = [];
  await c.IplGoogle.mount({ id: 'host' }, 'signin', (d) => done.push(d));
  lastInit(c).callback({ credential: 'CRED-1' }); await flush();
  assert.deepStrictEqual(c.viewLog.filter((x) => x[0] === 'status' && x[2] === 'error'), [['status', 'MSG:nonce_invalid', 'error']]);
  assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 0); assert.strictEqual(done.length, 0);
  assert.strictEqual(c.gis.inits.length, 2); assert.strictEqual(lastInit(c).nonce, 'N2');
});

t('MODULE: Google sending no credential at all is handled (and the button comes back)', async () => {
  const c = mod();
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  lastInit(c).callback({}); await flush();
  assert.ok(c.viewLog.some((x) => x[0] === 'status' && /did not send/i.test(x[1]) && x[2] === 'error'));
  assert.strictEqual(c.calls.filter((x) => x.indexOf('google:') === 0).length, 0, 'nothing was sent to the server');
  assert.strictEqual(c.gis.inits.length, 2);
});

t('MODULE: the switch (link mode) uses /api/auth/link, announces it, remembers it and forgets the cached config', async () => {
  const c = mod();
  const done = [];
  await c.IplGoogle.mount({ id: 'host' }, 'link', (d) => done.push(d));
  lastInit(c).callback({ credential: 'CRED-9' }); await flush();
  assert.ok(c.calls.includes('link:CRED-9:N1')); assert.ok(!c.calls.some((x) => x.indexOf('google:') === 0), 'a switch never calls the sign-in route');
  assert.strictEqual(ev(c, 'ipl:google-linked').length, 1); assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 0);
  assert.strictEqual(done.length, 1); assert.strictEqual(c.IplGoogle.account().linked, true); assert.strictEqual(c.IplGoogle.wasLinked(), true);
  await c.IplGoogle.config(); assert.strictEqual(c.calls.filter((x) => x === 'config').length, 2, 'whether anyone still has a passcode may have changed: ask again');
});

t('MODULE: a refused switch leaves the member exactly as they were', async () => {
  const err = Object.assign(new Error('HTTP 409'), { status: 409, serverData: { reason: 'google_account_in_use' } });
  const c = mod({ linkReply: () => Promise.reject(err) });
  await c.IplGoogle.mount({ id: 'host' }, 'link');
  lastInit(c).callback({ credential: 'CRED-9' }); await flush();
  assert.ok(c.viewLog.some((x) => x[0] === 'status' && x[1] === 'MSG:google_account_in_use'));
  assert.strictEqual(ev(c, 'ipl:google-linked').length, 0); assert.strictEqual(c.IplGoogle.account(), null); assert.strictEqual(c.IplGoogle.wasLinked(), false);
});

t('MODULE: drawing the sign-in card again (the page redraws often) reuses the unspent nonce: no request per redraw', async () => {
  const c = mod();
  await c.IplGoogle.mount({ id: 'host-a' }, 'signin');
  await c.IplGoogle.mount({ id: 'host-b' }, 'signin');
  await c.IplGoogle.mount({ id: 'host-c' }, 'signin');
  assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 1, 'one nonce for three draws');
  assert.strictEqual(c.gis.renders.length, 3); assert.strictEqual(lastInit(c).nonce, 'N1');
  c.clock.t += 9 * 60 * 1000;                                                    // the server forgets a nonce after 10 minutes: a stale one is never reused
  await c.IplGoogle.mount({ id: 'host-d' }, 'signin');
  assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 2); assert.strictEqual(lastInit(c).nonce, 'N2');
});

t('MODULE: a button left on screen gets a fresh nonce before the old one expires, and the timer stops when the button is gone', async () => {
  const c = mod();
  const host = { id: 'host' };
  await c.IplGoogle.mount(host, 'signin');
  assert.strictEqual(c.intervals.length, 1); const tick = c.intervals[0];
  tick.fn(); await flush(); assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 1, 'too early: nothing happens');
  c.clock.t += 8 * 60 * 1000 + 1;
  tick.fn(); await flush();
  assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 2); assert.strictEqual(lastInit(c).nonce, 'N2'); assert.strictEqual(c.gis.inits.length, 2);
  host.__gone = true; c.clock.t += 9 * 60 * 1000;
  tick.fn(); await flush();
  assert.strictEqual(tick.cleared, true, 'the card is gone: the timer stops'); assert.strictEqual(c.calls.filter((x) => x === 'nonce').length, 2);
});

t('MODULE: a failed nonce request says why instead of leaving a dead card', async () => {
  const err = Object.assign(new Error('HTTP 429'), { status: 429, serverData: { reason: 'rate_limited' } });
  const c = mod({ nonceError: err });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.ok(c.viewLog.some((x) => x[0] === 'trouble' && x[1] === 'MSG:rate_limited'), JSON.stringify(c.viewLog));
  assert.strictEqual(c.gis.inits.length, 0);
});

t('MODULE: a host that left the page while the answers were in flight is never drawn into', async () => {
  const c = mod();
  const host = { id: 'host', __gone: true };
  await c.IplGoogle.mount(host, 'signin');
  assert.strictEqual(c.gis.inits.length, 0); assert.strictEqual(c.viewLog.length, 0);
});

t('MODULE: the switch is offered once per launch, only to a member who has not switched, and never near a lock', async () => {
  const mk = async (o, account) => { const c = mod(o); await c.IplGoogle.config(); if (account !== undefined) c.IplGoogle.setAccount(account); return c; };
  let c = await mk({}, { linked: false });
  assert.strictEqual(c.IplGoogle.offerSwitch({ nearLock: true }), false, 'a lock is near: the member is left alone'); assert.strictEqual(c.sheets.length, 0);
  assert.strictEqual(c.IplGoogle.offerSwitch({ nearLock: false }), true); assert.strictEqual(c.sheets.length, 1);
  assert.ok(/switch to google/i.test(c.sheets[0].opts.title)); assert.strictEqual(c.sheets[0].opts.secondary, 'Not now');
  assert.strictEqual(c.IplGoogle.offerSwitch({ nearLock: false }), false, 'only once per launch'); assert.strictEqual(c.sheets.length, 1);
  c = await mk({}, { linked: true }); assert.strictEqual(c.IplGoogle.offerSwitch({}), false, 'already switched');
  c = await mk({}, null); assert.strictEqual(c.IplGoogle.offerSwitch({}), false, 'nobody signed in');
  c = await mk({ config: { ok: true, google: { enabled: false, client_id: null } } }, { linked: false }); assert.strictEqual(c.IplGoogle.offerSwitch({}), false, 'Google sign-in is not set up');
  c = await mk({ ua: UA.webviewAndroid }, { linked: false }); assert.strictEqual(c.IplGoogle.offerSwitch({}), false, 'inside the old app Google would refuse: not offered there');
  const w = mod(); w.IplGoogle.setAccount({ linked: false });
  assert.strictEqual(await w.IplGoogle.offerSwitchWhenReady({ nearLock: false }), true, 'asks the server first when it has not yet');
  const f = mod({ configError: new Error('HTTP 500') }); f.IplGoogle.setAccount({ linked: false });
  assert.strictEqual(await f.IplGoogle.offerSwitchWhenReady({}), false, 'a failed config request is a quiet no');
});

t('MODULE: the switch sheet links, then says the passcode is retired', async () => {
  const c = mod();
  await c.IplGoogle.config(); c.IplGoogle.setAccount({ linked: false });
  const sheet = c.IplGoogle.openSwitch(); await flush();
  assert.strictEqual(sheet.slot.id, 'sheet-slot'); assert.strictEqual(lastInit(c).nonce, 'N1');
  lastInit(c).callback({ credential: 'CRED-3' }); await flush();
  assert.ok(c.calls.includes('link:CRED-3:N1'));
  assert.ok(/Done: m\*\*\*@gmail\.com\. Your passcode is retired\./.test(sheet.finished), String(sheet.finished));
});

t('MODULE: sign in again: the sheet greets the member; backing out calls onCancel, signing in closes it quietly', async () => {
  const cancelled = [];
  let c = mod();
  const s1 = c.IplGoogle.signInAgain('Moe', { onCancel: () => cancelled.push(1) }); await flush();
  assert.ok(/welcome back, moe/i.test(s1.opts.message)); assert.strictEqual(s1.opts.secondary, 'Cancel');
  s1.dismiss(); assert.deepStrictEqual(cancelled, [1]);
  c = mod();
  const s2 = c.IplGoogle.signInAgain('Moe', { onCancel: () => cancelled.push(2) }); await flush();
  lastInit(c).callback({ credential: 'CRED-4' }); await flush();
  assert.strictEqual(s2.closed, true); assert.deepStrictEqual(cancelled, [1], 'a success is not a cancel'); assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 1);
  assert.ok(c.calls.includes('google:CRED-4:N1'), 'sign-in, not link');
});

t('MODULE: what this phone knows about the member: linked or not, remembered across launches', () => {
  const c = mod();
  assert.strictEqual(c.IplGoogle.wasLinked(), false);
  c.IplGoogle.setAccount({ linked: true, emailMasked: 'm***@gmail.com', pinEnabled: false });
  assert.strictEqual(c.IplGoogle.wasLinked(), true); assert.strictEqual(c.IplGoogle.account().emailMasked, 'm***@gmail.com');
  c.IplGoogle.setAccount({ linked: false }); assert.strictEqual(c.IplGoogle.wasLinked(), false); assert.strictEqual(c.IplGoogle.account().pinEnabled, true);
  c.IplGoogle.remember(true); assert.strictEqual(c.IplGoogle.wasLinked(), true); c.IplGoogle.remember(false); assert.strictEqual(c.IplGoogle.wasLinked(), false);
  c.IplGoogle.setAccount(null); assert.strictEqual(c.IplGoogle.account(), null);
});

t('MODULE: the real view (as shipped) escapes every text it draws and asks Google for the right button', () => {
  const c = mod();
  const real = c.realView;
  const box = (extra) => Object.assign({ innerHTML: '', querySelector: () => ({}), getBoundingClientRect: () => ({ width: 300 }) }, extra);
  const evil = '<img src=x onerror=alert(1)>';
  let host = box(); real.trouble(host, evil, () => {});
  assert.ok(host.innerHTML.indexOf('&lt;img') >= 0 && host.innerHTML.indexOf('<img') === -1, 'an error text must not become markup: ' + host.innerHTML);
  host = box(); real.blocked(host, evil);
  assert.ok(host.innerHTML.indexOf('<img') === -1 && /ipl\.example/.test(host.innerHTML) && /Copy link/.test(host.innerHTML), 'the notice names the app (escaped) and the link to copy: ' + host.innerHTML);
  host = box(); real.blocked(host, 'this app');
  assert.ok(/Chrome on your phone/.test(host.innerHTML) && !/Open in browser/.test(host.innerHTML), 'our own old app has no menu: it is told to open the link in Chrome');
  host = box(); real.blocked(host, 'Instagram');
  assert.ok(/Open in browser/.test(host.innerHTML) && /Instagram/.test(host.innerHTML), 'another app: use its menu');
  const w = { text: '' }, wrap = { classList: { contains: (k) => k === 'google-slot-wrap' }, querySelector: () => w };
  real.status({ parentNode: wrap, classList: {} }, '<b>x</b>', 'error');
  assert.strictEqual(w.text === undefined, false); assert.strictEqual(w.textContent, '<b>x</b>', 'a status line is set as text, never as markup'); assert.strictEqual(w.className, 'google-status error');
  const g = { renderButton: (h, o) => { g.got = o; } };
  real.renderButton(box(), g);
  assert.deepStrictEqual(plain(g.got), { type: 'standard', theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular', logo_alignment: 'left', width: 300 });
  real.renderButton(box({ getBoundingClientRect: () => ({ width: 900 }) }), g); assert.strictEqual(g.got.width, 380, 'never wider than Google allows a sensible button');
  real.renderButton(box({ getBoundingClientRect: () => ({ width: 40 }) }), g); assert.strictEqual(g.got.width, 200);
  assert.ok(/id="google-signin"/.test(real.loginBlockHtml()) && /or use your passcode/.test(real.loginBlockHtml()) && /role="status"/.test(real.loginBlockHtml()));
});

// ====================================================================== THE PAGE
function page(o = {}) {
  const c = { console, Date, Math, JSON, isFinite, Object, Array, String, calls: [], _state: o.state || null, _mustChange: !!o.mustChange };
  c.window = c;
  c._nowMs = () => (o.now != null ? o.now : Date.parse('2026-12-01T10:00:00Z'));
  c.esc = (s) => String(s); c.escAttr = (s) => String(s);
  c.Ui = { pin: () => '<pin>', info: (m) => c.calls.push(['info', m]) };
  c._showRulesModal = () => {}; c._showResetPasscodeModal = () => {}; c.logout = () => {};
  if (o.google !== false) {
    const g = o.google || {};
    c.IplGoogle = {
      loginBlockHtml: () => (g.block === false ? '' : '<div id="google-signin"></div>'), pinEnabled: () => g.pin !== false, enabled: () => g.enabled !== false,
      account: () => g.account || null, setAccount: (a) => c.calls.push(['setAccount', plain(a)]), offerSwitchWhenReady: (x) => c.calls.push(['offer', plain(x)]), openSwitch: () => {},
    };
  }
  vm.createContext(c);
  for (const n of o.fns || []) vm.runInContext(fnSource(n), c);
  return c;
}

t('PAGE: _lockWithin: a lock coming in the next 24 h, or one that only just passed, keeps the member undisturbed', () => {
  const NOW = Date.parse('2026-12-01T10:00:00Z'), H = 3600000;
  const lw = (iso, ms = 24 * H) => page({ now: NOW, state: iso === undefined ? {} : { deadline: { next_lock_utc: iso } }, fns: ['_lockWithin'] })._lockWithin(ms);
  assert.strictEqual(lw(new Date(NOW + 2 * H).toISOString()), true);
  assert.strictEqual(lw(new Date(NOW + 23 * H).toISOString()), true);
  assert.strictEqual(lw(new Date(NOW + 25 * H).toISOString()), false);
  assert.strictEqual(lw(new Date(NOW + 3 * 24 * H).toISOString()), false);
  assert.strictEqual(lw(new Date(NOW - 30 * 60000).toISOString()), true, 'just locked: the handover is being set up');
  assert.strictEqual(lw(new Date(NOW - 3 * 24 * H).toISOString()), false);
  assert.strictEqual(lw(null), false); assert.strictEqual(lw(undefined), false); assert.strictEqual(lw('not a date'), false);
  assert.strictEqual(page({ state: null, fns: ['_lockWithin'] })._lockWithin(24 * H), false, 'no state yet');
});

t('PAGE: after a sign-in the module learns what kind it was, and a member who has not switched is offered the switch (not while they must set a passcode)', () => {
  const NOW = Date.parse('2026-12-01T10:00:00Z');
  const deadline = (h) => ({ deadline: { next_lock_utc: new Date(NOW + h * 3600000).toISOString() } });
  let c = page({ now: NOW, state: deadline(100), fns: ['_lockWithin', '_googleAccount'] });
  c._googleAccount(undefined);       // a passcode sign-in
  assert.deepStrictEqual(c.calls[0], ['setAccount', { linked: false, emailMasked: null, pinEnabled: true }]);
  assert.deepStrictEqual(c.calls[1], ['offer', { nearLock: false }]);
  c = page({ now: NOW, state: deadline(5), fns: ['_lockWithin', '_googleAccount'] }); c._googleAccount(undefined);
  assert.deepStrictEqual(c.calls[1], ['offer', { nearLock: true }], 'a lock in 5 hours: the offer is told so and stays away');
  c = page({ now: NOW, state: deadline(100), fns: ['_lockWithin', '_googleAccount'] }); c._googleAccount({ linked: true, email_masked: 'm***@gmail.com', pin_enabled: false });
  assert.deepStrictEqual(c.calls, [['setAccount', { linked: true, emailMasked: 'm***@gmail.com', pinEnabled: false }]], 'a Google member is never offered the switch');
  c = page({ now: NOW, state: deadline(100), mustChange: true, fns: ['_lockWithin', '_googleAccount'] }); c._googleAccount(undefined);
  assert.deepStrictEqual(c.calls.map((x) => x[0]), ['setAccount'], 'a member who must set a new passcode is not asked anything else');
  c = page({ google: false, fns: ['_lockWithin', '_googleAccount'] }); c._googleAccount(undefined);
  assert.deepStrictEqual(c.calls, [], 'the module is absent: the page works as before');
  c = page({ now: NOW, state: deadline(100), fns: ['_lockWithin', '_googleAccount'] }); c._googleAccount({ linked: false, pin_enabled: true });
  assert.deepStrictEqual(c.calls[1], ['offer', { nearLock: false }], 'whoami says not linked: offered');
});

t('PAGE: the account menu: Reset passcode and the switch for a passcode member, a plain note for a Google member', () => {
  const labels = (g) => plain(page({ google: g, fns: ['_userMenuItems'] })._userMenuItems().filter((x) => !x.divider).map((x) => x.label));
  assert.deepStrictEqual(labels({ account: { linked: false }, enabled: true }), ['Scoring rules', 'Reset passcode', 'Switch to Google sign-in', 'Switch user']);
  assert.deepStrictEqual(labels({ account: { linked: false }, enabled: false }), ['Scoring rules', 'Reset passcode', 'Switch user'], 'no switch where Google is not set up');
  assert.deepStrictEqual(labels({ account: { linked: true, emailMasked: 'm***@gmail.com' }, enabled: true }), ['Scoring rules', 'Signed in with Google', 'Switch user'], 'a Google member has no passcode to reset');
  assert.deepStrictEqual(labels({ account: null, enabled: true }), ['Scoring rules', 'Reset passcode', 'Switch to Google sign-in', 'Switch user']);
  assert.deepStrictEqual(labels(false), ['Scoring rules', 'Reset passcode', 'Switch user'], 'without the module: exactly as before');
  const g = page({ google: { account: { linked: true, emailMasked: 'm***@gmail.com' } }, fns: ['_userMenuItems'] })._userMenuItems().find((x) => x.label === 'Signed in with Google');
  assert.strictEqual(g.hint, 'm***@gmail.com'); g.onClick();
});

t('PAGE: the sign-in card: the Google block on top, the passcode chips below; only Google when nobody has a passcode any more; unchanged without the module', () => {
  const card = (g, names = ['Moe', 'Sai']) => page({ google: g, fns: ['_buildLoginCard'] })._buildLoginCard(names);
  const withG = card({}); assert.ok(withG.indexOf('id="google-signin"') >= 0); assert.ok(withG.indexOf('login-chip') >= 0, 'members can still pick their name'); assert.ok(/Continue with Google, or pick your name/.test(withG));
  assert.ok(withG.indexOf('google-signin') < withG.indexOf('login-chip'), 'Google first');
  const only = card({ pin: false }); assert.ok(only.indexOf('id="google-signin"') >= 0); assert.strictEqual(only.indexOf('login-chip'), -1); assert.strictEqual(only.indexOf('register-input'), -1); assert.ok(/Sign in with Google to continue/.test(only));
  const notReady = card({ block: false }); assert.strictEqual(notReady.indexOf('google-signin'), -1); assert.ok(notReady.indexOf('login-chip') >= 0); assert.ok(/Pick your name to sign in\./.test(notReady));
  const none = card(false); assert.strictEqual(none.indexOf('google'), -1); assert.ok(/Pick your name to sign in\./.test(none));
  const empty = card({ block: false }, []); assert.ok(empty.indexOf('register-input') >= 0, 'no members yet: registration as before');
});

t('PAGE WIRING: the module is loaded, the card is drawn into after every render, events are handled, sign-out ends the session on the server first', () => {
  assert.ok(/<script src="\/static\/auth_google\.js\?v=[\w.]+"><\/script>/.test(HTML), 'the page does not load auth_google.js');
  assert.ok(/\$app\.innerHTML=html;\s*\n\s*if\(!_username&&window\.IplGoogle\)IplGoogle\.afterRender\(\);/.test(HTML), 'render() does not call IplGoogle.afterRender() for the sign-in card');
  for (const e of ['ipl:google-signed-in', 'ipl:google-linked', 'ipl:google-config']) assert.ok(HTML.indexOf('addEventListener("' + e + '"') >= 0, 'no listener for ' + e);
  const lo = fnSource('logout');
  assert.ok(lo.indexOf('IplApi.authLogout()') >= 0 && lo.indexOf('IplApi.authLogout()') < lo.indexOf('IplApi.logoutClearToken()'), 'logout must end the server session BEFORE it forgets the token');
  assert.ok(/_establishSession\(d\.name,d\.is_admin,d\.must_change,d\)/.test(HTML), 'the saved-session check and the Google sign-in pass whoami / the reply on');
  assert.ok(/Ui\.errorReason\(e\)==="pin_retired"/.test(HTML), 'a retired passcode moves the member on to Google');
  assert.ok(/IplGoogle\.wasLinked\(\)\)_googleSignInAgain/.test(HTML), 'a signed-out Google member is offered Google, not a passcode');
});

(async () => {
  let failed = 0;
  for (const x of tests) {
    try { await x.fn(); console.log('PASS  ' + x.name); } catch (e) { failed++; console.log('FAIL  ' + x.name + '\n      ' + String(e && e.stack || e).split('\n').slice(0, 4).join('\n      ')); }
  }
  if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
  console.log('\nall passed');
})();
