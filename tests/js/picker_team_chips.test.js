// Regression tests for the Add-players picker: the multi-team filter (Ui.chips) and taking a picked player OUT from inside the picker.
// Loads the REAL Static/ui.js and the REAL picker functions out of templates/index.html (so a change there is what gets tested).
//   node tests/js/picker_team_chips.test.js        (exit 1 on any failure; one PASS/FAIL line per case)
// What it guards (asked for by the league, 2026-11-18): (1) several teams can be on at once, none = every team; (2) a player who is already in the
// XI is taken out by tapping again (no trip back to the squad screen) and a captain who is removed loses the flag; (3) "Undo" and the swap sheet
// for a full XI are untouched.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.join(__dirname, '..', '..');
const UI_SRC = fs.readFileSync(process.env.UI_JS_PATH || path.join(ROOT, 'Static', 'ui.js'), 'utf8');   // UI_JS_PATH / INDEX_HTML_PATH: point the test at another copy (e.g. the live one, to see it catch the old behaviour)
const HTML = fs.readFileSync(process.env.INDEX_HTML_PATH || path.join(ROOT, 'templates', 'index.html'), 'utf8');

// Source of one top-level `function name(...) {...}` in the page script, found by brace matching (strings and regex literals in these
// functions never contain unbalanced braces).
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

const PLAYERS = [
  { id: 'p1', name: 'Virat Kohli', team: 'RCB', role: 'BAT', price: 15, season_pts: 1082 },
  { id: 'p2', name: 'Jos Buttler', team: 'GT', role: 'WK', price: 11, season_pts: 1055 },
  { id: 'p3', name: 'Rohit Sharma', team: 'MI', role: 'BAT', price: 12, season_pts: 900 },
  { id: 'p4', name: 'MS Dhoni', team: 'CSK', role: 'WK', price: 8, season_pts: 700 },
  { id: 'p5', name: 'Jasprit Bumrah', team: 'MI', role: 'BOWL', price: 13, season_pts: 950 },
  { id: 'p6', name: 'Rajat Patidar', team: 'RCB', role: 'BAT', price: 11, season_pts: 861 },
];

function load() {
  const ctx = { toasts: [], swaps: [], renders: 0 };
  ctx.window = ctx; ctx.console = console;
  ctx.addEventListener = () => {}; ctx.removeEventListener = () => {};
  ctx.setTimeout = setTimeout; ctx.clearTimeout = clearTimeout; ctx.navigator = {}; ctx.location = {};
  ctx.document = { addEventListener() {}, removeEventListener() {}, querySelectorAll: () => [], getElementById: () => null, body: {}, readyState: 'complete' };
  vm.createContext(ctx);
  vm.runInContext(UI_SRC, ctx, { filename: 'ui.js' });                       // the real Ui kit (Ui.chips, Ui.chipsSet, Ui.icon ...)
  ctx.BUDGET_TOTAL = 100; ctx.XI_SIZE = 11;
  ctx._nwDraft = { team: [], cap: null, vc: null };
  ctx._pickerSearch = ''; ctx._pickerRole = 'All'; ctx._pickerTeams = []; ctx._pickerSort = 'season'; ctx._pickerMaxPrice = null;
  ctx._state = null;                                                          // render() is only called when there is a state: keep it quiet
  ctx.Ui.toast = (msg, o) => ctx.toasts.push({ msg, o });
  ctx.Ui.warn = () => {};
  ctx._allPlayers = () => PLAYERS;
  ctx.resolvePlayer = (id) => PLAYERS.find((p) => p.id === id || p.name === id) || { name: id };
  ctx._showSwapModal = (id) => ctx.swaps.push(id);
  ctx._pickerUpdate = () => {};
  ctx._pickerShowNotice = (m, t) => { ctx.notice = { m, t }; };
  for (const n of ['_teamCost', '_remaining', '_priceBounds', '_pickerToggleTeam', '_pickerView', '_pickerActive', '_pickerClear', '_pickerRowClick', '_nwRemove', '_nwToggle'])
    vm.runInContext(fnSource(n), ctx, { filename: n });
  ctx.escAttr = (s) => String(s); ctx.esc = (s) => String(s);
  return ctx;
}
const names = (ctx) => ctx._pickerView().filtered.map((p) => p.name).sort();

