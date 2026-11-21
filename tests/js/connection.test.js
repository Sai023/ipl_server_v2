// Regression tests for ONE consistent "server trouble" pattern (2026-11-21, Phase 1.7 of the SA20 readiness plan).
// Before: a full-screen "Server Unavailable" overlay hid the whole app (an XI being edited included) after any 5xx on the 60 s poll, a second bare red
// banner said something else, a redraw wiped that banner, and a banner that was showing never cleared when the data had not changed.
// Now: Ui.connection (Static/ui.js) turns the outcome of EVERY /api/ call into one slim banner above the app that blocks nothing: a single failure is
// silent, the second raises it, a quiet probe backs off 3 / 5 / 10 / 20 / 40 / 60 s (never while the tab is hidden), offline outranks server trouble,
// "Retry now" checks at once, and the first good answer clears it and tells the page (ipl:recovered).
// Loads the REAL Static/ui.js (and Static/ipl_glue.js) into a vm with a fake clock, fake timers, a fake DOM and a scripted fetch.
//   node tests/js/connection.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');
const UI_SRC = process.env.UI_JS_PATH ? fs.readFileSync(process.env.UI_JS_PATH, 'utf8') : read('Static', 'ui.js');
const GLUE = read('Static', 'ipl_glue.js'), HTML = read('templates', 'index.html'), CSS = read('Static', 'ui.css'), STYLE = read('Static', 'style.css');

const SERVER_MSG = 'The server is having a moment. Retrying…';
const SEVERE_MSG = "Still can't reach the server. Showing the last data we have. Your picks are kept on this phone.";
const OFFLINE_MSG = "You're offline. Showing the last data we have.";
const OFFLINE_BOOT_MSG = "You're offline. We'll load your league as soon as you're back.";
const SIGNIN_MSG = 'Can’t confirm your sign-in — retrying…';
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

// A minimal element: what Ui.connection touches (innerHTML, className, attributes, children, one .ago child) and nothing more.
function makeEl(tag) {
  const e = { tagName: tag, id: '', className: '', textContent: '', style: {}, children: [], attrs: {}, parentNode: null, sets: 0, _html: '', _ago: null,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    appendChild(ch) { ch.parentNode = this; this.children.push(ch); return ch; },
    insertBefore(ch, ref) { ch.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(ch); else this.children.splice(i, 0, ch); return ch; },
    querySelector(sel) { return sel === '.ago' ? this._ago : null; },
    contains() { return false; }, focus() {}, remove() {}, addEventListener() {}, removeEventListener() {} };
  Object.defineProperty(e, 'firstChild', { get() { return this.children[0] || null; } });
  Object.defineProperty(e, 'innerHTML', { get() { return this._html; }, set(v) { this._html = String(v); this.sets++; this.children = []; this._ago = { textContent: '' }; } });
  return e;
}

