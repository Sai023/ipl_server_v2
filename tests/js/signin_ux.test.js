// Regression tests for the sign-in journey fixes of the UX pass (2026-11-22, "walk through the new member journey and the login process for existing users").
// Loads the REAL Static/ui.js and the REAL page functions out of templates/index.html (so a change there is what gets tested).
//   * THE SIGN-IN CARD: a passcode chip only for a member who has a passcode; the newcomer hint right under the Google button (and still there when nobody has a passcode)
//   * NAME PILLS: a member without a passcode opens Google's sheet, never a passcode prompt that can not work
//   * THE PASSCODE PROMPT: "that passcode is not right" only for a wrong passcode; no connection / too many tries / a failing server say what they are; no double send
//   * THE HISTORY LOAD: a failed or very slow load says so with "Try again" (it was a spinner for ever); an older reply never lands on another member or competition
//   * MY TEAM for a member who joined after the lock
//   node tests/js/signin_ux.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const UI_SRC = fs.readFileSync(process.env.UI_JS_PATH || path.join(ROOT, 'Static', 'ui.js'), 'utf8');
const HTML = fs.readFileSync(process.env.INDEX_HTML_PATH || path.join(ROOT, 'templates', 'index.html'), 'utf8');

// Source of one top-level `function name(...) {...}` in the page script, found by brace matching (a quote always opens a string: no apostrophes in comments inside these functions).
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
const tests = [];
const t = (name, fn) => tests.push({ name, fn });

const FUTURE = () => new Date(Date.now() + 5 * 86400e3).toISOString(), PAST = () => new Date(Date.now() - 3600e3).toISOString();
const FNS = ['_hasPasscode', 'attemptLogin', '_buildLoginCard', '_submitPasscode', '_loadHistory', '_retryHistory', '_historyErrorHtml',
  '_buildThisWeekCard', '_histWeeks', '_getThisWeekData', '_getCurrentWeekRow', '_lockIn', '_deadlineLocked', '_finalWeek', '_activeCompObj', '_nowMs'];

