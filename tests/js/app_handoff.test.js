// Regression tests for signing in from the installed Android app (2026-11-21). The app is a WebView and Google refuses to sign anybody in inside one, so the page hands the
// Google step to the phone's browser and the browser hands the member back with a one-time code (RFC 8252, with the PKCE proof of RFC 7636). Loads the REAL
// Static/auth_google.js and Static/ipl_glue.js and the REAL page functions out of templates/index.html; the DOM part of the module is replaced by a view that records what it
// was asked to show, and the Web Crypto API is Node's own (the hash is a real SHA-256).
//   * THE GLUE: the two calls go to the right addresses; a redeemed code stores the app's own session like a login; a refusal is never "your session ended"
//   * THE APP:  inside the app the member gets a button instead of the "open in Chrome" notice; the button keeps a secret, sends only its hash; the code that comes back is
//               redeemed with that secret, once; every way it can go wrong is said and leaves nothing behind
//   * THE BROWSER: the address carries the app's challenge; once the member is signed in the way back is offered (an Android intent with the code), asked for once, renewed
//               when it times out, forgotten when the member says not now; nothing happens in any other browser
//   * THE PAGE: the sign-in card says why the member is here, and a "signing you in" card while the code is redeemed
//   node tests/js/app_handoff.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert'), nodeCrypto = require('crypto');
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
const flush = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
const plain = (x) => JSON.parse(JSON.stringify(x === undefined ? null : x));
const tests = [];
const t = (name, fn) => tests.push({ name, fn });
const sha = (s) => nodeCrypto.createHash('sha256').update(s).digest('base64url');

const UA = {
  chromeAndroid: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36',
  appWebview15: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36 BCLFantasyApp/1.5',
  oldAppWebview: 'Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36',
  instagram: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 Instagram 350.0.0.38.109 Android',
  desktop: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
};
const VERIFIER = 'v'.repeat(43), CHALLENGE = sha(VERIFIER);
const CODE = 'c'.repeat(43);
const NOW = 1000000;
const INTENT = (code) => 'intent://auth?code=' + encodeURIComponent(code) + '#Intent;scheme=bclfantasy;package=com.iplfantasy.app;S.browser_fallback_url=' + encodeURIComponent('https://ipl.example/') + ';end';

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

t('GLUE: the browser asks for a code with ITS session and the app redeems it with no session; only the redeem stores a token', async () => {
  const c = glue({ status: 200, body: { ok: true, code: CODE, expires_in: 120, token: 'must-never-be-stored' } }, 'tok-browser');      // (a reply with a stray token: it is the browser's session, not a sign-in)
  const d = await c.IplApi.authHandoff(CHALLENGE);
  assert.strictEqual(d.code, CODE);
  const ask = c.sent[0];
  assert.strictEqual(ask.url, '/api/auth/handoff'); assert.strictEqual(ask.opts.method, 'POST'); assert.deepStrictEqual(JSON.parse(ask.opts.body), { challenge: CHALLENGE });
  assert.strictEqual(ask.opts.headers.Authorization, 'Bearer tok-browser', 'it is the signed-in browser that asks');
  assert.strictEqual(c.store.get('ipl_session_token'), 'tok-browser', 'asking for a code changes nothing about the browser session');
  const app = glue({ status: 200, body: { ok: true, linked: true, name: 'Moe', token: 'app-token' } }, null);
  const r = await app.IplApi.authHandoffRedeem(CODE, VERIFIER);
  assert.strictEqual(r.name, 'Moe');
  const red = app.sent[0];
  assert.strictEqual(red.url, '/api/auth/handoff/redeem'); assert.strictEqual(red.opts.method, 'POST'); assert.deepStrictEqual(JSON.parse(red.opts.body), { code: CODE, verifier: VERIFIER });
  assert.strictEqual(red.opts.headers.Authorization, undefined, 'the app has no session yet: the proof is the verifier');
  assert.strictEqual(app.store.get('ipl_session_token'), 'app-token', 'the app gets its own session, stored like a login');
  const none = glue({ status: 200, body: { ok: true } }, null);
  await none.IplApi.authHandoffRedeem(CODE, VERIFIER);
  assert.strictEqual(none.store.has('ipl_session_token'), false, 'no token in the reply, nothing stored');
});