// The page the controller lives in: virtual time (a timer fires at its exact moment, then promises settle), scripted /api/ answers, window and document events.
function load(o = {}) {
  const clock = { t: 1800000000000 }, timers = []; let nextId = 1;
  const c = { console, Promise, Error, TypeError, JSON, Object, RegExp, String, Array, Number, parseInt, encodeURIComponent, listeners: {}, docListeners: {}, events: [], sent: [], toasts: [], loader: null, loaderHidden: false };
  c.window = c; c.rand = 0.5;
  c.Math = Object.create(Math); c.Math.random = () => c.rand;
  c.Date = class extends Date { static now() { return clock.t; } constructor(...a) { if (a.length === 0) super(clock.t); else super(...a); } };
  c.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, fn, at: clock.t + (ms || 0), iv: 0 }); return id; };
  c.setInterval = (fn, ms) => { const id = nextId++; timers.push({ id, fn, at: clock.t + ms, iv: ms }); return id; };
  c.clearTimeout = c.clearInterval = (id) => { const i = timers.findIndex((x) => x.id === id); if (i >= 0) timers.splice(i, 1); };
  c.now = () => clock.t;
  c.pending = () => timers.filter((x) => !x.iv).length;          // probes waiting
  c.intervals = () => timers.filter((x) => x.iv).length;         // the banner's 30 s clock
  c.run = async (ms) => {
    const end = clock.t + ms;
    for (let guard = 0; guard < 20000; guard++) {
      const next = timers.filter((x) => x.at <= end).sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!next) break;
      clock.t = Math.max(clock.t, next.at);
      if (next.iv) next.at += next.iv; else timers.splice(timers.indexOf(next), 1);
      next.fn();
      await flush();
    }
    clock.t = end; await flush();
  };
  c.navigator = { onLine: !o.offline };
  c.addEventListener = (type, fn) => { (c.listeners[type] = c.listeners[type] || []).push(fn); };
  c.removeEventListener = () => {};
  c.fire = (type, detail) => { (c.listeners[type] || []).forEach((fn) => fn({ type, detail })); };
  c.docFire = (type) => { (c.docListeners[type] || []).forEach((fn) => fn({ type })); };
  c.dispatchEvent = (e) => { c.events.push(e); return true; };
  c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.eventsOf = (type) => c.events.filter((e) => e.type === type);
  const body = makeEl('body'); c.body = body;
  c.document = { readyState: 'complete', hidden: false, body,
    addEventListener(type, fn) { (c.docListeners[type] = c.docListeners[type] || []).push(fn); }, removeEventListener() {},
    createElement: (tag) => makeEl(tag), getElementById: (id) => (id === 'app-loading' ? c.loader : null), querySelector: () => null, querySelectorAll: () => [] };
  c.location = { reload() {} };
  c.api = () => ({ status: 200 });                                 // what the server answers (a status, or an Error for "no answer")
  c.fetch = function (input) {
    const url = typeof input === 'string' ? input : input.url;
    c.sent.push({ url, at: clock.t });
    const r = c.api(url);
    c.lastRaw = (r instanceof Error) ? Promise.reject(r) : Promise.resolve({ status: r.status, ok: r.status >= 200 && r.status < 300 });
    return c.lastRaw;
  };
  c.pings = () => c.sent.filter((x) => x.url === '/api/health');
  vm.createContext(c);
  vm.runInContext(UI_SRC, c, { filename: 'ui.js' });
  c.Ui.success = (m) => { c.toasts.push(m); };
  return c;
}
const decode = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
// What a member would see.
function banner(c) {
  const el = c.body.children.find((x) => x.id === 'ui-conn');
  if (!el) return { exists: false, shown: false };
  const m = /<span class="msg">([\s\S]*?)<\/span>/.exec(el.innerHTML), btn = el.children.find((x) => x.tagName === 'button');
  return { exists: true, el, shown: /(^| )show( |$)/.test(el.className), offlineStyle: /(^| )offline( |$)/.test(el.className), msg: m ? decode(m[1]) : '',
    ago: el._ago ? el._ago.textContent : '', button: btn ? btn.textContent : null, click: () => btn.onclick(), sets: el.sets };
}
const kind = (c) => c.Ui.connection.state().kind;
const down = (c, status = 503) => { c.api = () => ({ status }); };

const tests = [];
const t = (name, fn) => tests.push({ name, fn });

// ====================================================================== what counts as trouble
t('good answers show nothing, are remembered, and never even create the banner', async () => {
  const c = load();
  await c.fetch('/api/state'); await flush();
  assert.strictEqual(kind(c), null);
  assert.strictEqual(banner(c).exists, false);
  assert.strictEqual(c.Ui.connection.state().lastOk, c.now());
});

t('a request that got no answer is trouble; a request that we cancelled ourselves is not', async () => {
  const c = load();
  c.api = () => new TypeError('Failed to fetch'); await c.fetch('/api/a').catch(() => {}); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 1);
  const abort = Object.assign(new Error('The user aborted a request.'), { name: 'AbortError' });
  c.api = () => abort; await c.fetch('/api/b').catch(() => {}); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 1, 'an abort says nothing about the server');
});

