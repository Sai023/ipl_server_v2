// Regression tests for the edit-team flow (the Pick tab): picks are never lost, a locked week is read-only and says why, the Save button
// cannot be double-fired or fail silently, the hint line tells the member what is left to do, keyboard focus survives a redraw.
// Loads the REAL Static/ui.js and the REAL page functions out of templates/index.html (so a change there is what gets tested).
//   node tests/js/team_edit_ux.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
// Asked for 2026-11-18 ("comprehensively test the edit team functionality like a UX developer and make it as user friendly as possible").
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const UI_SRC = fs.readFileSync(process.env.UI_JS_PATH || path.join(ROOT, 'Static', 'ui.js'), 'utf8');
const HTML = fs.readFileSync(process.env.INDEX_HTML_PATH || path.join(ROOT, 'templates', 'index.html'), 'utf8');

// Source of one top-level `function name(...) {...}` in the page script, found by brace matching (the functions loaded here contain
// no regex literals with quotes in them, so a quote always opens a string).
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

const P = (id, name, team, role, price, pts) => ({ id, name, team, role, price, season_pts: pts });
const PLAYERS = [
  P('q1', 'Virat Kohli', 'RCB', 'BAT', 15, 1000), P('q2', 'Jos Buttler', 'GT', 'WK', 11, 900), P('q3', 'Rohit Sharma', 'MI', 'BAT', 12, 800),
  P('q4', 'MS Dhoni', 'CSK', 'WK', 8, 700), P('q5', 'Jasprit Bumrah', 'MI', 'BOWL', 13, 950), P('q6', 'Rajat Patidar', 'RCB', 'BAT', 11, 861),
  P('q7', 'Ravindra Jadeja', 'CSK', 'AR', 10, 750), P('q8', 'Rashid Khan', 'GT', 'BOWL', 9, 650), P('q9', 'Hardik Pandya', 'MI', 'AR', 12, 820),
  P('q10', 'Yuzvendra Chahal', 'PBKS', 'BOWL', 7, 500), P('q11', 'Sanju Samson', 'RR', 'WK', 9, 600), P('q12', 'Shubman Gill', 'GT', 'BAT', 10, 780),
  P('q13', 'Cheap Bowler', 'DC', 'BOWL', 4, 100), P('q14', 'Cheap Batter', 'DC', 'BAT', 4, 90),
];
// a valid XI: 11 + 8 + 11 + 10 + 9 + 7 + 9 + 10 + 4 + 4 + 12 = 95 CR
const XI = ['q2', 'q4', 'q6', 'q7', 'q8', 'q10', 'q11', 'q12', 'q13', 'q14', 'q3'];
const KEY = 'ipl_draft:sa20_2027:Sai';
const FUTURE = () => new Date(Date.now() + 5 * 86400e3).toISOString(), PAST = () => new Date(Date.now() - 3600e3).toISOString();
const clone = (x) => JSON.parse(JSON.stringify(x));

const LOADED = ['_teamCost', '_remaining', '_priceBounds', '_nwSig', '_nwDirty', '_nwHasUnsaved', '_getCurrentWeekRow', '_nwLoadSaved', '_nwNewWeek', '_nwInit',
  '_draftKey', '_draftStore', '_draftRestore', '_nwChanged', '_nwSetRole', '_nwSetCap', '_nwSetVc', '_nwRemove', '_nwToggle', '_nwDiscard', '_nwSave', '_nwTooLate', '_nwCloseLayer',
  '_nwPickRole', '_nwOpenPlayer', '_nowMs', '_deadlineLocked', '_finalWeek', '_deadlineText', '_lockUrgent', '_pickerRowsHtml', '_lockIn', '_buildActionBanner', '_nwShown', '_buildNwSquad', '_barParts', '_pickerXiHtml', '_pickerBarHtml', '_buildBudgetBar', '_pageStripHtml', '_statusChip', '_syncBarH', '_buildNextWeekCard', '_focusKey', '_focusFind', '_navDot',
  // Phase 1.6: the week a draft is for, Keep this XI, a draft from a week that moved on, the lock card wording, notices, local time
  '_nwTargetWeek', '_nwSameAsXi', '_nwCarried', '_nwKeep', '_nwWeekMoved', '_nwOfferStale', '_nwAdopt', '_nwLetGo', '_nwSyncTarget', '_whenLocal', '_hhmm', '_nwWhenLine',
  '_lockCardText', '_nwSeenKey', '_nwSeenWeek', '_nwDismissNotice', '_nwNotices',
  // the UX pass (2026-11-22): a finished competition says where to go; a failed history load says so
  '_nextComp', '_historyErrorHtml', '_buildNextCompBanner'];

function load(o = {}) {
  const c = { toasts: [], warns: [], fails: [], events: [], confirms: [], sheets: [], sent: [], timers: [], renders: 0, backs: 0, saveImpl: () => Promise.resolve({ ok: true }), store: o.store || new Map(), storeBroken: false };
  c.window = c; c.console = console; c.setTimeout = setTimeout; c.clearTimeout = clearTimeout;
  c.addEventListener = () => {}; c.removeEventListener = () => {}; c.dispatchEvent = (e) => { c.events.push(e && e.type); };
  c.CustomEvent = function (type) { this.type = type; };
  c.navigator = {}; c.location = {};
  c.localStorage = {
    getItem: (k) => { if (c.storeBroken) throw new Error('storage blocked'); return c.store.has(k) ? c.store.get(k) : null; },
    setItem: (k, v) => { if (c.storeBroken) throw new Error('storage blocked'); c.store.set(k, String(v)); },
    removeItem: (k) => { if (c.storeBroken) throw new Error('storage blocked'); c.store.delete(k); },
  };
  c.history = { state: null, back() { c.backs++; } };
  c.document = { addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], querySelector: () => null, getElementById: () => null, body: {}, activeElement: null, readyState: 'complete' };
  vm.createContext(c);
  vm.runInContext(UI_SRC, c, { filename: 'ui.js' });
  Object.assign(c, {
    BUDGET_TOTAL: 100, XI_SIZE: 11, _username: 'Sai', _activeComp: 'sa20_2027', _currentWeek: 3, _weekKnown: true,
    _nwDraft: { team: [], cap: null, vc: null }, _nwSaved: null, _nwCarry: false, _nwRestored: false, _lockedWeek: null, _nwSaving: false, _nwSlow: false, _nwSlowT: null,
    _nwTouched: false, _nwKeeping: false, _nwTarget: null, _nwOffer: false,
    _pickerSearch: '', _pickerRole: 'All', _pickerTeams: [], _pickerSort: 'season', _pickerMaxPrice: null, _pickerFiltersOpen: false, _pickerXiOpen: false, matches: [], _barH: 0,
    _pickOpen: false, _historyData: null, _historyFailed: false, _competitions: [], _state: { deadline: { next_lock_utc: FUTURE(), status: 'active' } }, $app: null,
  });
  const catalogue = o.players || PLAYERS;
  c._allPlayers = () => catalogue.slice().sort((a, b) => (a.name < b.name ? -1 : 1));
  c.resolvePlayer = (id) => catalogue.find((p) => p.id === id || p.name === id) || { name: String(id), role: '', team: '', price: 0 };
  c.render = () => { c.renders++; };
  c.esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  c.escAttr = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  c._avClass = () => ''; c._initials = (n) => String(n).slice(0, 2); c._roleBadge = (r) => (r ? '<i class="badge">' + r + '</i>' : '');
  c._wide = () => false; c._activeCompObj = () => ({ status: 'active' }); c._buildPicker = () => '<picker-stub/>';
  c.cssVars = {}; c.fakeHeader = null;
  c.document.documentElement = { style: { setProperty: (k, v) => { c.cssVars[k] = v; } } };
  c.document.querySelector = (sel) => (sel === '.app-bar' ? c.fakeHeader : null);
  if (o.realPicker) for (const n of ['_buildPicker', '_pickerView', '_pickerToolbarHtml', '_pickerFiltersHtml', '_pickerResultsHtml', '_pickerActive', '_pickerToggleXi']) vm.runInContext(fnSource(n), c, { filename: n });
  c.Ui.toast = (msg, opts) => { c.toasts.push({ msg, opts }); };
  c.Ui.warn = (m) => { c.warns.push(m); }; c.Ui.success = (m) => { c.toasts.push({ msg: m, type: 'success' }); };
  c.Ui.fail = (e, prefix) => { c.fails.push((prefix ? prefix + ': ' : '') + c.Ui.errorMessage(e)); };
  c.confirmAnswer = false;
  c.Ui.confirm = (opts) => { c.confirms.push(opts); return Promise.resolve(c.confirmAnswer); };
  c.Ui.sheet = (opts) => { c.sheets.push(opts); return () => {}; };
  c.blockings = []; c.kept = []; c.refreshes = 0; c.refreshOk = true;
  c.Ui.sheetBlocking = (o) => { c.blockings.push(o); return { close() {}, el: {} }; };
  c.keepImpl = () => Promise.resolve({ ok: true, touched_at: '2027-01-19T08:00:00+00:00' });
  c.IplApi = { saveNextWeek: (u, d, w) => { c.sent.push({ u, d: clone(d), w }); return c.saveImpl(); }, keepNextWeek: (u, w) => { c.kept.push({ u, w }); return c.keepImpl(); } };
  c._doStateRefresh = (cb) => { c.refreshes++; if (cb) cb(c.refreshOk); };
  for (const n of LOADED) vm.runInContext(fnSource(n), c, { filename: n });
  return c;
}
// the member has this XI saved on the server (captain q2, vice-captain q4): history row + draft state, as _nwInit would leave them
function withSaved(c, team = XI, cap = 'q2', vc = 'q4', week = {}) {
  const row = Object.assign({ is_current: true, next_week: { team, cap, vc }, this_week: { team: [], cap: null, vc: null } }, week);
  c._historyData = { weeks: [row] };
  c._nwInit(row);
  return row;
}
const keptDraft = (c) => (c.store.has(KEY) ? JSON.parse(c.store.get(KEY)) : null);
const arr = (x) => Array.from(x);

const queue = [], results = []; let failed = 0;
function test(name, fn) { queue.push({ name, fn }); }