t('GLUE: a refused redeem is never "your session ended" and clears a stale token; a browser whose session is gone IS told so', async () => {
  for (const [status, reason] of [[401, 'handoff_expired'], [400, 'credentials_required'], [429, 'rate_limited'], [401, undefined]]) {
    const c = glue({ status, body: reason ? { ok: false, error: 'x', code: status, reason } : {} }, 'a-stale-token');
    let err; await c.IplApi.authHandoffRedeem(CODE, VERIFIER).catch((e) => { err = e; });
    assert.ok(err, String(reason)); assert.strictEqual(ended(c).length, 0, String(reason) + ' was taken for a lost session');
    if (status === 401) assert.strictEqual(c.store.has('ipl_session_token'), false, String(reason) + ': a stale token is cleared, as for a wrong passcode');
  }
  const c2 = glue({ status: 401, body: { ok: false, error: 'invalid or expired token', code: 401, reason: 'session_ended' } }, 'tok-browser');
  await c2.IplApi.authHandoff(CHALLENGE).catch(() => {});
  assert.strictEqual(ended(c2).length, 1, 'the browser session is over: the page must say so'); assert.strictEqual(c2.store.has('ipl_session_token'), false);
});

// ====================================================================== THE MODULE
function mod(o = {}) {
  const clock = { t: NOW };
  const c = { console, Promise, JSON, Math, Object, Error, RegExp, String, Array, Uint8Array, encodeURIComponent, decodeURIComponent, store: new Map(), session: new Map(), events: [], timeouts: [], scripts: [], sheets: [], hsheets: [], claims: [], calls: [], viewLog: [], hist: [], clock };
  c.Date = { now: () => clock.t };
  c.window = c;
  c.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
  c.TextEncoder = TextEncoder;
  if (!o.noCrypto) c.crypto = nodeCrypto.webcrypto;
  if (o.gis) c.google = { accounts: { id: { initialize() {}, renderButton() {} } } };       // Google's library, when the case needs it
  c.setInterval = (fn, ms) => ({ fn, ms, cleared: false }); c.clearInterval = (h) => { if (h) h.cleared = true; };
  c.setTimeout = (fn, ms) => { const h = { fn, ms, cleared: false }; c.timeouts.push(h); return h; }; c.clearTimeout = (h) => { if (h) h.cleared = true; };
  c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.dispatchEvent = (e) => { c.events.push(e); };
  for (const [k, v] of Object.entries(o.store || {})) c.store.set(k, v);
  for (const [k, v] of Object.entries(o.session || {})) c.session.set(k, v);
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.sessionStorage = { getItem: (k) => (c.session.has(k) ? c.session.get(k) : null), setItem: (k, v) => c.session.set(k, String(v)), removeItem: (k) => c.session.delete(k) };
  c.navigator = { userAgent: o.ua || UA.chromeAndroid };
  c.location = { origin: 'https://ipl.example', pathname: '/', search: o.search || '', hash: o.hash || '', href: 'https://ipl.example/' };
  c.history = { replaceState: (s, title, url) => { c.hist.push(url); } };
  c.document = {
    body: { contains: (el) => !(el && el.__gone) },
    head: { appendChild(s) { c.scripts.push(s); } },
    createElement: (tag) => ({ tag, parentNode: null }),
    getElementById: () => null,
  };
  let n = 0;
  c.IplApi = {
    authConfig: () => { c.calls.push('config'); return Promise.resolve(o.config || { ok: true, google: { enabled: true, client_id: 'CID.apps.googleusercontent.com' }, pin_enabled: true }); },
    authNonce: () => { c.calls.push('nonce'); return Promise.resolve({ ok: true, nonce: 'N' + (++n) }); },
    authHandoff: (ch) => { c.calls.push('handoff:' + ch); return o.handoffReply ? o.handoffReply(ch) : Promise.resolve({ ok: true, code: CODE, expires_in: 120 }); },
    authHandoffRedeem: (code, v) => { c.calls.push('redeem:' + code + ':' + v); return o.redeemReply ? o.redeemReply(code, v) : Promise.resolve({ ok: true, linked: false, name: 'Moe', token: 'app-token', is_admin: false, must_change: false, email_masked: null }); },
  };
  c.Ui = { errorMessage: (e) => 'MSG:' + (((e && e.serverData) || {}).reason || (e && e.message)), errorReason: (e) => (((e && e.serverData) || {}).reason || ''), success() {}, info() {} };
  vm.createContext(c);
  vm.runInContext(MODULE, c, { filename: 'auth_google.js' });        // (at load the module reads the address: ?handoff=, ?handoff_code=)
  const rec = {
    loginBlockHtml: () => '<div id="google-signin"></div>', renderButton: () => c.viewLog.push(['renderButton']),
    status: (h, text, kind) => c.viewLog.push(['status', text, kind]), busy: () => {}, notLinked: () => {},
    blocked: (h, app) => c.viewLog.push(['blocked', app]), trouble: (h, msg) => c.viewLog.push(['trouble', msg]),
    appSignIn: (h, onTap) => { c.viewLog.push(['appSignIn']); c.tap = onTap; },
    handoffSheet: (opts) => {
      const s = { opts, waits: 0, readyLog: [], failLog: [], closed: false, waiting() { this.waits++; }, ready(href, ms) { this.readyLog.push([href, ms]); }, fail(text) { this.failLog.push(text); },
                  close() { this.closed = true; }, dismiss() { this.closed = true; if (opts.cancel) opts.cancel(); } };
      c.hsheets.push(s); return s;
    },
    sheet: (opts) => { const s = { slot: {}, opts, closed: false, close() { this.closed = true; }, finish() {}, dismiss() { this.closed = true; } }; c.sheets.push(s); return s; },
    claimSheet: (opts) => { const s = { opts, close() {}, dismiss() {}, busy() {}, fail() {} }; c.claims.push(s); return s; },
    closeSheets: () => { c.sheets.forEach((s) => s.close()); c.hsheets.forEach((s) => s.close()); },
  };
  c.IplGoogle._test.setView(rec);
  return c;
}
const ev = (c, type) => c.events.filter((e) => e.type === type);
const ok = { ok: true, google: { enabled: true, client_id: 'CID.apps.googleusercontent.com' }, pin_enabled: true };