t('any answer below 500 means the server is up (a 401, 409, 422 or 429 is the server talking); 500 and above is trouble', async () => {
  for (const status of [200, 204, 304, 400, 401, 403, 404, 409, 422, 423, 429, 499]) {
    const c = load(); down(c); await c.fetch('/api/a'); await flush();
    assert.strictEqual(c.Ui.connection.state().fails, 1, 'setup');
    c.api = () => ({ status }); await c.fetch('/api/b'); await flush();
    assert.strictEqual(c.Ui.connection.state().fails, 0, 'a ' + status + ' should count as the server being up');
  }
  for (const status of [500, 502, 503, 504, 599]) {
    const c = load(); down(c, status); await c.fetch('/api/a'); await flush();
    assert.strictEqual(c.Ui.connection.state().fails, 1, 'a ' + status + ' should count as trouble');
  }
});

// ====================================================================== the banner: when it appears, what it says
t('one failure shows nothing; a quiet probe follows after about 3 s and only a second failure raises the banner', async () => {
  const c = load(); down(c);
  await c.fetch('/api/state'); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 1); assert.strictEqual(banner(c).exists, false, 'nothing yet');
  await c.run(2900); assert.strictEqual(c.pings().length, 0, 'too early to probe');
  await c.run(200);  assert.strictEqual(c.pings().length, 1, 'one probe, at about 3 s');
  const b = banner(c);
  assert.ok(b.shown && !b.offlineStyle, 'the banner is up, in the amber style');
  assert.strictEqual(b.msg, SERVER_MSG); assert.strictEqual(b.button, 'Retry now'); assert.strictEqual(kind(c), 'server');
});

t('the banner is a polite live region and its "updated … ago" text is hidden from screen readers', async () => {
  const c = load(); down(c); await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  const b = banner(c);
  assert.strictEqual(b.el.attrs.role, 'status'); assert.strictEqual(b.el.attrs['aria-live'], 'polite');
  assert.ok(b.el.innerHTML.includes('class="ago" aria-hidden="true"'), 'the changing time text is aria-hidden');
});

t('after 90 s of trouble the wording gets firmer, says the picks are safe, and is announced only when it changes', async () => {
  const c = load();
  await c.fetch('/api/state'); await flush();                      // a good answer at the start
  await c.run(60000); down(c);
  await c.fetch('/api/state'); await flush();                      // trouble begins at 60 s
  await c.run(4000);                                               // the second failure at 63 s raises the banner
  let b = banner(c); assert.strictEqual(b.msg, SERVER_MSG); const sets = b.sets;
  await c.run(80000);                                              // 144 s: the 30 s clock repainted twice and nothing was said again
  b = banner(c); assert.strictEqual(b.msg, SERVER_MSG); assert.strictEqual(b.sets, sets, 'the same words are not announced again');
  await c.run(20000);                                              // 164 s
  b = banner(c);
  assert.strictEqual(b.msg, SEVERE_MSG); assert.ok(b.offlineStyle, 'red now'); assert.strictEqual(b.sets, sets + 1); assert.strictEqual(kind(c), 'severe');
  assert.strictEqual(b.ago.trim(), '(updated 3 min ago)', 'and how old what they are looking at is');
});

t('a banner that cleared and comes back with the same words is rebuilt, so it is announced again', async () => {
  const c = load(); down(c);
  await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  const first = banner(c).sets;
  c.api = () => ({ status: 200 }); banner(c).click(); await flush(); assert.strictEqual(banner(c).shown, false);
  down(c); await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  assert.ok(banner(c).shown); assert.strictEqual(banner(c).msg, SERVER_MSG); assert.ok(banner(c).sets > first, 'the text was rebuilt for the screen reader');
});

t('the sign-in message: shown when nothing else is wrong, cleared by an empty text, other names ignored, text escaped', () => {
  const c = load();
  c.Ui.connection.reason('signin', SIGNIN_MSG);
  assert.strictEqual(kind(c), 'signin'); let b = banner(c);
  assert.ok(b.shown && !b.offlineStyle); assert.strictEqual(b.msg, SIGNIN_MSG); assert.strictEqual(b.button, 'Retry now');
  c.Ui.connection.reason('whatever', 'x'); assert.strictEqual(b.msg, banner(c).msg, 'a name we do not know changes nothing');
  c.Ui.connection.reason('signin', ''); assert.strictEqual(kind(c), null); assert.strictEqual(banner(c).shown, false);
  c.Ui.connection.reason('signin', '<img src=x onerror=alert(1)>');
  assert.ok(banner(c).el.innerHTML.includes('&lt;img src=x'), 'escaped, never markup');
});