// google: false = the module is absent; otherwise {block, pin, pinMembers} like the stand-in of google_signin.test.js
function ux(o = {}) {
  const c = { console: { log: console.log, warn() {}, error() {} }, Promise, JSON, Date, Math, Object, Array, String, Error, TypeError, RegExp, Number, isFinite, parseInt, Set, Map, calls: [], timers: [], renders: 0, byId: {}, nw: 0 };   // (the page logs a failed load with console.error: not wanted in the test output)
  c.window = c;
  c.setTimeout = (fn, ms) => { const h = { fn, ms, cleared: false }; c.timers.push(h); return h; };
  c.clearTimeout = (h) => { if (h) h.cleared = true; };
  c.addEventListener = () => {}; c.removeEventListener = () => {}; c.dispatchEvent = () => {};
  c.CustomEvent = function (type) { this.type = type; };
  c.navigator = {}; c.location = {};
  c.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  c.document = { addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], querySelector: () => null, getElementById: (id) => c.byId[id] || null, body: {}, activeElement: null, readyState: 'complete' };
  vm.createContext(c);
  vm.runInContext(UI_SRC, c, { filename: 'ui.js' });
  c.Ui.pinReset = (inp, shake) => { c.calls.push(['pinReset', !!shake]); if (inp) inp.value = ''; };   // (the boxes are DOM: only that it was asked matters here)
  Object.assign(c, {
    _username: o.username === undefined ? 'Sai' : o.username, _historyData: null, _historyFailed: false, _historySeq: 0, _historyTimer: null, _historyViewWk: null,
    _currentWeek: 1, _weekKnown: true, _bootSeq: 0, _lockedWeek: null, _sessionLost: false,
    _activeComp: 'sa20_2027', _competitions: [{ slug: 'sa20_2027', name: 'SA20 2027', status: 'active' }],
    _state: { deadline: { next_lock_utc: FUTURE(), status: 'active' }, members: {} },
  });
  c.render = () => { c.renders++; };
  c.esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');   // (as in the page: its source has regex literals the extractor cannot read)
  c.escAttr = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  c._nwInit = (row) => { c.calls.push(['nwInit', row && row.week_no]); };
  c._showPasscodePromptModal = (name, note, reauth) => c.calls.push(['prompt', name]);
  c._googleSignInAgain = (name, onCancel, note, reauth) => c.calls.push(['google', name]);
  c._closePasscodePrompt = (done) => c.calls.push(['closePrompt', !!done]);
  c._reauthDone = () => c.calls.push(['reauthDone']); c._establishSession = (n) => c.calls.push(['established', n]);
  c.IplApi = { login: (n, p) => { c.calls.push(['login', n, p]); return c.loginImpl(n, p); }, getHistory: (u) => { c.calls.push(['getHistory', u]); return c.historyImpl(u); } };
  c.loginImpl = () => Promise.resolve({ ok: true, name: 'Sai', is_admin: false, must_change: false });
  c.historyImpl = () => Promise.resolve({ ok: true, current_week: 1, weeks: [{ week_no: 1, is_current: true }] });
  if (o.google !== false) {
    const g = o.google || {};
    c.IplGoogle = {
      loginBlockHtml: () => (g.block === false ? '' : '<div id="google-signin"></div>'), pinEnabled: () => g.pin !== false,
      pinMembers: () => (g.pinMembers === undefined ? null : g.pinMembers), redeeming: () => false, handoffNoteHtml: () => g.note || '', configPending: () => !!g.pending,
      remember: (v) => c.calls.push(['remember', v]),
    };
  }
  for (const n of FNS) vm.runInContext(fnSource(n), c);
  return c;
}
const timerOf = (c, ms) => c.timers.find((x) => x.ms === ms && !x.cleared);
const names = (c) => c.calls.map((x) => x[0]);

// ====================================================================== THE SIGN-IN CARD
t('CARD: with Google on, a passcode chip only for a member who has a passcode; the newcomer hint sits under the Google button, before the chips', () => {
  const c = ux({ google: { pinMembers: ['Moe'] } });
  const h = c._buildLoginCard(['Moe', 'Sai', 'Zed']);
  assert.ok(h.indexOf("attemptLogin('Moe')") >= 0, 'Moe has a passcode: a chip');
  assert.strictEqual(h.indexOf("attemptLogin('Sai')"), -1, 'Sai has none (he signs in with Google): a chip for him would ask for something that does not exist');
  assert.strictEqual(h.indexOf("attemptLogin('Zed')"), -1);
  assert.ok(/or use your passcode/.test(h), 'the chips are introduced as the second way');
  assert.ok(/Continue with Google, or pick your name to use your passcode\./.test(h));
  const g = h.indexOf('id="google-signin"'), hint = h.indexOf('New here?'), or = h.indexOf('or use your passcode'), chip = h.indexOf('login-chip');
  assert.ok(g >= 0 && g < hint && hint < or && or < chip, 'Google, then the newcomer hint, then "or use your passcode", then the names: ' + [g, hint, or, chip]);
  assert.ok(/New here\? Continue with Google, then choose/.test(h) && /I.m new here/.test(h));
  assert.strictEqual(h.indexOf('register-input'), -1, 'no passcode registration while Google is on');
});

t('CARD: nobody left on a passcode: no chips, no divider, but the newcomer hint stays (it used to vanish with the chips)', () => {
  for (const g of [{ pin: false, pinMembers: [] }, { pinMembers: [] }]) {     // the second: the flag and the list disagree for a moment; the list wins
    const h = ux({ google: g })._buildLoginCard(['Moe', 'Sai']);
    assert.strictEqual(h.indexOf('login-chip'), -1); assert.strictEqual(h.indexOf('or use your passcode'), -1);
    assert.ok(/Sign in with Google to continue\./.test(h) && !/pick your name/.test(h), 'the sentence matches what is on the card');
    assert.ok(/New here\? Continue with Google, then choose/.test(h), 'a newcomer is still told how to join');
  }
});