t('APP: the app is recognised by its user-agent token and nothing else is', () => {
  const c = mod();
  const s = (ua) => plain(c.IplGoogle.appShell(ua));
  assert.deepStrictEqual(s(UA.appWebview15), { inApp: true, version: '1.5' });
  assert.deepStrictEqual(s('x BCLFantasyApp/2.10.3 y'), { inApp: true, version: '2.10.3' });
  for (const ua of [UA.oldAppWebview, UA.chromeAndroid, UA.instagram, UA.desktop, UA.iphone, '', 'BCLFantasy-App/1.4']) assert.strictEqual(s(ua).inApp, false, ua);
});

t('APP: inside the app the member gets a button that opens the browser, not the "open in Chrome" notice, and Google\'s library is never loaded there', async () => {
  const c = mod({ ua: UA.appWebview15 });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.deepStrictEqual(plain(c.viewLog), [['appSignIn']]);
  assert.strictEqual(c.scripts.length, 0, 'Google is not loaded inside the app');
  assert.ok(!c.calls.includes('nonce'), 'and no nonce is wasted');
});

t('APP: everywhere else nothing changed: the old app and other built-in browsers still get the notice; the switch ("link") never gets the app button; no Web Crypto means the notice', async () => {
  for (const [ua, who] of [[UA.oldAppWebview, 'this app'], [UA.instagram, 'Instagram']]) {
    const c = mod({ ua });
    await c.IplGoogle.mount({ id: 'host' }, 'signin');
    assert.deepStrictEqual(plain(c.viewLog), [['blocked', who]], who);
  }
  const link = mod({ ua: UA.appWebview15 });
  await link.IplGoogle.mount({ id: 'host' }, 'link');
  assert.deepStrictEqual(plain(link.viewLog), [['blocked', 'this app']], 'a member who is already signed in has no use for it');
  const nocrypto = mod({ ua: UA.appWebview15, noCrypto: true });
  await nocrypto.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.deepStrictEqual(plain(nocrypto.viewLog), [['blocked', 'this app']], 'a phone that cannot hash and keep a secret falls back to the notice');
  const chrome = mod({ gis: true });
  await chrome.IplGoogle.mount({ id: 'host' }, 'signin');
  assert.ok(chrome.viewLog.some((x) => x[0] === 'renderButton') && !chrome.viewLog.some((x) => x[0] === 'appSignIn'), 'an ordinary browser gets Google\'s own button');
});

