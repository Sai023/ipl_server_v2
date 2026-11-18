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
  '_nwPickRole', '_nwOpenPlayer', '_deadlineLocked', '_deadlineText', '_lockUrgent', '_pickerRowsHtml', '_lockIn', '_buildActionBanner', '_nwShown', '_buildNwSquad', '_buildBudgetBar', '_buildNextWeekCard', '_focusKey', '_focusFind', '_navDot'];

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
    _pickOpen: false, _historyData: null, _state: { deadline: { next_lock_utc: FUTURE(), status: 'active' } }, $app: null,
  });
  const catalogue = o.players || PLAYERS;
  c._allPlayers = () => catalogue.slice().sort((a, b) => (a.name < b.name ? -1 : 1));
  c.resolvePlayer = (id) => catalogue.find((p) => p.id === id || p.name === id) || { name: String(id), role: '', team: '', price: 0 };
  c.render = () => { c.renders++; };
  c.esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  c.escAttr = (s) => String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  c._avClass = () => ''; c._initials = (n) => String(n).slice(0, 2); c._roleBadge = (r) => (r ? '<i class="badge">' + r + '</i>' : '');
  c._wide = () => false; c._activeCompObj = () => ({ status: 'active' }); c._buildPicker = () => '<picker-stub/>';
  c.Ui.toast = (msg, opts) => { c.toasts.push({ msg, opts }); };
  c.Ui.warn = (m) => { c.warns.push(m); }; c.Ui.success = (m) => { c.toasts.push({ msg: m, type: 'success' }); };
  c.Ui.fail = (e, prefix) => { c.fails.push((prefix ? prefix + ': ' : '') + c.Ui.errorMessage(e)); };
  c.confirmAnswer = false;
  c.Ui.confirm = (opts) => { c.confirms.push(opts); return Promise.resolve(c.confirmAnswer); };
  c.Ui.sheet = (opts) => { c.sheets.push(opts); return () => {}; };
  c.IplApi = { saveNextWeek: (u, d) => { c.sent.push({ u, d: clone(d) }); return c.saveImpl(); } };
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
test('a kept draft from another week is dropped and cleaned up', (c) => {
  const row = withSaved(c); c._nwRemove('q13', true);
  const d = keptDraft(c); d.wk = 2; c.store.set(KEY, JSON.stringify(d));
  const c2 = load({ store: c.store }); c2._historyData = { weeks: [row] }; c2._nwInit(row);
  assert.strictEqual(c2._nwHasUnsaved(), false); assert.strictEqual(c2._nwRestored, false); assert.strictEqual(keptDraft(c2), null);
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
const fullDraft = (c, cap = 'q2', vc = 'q4') => { withSaved(c, XI, cap, vc); c._nwRemove('q13', true); c._nwToggle('q13'); c._nwSetRole('q6', 'vc'); c._nwSetRole('q6', 'none'); c._nwDraft.vc = vc; c._nwSetRole('q3', 'none'); return c; };
test('Save while locked sends nothing and says so', (c) => {
  withSaved(c); c._nwSetRole('q6', 'vc'); c._state.deadline.next_lock_utc = PAST();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /locked/i);
});
test('Save with fewer than 11 players asks for 11 and sends nothing', (c) => {
  c._nwToggle('q2'); c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.match(c.warns[0], /11/);
});
test('no captain: asks before saving (focus starts on "Choose captain"), nothing is sent yet', (c) => {
  withSaved(c, XI, null, 'q4'); c._nwDraft.cap = null; c._nwChanged();
  c._nwSave();
  assert.strictEqual(c.sent.length, 0); assert.strictEqual(c.confirms.length, 1);
  const q = c.confirms[0];
  assert.strictEqual(q.confirmText, 'Save anyway'); assert.strictEqual(q.cancelText, 'Choose captain'); assert.strictEqual(q.focusCancel, true);
});
test('closing the captain question (or "Choose captain") saves nothing and opens the captain list', async (c) => {
  withSaved(c, XI, null, 'q4'); c._nwDraft.cap = null; c._nwChanged();
  c.confirmAnswer = false; c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c.sent.length, 0); assert.strictEqual(c.sheets[0].title, 'Choose your captain');
});
test('"Save anyway" sends the XI without a captain', async (c) => {
  withSaved(c, XI, null, 'q4'); c._nwDraft.cap = null; c._nwChanged();
  c.confirmAnswer = true; c._nwSave(); await new Promise((r) => setTimeout(r, 10));
  assert.strictEqual(c.sent.length, 1); assert.strictEqual(c.sent[0].d.cap, null); assert.strictEqual(c.sent[0].u, 'Sai');
});
test('a missing vice-captain does not stop the save (only the captain is asked about)', (c) => {
  withSaved(c, XI, 'q2', null); c._nwDraft.vc = null; c._nwChanged();
  c._nwSave();
  assert.strictEqual(c.confirms.length, 0); assert.strictEqual(c.sent.length, 1);
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
test('hint, full XI without roles: offers one button for the captain, then for the vice-captain', (c) => {
  withSaved(c, XI, null, null); c._nwDraft.cap = null; c._nwDraft.vc = null; c._nwSaved = c._nwSig(c._nwDraft);
  let h = bar(c); assert.match(hintOf(h), /^Choose your captain and vice-captain/); assert.match(h, /onclick="_nwPickRole\('cap'\)"[^>]*>Choose</);
  c._nwDraft.cap = 'q2'; h = bar(c); assert.match(hintOf(h), /^Choose your vice-captain/); assert.match(h, /_nwPickRole\('vc'\)/);
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

(async () => {
  for (const t of queue) {
    try { await t.fn(load()); results.push('PASS  ' + t.name); }
    catch (e) { failed++; results.push('FAIL  ' + t.name + '\n        ' + String(e && e.message).split('\n')[0]); }
  }
  console.log(results.join('\n'));
  console.log(`\n${results.length - failed} of ${results.length} passed${failed ? '  --  ' + failed + ' FAILED' : ''}`);
  process.exit(failed ? 1 : 0);
})();