t('CARD: an older server that does not list the passcode names gets a chip for everybody (as before); Google off or not ready changes nothing', () => {
  const old = ux({ google: { pinMembers: null } })._buildLoginCard(['Moe', 'Sai']);
  assert.ok(old.indexOf("attemptLogin('Moe')") >= 0 && old.indexOf("attemptLogin('Sai')") >= 0);
  const notReady = ux({ google: { block: false, pinMembers: ['Moe'] } })._buildLoginCard(['Moe', 'Sai']);
  assert.ok(notReady.indexOf("attemptLogin('Sai')") >= 0, 'until the server says Google is on, everybody has a chip');
  assert.ok(/Pick your name to sign in\./.test(notReady) && notReady.indexOf('register-input') >= 0);
  const off = ux({ google: false })._buildLoginCard(['Moe', 'Sai']);
  assert.ok(off.indexOf("attemptLogin('Sai')") >= 0 && off.indexOf('register-input') >= 0 && off.indexOf('Register here') >= 0, 'without the module: exactly as before');
});

t('CARD: until the server has said whether Google sign-in is set up the card is a neutral "Getting sign-in ready", not the passcode card for a moment; it keeps the app hand-off note', () => {
  const h = ux({ google: { pending: true, note: '<p class="handoff-note">You are signing in to the BCL Fantasy app.</p>' } })._buildLoginCard(['Moe', 'Sai']);
  assert.ok(/Getting sign-in ready/.test(h) && /Who.s playing\?/.test(h) && /role="status"/.test(h), h);
  for (const wrong of ['login-chip', 'register-input', 'Register here', 'Pick your name', 'google-signin', 'New here?']) assert.strictEqual(h.indexOf(wrong), -1, 'not yet: ' + wrong);
  assert.ok(/handoff-note/.test(h), 'a member who came from the app is still told why they are here');
  const ready = ux({ google: { pending: false, pinMembers: ['Moe'] } })._buildLoginCard(['Moe', 'Sai']);
  assert.ok(!/Getting sign-in ready/.test(ready) && ready.indexOf('login-chip') >= 0, 'once it has answered the real card shows');
  const absent = ux({ google: false })._buildLoginCard(['Moe']);
  assert.ok(!/Getting sign-in ready/.test(absent), 'without the module there is nothing to wait for');
});

t('CARD: a name with an apostrophe or a quote cannot break out of the chip', () => {
  const h = ux({ google: { pinMembers: ["O'Neil", 'A"B'] } })._buildLoginCard(["O'Neil", 'A"B']);
  assert.ok(h.indexOf("attemptLogin('O\\'Neil')") >= 0, 'the apostrophe is escaped inside the handler: ' + h);
  assert.ok(!/onclick="attemptLogin\('O'Neil'\)"/.test(h), 'and never left bare, which would end the string early');
  assert.ok(h.indexOf('A&quot;B') >= 0, 'a double quote is escaped in the visible name');
});

// ====================================================================== NAME PILLS AND CHIPS
t('PILLS: a member who has a passcode gets the passcode prompt; one who has none gets Google\'s sheet; nobody is asked for a passcode that cannot exist', () => {
  let c = ux({ google: { pinMembers: ['Moe'] } });
  assert.strictEqual(c.attemptLogin('Moe'), true); assert.strictEqual(c.attemptLogin('Sai'), true);
  assert.deepStrictEqual(c.calls, [['prompt', 'Moe'], ['google', 'Sai']]);
  c = ux({ google: { pinMembers: null } }); c.attemptLogin('Sai');
  assert.deepStrictEqual(c.calls, [['prompt', 'Sai']], 'the server has not said who has a passcode: the prompt copes (pin_retired carries on with Google)');
  c = ux({ google: false }); c.attemptLogin('Sai');
  assert.deepStrictEqual(c.calls, [['prompt', 'Sai']], 'no Google module: as before');
  c = ux({ google: { pinMembers: [] } }); assert.strictEqual(c.attemptLogin(''), false); assert.strictEqual(c.attemptLogin(null), false);
  assert.deepStrictEqual(c.calls, [], 'no name: nothing opens');
});