t('APP: the button keeps a fresh secret in the app and sends the browser only its SHA-256 hash, by loading /app-signin?c=<challenge>', async () => {
  const c = mod({ ua: UA.appWebview15 });
  await c.IplGoogle.mount({ id: 'host' }, 'signin');
  c.tap(); await flush();
  const kept = JSON.parse(c.store.get('ipl_handoff_v'));
  assert.ok(/^[A-Za-z0-9_-]{43}$/.test(kept.v), 'a 43-character base64url secret'); assert.strictEqual(kept.t, NOW);
  assert.strictEqual(c.location.href, 'https://ipl.example/app-signin?c=' + sha(kept.v), 'the address carries the hash and not the secret');
  assert.ok(!c.location.href.includes(kept.v));
  assert.ok(c.viewLog.some((x) => x[0] === 'status' && /Return to the app/.test(x[1]) && x[2] === 'info'), 'the member is told what to do next');
  const first = kept.v; c.tap(); await flush();
  assert.notStrictEqual(JSON.parse(c.store.get('ipl_handoff_v')).v, first, 'every tap makes a new secret (the latest one wins)');
});

t('APP: the way back (?handoff_code=) is redeemed once with the kept secret: the member is signed in like for Google, welcomed, and nothing is left behind', async () => {
  let release; const c = mod({ ua: UA.appWebview15, search: '?handoff_code=' + CODE, store: { ipl_handoff_v: JSON.stringify({ v: VERIFIER, t: NOW - 60000 }) }, redeemReply: () => new Promise((r) => { release = r; }) });
  assert.strictEqual(c.IplGoogle.redeeming(), true, 'while the server answers the sign-in card says so');
  assert.deepStrictEqual(plain(c.hist), ['/'], 'the code is taken out of the address at once');
  assert.deepStrictEqual(plain(c.calls), ['redeem:' + CODE + ':' + VERIFIER]);
  release({ ok: true, linked: true, name: 'Moe', token: 'app-token', is_admin: false, must_change: false, email_masked: 'm***@gmail.com' }); await flush();
  assert.strictEqual(c.IplGoogle.redeeming(), false);
  assert.deepStrictEqual(c.events.map((e) => e.type), ['ipl:google-signed-in', 'ipl:app-handoff'], 'signed in first, then welcomed');
  assert.strictEqual(plain(c.events[0].detail).name, 'Moe'); assert.deepStrictEqual(plain(c.events[1].detail), { ok: true, name: 'Moe' });
  assert.strictEqual(c.store.has('ipl_handoff_v'), false, 'the secret is gone once it has been used');
  assert.deepStrictEqual(plain(c.IplGoogle.account()), { linked: true, emailMasked: 'm***@gmail.com', pinEnabled: false });
});