const results = []; let failed = 0;
function test(name, fn) { try { fn(load()); results.push('PASS  ' + name); } catch (e) { failed++; results.push('FAIL  ' + name + '\n        ' + String(e.message).split('\n')[0]); } }

// ---- the team filter ----
test('no team on = every team', (c) => { assert.strictEqual(c._pickerView().filtered.length, PLAYERS.length); });
test('one team on', (c) => { c._pickerToggleTeam('MI'); assert.deepStrictEqual(names(c), ['Jasprit Bumrah', 'Rohit Sharma']); });
test('several teams on at once (the new thing)', (c) => {
  c._pickerToggleTeam('MI'); c._pickerToggleTeam('RCB'); c._pickerToggleTeam('CSK');
  assert.deepStrictEqual(Array.from(c._pickerTeams), ['MI', 'RCB', 'CSK']);
  assert.deepStrictEqual(names(c), ['Jasprit Bumrah', 'MS Dhoni', 'Rajat Patidar', 'Rohit Sharma', 'Virat Kohli']);
});
test('tapping a team that is on switches just that team off', (c) => {
  c._pickerToggleTeam('MI'); c._pickerToggleTeam('RCB'); c._pickerToggleTeam('MI');
  assert.deepStrictEqual(Array.from(c._pickerTeams), ['RCB']);
  assert.deepStrictEqual(names(c), ['Rajat Patidar', 'Virat Kohli']);
});
test('the "All" chip ("") switches every team off', (c) => { c._pickerToggleTeam('MI'); c._pickerToggleTeam('GT'); c._pickerToggleTeam(''); assert.deepStrictEqual(Array.from(c._pickerTeams), []); assert.strictEqual(c._pickerView().filtered.length, PLAYERS.length); });
test('role counts follow the chosen teams', (c) => { c._pickerToggleTeam('MI'); c._pickerToggleTeam('RCB'); const k = c._pickerView().counts; assert.strictEqual(k.All, 4); assert.strictEqual(k.BAT, 3); assert.strictEqual(k.BOWL, 1); assert.strictEqual(k.WK, 0); });
test('teams combine with the role and the search filters', (c) => { c._pickerToggleTeam('MI'); c._pickerToggleTeam('GT'); c._pickerRole = 'WK'; assert.deepStrictEqual(names(c), ['Jos Buttler']); c._pickerRole = 'All'; c._pickerSearch = 'sharma'; assert.deepStrictEqual(names(c), ['Rohit Sharma']); });
test('the filter badge counts the team group once, however many teams', (c) => { c._pickerToggleTeam('MI'); c._pickerToggleTeam('GT'); c._pickerToggleTeam('RCB'); assert.strictEqual(c._pickerActive(c._pickerView()), 1); });
test('"Clear all" empties the teams too', (c) => { c._pickerToggleTeam('MI'); c._pickerToggleTeam('GT'); c._pickerClear(); assert.deepStrictEqual(Array.from(c._pickerTeams), []); });