t('priority: offline outranks server trouble, which outranks the sign-in message', async () => {
  const c = load(); down(c);
  c.Ui.connection.reason('signin', SIGNIN_MSG); assert.strictEqual(kind(c), 'signin');
  await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  assert.strictEqual(kind(c), 'server');
  c.navigator.onLine = false; c.fire('offline'); assert.strictEqual(kind(c), 'offline');
  c.navigator.onLine = true; c.fire('online'); await flush();      // the failures of the time offline are forgotten; the first new one is silent
  assert.strictEqual(kind(c), 'signin', 'once the network is back only the sign-in message is left');
  c.Ui.connection.reason('signin', ''); assert.strictEqual(kind(c), null);
});

// ====================================================================== probing
t('while in trouble the probe backs off: 3, 5, 10, 20, 40, then every 60 s', async () => {
  const c = load(); down(c);
  const t0 = c.now(); await c.fetch('/api/state'); await flush();
  await c.run(260000);
  assert.deepStrictEqual(c.pings().map((p) => Math.round((p.at - t0) / 1000)), [3, 8, 18, 38, 78, 138, 198, 258]);
});

t('the delay is jittered by up to 20% so phones do not knock in step', async () => {
  for (const [rand, ms] of [[0, 2400], [1, 3600]]) {
    const c = load(); c.rand = rand; down(c);
    const t0 = c.now(); await c.fetch('/api/state'); await flush(); await c.run(4000);
    assert.strictEqual(c.pings()[0].at - t0, ms, 'rand=' + rand);
  }
});

t('however many failures arrive, only one probe is ever waiting', () => {
  const c = load();
  for (let i = 0; i < 10; i++) c.Ui.connection.report(500);
  assert.strictEqual(c.pending(), 1); c.Ui.connection.report(0); assert.strictEqual(c.pending(), 1);
});

t('a probe never runs while the tab is hidden, and one runs at once when it is shown again', async () => {
  const c = load(); down(c); c.document.hidden = true;
  await c.fetch('/api/state'); await flush();
  await c.run(30000); assert.strictEqual(c.pings().length, 0, 'nothing is sent to the server for nobody');
  c.document.hidden = false; c.docFire('visibilitychange'); await flush();
  assert.strictEqual(c.pings().length, 1, 'one probe as soon as the tab is shown');
});

t('"Retry now" checks at once instead of waiting, cancels the wait, and tells the page', async () => {
  const c = load(); down(c);
  await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  const before = c.pings().length; banner(c).click(); await flush();
  assert.strictEqual(c.pings().length, before + 1, 'a probe straight away');
  assert.strictEqual(c.eventsOf('ipl:retry').length, 1, 'the page re-runs its boot on this');
  assert.strictEqual(c.pending(), 1, 'exactly one probe is waiting afterwards, not two');
  await c.run(4000); assert.strictEqual(c.pings().length, before + 1, 'the wait that was cancelled does not fire anyway (the next one is 5 s after the retry)');
  c.api = () => ({ status: 200 }); banner(c).click(); await flush();
  assert.strictEqual(kind(c), null, 'the banner clears the moment the server answers');
});