t('APP: a way back with no kept secret, or one older than fifteen minutes, is never sent to the server and says what to do', async () => {
  for (const store of [{}, { ipl_handoff_v: JSON.stringify({ v: VERIFIER, t: NOW - 16 * 60 * 1000 }) }, { ipl_handoff_v: 'not json' }]) {
    const c = mod({ ua: UA.appWebview15, search: '?handoff_code=' + CODE, store });
    await flush();
    assert.deepStrictEqual(plain(c.calls), [], 'the code is not even sent');
    const e = ev(c, 'ipl:app-handoff')[0]; assert.strictEqual(e.detail.ok, false); assert.ok(/start again/i.test(e.detail.message));
    assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 0); assert.strictEqual(c.IplGoogle.redeeming(), false);
  }
});

t('APP: a refused code is said, the secret is thrown away, and the sign-in card comes back', async () => {
  const err = Object.assign(new Error('HTTP 401'), { serverData: { reason: 'handoff_expired' } });
  const c = mod({ ua: UA.appWebview15, search: '?handoff_code=' + CODE, store: { ipl_handoff_v: JSON.stringify({ v: VERIFIER, t: NOW }) }, redeemReply: () => Promise.reject(err) });
  await flush();
  assert.strictEqual(c.IplGoogle.redeeming(), false); assert.strictEqual(c.store.has('ipl_handoff_v'), false);
  assert.deepStrictEqual(plain(ev(c, 'ipl:app-handoff')[0].detail), { ok: false, message: 'MSG:handoff_expired' });
  assert.strictEqual(ev(c, 'ipl:google-signed-in').length, 0);
});

t('APP: a malformed code in the address is ignored (and cleaned); other parts of the address stay', async () => {
  const bad = mod({ ua: UA.appWebview15, search: '?handoff_code=short', store: { ipl_handoff_v: JSON.stringify({ v: VERIFIER, t: NOW }) } });
  await flush();
  assert.deepStrictEqual(plain(bad.calls), []); assert.strictEqual(ev(bad, 'ipl:app-handoff').length, 0); assert.deepStrictEqual(plain(bad.hist), ['/']);
  assert.strictEqual(bad.store.has('ipl_handoff_v'), true, 'a junk link does not cost the member the secret of a sign-in in progress');
  const keep = mod({ ua: UA.appWebview15, search: '?x=1&handoff_code=' + CODE + '&y=2%203', hash: '#top', store: { ipl_handoff_v: JSON.stringify({ v: VERIFIER, t: NOW }) } });
  await flush();
  assert.deepStrictEqual(plain(keep.hist), ['/?x=1&y=2%203#top']);
});

// ---- the browser's half ----
t('BROWSER: ?handoff=<challenge> in Android Chrome is remembered, taken out of the address, and the sign-in card says why the member is here', () => {
  const c = mod({ search: '?handoff=' + CHALLENGE });
  assert.ok(/Return to the app/.test(c.IplGoogle.handoffNoteHtml()) && /signing in to the BCL Fantasy app/.test(c.IplGoogle.handoffNoteHtml()));
  assert.deepStrictEqual(plain(c.hist), ['/']);
  assert.strictEqual(JSON.parse(c.session.get('ipl_handoff_c')).c, CHALLENGE);
  assert.strictEqual(mod().IplGoogle.handoffNoteHtml(), '', 'nothing is said to a member who was not sent here by the app');
});

t('BROWSER: the challenge survives a reload of the tab for fifteen minutes and not longer', () => {
  const fresh = mod({ session: { ipl_handoff_c: JSON.stringify({ c: CHALLENGE, t: NOW - 60000 }) } });
  assert.ok(fresh.IplGoogle.handoffNoteHtml());
  const old = mod({ session: { ipl_handoff_c: JSON.stringify({ c: CHALLENGE, t: NOW - 16 * 60 * 1000 }) } });
  assert.strictEqual(old.IplGoogle.handoffNoteHtml(), '');
  const junk = mod({ session: { ipl_handoff_c: 'junk' } });
  assert.strictEqual(junk.IplGoogle.handoffNoteHtml(), '');
});