// ====================================================================== THE PASSCODE PROMPT
function prompt(c, pc = '1234') {
  c.byId['passcode-input'] = { value: pc }; c.byId['passcode-error'] = { textContent: '' }; c.byId['passcode-submit-btn'] = { disabled: false, textContent: 'Sign in' };
  return { inp: c.byId['passcode-input'], err: c.byId['passcode-error'], btn: c.byId['passcode-submit-btn'] };
}
const fail = (status, body, extra) => Object.assign(new Error('HTTP ' + status), { status, serverMessage: (body && body.error) || '', serverData: body || {} }, extra);

t('PROMPT: a wrong passcode says so in plain words (not the API wording "wrong name or passcode"), clears the boxes and lets them try again', async () => {
  const c = ux(); const p = prompt(c);
  c.loginImpl = () => Promise.reject(fail(401, { error: 'wrong name or passcode', reason: 'bad_credentials' }));
  c._submitPasscode('Moe'); await flush();
  assert.strictEqual(p.err.textContent, '⚠ That passcode is not right.');
  assert.strictEqual(p.btn.disabled, false); assert.strictEqual(p.btn.textContent, 'Sign in'); assert.ok(names(c).indexOf('pinReset') >= 0 && p.inp.value === '');
});

t('PROMPT: a good passcode is never called wrong because the server did not answer: no connection, a failing server and too many tries each say what they are', async () => {
  const cases = [
    [Object.assign(new TypeError('Failed to fetch'), {}), /Can.t reach the server/],
    [fail(500, { error: 'boom', reason: 'server_error', request_id: 'ab12cd34' }), /The server had a problem.*\(ref ab12cd34\)/],
    [fail(429, { error: 'slow down', reason: 'rate_limited' }), /Too many tries\. Wait a minute/],
    [fail(502, {}), /The server had a problem/],
  ];
  for (const [err, want] of cases) {
    const c = ux(); const p = prompt(c);
    c.loginImpl = () => Promise.reject(err);
    c._submitPasscode('Moe'); await flush();
    assert.ok(want.test(p.err.textContent), 'shown: ' + p.err.textContent + ' wanted ' + want);
    assert.ok(!/not right|Wrong passcode|wrong/i.test(p.err.textContent), 'must not blame the passcode: ' + p.err.textContent);
    assert.strictEqual(p.btn.disabled, false, 'the button is back, they can try again');
  }
});

t('PROMPT: a member who switched to Google (maybe on another phone) carries on with Google, here; a good passcode signs in', async () => {
  let c = ux(); prompt(c);
  c.loginImpl = () => Promise.reject(fail(401, { error: 'This account signs in with Google.', reason: 'pin_retired' }));
  c._submitPasscode('Moe'); await flush();
  assert.deepStrictEqual(c.calls.filter((x) => x[0] !== 'login'), [['remember', true], ['closePrompt', true], ['google', 'Moe']]);
  c = ux(); const p = prompt(c, '4321');
  c._submitPasscode('Moe'); await flush();
  assert.deepStrictEqual(c.calls.filter((x) => x[0] !== 'pinReset'), [['login', 'Moe', '4321'], ['closePrompt', true], ['established', 'Sai']]);
  assert.strictEqual(p.err.textContent, '');
});