// ====================================================================== recovery
t('recovery: the banner clears, "Connected again." shows once, the page is told once, and all timers stop', async () => {
  const c = load(); down(c);
  await c.fetch('/api/state'); await flush(); await c.run(4000);
  assert.strictEqual(kind(c), 'server');
  c.api = () => ({ status: 200 });
  await c.run(6000);                                               // the next probe (8 s) gets a good answer
  assert.strictEqual(kind(c), null); assert.strictEqual(banner(c).shown, false);
  assert.deepStrictEqual(c.toasts, ['Connected again.']); assert.strictEqual(c.eventsOf('ipl:recovered').length, 1);
  const before = c.sent.length; await c.run(300000);
  assert.strictEqual(c.sent.length, before, 'no more probes once the server is back');
  assert.strictEqual(c.pending(), 0); assert.strictEqual(c.intervals(), 0, 'the banner clock is stopped too');
  await c.fetch('/api/state'); await flush();
  assert.deepStrictEqual(c.toasts, ['Connected again.']); assert.strictEqual(c.eventsOf('ipl:recovered').length, 1, 'a healthy answer later is not a second recovery');
});

t('one failure that cleared by itself never showed a banner or a toast, but the page still refreshes', async () => {
  const c = load(); down(c); await c.fetch('/api/a'); await flush();
  c.api = () => ({ status: 200 }); await c.fetch('/api/b'); await flush();
  assert.strictEqual(banner(c).exists, false); assert.deepStrictEqual(c.toasts, []);
  assert.strictEqual(c.eventsOf('ipl:recovered').length, 1); assert.strictEqual(c.pending(), 0, 'and the waiting probe was cancelled');
});

// ====================================================================== offline
t('offline: its own banner; failures while offline are not held against the server; back online it checks at once', async () => {
  const c = load();
  await c.fetch('/api/state'); await flush(); await c.run(180000);  // a good answer, then three quiet minutes
  c.navigator.onLine = false; c.fire('offline');
  let b = banner(c);
  assert.ok(b.shown && b.offlineStyle); assert.strictEqual(b.msg, OFFLINE_MSG); assert.strictEqual(b.ago.trim(), '(updated 3 min ago)'); assert.strictEqual(b.button, 'Retry now');
  c.api = () => new TypeError('Failed to fetch'); await c.fetch('/api/x').catch(() => {}); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 0, 'the phone is offline, the server did nothing wrong');
  assert.strictEqual(c.pending(), 0, 'and nobody keeps knocking on a closed door');
  c.navigator.onLine = true; c.api = () => ({ status: 200 }); c.fire('online'); await flush();
  assert.deepStrictEqual(c.toasts, ['Back online.']); assert.strictEqual(c.pings().length, 1, 'one probe straight away');
  assert.strictEqual(kind(c), null); assert.strictEqual(banner(c).shown, false);
});

t('coming online when we were never offline says nothing', async () => {
  const c = load(); c.fire('online'); await flush();
  assert.deepStrictEqual(c.toasts, []);
});

t('an answer from the server proves the phone is online: a missed "online" event cannot leave "You\'re offline" stuck', async () => {
  const c = load(); c.navigator.onLine = false; c.fire('offline');
  assert.strictEqual(kind(c), 'offline');
  await c.fetch('/api/state'); await flush();
  assert.strictEqual(kind(c), null); assert.strictEqual(banner(c).shown, false);
});

t('a phone that starts offline says so as soon as the screen is up', () => {
  assert.strictEqual(load().Ui.connection.state().offline, false);
  const c = load({ offline: true });
  assert.strictEqual(c.Ui.connection.state().offline, true);
  c.Ui.connection.refresh();                                       // the page calls this when the boot screen goes
  assert.strictEqual(banner(c).msg, OFFLINE_MSG);
});

// ====================================================================== the boot screen
t('while the boot screen is up, server trouble and the sign-in message wait; refresh() shows them when it goes', async () => {
  const c = load(); c.loader = { classList: { contains: (n) => n === 'hidden' && c.loaderHidden } };
  down(c); await c.fetch('/api/a'); await c.fetch('/api/b'); await flush();
  c.Ui.connection.reason('signin', SIGNIN_MSG);
  assert.strictEqual(kind(c), null, 'the boot screen already explains a slow start'); assert.strictEqual(banner(c).exists, false);
  c.loaderHidden = true; c.Ui.connection.refresh();
  assert.strictEqual(kind(c), 'server'); assert.strictEqual(banner(c).msg, SERVER_MSG);
});