t('BROWSER: only Android Chrome-like browsers act on it: not a desktop, not an iPhone, not the app itself, not a malformed challenge (all are still cleaned from the address)', () => {
  for (const [o, why] of [[{ ua: UA.desktop, search: '?handoff=' + CHALLENGE }, 'desktop'], [{ ua: UA.iphone, search: '?handoff=' + CHALLENGE }, 'iphone'], [{ ua: UA.appWebview15, search: '?handoff=' + CHALLENGE }, 'the app'],
                          [{ search: '?handoff=' + CHALLENGE.slice(1) }, 'short'], [{ search: '?handoff=' + CHALLENGE + 'x' }, 'long'], [{ search: '?handoff=' + CHALLENGE.slice(1) + '+' }, 'bad character']]) {
    const c = mod(o);
    assert.strictEqual(c.IplGoogle.handoffNoteHtml(), '', why); assert.deepStrictEqual(plain(c.hist), ['/'], why); assert.strictEqual(c.session.size, 0, why);
    assert.strictEqual(c.IplGoogle.offerHandoff('Moe'), false, why);
  }
});

t('BROWSER: once signed in the member is offered the way back: ONE request for a code with that challenge, ONE sheet, whose link is the Android intent that opens the app with the code', async () => {
  const c = mod({ search: '?handoff=' + CHALLENGE });
  assert.strictEqual(c.IplGoogle.offerHandoff('Moe'), true);
  assert.strictEqual(c.hsheets.length, 1); assert.strictEqual(c.hsheets[0].opts.name, 'Moe');
  assert.strictEqual(c.hsheets[0].waits, 1, 'it says it is getting the sign-in ready');
  await flush();
  assert.deepStrictEqual(plain(c.calls.filter((x) => x.indexOf('handoff:') === 0)), ['handoff:' + CHALLENGE]);
  assert.deepStrictEqual(plain(c.hsheets[0].readyLog), [[INTENT(CODE), 110000]], 'the link names the app by package and falls back to the site; the sheet renews it 10 seconds before the code dies');
  assert.strictEqual(c.IplGoogle.offerHandoff('Moe'), false, 'not twice while the sheet is open'); assert.strictEqual(c.hsheets.length, 1);
});

t('BROWSER: the link is built from the code the server gave, however odd, and never from anything else', async () => {
  const c = mod({ search: '?handoff=' + CHALLENGE, handoffReply: () => Promise.resolve({ ok: true, code: 'a b/c?d&e', expires_in: 30 }) });
  c.IplGoogle.offerHandoff('Moe'); await flush();
  assert.deepStrictEqual(plain(c.hsheets[0].readyLog), [[INTENT('a b/c?d&e'), 20000]]);
  const slow = mod({ search: '?handoff=' + CHALLENGE, handoffReply: () => Promise.resolve({ ok: true, code: CODE }) });
  slow.IplGoogle.offerHandoff('Moe'); await flush();
  assert.strictEqual(slow.hsheets[0].readyLog[0][1], 110000, 'no expires_in: the two-minute default');
});

t('BROWSER: a failed request is said on the sheet, and the sheet can ask again', async () => {
  let fail = true;
  const c = mod({ search: '?handoff=' + CHALLENGE, handoffReply: () => (fail ? Promise.reject(Object.assign(new Error('HTTP 500'), { serverData: { reason: 'server_error' } })) : Promise.resolve({ ok: true, code: CODE, expires_in: 120 })) });
  c.IplGoogle.offerHandoff('Moe'); await flush();
  const s = c.hsheets[0];
  assert.deepStrictEqual(plain(s.failLog), ['MSG:server_error']); assert.strictEqual(s.readyLog.length, 0);
  fail = false; s.opts.again(); await flush();
  assert.strictEqual(s.waits, 2, 'it shows the waiting state again'); assert.strictEqual(s.readyLog.length, 1);
  assert.strictEqual(c.calls.filter((x) => x.indexOf('handoff:') === 0).length, 2, 'two requests in all: one per try');
});