t('PROMPT: the fourth digit and Enter can both send the passcode; the second send is ignored while the first is on its way', async () => {
  const c = ux(); const p = prompt(c);
  c.loginImpl = () => new Promise(() => {});          // never answers
  c._submitPasscode('Moe');
  assert.strictEqual(p.btn.disabled, true); assert.strictEqual(p.btn.textContent, 'Signing in…');
  c._submitPasscode('Moe'); c._submitPasscode('Moe');
  assert.strictEqual(c.calls.filter((x) => x[0] === 'login').length, 1, 'one request, however many times it was triggered');
});

t('PROMPT: fewer than four digits is refused on the spot and sends nothing', () => {
  const c = ux(); const p = prompt(c, '12');
  c._submitPasscode('Moe');
  assert.strictEqual(p.err.textContent, 'Passcode must be exactly 4 digits.'); assert.strictEqual(c.calls.filter((x) => x[0] === 'login').length, 0);
});

// ====================================================================== THE HISTORY LOAD (My team, Pick)
const row = (o) => Object.assign({ week_no: 1, is_current: true, this_week: { team: [], cap: null, vc: null }, next_week: { team: [], cap: null, vc: null } }, o);

t('HISTORY: a good answer fills the page and clears any earlier failure', async () => {
  const c = ux();
  c._historyFailed = true;
  c._loadHistory(); assert.strictEqual(c._historyFailed, false, 'a new try starts clean');
  await flush();
  assert.ok(c._historyData && c._historyData.ok); assert.strictEqual(c._historyFailed, false); assert.strictEqual(c._weekKnown, true);
  assert.ok(names(c).indexOf('nwInit') >= 0 && c.renders >= 1);
  assert.strictEqual(timerOf(c, 25000), undefined, 'the slow-load timer is cancelled once the answer is in');
});

t('HISTORY: a failed load (no network, a server error, or ok:false) says so on My team with "Try again" instead of "Loading your team" for ever', async () => {
  for (const impl of [() => Promise.reject(new TypeError('Failed to fetch')), () => Promise.reject(fail(503, {})), () => Promise.resolve({ ok: false }), () => Promise.resolve(null)]) {
    const c = ux(); c.historyImpl = impl;
    c._loadHistory(); await flush();
    assert.strictEqual(c._historyFailed, true); assert.strictEqual(c._historyData, null);
    assert.ok(c.renders >= 1, 'the page was redrawn to show it');
    const card = c._buildThisWeekCard();
    assert.ok(/Could not load your team/.test(card) && /_retryHistory\(\)/.test(card) && /Your saved picks are safe/.test(card), card);
    assert.ok(!/Loading your team/.test(card) && !/ui-spinner/.test(card), 'no spinner');
  }
});

t('HISTORY: while it is loading My team shows the spinner (not the error); Try again asks again and clears the error', async () => {
  const c = ux(); c.historyImpl = () => new Promise(() => {});
  c._loadHistory();
  assert.ok(/Loading your team/.test(c._buildThisWeekCard()) && !/Could not load/.test(c._buildThisWeekCard()));
  c._historyFailed = true; const before = c.renders;
  c.historyImpl = () => Promise.resolve({ ok: true, current_week: 1, weeks: [row({})] });
  c._retryHistory();
  assert.strictEqual(c._historyFailed, false); assert.ok(c.renders > before, 'the card is redrawn at once (the spinner returns while it asks)');
  assert.strictEqual(c.calls.filter((x) => x[0] === 'getHistory').length, 2);
  await flush(); assert.ok(c._historyData);
});

t('HISTORY: a server that is still waking up: after 25 s the spinner becomes "Try again"; an answer that arrives later still lands and clears it', async () => {
  const c = ux(); let answer; c.historyImpl = () => new Promise((res) => { answer = res; });
  c._loadHistory();
  const slow = timerOf(c, 25000); assert.ok(slow, 'a 25 s timer is running');
  assert.strictEqual(c._historyFailed, false); slow.fn();
  assert.strictEqual(c._historyFailed, true); assert.ok(/Could not load your team/.test(c._buildThisWeekCard()));
  answer({ ok: true, current_week: 2, weeks: [row({ week_no: 2 })] }); await flush();
  assert.strictEqual(c._historyFailed, false, 'the late answer clears the error'); assert.strictEqual(c._currentWeek, 2); assert.ok(c._historyData);
});