test('the whole page script parses (a stray character in one edit must not take the app down)', () => {
  const blocks = [...HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  assert.ok(blocks.length >= 1);
  blocks.forEach((b, i) => assert.doesNotThrow(() => new vm.Script(b, { filename: 'inline' + (i + 1) })));
});

// ============ 1. picks are never lost ============
test('an edit is kept on the phone; going back to the saved XI (or emptying the draft) clears the kept copy', (c) => {
  withSaved(c);
  c._nwRemove('q13', true);
  const d = keptDraft(c);
  assert.ok(d, 'the edited draft is kept');
  assert.deepStrictEqual(d.team, XI.filter((x) => x !== 'q13'));
  assert.strictEqual(d.wk, 3); assert.strictEqual(d.cap, 'q2');
  c._nwToggle('q13');                                              // back to the same set of players
  assert.strictEqual(c._nwHasUnsaved(), false);
  assert.strictEqual(keptDraft(c), null, 'nothing unsaved, nothing kept');
  c._nwDraft = { team: [], cap: null, vc: null }; c._nwChanged();
  assert.strictEqual(keptDraft(c), null, 'an empty draft has nothing to lose');
});
test('the draft comes back after a reload / a closed app (same week, same saved XI) and says so', (c) => {
  const row = withSaved(c);
  c._nwRemove('q13', true); c._nwSetRole('q6', 'vc');
  const c2 = load({ store: c.store });                              // "reload": a new page, the same phone storage
  c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.deepStrictEqual(arr(c2._nwDraft.team), XI.filter((x) => x !== 'q13'));
  assert.strictEqual(c2._nwDraft.vc, 'q6');
  assert.strictEqual(c2._nwRestored, true); assert.strictEqual(c2._nwHasUnsaved(), true);
});
test('a kept draft made against an older saved XI is dropped (the server moved on, e.g. saved from another phone)', (c) => {
  const row = withSaved(c); c._nwRemove('q13', true);
  const newer = { next_week: { team: ['q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8', 'q9', 'q10', 'q11'], cap: 'q1', vc: 'q2' }, this_week: { team: [] } };
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [Object.assign({ is_current: true }, newer)] }; c2._nwInit(newer);
  assert.strictEqual(c2._nwRestored, false); assert.deepStrictEqual(arr(c2._nwDraft.team), newer.next_week.team); assert.strictEqual(keptDraft(c2), null);
});
test('a corrupt or odd kept draft is ignored without an error', (c) => {
  const row = withSaved(c); const sig = c._nwSaved;
  const odd = ['not json', '{"wk":3}', JSON.stringify({ wk: 3, base: sig, team: 'q1' }), JSON.stringify({ wk: 3, base: sig, team: ['q1', 'q1'] }),
    JSON.stringify({ wk: 3, base: sig, team: PLAYERS.map((p) => p.id).slice(0, 12) }), JSON.stringify({ wk: 3, base: sig, team: [1, 2] }), JSON.stringify({ wk: 3, base: sig, team: [] }), 'null'];
  for (const raw of odd) {
    c.store.set(KEY, raw);
    const c2 = load({ store: c.store }); c2._historyData = { weeks: [row] };
    assert.doesNotThrow(() => c2._nwInit(row), raw);
    assert.strictEqual(c2._nwHasUnsaved(), false, 'nothing restored from: ' + raw);
  }
});
test('on restore a captain who is not in the team is dropped, and one player cannot be both captain and vice-captain', (c) => {
  const row = withSaved(c); const sig = c._nwSaved;
  c.store.set(KEY, JSON.stringify({ wk: 3, base: sig, team: ['q2', 'q4', 'q6'], cap: 'q99', vc: 'q4' }));
  let c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2._nwDraft.cap, null); assert.strictEqual(c2._nwDraft.vc, 'q4');
  c.store.set(KEY, JSON.stringify({ wk: 3, base: sig, team: ['q2', 'q4', 'q6'], cap: 'q4', vc: 'q4' }));
  c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2._nwDraft.cap, 'q4'); assert.strictEqual(c2._nwDraft.vc, null);
});
test('a kept draft identical to the saved XI is not "restored" and its key is removed', (c) => {
  const row = withSaved(c);
  c.store.set(KEY, JSON.stringify({ wk: 3, base: c._nwSaved, team: XI.slice().reverse(), cap: 'q2', vc: 'q4' }));
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2._nwRestored, false); assert.strictEqual(keptDraft(c2), null);
});
test('storage that throws (private mode, storage full) never breaks editing', (c) => {
  withSaved(c); c.storeBroken = true;
  assert.doesNotThrow(() => c._nwRemove('q13', true));
  assert.doesNotThrow(() => c._nwRemove('q14', true));
  assert.doesNotThrow(() => c._nwToggle('q5'));                       // 95 - 4 - 4 + 13 = 100: fits
  assert.ok(c._nwDraft.team.indexOf('q5') >= 0);
  assert.doesNotThrow(() => c._nwInit({ next_week: { team: XI, cap: 'q2', vc: 'q4' } }));
});
test('kept drafts are per member and per competition', (c) => {
  withSaved(c); c._nwRemove('q13', true);
  assert.ok(c.store.has('ipl_draft:sa20_2027:Sai'));
  const row = { next_week: { team: XI, cap: 'q2', vc: 'q4' }, this_week: { team: [] } };
  const other = load({ store: c.store }); other._activeComp = 'ipl_2026'; other._historyData = { weeks: [row] }; other._nwInit(row);
  assert.strictEqual(other._nwHasUnsaved(), false, 'another competition does not see it');
  const other2 = load({ store: c.store }); other2._username = 'Moe'; other2._historyData = { weeks: [row] }; other2._nwInit(row);
  assert.strictEqual(other2._nwHasUnsaved(), false, 'another member does not see it');
});
test('a member with nothing saved yet: first picks count as unsaved and come back after a reload', (c) => {
  const row = { is_current: true, next_week: { team: [] }, this_week: { team: [] } };
  c._historyData = { weeks: [row] }; c._nwInit(row);
  assert.strictEqual(c._nwSaved, null); assert.strictEqual(c._nwHasUnsaved(), false, 'an empty draft is not "unsaved"');
  c._nwToggle('q2'); c._nwToggle('q4');
  assert.strictEqual(c._nwHasUnsaved(), true);
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.deepStrictEqual(arr(c2._nwDraft.team), ['q2', 'q4']); assert.strictEqual(c2._nwRestored, true);
});
test('a brand-new member (no history rows at all, e.g. before the first SA20 week): first picks still survive a reload', (c) => {
  c._historyData = { weeks: [] };                                   // what the server sends before the first week exists
  c._nwInit(c._getCurrentWeekRow());                                // -> _nwInit(null)
  c._nwToggle('q2'); c._nwToggle('q4'); c._nwToggle('q6');
  assert.ok(keptDraft(c));
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [] }; c2._nwInit(c2._getCurrentWeekRow());
  assert.deepStrictEqual(arr(c2._nwDraft.team), ['q2', 'q4', 'q6']); assert.strictEqual(c2._nwRestored, true);
  assert.doesNotThrow(() => load()._nwInit(null));
});
test('the kept copy is found under the competition the member chose, even before the competition list has arrived', (c) => {
  c._activeComp = '';                                               // _activeComp is only set once /api/competitions answers...
  c.IplComp = { get: () => 'sa20_2027' };                           // ...the stored selection is there from the start
  assert.strictEqual(c._draftKey(), 'ipl_draft:sa20_2027:Sai');
  c.IplComp = null; c._activeComp = 'ipl_2026'; assert.strictEqual(c._draftKey(), 'ipl_draft:ipl_2026:Sai');
});
test('"Discard changes" goes back to the saved XI and clears the kept copy; Undo brings the edits back', (c) => {
  withSaved(c); c._nwRemove('q13', true); c._nwRemove('q14', true); c._nwToggle('q5');
  c._nwDiscard();
  assert.deepStrictEqual(arr(c._nwDraft.team), XI); assert.strictEqual(c._nwDraft.cap, 'q2'); assert.strictEqual(keptDraft(c), null);
  const undo = c.toasts[c.toasts.length - 1];
  assert.match(undo.msg, /discarded/i); assert.strictEqual(undo.opts.action.label, 'Undo');
  undo.opts.action.onClick();
  assert.strictEqual(c._nwDraft.team.indexOf('q13'), -1); assert.ok(c._nwDraft.team.indexOf('q5') >= 0); assert.ok(keptDraft(c));
});
test('"Discard changes" for a member with nothing saved empties the draft', (c) => {
  c._historyData = { weeks: [{ is_current: true, next_week: { team: [] }, this_week: { team: [] } }] };
  c._nwToggle('q2'); c._nwDiscard();
  assert.deepStrictEqual(arr(c._nwDraft.team), []);
});
test('a new week (the rollover ran while the app was open): the draft restarts from what plays now and the old kept copy goes', (c) => {
  withSaved(c); c._nwRemove('q13', true);
  const row = { next_week: { team: [] }, this_week: { team: ['q1', 'q2', 'q3'], cap: 'q1', vc: 'q2' } };
  c._currentWeek = 4; c._nwNewWeek(row);
  assert.deepStrictEqual(arr(c._nwDraft.team), ['q1', 'q2', 'q3']); assert.strictEqual(c._nwCarry, true); assert.strictEqual(c._nwHasUnsaved(), false); assert.strictEqual(keptDraft(c), null);
  c._nwNewWeek({ next_week: { team: [] }, this_week: { team: [] } });
  assert.deepStrictEqual(arr(c._nwDraft.team), []); assert.strictEqual(c._nwSaved, null);
});
test('every kind of change goes through the same path: kept + redrawn', (c) => {
  withSaved(c); const before = c.renders;
  c._nwSetRole('q6', 'cap'); c._nwSetCap('q7'); c._nwSetVc('q8'); c._nwRemove('q13', true); c._nwToggle('q13');
  assert.strictEqual(c.renders - before, 5);
  assert.ok(keptDraft(c) || !c._nwHasUnsaved());
});