t('BROWSER: "Not now" forgets the app\'s challenge for good (a later sign-in does not bring the sheet back); the page closing the sheets does not', async () => {
  const c = mod({ search: '?handoff=' + CHALLENGE });
  c.IplGoogle.offerHandoff('Moe'); await flush();
  c.hsheets[0].dismiss();
  assert.strictEqual(c.session.has('ipl_handoff_c'), false); assert.strictEqual(c.IplGoogle.handoffNoteHtml(), '');
  assert.strictEqual(c.IplGoogle.offerHandoff('Moe'), false);
  const d = mod({ search: '?handoff=' + CHALLENGE });
  d.IplGoogle.offerHandoff('Moe'); await flush();
  d.IplGoogle.closeSheets();                     // (the page closes every sheet when a sign-in lands)
  assert.strictEqual(d.IplGoogle.offerHandoff('Moe'), true, 'the member is still on the way back to the app');
  assert.strictEqual(d.hsheets.length, 2);
});

t('BROWSER: once the way back is taken the page stops remembering the app\'s challenge, yet the sheet can still ask for a new code (the app may not have opened)', async () => {
  const c = mod({ search: '?handoff=' + CHALLENGE });
  c.IplGoogle.offerHandoff('Moe'); await flush();
  const s = c.hsheets[0];
  s.opts.sent();
  assert.strictEqual(c.session.has('ipl_handoff_c'), false); assert.strictEqual(c.IplGoogle.handoffNoteHtml(), '');
  assert.strictEqual(c.IplGoogle.offerHandoff('Moe'), false, 'a later sign-in does not bring the sheet back');
  s.opts.again(); await flush();
  assert.deepStrictEqual(plain(c.calls.filter((x) => x.indexOf('handoff:') === 0)), ['handoff:' + CHALLENGE, 'handoff:' + CHALLENGE], 'the same challenge, a second code');
  assert.strictEqual(s.readyLog.length, 2);
});

t('BROWSER: nothing is offered inside the app, or to a member the app did not send; and the "switch to Google" offer waits while a way back is pending', async () => {
  const app = mod({ ua: UA.appWebview15 }); assert.strictEqual(app.IplGoogle.offerHandoff('Moe'), false);
  const plainBrowser = mod(); assert.strictEqual(plainBrowser.IplGoogle.offerHandoff('Moe'), false); assert.strictEqual(plainBrowser.hsheets.length, 0);
  const c = mod({ search: '?handoff=' + CHALLENGE });
  await c.IplGoogle.config(); c.IplGoogle.setAccount({ linked: false, emailMasked: null, pinEnabled: true });
  assert.strictEqual(c.IplGoogle.offerSwitch(), false, 'the member came for the app, not for this');
  c.IplGoogle.offerHandoff('Moe'); await flush(); c.hsheets[0].dismiss();
  assert.strictEqual(c.IplGoogle.offerSwitch(), true, 'and afterwards they are offered it once, as always');
});

// ====================================================================== THE PAGE
function page(o = {}) {
  const c = { console, Date, Math, JSON, isFinite, Object, Array, String, calls: [], _state: null, _mustChange: !!o.mustChange, _username: o.username === undefined ? 'Moe' : o.username };
  c.window = c;
  c.esc = (s) => String(s); c.escAttr = (s) => String(s);
  c.Ui = { pin: () => '<pin>', error: (m) => c.calls.push(['error', m]), success: (m) => c.calls.push(['success', m]) };
  c._lockWithin = () => false;
  if (o.google !== false) {
    const g = o.google || {};
    c.IplGoogle = {
      loginBlockHtml: () => '<div id="google-signin"></div>', pinEnabled: () => true, redeeming: () => !!g.redeeming, handoffNoteHtml: () => g.note || '',
      setAccount: () => {}, offerSwitchWhenReady: () => {}, offerHandoff: (n) => c.calls.push(['offerHandoff', n]),
    };
  }
  vm.createContext(c);
  for (const n of o.fns || []) vm.runInContext(fnSource(n), c);
  return c;
}