t('HISTORY: the timer does nothing once the data is there (no error card on top of a loaded team)', async () => {
  const c = ux(); c._loadHistory(); const slow = timerOf(c, 25000);
  await flush(); assert.ok(c._historyData);
  c._historyFailed = false; if (slow) slow.fn();
  assert.strictEqual(c._historyFailed, false);
});

t('HISTORY: a reply for an older request, another member or another competition is ignored', async () => {
  // an older request: a retry started a newer one, the old answer arrives last
  let c = ux(); const answers = []; c.historyImpl = () => new Promise((res) => answers.push(res));
  c._loadHistory(); c._loadHistory();
  answers[0]({ ok: true, current_week: 7, weeks: [row({ week_no: 7 })] }); await flush();
  assert.strictEqual(c._historyData, null, 'the first request was replaced: its answer does not count');
  answers[1]({ ok: true, current_week: 3, weeks: [row({ week_no: 3 })] }); await flush();
  assert.strictEqual(c._currentWeek, 3);
  // another member signed in meanwhile
  c = ux(); c.historyImpl = () => new Promise((res) => answers.push(res)); answers.length = 0;
  c._loadHistory(); c._username = 'Moe';
  answers[0]({ ok: true, current_week: 5, weeks: [row({ week_no: 5 })] }); await flush();
  assert.strictEqual(c._historyData, null, 'Sai\'s rows must never land on Moe');
  // another competition (the page counts a switch in _bootSeq)
  c = ux(); answers.length = 0; c.historyImpl = () => new Promise((res) => answers.push(res));
  c._loadHistory(); c._bootSeq++;
  answers[0]({ ok: true, current_week: 5, weeks: [row({ week_no: 5 })] }); await flush();
  assert.strictEqual(c._historyData, null);
  // and a FAILURE of a stale request shows nothing
  c = ux(); const rej = []; c.historyImpl = () => new Promise((_, r) => rej.push(r));
  c._loadHistory(); c._loadHistory(); rej[0](new Error('old')); await flush();
  assert.strictEqual(c._historyFailed, false, 'a stale failure must not raise the error card over a request that is still running');
});

t('HISTORY: nobody signed in: nothing is requested', () => {
  const c = ux({ username: null }); c._loadHistory();
  assert.strictEqual(c.calls.filter((x) => x[0] === 'getHistory').length, 0);
});

// ====================================================================== MY TEAM FOR A MEMBER WHO JOINED AFTER THE LOCK
function lateJoiner(deadline, over) {
  const c = ux({ username: 'Nina' });
  c._historyData = { ok: true, current_week: 1, weeks: [row({})] };
  c._state = { deadline: Object.assign({ status: 'active', managed: true }, deadline), members: {} };
  Object.assign(c, over || {});
  return c;
}

t('MY TEAM: a member who joined after the lock is told this week scores 0 and when picks open, not to "pick your team before the lock"', () => {
  const h = lateJoiner({ next_lock_utc: PAST(), awaiting: { kind: 'rollover', to_week: 2 } })._buildThisWeekCard();
  assert.ok(/No XI locked for this week/.test(h) && /scores 0 points/.test(h) && /Next week.s picks open after the next rollover/.test(h), h);
  assert.ok(!/before the lock/.test(h) && !/Pick your team<\/button>/.test(h), 'nothing to pick for this week any more: no button either');
});