t('being offline is not something the boot screen explains, so it shows through, worded for a screen with nothing on it yet', () => {
  const c = load(); c.loader = { classList: { contains: () => false } };
  c.navigator.onLine = false; c.fire('offline');
  assert.strictEqual(kind(c), 'offline'); assert.strictEqual(banner(c).msg, OFFLINE_BOOT_MSG); assert.strictEqual(banner(c).ago, '', 'no "updated" time: nothing has been shown yet');
  c.loaderHidden = true; c.loader = { classList: { contains: (n) => n === 'hidden' } }; c.Ui.connection.refresh();
  assert.strictEqual(banner(c).msg, OFFLINE_MSG, 'once the screen is up it speaks of the data on it');
});

// ====================================================================== the fetch wrapper
t('the wrapper wraps once, hands back the very same promise, counts only our own /api/ calls, and leaves failures to the caller', async () => {
  const c = load();
  c.Ui.connection.watch(); c.Ui.connection.watch();                // already wrapped at load: asking again must not wrap twice
  down(c);
  const p = c.fetch('/api/state'); assert.strictEqual(p, c.lastRaw, 'the caller gets the promise fetch returned');
  await p; await flush(); assert.strictEqual(c.Ui.connection.state().fails, 1, 'counted once, not once per wrap');
  await c.fetch('/static/ui.css'); await c.fetch('https://example.com/api/x'); await c.fetch('/apiary'); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 1, 'nothing but our own /api/ calls is evidence');
  await c.fetch({ url: '/api/y' }); await flush();
  assert.strictEqual(c.Ui.connection.state().fails, 2, 'a Request object is read by its url');
  const boom = new TypeError('Failed to fetch'); c.api = () => boom;
  let err; await c.fetch('/api/z').catch((e) => { err = e; }); assert.strictEqual(err, boom, 'the caller still sees the failure itself');
});