// ============ 2. saving ============
test('Save while locked sends nothing and says so', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); c._state.deadline.next_lock_utc = PAST();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /locked/i);
});
test('Save with fewer than 11 players asks for 11 and sends nothing', (c) => {
  c._nwToggle('q2'); c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /11/);
});
test('captain AND vice-captain are mandatory: with no captain Save sends nothing, says so and opens the captain list (there is no "save anyway")', (c) => {
  withSaved(c, XI, null, 'q4'); c._nwDraft.cap = null; c._nwChanged();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /Choose your captain first/);
  assert.strictEqual(c.sheets.length, 1); assert.strictEqual(c.sheets[0].title, 'Choose your captain');
  assert.strictEqual(c.confirms.length, 0, 'no question that lets the member save without one');
});
test('with a captain but no vice-captain Save sends nothing and opens the vice-captain list', (c) => {
  withSaved(c, XI, 'q2', null); c._nwDraft.vc = null; c._nwChanged();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /Choose your vice-captain first/); assert.strictEqual(c.sheets[0].title, 'Choose your vice-captain');
});
test('with neither chosen the captain is asked for first', (c) => {
  withSaved(c, XI, null, null); c._nwDraft.cap = null; c._nwDraft.vc = null; c._nwChanged();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.strictEqual(c.sheets[0].title, 'Choose your captain');
});
test('both chosen: the XI is sent with both', async (c) => {
  withSaved(c, XI, null, null); c._nwDraft.cap = null; c._nwDraft.vc = null; c._nwChanged();
  c._nwSetRole('q2', 'cap'); c._nwSetRole('q4', 'vc');
  c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c.sent.length, 1); assert.strictEqual(c.sent[0].d.cap, 'q2'); assert.strictEqual(c.sent[0].d.vc, 'q4'); assert.strictEqual(c.warns.length, 0);
});
test('one request at a time: a second Save while saving sends nothing', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); c.saveImpl = () => new Promise(() => {});
  c._nwSave(); c._nwSave();
  assert.strictEqual(c.sent.length, 1); assert.strictEqual(c._nwSaving, true);
});
test('after a successful save: marked saved, the kept copy goes, the app is told', async (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); const sig = c._nwSig(c._nwDraft);
  assert.ok(keptDraft(c));
  c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c._nwSaving, false); assert.strictEqual(c._nwSaved, sig); assert.strictEqual(c._nwHasUnsaved(), false);
  assert.strictEqual(keptDraft(c), null); assert.ok(c.events.indexOf('ipl:saved') >= 0);
  assert.ok(c.toasts.some((t) => t.msg === 'Team saved'));
});
test('edits made while the request is on its way stay unsaved (and kept)', async (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); let done; c.saveImpl = () => new Promise((r) => { done = r; });
  c._nwSave(); c._nwSetRole('q7', 'vc');                                       // changed again while saving
  done({ ok: true }); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c._nwHasUnsaved(), true); assert.ok(keptDraft(c));
});
test('the server says "too late" (423): the week locks, the picker closes, no scary generic error', async (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); c._pickOpen = true; c.history.state = { ui: 'pick' };
  c.saveImpl = () => { const e = new Error('HTTP 423'); e.status = 423; e.serverMessage = 'The weekly deadline has passed'; return Promise.reject(e); };
  c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c._lockedWeek, 3); assert.strictEqual(c._deadlineLocked(), true);
  assert.strictEqual(c._pickOpen, false); assert.strictEqual(c.backs, 1);
  assert.strictEqual(c.fails.length, 0); assert.strictEqual(c._nwSaving, false);
  assert.ok(c.warns.some((w) => /locked/i.test(w) && /last saved XI/.test(w)), 'a Save started from another tab must say what happened');
});
test('offline: the error says the picks are kept on the phone, and the member can try again', async (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc');
  c.saveImpl = () => Promise.reject(new TypeError('Failed to fetch'));
  c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  const t = c.toasts[c.toasts.length - 1];
  assert.match(t.msg, /Could not save your team/); assert.match(t.msg, /kept on this phone/); assert.strictEqual(t.opts.type, 'error');
  assert.strictEqual(c._nwSaving, false); assert.ok(keptDraft(c), 'still kept');
  c.saveImpl = () => Promise.resolve({ ok: true }); c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c._nwHasUnsaved(), false);
});
test('a refusal from the server (e.g. 422 over budget) is shown as the server says it, without the offline note', async (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc');
  c.saveImpl = () => { const e = new Error('HTTP 422'); e.status = 422; e.serverMessage = 'Over budget by 3.0 CR'; return Promise.reject(e); };
  c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.match(c.fails[0], /Over budget by 3\.0 CR/); assert.ok(!/kept on this phone/.test(c.fails[0]));
});
test('a slow save (the free server may be asleep) says so after a few seconds', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); c.saveImpl = () => new Promise(() => {});
  c.setTimeout = (f, ms) => { c.timers.push({ f, ms }); return c.timers.length; };
  c._nwSave();
  assert.match(c._buildBudgetBar(), /Saving your team/);
  const slow = c.timers.find((t) => t.ms === 4000); assert.ok(slow, 'a 4 s timer is set');
  slow.f();
  assert.match(c._buildBudgetBar(), /Still saving[^<]*asleep/);
});