t('PAGE: the sign-in card says why the member is here, and while the app redeems its code it shows a card of its own instead of the sign-in choices', () => {
  const card = (g) => page({ fns: ['_buildLoginCard'], google: g })._buildLoginCard(['Moe', 'Sai']);
  const note = '<p class="handoff-note">NOTE</p>';
  assert.ok(card({ note }).indexOf(note) > card({ note }).indexOf('Who\'s playing?'), 'the note sits under the heading');
  assert.strictEqual(card({}).indexOf('handoff-note'), -1, 'no note when the app sent nobody');
  const busy = card({ redeeming: true });
  assert.ok(/Signing you in/.test(busy) && busy.indexOf('login-chip') === -1 && busy.indexOf('google-signin') === -1, 'no chips, no Google button while the code is redeemed');
  assert.doesNotThrow(() => card(false), 'and a page without the Google module is as it was');
});

t('PAGE: after every sign-in the member the app sent is offered the way back, unless they still have to choose a new passcode', () => {
  const ok = page({ fns: ['_googleAccount'] }); ok._googleAccount(null);
  assert.deepStrictEqual(plain(ok.calls.filter((x) => x[0] === 'offerHandoff')), [['offerHandoff', 'Moe']]);
  const must = page({ fns: ['_googleAccount'], mustChange: true }); must._googleAccount(null);
  assert.strictEqual(must.calls.filter((x) => x[0] === 'offerHandoff').length, 0, 'the forced new-passcode dialog comes first');
  const none = page({ fns: ['_googleAccount'], google: false });
  assert.doesNotThrow(() => none._googleAccount(null));
});

t('PAGE: the app\'s sign-in is welcomed or its failure said, and the card redrawn when nobody is signed in', () => {
  assert.ok(/addEventListener\("ipl:app-handoff",function\(e\)\{[^]*?Ui\.success\("Signed in"[^]*?Ui\.error\(d\.message/.test(HTML), 'no listener for ipl:app-handoff');
  assert.ok(/Ui\.error\(d\.message\|\|"Could not sign in\. Please try again\."\);\s*\n\s*if\(!_username&&_state\)render\(_state\);/.test(HTML), 'after a failure the sign-in card is drawn again');
  assert.ok(/<script src="\/static\/auth_google\.js\?v=[\w.]+"><\/script>/.test(HTML));
});

t('PAGE: the glue treats the redeem as a sign-in attempt and the module is told the page is ready before it reads the address (the module loads LAST)', () => {
  assert.ok(/auth\\\/handoff\\\/redeem/.test(GLUE), 'the glue\'s sign-in-attempt rule must include the redeem');
  const order = ['ipl_glue.js', 'mc_hub.js', 'auth_google.js'].map((f) => HTML.indexOf('/static/' + f));
  assert.ok(order[0] > 0 && order[0] < order[1] && order[1] < order[2], 'the page script and the glue are in place before auth_google.js runs its start-up');
  assert.ok(HTML.indexOf('window.addEventListener("ipl:app-handoff"') < order[0], 'and the listeners exist before it fires anything');
});

// A test that waits for a promise that never settles would let Node leave quietly with exit code 0 and the later tests would never run (a silent pass).
// So every test has a time limit, and leaving early is itself a failure.
const withTimeout = (p, ms, name) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timed out after ' + ms + ' ms: a promise never settled')), ms))]);
(async () => {
  let failed = 0, ran = 0;
  process.on('exit', (code) => { if (ran < tests.length && code === 0) { console.log('\nINCOMPLETE: only ' + ran + ' of ' + tests.length + ' tests ran'); process.exitCode = 1; } });
  for (const x of tests) {
    try { await withTimeout(Promise.resolve().then(x.fn), 8000, x.name); console.log('PASS  ' + x.name); } catch (e) { failed++; console.log('FAIL  ' + x.name + '\n      ' + String(e && e.stack || e).split('\n').slice(0, 4).join('\n      ')); }
    ran++;
  }
  if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
  console.log('\nall passed');
})();