t('MY TEAM: the final week, a week before the lock, and a finished competition each get their own honest sentence', () => {
  let h = lateJoiner({ next_lock_utc: null, season_over: true })._buildThisWeekCard();
  assert.ok(/final week and no XI was locked/.test(h) && !/next rollover/.test(h), h);
  h = lateJoiner({ next_lock_utc: FUTURE() })._buildThisWeekCard();
  assert.ok(/Pick your team before the lock and it will appear here\./.test(h) && /switchTab\('next'\)/.test(h), 'before the lock: pick (and the button is there)');
  h = lateJoiner({ next_lock_utc: PAST(), status: 'completed' }, { _currentWeek: 3, _historyData: { ok: true, current_week: 3, weeks: [row({ week_no: 3 })] }, _competitions: [{ slug: 'sa20_2027', name: 'SA20 2027', status: 'completed' }] })._buildThisWeekCard();
  assert.ok(/You did not have a team locked this week\./.test(h) && !/next rollover/.test(h), 'a finished competition never promises a next rollover: ' + h);
});

// ====================================================================== CONTRACTS OF THE PIECES THE UNIT TESTS CANNOT REACH (the page script is one big file: these pin the lines that matter)
t('PAGE: the finished-competition banner is drawn on the tabs that carry the deadline banner, and the page redraws when Google turns out to be unavailable', () => {
  assert.ok(/\["match-centre","leaderboard","team"\]\.indexOf\(_activeTab\)>-1\)html\+=_buildActionBanner\(\)\+_buildNextCompBanner\(\);/.test(HTML), 'render() does not draw _buildNextCompBanner next to _buildActionBanner');
  assert.ok(/addEventListener\("ipl:google-unavailable",function\(\)\{if\(!_username&&_state\)render\(_state\);\}\)/.test(HTML), 'nothing redraws the card when Google is not available (the neutral card would stay)');
});

t('PAGE: the passcode prompt is a dialog, closes on Escape like the other sheets, and (opened from "You have been signed out") still returns there', () => {
  assert.ok(/<div id="passcode-prompt-modal" role="dialog" aria-modal="true" aria-label="Sign in"/.test(HTML), 'no dialog role on the passcode prompt');
  assert.ok(/if\(document\.getElementById\("passcode-prompt-modal"\)\)\{_closePasscodePrompt\(\);return;\}/.test(HTML), 'Escape does not close the passcode prompt through _closePasscodePrompt (which brings the signed-out sheet back)');
});

t('PAGE: signing out and switching competition drop a history request that is still on its way; the boot asks again when its own request failed', () => {
  for (const f of ['logout', '_resetCompScope']) {
    const s = fnSource(f);
    assert.ok(/_historySeq\+\+/.test(s) && /_historyFailed=false/.test(s) && /clearTimeout\(_historyTimer\)/.test(s), f + ' leaves a history request (or its failure flag, or its 25 s timer) alive');
  }
  assert.ok(/else if\(savedUser&&_username&&!_historyData\)_loadHistory\(\);/.test(fnSource('_bootstrap')), 'a failed history request at boot is never retried (the page spun for ever)');
  for (const where of ['_doStateRefresh', '_bootstrap']) assert.ok(/_historyData=history;_historyFailed=false;/.test(fnSource(where)), where + ' does not clear a failed-history flag when the history arrives');
});

// A test that waits for a promise that never settles would let Node leave quietly with exit code 0 and the later tests would never run (a silent pass).
const withTimeout = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timed out after ' + ms + ' ms: a promise never settled')), ms))]);
(async () => {
  let failed = 0, ran = 0;
  process.on('exit', (code) => { if (ran < tests.length && code === 0) { console.log('\nINCOMPLETE: only ' + ran + ' of ' + tests.length + ' tests ran'); process.exitCode = 1; } });
  for (const x of tests) {
    try { await withTimeout(Promise.resolve().then(x.fn), 8000); console.log('PASS  ' + x.name); } catch (e) { failed++; console.log('FAIL  ' + x.name + '\n      ' + String((e && e.stack) || e).split('\n').slice(0, 6).join('\n      ')); }
    ran++;
  }
  if (failed) { console.log('\n' + failed + ' FAILED'); process.exit(1); }
  console.log('\n' + tests.length + ' of ' + tests.length + ' passed');
})();