// ============ 3. the deadline ============
test('locked = the deadline has passed, the season is over, or the server said "too late" for this week', (c) => {
  assert.strictEqual(c._deadlineLocked(), false);
  c._state.deadline.next_lock_utc = PAST(); assert.strictEqual(c._deadlineLocked(), true);
  c._state.deadline.next_lock_utc = FUTURE(); assert.strictEqual(c._deadlineLocked(), false);
  c._state.deadline.status = 'completed'; assert.strictEqual(c._deadlineLocked(), true);
  c._state.deadline.status = 'active'; c._lockedWeek = 3; assert.strictEqual(c._deadlineLocked(), true, 'the server is the authority');
  c._currentWeek = 4; assert.strictEqual(c._deadlineLocked(), false, 'a new week unlocks');
  c._state = null; assert.strictEqual(c._deadlineLocked(), false);
});
test('the countdown text agrees with a server-side lock', (c) => {
  assert.match(c._deadlineText(), /locks in/);
  c._lockedWeek = 3; assert.match(c._deadlineText(), /Locked/);
});
test('locked: the squad shown is the XI the server holds, not an unsaved edit; not locked: the draft', (c) => {
  withSaved(c); c._nwRemove('q13', true); c._nwToggle('q1');
  assert.strictEqual(c._nwShown(), c._nwDraft, 'not locked: the draft');
  c._state.deadline.next_lock_utc = PAST();
  assert.deepStrictEqual(arr(c._nwShown().team), XI); assert.strictEqual(c._nwShown().cap, 'q2');
});
test('locked, nothing was ever saved, an unsaved draft exists: nothing is shown as "your XI" (it does not count)', (c) => {
  c._historyData = { weeks: [] }; c._nwInit(null); c._nwToggle('q2'); c._nwToggle('q4'); c._state.deadline.next_lock_utc = PAST();
  assert.deepStrictEqual(arr(c._nwShown().team), []);
  assert.match(c._buildNwSquad(), /No XI was saved for this week/); assert.ok(!/is-static/.test(c._buildNwSquad()));
});
test('locked squad: read-only rows (no buttons), no "tap a player" tip, no Discard link, plain "YOUR XI" label', (c) => {
  withSaved(c); c._nwRemove('q13', true); c._state.deadline.next_lock_utc = PAST();
  const h = c._buildNwSquad();
  assert.ok(!/<button/.test(h), 'nothing to press'); assert.match(h, /role="list"/); assert.match(h, /is-static/);
  assert.match(h, /YOUR XI/); assert.ok(!/DRAFT/.test(h)); assert.ok(!/nw-tip|nw-discard/.test(h));
  assert.strictEqual((h.match(/is-static/g) || []).length, 11, 'the saved 11, not the unsaved 10');
});
test('open squad: rows are buttons that remember their place for the keyboard; "Discard changes" only while something is unsaved', (c) => {
  withSaved(c);
  let h = c._buildNwSquad();
  assert.strictEqual((h.match(/<button type="button" class="nw-player-row/g) || []).length, 11); assert.match(h, /data-fk="sq:q2"/); assert.ok(!/nw-discard/.test(h));
  c._nwRemove('q13', true); h = c._buildNwSquad(); assert.match(h, /nw-discard/);
});
test('empty squad: no wall of identical "pick a player" slots, one short note instead', (c) => {
  const h = c._buildNwSquad();
  assert.ok(!/nw-add-slot|pick a player/.test(h)); assert.match(h, /nw-empty/);
  c._wide = () => true; assert.match(c._buildNwSquad(), /list on the right/);
});
test('locked card: a lock note, no budget bar, no Add players, no picker, one column; unsaved edits are called out', (c) => {
  withSaved(c); c._state.deadline.next_lock_utc = PAST();
  let h = c._buildNextWeekCard();
  assert.match(h, /nw-lockbar/); assert.match(h, /Picks are locked/); assert.match(h, /nw-layout nw-locked/);
  assert.ok(!/add-players|nw-budget-bar|nw-right|picker-stub/.test(h)); assert.ok(!/nw-lock-warn/.test(h));
  c._nwRemove('q13', true); h = c._buildNextWeekCard(); assert.match(h, /nw-lock-warn/);
});
test('the final week of a scheduled competition: no "Week N draft" for a week that does not exist, no promise of a next rollover', (c) => {
  withSaved(c); c._currentWeek = 3;
  c._state.deadline = { status: 'active', managed: true, season_over: true, next_lock_utc: null };
  assert.strictEqual(c._finalWeek(), true);
  const h = c._buildNextWeekCard();
  assert.match(h, /Final week/); assert.match(h, /Picks are closed/); assert.match(h, /no next week to pick for/); assert.match(h, /nw-layout nw-locked/);
  assert.ok(!/Week 4 draft|next rollover|Pick your team|Picks are locked/.test(h), 'the ordinary locked wording must not appear');
  assert.match(c._buildBudgetBar(), /final week/); assert.ok(!/next rollover/.test(c._buildBudgetBar()));
  // an ordinary locked week and a finished competition are unchanged
  c._state.deadline = { status: 'active', next_lock_utc: PAST() };
  assert.strictEqual(c._finalWeek(), false); assert.match(c._buildNextWeekCard(), /Week 4 draft/); assert.match(c._buildNextWeekCard(), /next rollover/);
  c._state.deadline = { status: 'completed', season_over: true, next_lock_utc: null }; assert.strictEqual(c._finalWeek(), false);
  c._state = null; assert.strictEqual(c._finalWeek(), false);
});
test('open card: the Add players button sits ABOVE the list (it stays put however long the list grows)', (c) => {
  withSaved(c);
  const h = c._buildNextWeekCard();
  assert.ok(h.indexOf('add-players') > 0 && h.indexOf('add-players') < h.indexOf('nw-squad-head'));
  assert.match(h, /data-fk="nw-cta"/); assert.match(h, /Change players/); assert.match(h, /picker-stub/);
});
test('a locked week cannot be edited from anywhere: no player sheet, no captain list, no picker', (c) => {
  withSaved(c); c._state.deadline.next_lock_utc = PAST();
  c._nwOpenPlayer('q2'); c._nwPickRole('cap');
  assert.strictEqual(c.sheets.length, 0);
});

test('the countdown is a readable pill and turns red in the last hour (not before, not after the lock)', (c) => {
  assert.strictEqual(c._lockUrgent(), false);
  assert.match(c._buildNextWeekCard(), /class="nw-countdown"/); assert.ok(!/nw-countdown urgent/.test(c._buildNextWeekCard()));
  c._state.deadline.next_lock_utc = new Date(Date.now() + 30 * 60e3).toISOString();
  assert.strictEqual(c._lockUrgent(), true); assert.match(c._buildNextWeekCard(), /class="nw-countdown urgent"/);
  c._state.deadline.next_lock_utc = new Date(Date.now() + 61 * 60e3).toISOString(); assert.strictEqual(c._lockUrgent(), false);
  c._state.deadline.next_lock_utc = PAST(); assert.strictEqual(c._lockUrgent(), false, 'once it has passed it is over, not urgent');
  c._state.deadline.next_lock_utc = FUTURE(); c._state.deadline.status = 'completed'; assert.strictEqual(c._lockUrgent(), false);
});
test('while the players have not arrived yet the picker says "Loading", not "No players match these filters"', (c) => {
  assert.match(load({ players: [] })._pickerRowsHtml({ filtered: [] }), /Loading the players/);
  assert.match(c._pickerRowsHtml({ filtered: [] }), /No players match these filters/);
});

// the banner on the other tabs must never say "ready" about an XI that has not been saved
test('banner on the other tabs: "ready" only when SAVED; a complete but unsaved XI offers one-tap "Save XI"', (c) => {
  c._historyData = { weeks: [] }; withSaved(c);
  let h = c._buildActionBanner();
  assert.match(h, /Your XI is ready \(11\/11\)/); assert.match(h, /action-banner ok/); assert.match(h, /Edit team/); assert.ok(!/Save XI/.test(h));
  c._nwSetRole('q6', 'vc');                                          // complete, but changed and not saved
  h = c._buildActionBanner();
  assert.match(h, /complete but not saved yet/); assert.ok(!/Your XI is ready/.test(h)); assert.ok(!/action-banner ok/.test(h));
  assert.match(h, /onclick="_nwSave\(\)">Save XI</);
  c._nwSaving = true; h = c._buildActionBanner();                    // while the request is on its way the button shows it and cannot be pressed again
  assert.match(h, /disabled aria-busy="true">Saving/);
});
test('banner: an XI without a captain / vice-captain is never "ready": it says what it needs, and its button chooses it', (c) => {
  c._historyData = { weeks: [] };
  withSaved(c, XI, null, null);                                       // saved, complete, but nobody is captain
  let h = c._buildActionBanner();
  assert.match(h, /Your XI needs a captain and a vice-captain/); assert.ok(!/action-banner ok/.test(h)); assert.ok(!/Your XI is ready/.test(h));
  assert.match(h, /onclick="_nwPickRole\('cap'\)">Choose captain</); assert.ok(!/Save XI/.test(h));
  c._nwDraft.cap = 'q2'; h = c._buildActionBanner();
  assert.match(h, /Your XI needs a vice-captain/); assert.match(h, /onclick="_nwPickRole\('vc'\)">Choose vice-captain</);
  c._nwDraft.vc = 'q4'; h = c._buildActionBanner();                   // both chosen, not yet saved -> one-tap save
  assert.match(h, /complete but not saved yet/); assert.match(h, /Save XI/);
});
test('banner: a part-way draft says how many, and "not saved yet" when it is only on this phone; nothing picked says so', (c) => {
  c._historyData = { weeks: [{ is_current: true, next_week: { team: [] }, this_week: { team: [] } }] };
  assert.match(c._buildActionBanner(), /haven.t picked a team yet/);
  c._nwToggle('q2'); c._nwToggle('q4');
  const h = c._buildActionBanner();
  assert.match(h, /2\/11 players picked, not saved yet/); assert.match(h, /switchTab\('next'\)">Pick team</);
});
test('banner: nothing once the picks are locked, the competition is over, or nobody is signed in', (c) => {
  withSaved(c); assert.notStrictEqual(c._buildActionBanner(), '');
  c._state.deadline.next_lock_utc = PAST(); assert.strictEqual(c._buildActionBanner(), '');
  c._state.deadline.next_lock_utc = FUTURE(); c._lockedWeek = 3; assert.strictEqual(c._buildActionBanner(), '');
  c._lockedWeek = null; c._username = null; assert.strictEqual(c._buildActionBanner(), '');
  c._username = 'Sai'; c._activeCompObj = () => ({ status: 'completed' }); assert.strictEqual(c._buildActionBanner(), '');
});

// ============ 4. the hint line + Save button ============
const bar = (c) => c._buildBudgetBar();
const hintOf = (h) => (h.match(/<div class="nw-hint[^"]*" role="status">[\s\S]*?<span>([^<]*)<\/span>/) || [])[1];
const saveBtn = (h) => (h.match(/<button id="nw-save-btn"[^>]*>([^<]*)<\/button>/) || []);
test('hint, empty draft: says how many players and what each can cost on average; Save is off', (c) => {
  const h = bar(c);
  assert.strictEqual(hintOf(h), 'Pick your 11 players. About 9.1 CR each keeps you in budget.'); assert.match(saveBtn(h)[0], /disabled/);
});
test('hint, part way: counts what is left and the average', (c) => {
  ['q2', 'q4', 'q6', 'q7'].forEach((id) => c._nwToggle(id));      // 11 + 8 + 11 + 10 = 40 -> 60 left for 7
  assert.strictEqual(hintOf(bar(c)), 'Pick 7 more players. About 8.6 CR each keeps you in budget.');
});
test('hint, one short: singular, and the CR left', (c) => {
  XI.slice(0, 10).forEach((id) => c._nwToggle(id));
  assert.strictEqual(hintOf(bar(c)), 'Pick 1 more player. You have 17.0 CR left.');   // 11+8+11+10+9+7+9+10+4+4 = 83 spent
});
test('hint, a dead end (the money left cannot fill the spots): says so and what to do', (c) => {
  const rich = [P('r1', 'A', 'X', 'BAT', 20, 1), P('r2', 'B', 'X', 'BAT', 20, 1), P('r3', 'C', 'X', 'BAT', 20, 1), P('r4', 'D', 'X', 'BAT', 20, 1), P('r5', 'E', 'X', 'BAT', 18, 1), P('c1', 'F', 'X', 'BAT', 4, 1)];
  const k = load({ players: rich }); ['r1', 'r2', 'r3', 'r4', 'r5'].forEach((id) => k._nwToggle(id));   // 98 spent, 2 left, 6 spots to fill at 4 each
  const h = hintOf(bar(k));
  assert.match(h, /Only 2\.0 CR left for 6 more players \(the cheapest costs 4\.0\)/); assert.match(h, /cheaper/);
});
test('hint, over budget: how far over', (c) => {
  c._nwDraft = { team: ['q1', 'q5', 'q3', 'q9', 'q2', 'q6', 'q7', 'q12', 'q8', 'q11', 'q4'], cap: 'q1', vc: 'q5' };   // 15+13+12+12+11+11+10+10+9+9+8 = 120
  assert.match(hintOf(bar(c)), /^Over budget by 20\.0 CR/);
});
test('hint, full XI without roles: says they are needed to save and offers one button for the captain, then for the vice-captain', (c) => {
  withSaved(c, XI, null, null); c._nwDraft.cap = null; c._nwDraft.vc = null; c._nwSaved = c._nwSig(c._nwDraft);
  let h = bar(c); assert.strictEqual(hintOf(h), 'Choose your captain (\u00d72 points) and vice-captain (\u00d71.5 points) to save.'); assert.match(h, /onclick="_nwPickRole\('cap'\)"[^>]*>Choose</);
  c._nwDraft.vc = 'q4'; h = bar(c); assert.strictEqual(hintOf(h), 'Choose your captain (\u00d72 points) to save.'); assert.match(h, /_nwPickRole\('cap'\)/);
  c._nwDraft.cap = 'q2'; c._nwDraft.vc = null; h = bar(c); assert.strictEqual(hintOf(h), 'Choose your vice-captain (\u00d71.5 points) to save.'); assert.match(h, /_nwPickRole\('vc'\)/);
});
test('Save is OFF until BOTH captain and vice-captain are chosen (a complete, in-budget XI is not enough)', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc');                              // changed, complete, both roles: Save is on
  assert.ok(!/disabled/.test(saveBtn(bar(c))[0]));
  c._nwDraft.vc = null; assert.match(saveBtn(bar(c))[0], /disabled/, 'no vice-captain');
  c._nwDraft.vc = 'q4'; c._nwDraft.cap = null; assert.match(saveBtn(bar(c))[0], /disabled/, 'no captain');
  c._nwDraft.cap = 'q2'; c._nwDraft.vc = 'q6'; assert.ok(!/disabled/.test(saveBtn(bar(c))[0]), 'both chosen (and different from what is saved): on again');
});
test('the tip above the list says both are needed', (c) => {
  withSaved(c); assert.match(c._buildNwSquad(), /You need both/);
});
test('hint, unsaved changes; restored picks say so (also in front of what is left to do)', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc');
  assert.strictEqual(hintOf(bar(c)), 'You have unsaved changes.');
  c._nwRestored = true; assert.strictEqual(hintOf(bar(c)), 'Restored your unsaved picks. Save to keep them.');
  c._nwRemove('q13', true); c._nwRestored = true; assert.match(hintOf(bar(c)), /^Restored your unsaved picks\. Pick 1 more player/);
});
test('Save button: "Save Draft" when something changed, "Saved ✓" (off) when not, "Locked" (off) when locked, "Saving…" (off, busy) while saving', (c) => {
  withSaved(c);
  let b = saveBtn(bar(c)); assert.strictEqual(b[1], 'Saved ✓'); assert.match(b[0], /disabled/);
  c._nwSetRole('q6', 'vc'); b = saveBtn(bar(c)); assert.strictEqual(b[1], 'Save Draft'); assert.ok(!/disabled/.test(b[0]));
  c._nwSaving = true; b = saveBtn(bar(c)); assert.strictEqual(b[1], 'Saving…'); assert.match(b[0], /disabled/); assert.match(b[0], /aria-busy="true"/);
  c._nwSaving = false; c._state.deadline.next_lock_utc = PAST(); b = saveBtn(bar(c)); assert.strictEqual(b[1], 'Locked'); assert.match(b[0], /disabled/);
});
test('Save is off for an incomplete or over-budget XI (the hint says why)', (c) => {
  c._nwToggle('q2'); assert.match(saveBtn(bar(c))[0], /disabled/);
  c._nwDraft = { team: ['q1', 'q5', 'q3', 'q9', 'q2', 'q6', 'q7', 'q12', 'q8', 'q11', 'q4'], cap: 'q1', vc: 'q5' }; assert.match(saveBtn(bar(c))[0], /disabled/);
});

// ============ 5. captain / vice-captain in one list ============
test('captain list: the whole XI, best scorers first, the current choice ticked, a vice-captain flagged', (c) => {
  withSaved(c, XI, 'q2', 'q4'); c._nwPickRole('cap');
  const s = c.sheets[0];
  assert.strictEqual(s.title, 'Choose your captain'); assert.match(s.subtitle, /double/);
  assert.strictEqual(s.items.length, 11);
  const pts = s.items.map((it) => PLAYERS.find((p) => p.name === it.label).season_pts);
  assert.deepStrictEqual(pts, pts.slice().sort((a, b) => b - a));
  assert.strictEqual(s.items.filter((it) => it.checked).length, 1); assert.strictEqual(s.items.find((it) => it.checked).label, 'Jos Buttler');
  assert.match(s.items.find((it) => it.label === 'MS Dhoni').desc, /now vice-captain/);
});
test('choosing a captain offers the vice-captain straight away when there is none; not when there is one', async (c) => {
  withSaved(c, XI, null, null); c._nwDraft.cap = null; c._nwDraft.vc = null; c._nwChanged();
  c._nwPickRole('cap'); c.sheets[0].items.find((it) => it.label === 'Jos Buttler').onClick();
  assert.strictEqual(c._nwDraft.cap, 'q2'); await new Promise((r) => setTimeout(r, 120));
  assert.strictEqual(c.sheets.length, 2); assert.strictEqual(c.sheets[1].title, 'Choose your vice-captain');
  c.sheets[1].items.find((it) => it.label === 'MS Dhoni').onClick();
  assert.strictEqual(c._nwDraft.vc, 'q4'); await new Promise((r) => setTimeout(r, 120));
  assert.strictEqual(c.sheets.length, 2, 'no third sheet');
  c._nwPickRole('cap'); c.sheets[2].items.find((it) => it.label === 'Sanju Samson').onClick(); await new Promise((r) => setTimeout(r, 120));
  assert.strictEqual(c.sheets.length, 3, 'a vice-captain exists: no chained sheet');
});
test('making the vice-captain the captain moves them (they cannot be both)', (c) => {
  withSaved(c, XI, 'q2', 'q4'); c._nwPickRole('cap'); c.sheets[0].items.find((it) => it.label === 'MS Dhoni').onClick();
  assert.strictEqual(c._nwDraft.cap, 'q4'); assert.strictEqual(c._nwDraft.vc, null);
});

// ============ 6. the nav dot ============
test('the Pick tab shows a dot only while there are unsaved picks, never when locked or signed out', (c) => {
  withSaved(c);
  assert.strictEqual(c._navDot({ id: 'next' }), '');
  c._nwSetRole('q6', 'vc');
  assert.match(c._navDot({ id: 'next' }), /nav-dot/); assert.match(c._navDot({ id: 'next' }), /unsaved changes/);
  assert.strictEqual(c._navDot({ id: 'team' }), '', 'only the Pick tab');
  c._state.deadline.next_lock_utc = PAST(); assert.strictEqual(c._navDot({ id: 'next' }), '');
  c._state.deadline.next_lock_utc = FUTURE(); c._username = null; assert.strictEqual(c._navDot({ id: 'next' }), '');
});

// ============ 7. keyboard focus survives a redraw ============
function fakeApp(map) {
  // data-fk lookups only, with the same unescaping a real selector does; entries: { disabled, offsetParent }
  return { contains: (el) => !!el && el.inApp === true, querySelector: (sel) => { const m = /^\[data-fk="(.*)"\]$/.exec(sel); if (!m) return null; const key = m[1].replace(/\\(["\\])/g, '$1'); return map[key] || null; } };
}
const el = (extra) => Object.assign({ disabled: false, offsetParent: {}, inApp: true, getAttribute: (k) => (k === 'data-fk' ? 'x' : null) }, extra);
test('focus key: only for a control inside the page that carries one', (c) => {
  c.$app = fakeApp({});
  c.document.activeElement = c.document.body; assert.strictEqual(c._focusKey(), null);
  c.document.activeElement = el({ getAttribute: () => 'pk:q1' }); assert.strictEqual(c._focusKey(), 'pk:q1');
  c.document.activeElement = el({ inApp: false, getAttribute: () => 'pk:q1' }); assert.strictEqual(c._focusKey(), null, 'a sheet or dialog is not the page');
  c.document.activeElement = el({ getAttribute: () => null }); assert.strictEqual(c._focusKey(), null);
});
test('focus find: the same control is found again; odd characters in the key are escaped both ways', (c) => {
  const a = el(), b = el();
  c.$app = fakeApp({ 'pk:q1': a, 'pk:a"b\\c': b });
  assert.strictEqual(c._focusFind('pk:q1'), a); assert.strictEqual(c._focusFind('pk:a"b\\c'), b); assert.strictEqual(c._focusFind('nope'), null); assert.strictEqual(c._focusFind(null), null);
});
test('focus find: a player taken out lands on Add players; the strip cross on the strip toggle; closing the picker on the button that opened it', (c) => {
  const cta = el(), xi = el();
  c.$app = fakeApp({ 'nw-cta': cta, 'pk-xi': xi });
  assert.strictEqual(c._focusFind('sq:q2'), cta); assert.strictEqual(c._focusFind('xx:q2'), xi);
  assert.strictEqual(c._focusFind('pk-done'), cta); assert.strictEqual(c._focusFind('pk-back'), cta);
});
test('focus find: a Save that has just become disabled hands focus to the nearest working control; disabled / hidden ones are never chosen', (c) => {
  const save = el({ disabled: true }), cta = el(), hint = el({ offsetParent: null });
  c.$app = fakeApp({ 'nw-save': save, 'nw-hintbtn': hint, 'nw-cta': cta });
  assert.strictEqual(c._focusFind('nw-save'), cta, 'save is disabled, the hint button is hidden -> Add players');
  hint.offsetParent = {}; assert.strictEqual(c._focusFind('nw-save'), hint);
});


// ============ 8. the strip under the app bar, and the two floating bars ============
const bareParts = (h) => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
test('strip under the app bar: ONE row = the status + a single capsule with members / matches / budget (not four chips that wrap)', (c) => {
  c.matches = new Array(34).fill({});
  c._activeCompObj = () => ({ status: 'upcoming' });
  const h = c._pageStripHtml(3);
  assert.strictEqual((h.match(/<span class="chip[ "]/g) || []).length, 2, 'the status chip and the stats capsule: two items in the row');
  assert.match(h, /<span class="chip stats">/); assert.match(h, /<b>3<\/b> members/); assert.match(h, /<b>34<\/b> matches/); assert.match(h, /<b>100<\/b> CR<span class="ui-sr-only"> budget<\/span>/);
});
test('strip: still fine when the competition list has not arrived yet (no status chip) and for a finished competition', (c) => {
  assert.strictEqual(c._pageStripHtml(3).indexOf('class="chip stats"') > 0, true);
  const real = load();   // the page's own status chip
  real._activeCompObj = () => ({ status: 'completed', champion: 'Sai' });
  assert.match(real._pageStripHtml(3), /Finished[^<]*Sai/);
  real._activeCompObj = () => ({});
  assert.ok(!/class="chip (live|soon|done)"/.test(real._pageStripHtml(3)));
});
test('bar numbers: CR left, picked / left, the role balance and the progress track', (c) => {
  ['q2', 'q4', 'q6', 'q10'].forEach((id) => c._nwToggle(id));        // WK, WK, BAT, BOWL = 11 + 8 + 11 + 7 = 37
  const P = c._barParts();
  assert.strictEqual(P.rem, 63); assert.strictEqual(P.count, 4); assert.strictEqual(P.left, 7);
  assert.match(P.main, />63\.0<span class="nw-budget-denom"> \/ 100CR/); assert.match(P.main, /nw-budget-amt ok/);
  assert.match(P.track, /width:37%/); assert.match(P.badge, />4 \/ 11</);
  assert.strictEqual(bareParts(P.pills), '2 WK 1 BAT 0 AR 1 BOWL');
});
test('bar numbers: low budget warns, over budget is flagged, a full XI has nothing left to pick', (c) => {
  XI.forEach((id) => c._nwToggle(id));                                // 95 CR: 5 left
  let P = c._barParts(); assert.strictEqual(P.left, 0); assert.match(P.main, /nw-budget-amt warn/);
  c._nwDraft = { team: ['q1', 'q5', 'q3', 'q9', 'q2', 'q6', 'q7', 'q12', 'q8', 'q11', 'q4'], cap: 'q1', vc: 'q5' };
  P = c._barParts(); assert.match(P.main, /nw-budget-amt over/); assert.match(P.track, /width:100%/);
});
test('Pick tab: the bar lives in the sticky wrapper, the hint is OUTSIDE it (the hint scrolls away, the bar stays)', (c) => {
  withSaved(c); const h = c._buildBudgetBar();
  assert.ok(h.startsWith('<div class="nw-stick"><div class="nw-budget-bar">'));
  assert.match(h, /<\/button><\/div><\/div><div class="nw-hint/, 'the bar closes, THEN the wrapper closes, and only then the hint starts');
  assert.strictEqual((h.match(/class="nw-hint/g) || []).length, 1);
  assert.ok(h.indexOf('nw-save-btn') < h.indexOf('class="nw-hint'), 'Save is inside the sticky part');
});
test('Add players: the floating summary says how many are left to pick, the CR left and the role balance, with no Save button', (c) => {
  ['q2', 'q4', 'q6', 'q10'].forEach((id) => c._nwToggle(id));
  const h = c._pickerBarHtml();
  assert.match(h, /^<div class="pb-wrap"><div class="nw-budget-bar picker-bar" role="group" aria-label="Your squad so far">/);
  assert.match(h, />63\.0<span class="nw-budget-denom"> \/ 100CR/); assert.match(h, />4 \/ 11</);
  assert.strictEqual(bareParts(/<div class="nw-role-pills">([\s\S]*?)<button/.exec(h)[1]), '2 WK 1 BAT 0 AR 1 BOWL');
  assert.match(h, /<span class="ui-sr-only">Your XI: <\/span><b>7<\/b> to pick/);
  assert.ok(!/nw-save-btn|Save/.test(h), 'saving happens on the squad screen');
});
test('Add players summary: the right-hand button opens "Your XI" (a drawer INSIDE the sticky summary, so it opens in view)', (c) => {
  c._nwToggle('q2');
  let h = c._pickerBarHtml();
  assert.match(h, /<button type="button" class="pb-xi" id="lh-xi" data-fk="pk-xi" aria-expanded="false" aria-controls="picker-xi" onclick="_pickerToggleXi\(\)">/);
  assert.match(h, /<div class="picker-xi" id="picker-xi" role="region" aria-label="Your XI" hidden>/);
  assert.ok(h.indexOf('id="picker-xi"') > h.indexOf('class="pb-xi'), 'the drawer comes after the bar');
  assert.ok(h.lastIndexOf('</div>') === h.length - 6, 'and both are inside the one wrapper that sticks');
  c._pickerXiOpen = true; h = c._pickerBarHtml();
  assert.match(h, /class="pb-xi open"[^>]*aria-expanded="true"/); assert.ok(!/id="picker-xi"[^>]*hidden/.test(h)); assert.match(h, /xi-cell/);
});
test('Add players summary: a full XI says "XI full" (not "0 to pick")', (c) => {
  XI.forEach((id) => c._nwToggle(id));
  const h = c._pickerBarHtml();
  assert.match(h, /class="pb-xi full"/); assert.match(h, /XI full/); assert.ok(!/to pick/.test(h));
});
test('Add players page: header (back / title / Done), then the floating summary, then search + filters; the old "n/11 picked" line is gone', (c) => {
  const k = load({ realPicker: true }); ['q2', 'q4'].forEach((id) => k._nwToggle(id));
  const h = k._buildPicker();
  const at = (x) => h.indexOf(x);
  assert.ok(at('layer-head') < at('pb-wrap') && at('pb-wrap') < at('picker-toolbar') && at('picker-toolbar') < at('id="plist"'));
  assert.match(h, /aria-label="Back to my squad"/); assert.match(h, /class="lh-title">Add players</); assert.match(h, /data-fk="pk-done" onclick="_pickClose\(\)">Done</);
  assert.ok(!/lh-sub|picked ·/.test(h), 'the header no longer carries the small count line');
  assert.strictEqual((h.match(/id="lh-xi"/g) || []).length, 1); assert.strictEqual((h.match(/id="picker-xi"/g) || []).length, 1);
});
test('the sticky bars sit under the app bar\'s REAL height: published as --app-bar-h, only when it changes', (c) => {
  c._syncBarH(); assert.deepStrictEqual(c.cssVars, {}, 'no app bar yet (e.g. the sign-in screen): nothing to publish');
  c.fakeHeader = { getBoundingClientRect: () => ({ height: 71.4 }) };
  c._syncBarH(); assert.strictEqual(c.cssVars['--app-bar-h'], '71px');
  c.cssVars = {}; c._syncBarH(); assert.deepStrictEqual(c.cssVars, {}, 'unchanged: not written again');
  c.fakeHeader = { getBoundingClientRect: () => ({ height: 113 }) }; c._syncBarH(); assert.strictEqual(c.cssVars['--app-bar-h'], '113px');
  c.fakeHeader = { getBoundingClientRect: () => ({ height: 0 }) }; c.cssVars = {}; c._syncBarH(); assert.deepStrictEqual(c.cssVars, {}, 'a hidden app bar (0 px) is ignored');
});

// ---- the stylesheets: the rules that make these bars float have to be there (and the old ones that broke them gone) ----
const CSS = {};
const CSS_DIR = process.env.CSS_DIR || path.join(ROOT, 'Static');   // CSS_DIR: point the test at another copy of the stylesheets (to see it catch a broken one)
for (const f of ['theme', 'style', 'ui', 'screens', 'paper']) CSS[f] = fs.readFileSync(path.join(CSS_DIR, f + '.css'), 'utf8');
const ALL_CSS = ['theme', 'style', 'ui', 'screens', 'paper'].map((f) => CSS[f]).join('\n');
const ruleBodies = (sel) => { const out = []; const re = new RegExp('(^|[}\\s])' + sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}', 'g'); let m; while ((m = re.exec(ALL_CSS))) out.push(m[2]); return out; };
test('css: the "Add players" shell does not clip (overflow:hidden switched every sticky part off: the header and summary scrolled away with the list)', () => {
  const bodies = ruleBodies('.nw-right.layer-open .picker-shell');
  assert.ok(bodies.some((b) => /overflow:\s*visible/.test(b)), 'an overflow:visible rule for the shell inside the layer');
  assert.ok(!bodies.some((b) => /overflow:\s*hidden/.test(b)));
});
test('css: nothing positions the budget bar by a hard-coded 64px any more (that was 5px less than the app bar: it slid under it)', () => {
  assert.ok(!/\.nw-budget-bar\s*\{[^}]*position:\s*sticky/.test(ALL_CSS));
  assert.ok(!/top:\s*64px/.test(CSS.screens + CSS.paper));
  assert.ok(!/picker-toolbar\s*\{[^}]*position:\s*sticky/.test(ALL_CSS), 'the toolbar scrolls with the list; header + summary stay');
});
test('css: the floating bars are placed from measured sizes (--app-bar-h, --lh-h) with a step under them', () => {
  assert.match(CSS.theme, /--app-bar-h:\s*\d+px/); assert.match(CSS.theme, /--lh-h:\s*\d+px/);
  const stick = ruleBodies('.nw-stick').join(' ');
  assert.match(stick, /position:\s*sticky/); assert.match(stick, /top:\s*var\(--app-bar-h/); assert.match(stick, /padding:\s*8px/, 'a step of card colour between the app bar and the bar');
  const wrap = ruleBodies('.pb-wrap').join(' ');
  assert.match(wrap, /position:\s*sticky/); assert.match(wrap, /top:\s*var\(--lh-h/); assert.match(wrap, /padding:\s*8px/);
  assert.match(ruleBodies('.layer-head').join(' '), /height:\s*var\(--lh-h/);
  assert.match(ruleBodies('.nw-left').join(' '), /top:\s*calc\(var\(--app-bar-h/, 'the desktop side panel keeps a step under the app bar too');
});
test('css: the strip never wraps onto a second line (it scrolls sideways on a very narrow phone instead)', () => {
  const strip = ruleBodies('.page-strip').pop();            // the last rule wins (paper.css is loaded last)
  assert.match(strip, /flex-wrap:\s*nowrap/); assert.match(strip, /overflow-x:\s*auto/);
  assert.match(ruleBodies('.page-strip .chip').join(' '), /flex:\s*0 0 auto/);
});
test('css: the summary is hidden where the squad is already on screen (wide layout), the old header toggle styles are gone', () => {
  assert.match(ALL_CSS, /@media \(min-width: 1000px\) \{ \.pb-wrap \{ display: none; \} \}/);
  assert.ok(!/button\.lh-xi/.test(ALL_CSS));
});

// ───── Phase 1.6 (2026-11-20): which week a draft is for, Keep this XI, a draft from a week that moved on is offered back, lock card wording, notices, local time ─────
const MANAGED = (over = {}) => Object.assign({ status: 'active', managed: true, locked: false, awaiting: null, season_over: false, applied_to: 3, next_lock_utc: FUTURE() }, over);
const carriedRow = (extra = {}) => Object.assign({ week_no: 3, is_current: true, this_week: { team: XI, cap: 'q2', vc: 'q4' }, next_week: { team: XI, cap: 'q2', vc: 'q4' } }, extra);
const tick = () => new Promise((r) => setTimeout(r, 0));
const REFUSED = { status: 409, serverData: { reason: 'week_changed', current_week: 4, draft_week: 5 } };

test('the week a draft is for: the handover it will be promoted at (the row week does not change at the first week start)', (c) => {
  c._currentWeek = 3;
  assert.strictEqual(c._nwTargetWeek({ managed: true, applied_to: 0 }), 1, 'before the season starts a draft is for week 1');
  c._currentWeek = 1; assert.strictEqual(c._nwTargetWeek({ managed: true, applied_to: 1 }), 2, 'after the start the row week is still 1, but a draft is for week 2');
  c._currentWeek = 2; assert.strictEqual(c._nwTargetWeek({ managed: true, applied_to: 2 }), 3);
  assert.strictEqual(c._nwTargetWeek({ managed: true, applied_to: 2, awaiting: { kind: 'rollover', to_week: 3 } }), 3, 'while a handover is waiting it is that week');
  c._currentWeek = 3; assert.strictEqual(c._nwTargetWeek({ status: 'active' }), 4, 'without a lock schedule: the week after this one');
  c._currentWeek = 1; assert.strictEqual(c._nwTargetWeek({ status: 'active' }), 1, '... and the first week is picked before it starts');
});

test('a draft from a week that moved on is OFFERED back when the app opens, never deleted: it stays on the phone until the member answers', (c) => {
  const row = withSaved(c); c._nwRemove('q13', true);
  const d = keptDraft(c); d.wk = 2; d.to = 3;   // made while week 2 was playing, for the Week 3 draft
  c.store.set(KEY, JSON.stringify(d));
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2.blockings.length, 1);
  assert.match(c2.blockings[0].message, /It was for Week 3, but Week 3 is already playing\. Use it as your Week 4 draft\?/);
  assert.strictEqual(c2.blockings[0].primary.label, 'Use it as Week 4 draft'); assert.strictEqual(c2.blockings[0].secondary.label, 'Discard');
  assert.strictEqual(c2._nwHasUnsaved(), false, 'nothing is changed until the member answers'); assert.ok(keptDraft(c2), 'and the copy is still on the phone');
  c2.blockings[0].primary.onClick();
  assert.strictEqual(c2._nwHasUnsaved(), true); assert.strictEqual(c2._nwDraft.team.length, 10); assert.strictEqual(keptDraft(c2).wk, 3, 'now kept against the week that is open');
  assert.ok(c2.toasts.some((t) => /Draft brought back/.test(t.msg)));
  const c3 = load({ store: new Map([[KEY, JSON.stringify(d)]]) }); c3._historyData = { weeks: [row] }; c3._nwInit(row);
  c3.blockings[0].secondary.onClick();
  assert.strictEqual(keptDraft(c3), null); assert.strictEqual(c3._nwHasUnsaved(), false);
});

test('a draft made before the first week started (same row week, but drafts are for the next week now) is offered back too', (c) => {
  c._currentWeek = 1; c._state.deadline = MANAGED({ applied_to: 0 });
  const row = withSaved(c); c._nwRemove('q13', true);                      // an edit of the pre-season pick, kept on the phone with to = 1
  assert.strictEqual(keptDraft(c).to, 1);
  const c2 = load({ store: c.store }); c2._currentWeek = 1; c2._state.deadline = MANAGED({ applied_to: 1 });   // the season has started since
  c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2.blockings.length, 1); assert.match(c2.blockings[0].message, /It was for Week 1, but Week 1 is already playing\. Use it as your Week 2 draft\?/);
});

test('picks not yet saved when a handover runs while the app is open are offered back, not lost; the screen shows the new week meanwhile', (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); withSaved(c);
  c._nwRemove('q13', true);
  const next = { week_no: 4, is_current: true, next_week: { team: XI, cap: 'q2', vc: 'q4' }, this_week: { team: XI, cap: 'q2', vc: 'q4' } };
  c._currentWeek = 4; c._nwNewWeek(next, 5);
  assert.strictEqual(c.blockings.length, 1); assert.match(c.blockings[0].message, /It was for Week 4, but Week 4 is already playing\. Use it as your Week 5 draft\?/);
  assert.deepStrictEqual(arr(c._nwDraft.team), XI, 'meanwhile the screen shows what the server holds for the new week');
  c._nwNewWeek(next, 6); assert.strictEqual(c.blockings.length, 1, 'one question at a time');
  c.blockings[0].primary.onClick();
  assert.strictEqual(c._nwDraft.team.length, 10); assert.strictEqual(c._nwHasUnsaved(), true); assert.strictEqual(keptDraft(c).to, 6);
});

test('nothing to offer: a handover with no unsaved picks (or before the server has said which week drafts are for) asks nothing', (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); withSaved(c);
  c._currentWeek = 4; c._nwNewWeek({ next_week: { team: XI, cap: 'q2', vc: 'q4' }, this_week: { team: XI, cap: 'q2', vc: 'q4' } }, 5);
  assert.strictEqual(c.blockings.length, 0);
  const c2 = load(); c2._state = null; c2._historyData = { weeks: [] }; c2._nwInit(null); assert.strictEqual(c2._nwTarget, null); assert.strictEqual(c2.blockings.length, 0);
});

test('Keep this XI: a carried-over draft shows an enabled Keep button and Edit, and says what it is', (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); c._currentWeek = 3;
  const row = carriedRow(); c._historyData = { weeks: [row] }; c._nwInit(row);
  assert.strictEqual(c._nwCarried(), true);
  const bar = c._buildBudgetBar();
  assert.match(bar, />Keep this XI</); assert.ok(!/id="nw-save-btn"[^>]*disabled/.test(bar), 'the Keep button is enabled'); assert.match(bar, /onclick="_nwKeep\(\)"/);
  assert.match(bar, /Week 4 draft: using last week’s XI/); assert.match(bar, /data-fk="nw-edit"/); assert.match(bar, /btn-gold/);
  c._nwSetCap('q3'); assert.strictEqual(c._nwCarried(), false, 'once it is edited it is an ordinary draft'); assert.match(c._buildBudgetBar(), />Save Draft</);
});

test('Keep this XI: pressing it tells the server, marks the draft kept and changes nothing about the team', async (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); c._currentWeek = 3;
  const row = carriedRow(); c._historyData = { weeks: [row] }; c._nwInit(row);
  c._nwKeep(); assert.strictEqual(c._nwKeeping, true); assert.match(c._buildBudgetBar(), /Keeping/); c._nwKeep();
  await tick();
  assert.deepStrictEqual(c.kept, [{ u: 'Sai', w: 4 }]); assert.strictEqual(c.sent.length, 0, 'a keep is not a save, and a second press while one is in flight is ignored');
  assert.strictEqual(c._nwTouched, true); assert.strictEqual(c._nwKeeping, false); assert.strictEqual(row.next_week_touched_at, '2027-01-19T08:00:00+00:00');
  assert.strictEqual(c._nwCarried(), false); assert.deepStrictEqual(arr(c._nwDraft.team), XI);
  const bar = c._buildBudgetBar();
  assert.match(bar, /Kept ✓/); assert.match(bar, /Kept\. We will use this XI for Week 4/); assert.match(bar, /id="nw-save-btn"[^>]*disabled/);
  assert.ok(c.toasts.some((t) => /Kept\. We will use this XI for Week 4/.test(t.msg)));
});

test('Keep this XI: not while picks are locked; a refused week is explained, not shown as an error', async (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); const row = carriedRow(); c._historyData = { weeks: [row] }; c._nwInit(row);
  c._state.deadline = MANAGED({ next_lock_utc: PAST(), locked: true, awaiting: { kind: 'rollover', to_week: 4 } });
  c._nwKeep(); assert.strictEqual(c.kept.length, 0); assert.match(c.warns[0], /locked/i);
  c._state.deadline = MANAGED({ applied_to: 3 });
  c.keepImpl = () => Promise.reject(REFUSED);
  c._nwKeep(); await tick();
  assert.strictEqual(c.refreshes, 1, 'the page brings itself up to date'); assert.strictEqual(c.fails.length, 0, 'and the refusal is not shown as an error');
  assert.ok(c.toasts.some((t) => /nothing was kept/.test(t.msg)));
  c.keepImpl = () => Promise.reject({ status: 423, serverData: { reason: 'locked' } }); c._nwKeep(); await tick();
  assert.strictEqual(c._lockedWeek, 3, 'a too-late keep locks the screen like a too-late save');
});

test('Save states the week the draft is for and marks the draft kept', async (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); const row = withSaved(c); c._nwSetCap('q3');
  c._nwSave(); await tick();
  assert.strictEqual(c.sent.length, 1); assert.strictEqual(c.sent[0].w, 4);
  assert.strictEqual(c._nwTouched, true); assert.ok(row.next_week_touched_at);
});

test('a save the server refuses because the week moved on becomes a question: save it as the new draft, or discard', async (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); withSaved(c); c._nwSetCap('q3');
  c._doStateRefresh = (cb) => { c.refreshes++; c._state.deadline = MANAGED({ applied_to: 4 }); c._nwTarget = 5; cb(true); };   // the refresh moves the target on, as the real one does
  c.saveImpl = () => Promise.reject(REFUSED);
  c._nwSave(); await tick();
  assert.strictEqual(c.refreshes, 1); assert.strictEqual(c.blockings.length, 1); assert.strictEqual(c.fails.length, 0); assert.strictEqual(c.warns.length, 0);
  const b = c.blockings[0];
  assert.strictEqual(b.title, 'Week 4 just started');
  assert.strictEqual(b.message, 'Your draft was for the Week 4 lineup, but Week 4 is already playing. Save it as your Week 5 draft?');
  assert.strictEqual(b.primary.label, 'Save as Week 5 draft'); assert.strictEqual(b.secondary.label, 'Discard');
  c.saveImpl = () => Promise.resolve({ ok: true, touched_at: 'x' });
  b.primary.onClick(); await tick();
  assert.strictEqual(c.sent.length, 2); assert.strictEqual(c.sent[1].w, 5, 'saved again, for the week that is open now'); assert.strictEqual(c.sent[1].d.cap, 'q3', 'with the draft the member had');
});

test('a refused save, discarded: nothing is saved and the kept copy goes', async (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); withSaved(c); c._nwSetCap('q3'); assert.ok(keptDraft(c));
  c._doStateRefresh = (cb) => { c._state.deadline = MANAGED({ applied_to: 4 }); c._nwTarget = 5; cb(true); };
  c.saveImpl = () => Promise.reject(REFUSED);
  c._nwSave(); await tick();
  c.blockings[0].secondary.onClick();
  assert.strictEqual(c.sent.length, 1, 'only the refused attempt'); assert.strictEqual(keptDraft(c), null);
});

test('the state sync adopts the first answer quietly and refetches when a handover moves the draft week', (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 });
  c._nwTarget = null; c._nwSyncTarget({ deadline: MANAGED({ applied_to: 3 }) }); assert.strictEqual(c._nwTarget, 4); assert.strictEqual(c.refreshes, 0);
  c._nwSyncTarget({ deadline: MANAGED({ applied_to: 3 }) }); assert.strictEqual(c.refreshes, 0);
  c._nwSyncTarget({ deadline: MANAGED({ applied_to: 4 }) }); assert.strictEqual(c.refreshes, 1, 'the first week start moves the target without moving the current week');
  c._username = null; c._nwSyncTarget({ deadline: MANAGED({ applied_to: 5 }) }); assert.strictEqual(c.refreshes, 1);
});

test('the Pick card names the week the draft is for (Week 2 after the first week starts) and when it locks in the member’s own time', (c) => {
  c._currentWeek = 1; c._state.deadline = MANAGED({ applied_to: 1, next_lock_utc: '2027-01-18T14:00:00+00:00' }); withSaved(c);
  let h = c._buildNextWeekCard(); assert.match(h, /Week 2 draft/); assert.ok(!/Pick your team/.test(h));
  assert.match(h, /class="nw-when">Locks [A-Z][a-z]{2},? 18 Jan,? \d{2}:\d{2}</);
  c._state.deadline = MANAGED({ applied_to: 0 }); h = c._buildNextWeekCard(); assert.match(h, /Pick your team/);
  c._state.deadline = MANAGED({ applied_to: 1, next_lock_utc: PAST(), locked: true, awaiting: { kind: 'rollover', to_week: 2 } }); assert.ok(!/nw-when/.test(c._buildNextWeekCard()), 'no "locks at" once it has locked');
});

test('local lock time: weekday, day, month and the time in the member’s zone; junk gives nothing', (c) => {
  assert.match(c._whenLocal('2027-01-18T14:00:00+00:00', 'en-GB', 'Africa/Johannesburg'), /^Mon,? 18 Jan,? 16:00$/);
  assert.match(c._whenLocal('2027-01-17T12:30:00+00:00', 'en-GB', 'Africa/Johannesburg'), /^Sun,? 17 Jan,? 14:30$/);
  assert.strictEqual(c._hhmm('2027-01-17T12:30:00+00:00', 'en-GB', 'Africa/Johannesburg'), '14:30');
  assert.strictEqual(c._whenLocal('not a date'), ''); assert.strictEqual(c._hhmm(null), ''); assert.strictEqual(c._whenLocal('2027-01-18T14:00:00+00:00', 'en-GB', 'Not/AZone'), '');
});

test('the lock bar says what is going on: setting up the next week, slow, closed for good, or locked until the next rollover', (c) => {
  withSaved(c); c._currentWeek = 3;
  c._state.deadline = MANAGED({ locked: true, next_lock_utc: new Date(Date.now() - 30e3).toISOString(), awaiting: { kind: 'rollover', to_week: 4 } });
  let lc = c._lockCardText(); assert.match(lc.title, /^Picks locked at \d{2}:\d{2}$/); assert.strictEqual(lc.text, 'Setting up Week 4. This takes a few seconds.');
  assert.match(c._deadlineText(), /Locked — setting up Week 4/);
  const h = c._buildNextWeekCard(); assert.match(h, /Setting up Week 4/); assert.ok(!/next rollover/.test(h));
  c._state.deadline = MANAGED({ locked: true, next_lock_utc: new Date(Date.now() - 5 * 60e3).toISOString(), awaiting: { kind: 'rollover', to_week: 4 } });
  lc = c._lockCardText(); assert.strictEqual(lc.title, 'Still setting up Week 4'); assert.strictEqual(lc.text, 'This is taking longer than usual. Your locked XI is safe.');
  c._state.deadline = MANAGED({ season_over: true, locked: true, next_lock_utc: null }); assert.strictEqual(c._lockCardText().title, 'Picks are closed');
  c._state.deadline = { status: 'active', next_lock_utc: PAST() };
  lc = c._lockCardText(); assert.strictEqual(lc.title, 'Picks are locked'); assert.match(lc.text, /New picks open again after the next rollover/); assert.match(c._deadlineText(), /Locked — awaiting rollover/);
});

test('notices: no XI for the week that is playing; "Week N is open" once, only after a handover that carried the XI over', (c) => {
  const weeks = (cur, prev) => ({ weeks: [Object.assign({ week_no: 2, is_current: false, this_week: { team: XI }, next_week: { team: XI } }, prev), Object.assign({ week_no: 3, is_current: true }, cur)] });
  c._currentWeek = 3; c._state.deadline = MANAGED({ applied_to: 3 });
  c._historyData = weeks({ this_week: { team: [] }, next_week: { team: [] } }, {});
  let n = c._nwNotices(); assert.match(n, /No XI for Week 3/); assert.match(n, /score 0 until Week 4\. You can still draft Week 4\./); assert.match(n, /nw-notice warn/);
  c._state.deadline = MANAGED({ applied_to: 3, season_over: true }); assert.match(c._nwNotices(), /score 0 for the rest of the season/);
  c._state.deadline = MANAGED({ applied_to: 2 }); assert.strictEqual(c._nwNotices(), '', 'this week has not been handed over yet');
  c._state.deadline = MANAGED({ applied_to: 3 });
  c._historyData = weeks({ this_week: { team: XI }, next_week: { team: XI } }, { next_week_touched_at: null });
  n = c._nwNotices(); assert.match(n, /Week 3 is open/); assert.match(n, /Your XI carried over\./); assert.match(n, /_nwDismissNotice\(\)/);
  c._nwDismissNotice(); assert.strictEqual(c.store.get('ipl_seen_week:sa20_2027:Sai'), '3'); assert.strictEqual(c._nwNotices(), '', 'said once');
  c.store.clear(); c._historyData = weeks({ this_week: { team: XI }, next_week: { team: XI } }, { next_week_touched_at: '2027-01-12T08:00:00+00:00' });
  assert.strictEqual(c._nwNotices(), '', 'the member picked it themselves: nothing to explain');
  c._state.deadline = { status: 'active', next_lock_utc: FUTURE() }; assert.strictEqual(c._nwNotices(), '', 'only for a competition on a lock schedule');
});

test('the banner on the other tabs keeps a carried-over XI in one tap, and is not "ready" green until the member has', (c) => {
  c._state.deadline = MANAGED({ applied_to: 3 }); const row = carriedRow(); c._historyData = { weeks: [row] }; c._nwInit(row);
  let b = c._buildActionBanner();
  assert.match(b, /Using last week’s XI\. Keep it, or change it\./); assert.match(b, /onclick="_nwKeep\(\)">Keep this XI</); assert.ok(!/action-banner ok/.test(b));
  row.next_week_touched_at = '2027-01-19T08:00:00+00:00'; c._nwInit(row); b = c._buildActionBanner();
  assert.match(b, /Your XI is ready/); assert.match(b, /action-banner ok/); assert.ok(!/Keep this XI/.test(b));
});

// ── UX pass 2026-11-22: a failed history load, and a finished competition ──
const COMPS = [{ slug: 'ipl_2026', name: 'IPL 2026', status: 'completed', champion: 'Sai' }, { slug: 'sa20_2027', name: 'SA20 2027', status: 'upcoming' }];
const onFinished = (c, comps = COMPS) => { c._competitions = comps; c._activeComp = 'ipl_2026'; c._activeCompObj = () => comps.filter((x) => x.slug === 'ipl_2026')[0] || {}; return c; };

test('Pick card: the saved XI did not load -> an error card with Try again, never an empty editor that looks like a lost team', (c) => {
  c._historyData = null; c._historyFailed = true;
  let h = c._buildNextWeekCard();
  assert.match(h, /Could not load your team/); assert.match(h, /_retryHistory\(\)/); assert.match(h, /Your saved picks are safe/);
  assert.ok(!/add-players|nw-budget-bar|nw-right|picker-stub|nw-layout/.test(h), 'no editor to start from an empty draft');
  c._historyFailed = false; h = c._buildNextWeekCard();
  assert.match(h, /nw-layout/, 'while it is only loading (not failed) the editor shows as before');
  withSaved(c); c._historyFailed = true; h = c._buildNextWeekCard();
  assert.match(h, /nw-layout/); assert.ok(!/Could not load your team/.test(h), 'the data is there: a stale failure flag never hides a loaded team');
});

test('a finished competition: the Pick card names the competition that is live (else the first still to come) and has a button to go there', (c) => {
  onFinished(c);
  let h = c._buildNextWeekCard();
  assert.match(h, /This competition has finished/); assert.match(h, /SA20 2027 starts soon: pick your team there\./); assert.match(h, /onclick="_switchComp\('sa20_2027'\)">Go to SA20 2027</);
  onFinished(c, [COMPS[0], { slug: 'up_1', name: 'Later Cup', status: 'upcoming' }, { slug: 'sa20_2027', name: 'SA20 2027', status: 'active' }]);
  h = c._buildNextWeekCard(); assert.match(h, /SA20 2027 is live now/); assert.match(h, /_switchComp\('sa20_2027'\)/, 'live beats an earlier "upcoming" in the list');
  onFinished(c, [COMPS[0]]);
  h = c._buildNextWeekCard(); assert.match(h, /Picks are closed\./); assert.ok(!/_switchComp|Go to/.test(h), 'nothing to go to: no button, no sentence about switching');
});

test('_nextComp: only a finished competition has a next one; never itself; a slug cannot break out of the handler', (c) => {
  c._competitions = COMPS; c._activeCompObj = () => COMPS[1];
  assert.strictEqual(c._nextComp(), null, 'the open competition has nothing to hand over to');
  onFinished(c); assert.strictEqual(c._nextComp().slug, 'sa20_2027');
  onFinished(c, [Object.assign({}, COMPS[0]), { slug: "x');alert(1);//", name: 'Evil <b>', status: 'active' }]);
  const b = c._buildNextCompBanner();
  assert.ok(b.indexOf("_switchComp('x\\');alert(1);//')") >= 0, 'the slug is escaped inside the handler: ' + b); assert.ok(b.indexOf('Evil <b>') === -1 && b.indexOf('Evil &lt;b&gt;') >= 0, 'the name is escaped as text');
});

test('the banner for a finished competition: signed-in members only, one tap to the next competition; the pick-deadline banner stays out of its way', (c) => {
  onFinished(c);
  const b = c._buildNextCompBanner();
  assert.match(b, /class="action-banner"/); assert.match(b, /role="region" aria-label="Next competition"/);
  assert.match(b, /IPL 2026 has finished/); assert.match(b, /SA20 2027 starts soon\. Pick your team there\./); assert.match(b, /onclick="_switchComp\('sa20_2027'\)">Go to SA20 2027</);
  assert.strictEqual(c._buildActionBanner(), '', 'the deadline banner is silent for a finished competition (as before)');
  c._username = null; assert.strictEqual(c._buildNextCompBanner(), '', 'signed out: the sign-in card is the whole story');
  c._username = 'Sai'; c._activeCompObj = () => COMPS[1]; assert.strictEqual(c._buildNextCompBanner(), '', 'an open competition has the deadline banner instead');
  onFinished(c, [COMPS[0]]); assert.strictEqual(c._buildNextCompBanner(), '', 'nothing to point at: no banner');
});

(async () => {
  for (const t of queue) {
    try { await t.fn(load()); results.push('PASS  ' + t.name); }
    catch (e) { failed++; results.push('FAIL  ' + t.name + '\n        ' + String(e && e.message).split('\n')[0]); }
  }
  console.log(results.join('\n'));
  console.log(`\n${results.length - failed} of ${results.length} passed${failed ? '  --  ' + failed + ' FAILED' : ''}`);
  process.exit(failed ? 1 : 0);
})();