// ====================================================================== where it sits
t('the banner is the first thing in <body>, in the normal flow, so it pushes the page down and never covers the app bar', () => {
  const c = load(); c.body.appendChild(makeEl('div'));
  c.Ui.connection.reason('signin', SIGNIN_MSG);
  assert.strictEqual(c.body.children[0].id, 'ui-conn', 'above everything else');
  const rule = /#ui-conn\s*\{[^}]*\}/.exec(CSS)[0];
  assert.ok(!/position\s*:\s*(fixed|sticky|absolute)/.test(rule), 'a banner that is fixed would cover the sticky app bar');
  assert.ok(/position\s*:\s*relative/.test(rule) && /z-index\s*:\s*var\(--z-banner\)/.test(rule), 'lifted above the boot screen (z 100) so being offline at launch is visible');
  assert.ok(/--z-banner\s*:\s*(\d+)/.test(read('Static', 'theme.css')) && +/--z-banner\s*:\s*(\d+)/.exec(read('Static', 'theme.css'))[1] > 100, 'the token really is above the boot screen');
  assert.ok(+/--z-banner\s*:\s*(\d+)/.exec(read('Static', 'theme.css'))[1] < +/--z-dialog\s*:\s*(\d+)/.exec(read('Static', 'theme.css'))[1], 'and below dialogs');
  assert.ok(/#ui-conn\.show\s*\{\s*display\s*:\s*flex/.test(CSS), 'shown by a class');
  assert.ok(/#ui-conn\.offline\s*\{/.test(CSS) && /#ui-conn button/.test(CSS) && /#ui-conn \.ago/.test(CSS));
});

// ====================================================================== the glue and the page
function glueEnv() {
  const c = { console, Promise, JSON, Date, Math, URL, encodeURIComponent, Object, Error, RegExp, setTimeout, clearTimeout, listeners: {}, events: [], sent: [], store: new Map() };
  c.window = c; c.setInterval = () => 1; c.clearInterval = () => {};
  c.document = { readyState: 'complete', hidden: false, addEventListener() {}, getElementById: () => null };
  c.navigator = { onLine: true };
  c.addEventListener = (type, fn) => { (c.listeners[type] = c.listeners[type] || []).push(fn); };
  c.dispatchEvent = (e) => { c.events.push(e); return true; }; c.CustomEvent = function (type, init) { this.type = type; this.detail = init && init.detail; };
  c.localStorage = { getItem: (k) => (c.store.has(k) ? c.store.get(k) : null), setItem: (k, v) => c.store.set(k, String(v)), removeItem: (k) => c.store.delete(k) };
  c.answer = (url) => (/\/api\/poll/.test(url) ? { state_etag: 'e1' } : (/\/api\/state/.test(url) ? { _saved: 'e1' } : {}));
  c.fetch = (url) => { c.sent.push(String(url).split('?')[0]); const body = c.answer(String(url)); return Promise.resolve({ status: 200, ok: true, headers: { get: () => null }, json: () => Promise.resolve(body) }); };
  c.count = (u) => c.sent.filter((x) => x === u).length;
  vm.createContext(c); vm.runInContext(GLUE, c, { filename: 'ipl_glue.js' });
  return c;
}

t('GLUE: when the server comes back the state is fetched again at once, even if its version has not changed', async () => {
  const c = glueEnv(); await flush();
  assert.strictEqual(c.count('/api/poll'), 1); assert.strictEqual(c.count('/api/state'), 1, 'the first poll loads the state');
  c.listeners['ipl:recovered'][0](); await flush();
  assert.strictEqual(c.count('/api/poll'), 2, 'polls straight away, not in a minute'); assert.strictEqual(c.count('/api/state'), 2, 'and refetches what it missed');
});

t('GLUE: "Retry now" is the page\'s to handle; the glue does not also poll on it (that would fetch everything twice)', () => {
  const c = glueEnv();
  assert.ok(!(c.listeners['ipl:retry'] || []).length);
  assert.ok(/addEventListener\("ipl:retry",function\(\)\{_bootTries=0;_bootstrap\(\);/.test(HTML), 'the page re-runs its boot');
});

t('GLUE: a failing poll is silent (the controller owns the message) and nothing blocks the screen', async () => {
  const c = glueEnv(); await flush();
  c.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
  c.listeners['ipl:recovered'][0](); await flush();
  assert.deepStrictEqual(c.events.map((e) => e.type).filter((x) => x === 'ipl:error'), []);
});

t('PAGE: the sign-in message goes through the one controller; the boot screen asks for the banner when it goes; a failed save promises the picks are kept', () => {
  assert.ok(/function _signinTrouble\(on\)\{Ui\.connection\.reason\("signin"/.test(HTML));
  assert.ok(/function hideLoading\(\)\{[^\n]*Ui\.connection\.refresh\(\)/.test(HTML), 'trouble held back during the boot screen must show when it goes');
  const m = /var trouble=!\(e&&e\.status\)\|\|e\.status>=500;[^\n]*\n[^\n]*kept on this phone/.exec(HTML);
  assert.ok(m, 'a save that failed because of the network or a 5xx says the picks are kept on this phone');
});

t('GUARD: the blocking overlay, the old red banner and their dead code cannot come back', () => {
  const bad = ['error-banner', 'showErrorBanner', 'hideErrorBanner', '$errorBanner', '_showOverlay', '_scheduleOverlay', '_cancelOverlay', 'ipl-maintenance-overlay', 'ipl-overlay-style',
    'ipl:rollover-triggered', 'ipl:error', 'Safe boot', 'Ui.connection.set(', 'Ui.connection.clear('];
  for (const [name, src] of [['templates/index.html', HTML], ['Static/ipl_glue.js', GLUE], ['Static/ui.js', UI_SRC], ['Static/ui.css', CSS], ['Static/style.css', STYLE]]) {
    for (const s of bad) assert.ok(!src.includes(s), name + ' mentions "' + s + '" again');
  }
});

(async () => {
  let failed = 0;
  for (const { name, fn } of tests) {
    try { await fn(); console.log('PASS  ' + name); } catch (e) { failed++; console.log('FAIL  ' + name + '\n      ' + String(e && e.stack || e).split('\n').slice(0, 3).join('\n      ')); }
  }
  console.log(failed ? '\n' + failed + ' failed' : '\nall passed');
  process.exit(failed ? 1 : 0);
})();