// ---- in / out from inside the picker ----
test('tapping a player who is not in the XI adds him', (c) => { c._pickerRowClick('p1'); assert.deepStrictEqual(c._nwDraft.team, ['p1']); });
test('tapping a player who IS in the XI takes him out again (no "already in your XI" dead end)', (c) => {
  c._pickerRowClick('p1'); c._pickerRowClick('p2');
  c._pickerRowClick('p1');
  assert.deepStrictEqual(c._nwDraft.team, ['p2']);
  assert.ok(!c.notice, 'no notice: the tap did the removal');
  assert.match(c.toasts[0].msg, /Virat Kohli removed/);
  assert.strictEqual(c.toasts[0].o.action.label, 'Undo');
});
test('removing the captain from the picker clears the captain flag; Undo brings both back', (c) => {
  c._pickerRowClick('p1'); c._pickerRowClick('p2'); c._nwDraft.cap = 'p1'; c._nwDraft.vc = 'p2';
  c._pickerRowClick('p1');
  assert.strictEqual(c._nwDraft.cap, null); assert.strictEqual(c._nwDraft.vc, 'p2'); assert.deepStrictEqual(c._nwDraft.team, ['p2']);
  c.toasts[0].o.action.onClick();
  assert.deepStrictEqual(c._nwDraft.team, ['p1', 'p2']); assert.strictEqual(c._nwDraft.cap, 'p1');
});
test('a player stored by NAME in a saved draft is still recognised as picked and taken out', (c) => {
  c._nwDraft.team = ['Virat Kohli']; c._pickerRowClick('p1');
  assert.deepStrictEqual(c._nwDraft.team, []);
});
test('removing frees the budget, so a dearer player can then be added', (c) => {
  c._pickerRowClick('p1'); c._pickerRowClick('p3'); c._pickerRowClick('p5');       // 15 + 12 + 13 = 40
  assert.strictEqual(c._remaining(), 60);
  c._pickerRowClick('p5'); assert.strictEqual(c._remaining(), 73);
});
test('with a full XI, tapping someone outside it still opens the swap sheet (unchanged)', (c) => {
  c.XI_SIZE = 2; vm.runInContext('var XI_SIZE = 2;', c);
  c._pickerRowClick('p1'); c._pickerRowClick('p2');
  c._pickerRowClick('p3');
  assert.deepStrictEqual(c.swaps, ['p3']); assert.deepStrictEqual(c._nwDraft.team, ['p1', 'p2']);
});

// ---- Ui.chips markup ----
test('Ui.chips: an "All" chip + one chip per option, pressed state follows the values', (c) => {
  const h = c.Ui.chips({ id: 'pf-team', label: 'Teams', values: ['MI', 'GT'], onToggle: '_t', allLabel: 'All', options: [{ v: 'MI', dot: 'av-mi' }, { v: 'GT' }, { v: 'RR' }] });
  assert.strictEqual((h.match(/class="tchip(?: on)?"/g) || []).length, 4);
  assert.match(h, /data-v="" onclick="_t\(/);                                        // the All chip passes ""
  assert.strictEqual((h.match(/aria-pressed="true"/g) || []).length, 2);              // MI and GT, not All
  assert.match(h, /<i class="tchip-dot av-mi"/);
  assert.match(h, /role="group" aria-label="Teams"/);
});
test('Ui.chips: with nothing on, the All chip is the pressed one', (c) => {
  const h = c.Ui.chips({ label: 'x', values: [], onToggle: '_t', allLabel: 'All', options: [{ v: 'MI' }] });
  assert.match(h, /class="tchip on" aria-pressed="true" data-v=""/);
});
test('Ui.chips: values are HTML-escaped', (c) => {
  const h = c.Ui.chips({ label: 'x', values: [], onToggle: '_t', options: [{ v: 'A"B', l: '<b>' }] });
  assert.ok(!/<b>/.test(h) && /&lt;b&gt;/.test(h) && /data-v="A&quot;B"/.test(h));
});
test('Ui.chipsSet flips pressed state in place (All is pressed only when nothing else is)', (c) => {
  const mk = (v) => { const a = { v, cls: new Set(), attrs: {} }; return { getAttribute: (k) => (k === 'data-v' ? v : a.attrs[k]), setAttribute: (k, x) => { a.attrs[k] = x; }, classList: { toggle: (k, on) => (on ? a.cls.add(k) : a.cls.delete(k)) }, _a: a }; };
  const chips = [mk(''), mk('MI'), mk('GT')];
  c.Ui.chipsSet({ querySelectorAll: () => chips }, ['GT']);
  assert.deepStrictEqual(chips.map((x) => x._a.attrs['aria-pressed']), ['false', 'false', 'true']);
  c.Ui.chipsSet({ querySelectorAll: () => chips }, []);
  assert.deepStrictEqual(chips.map((x) => x._a.attrs['aria-pressed']), ['true', 'false', 'false']);
});

console.log(results.join('\n'));
console.log(`\n${results.length - failed} of ${results.length} passed${failed ? '  --  ' + failed + ' FAILED' : ''}`);
process.exit(failed ? 1 : 0);
