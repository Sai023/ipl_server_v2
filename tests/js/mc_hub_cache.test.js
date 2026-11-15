// Regression test for the Match Centre cache (Static/mc_hub.js): what is shown must always belong to the competition AND the member
// on screen. Loads the REAL mc_hub.js into a fresh sandbox per case, with a controllable fake API.   node tests/js/mc_hub_cache.test.js
//
// The bug it guards against (seen on a phone, 2026-11-15): after switching IPL -> SA20 the Matches tab kept showing IPL's totals and weeks
// under the SA20 header until the next 60-second poll, because the cache was only cleared by a "state-updated" event.
// MC_HUB_PATH=<file> points the test at another copy (e.g. an older version, to see that the test really catches the bug).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const SRC = fs.readFileSync(process.env.MC_HUB_PATH || path.join(__dirname, '..', '..', 'Static', 'mc_hub.js'), 'utf8');

function load() {
  const ctx = { calls: [] }, listeners = {};
  ctx.window = ctx;
  ctx.addEventListener = (t, f) => { (listeners[t] = listeners[t] || []).push(f); };
  ctx.dispatch = (t) => (listeners[t] || []).forEach((f) => f({ type: t }));
  ctx.document = { querySelectorAll: () => [] };
  ctx.console = console;
  ctx.comp = 'ipl_2026';
  ctx.IplComp = { get: () => ctx.comp };
  ctx.IplApi = { getMatchCentre: (user) => new Promise((resolve) => ctx.calls.push({ user, comp: ctx.comp, resolve })) };
  ctx.Ui = { accOpen: (id, def) => !!def, accToggle() {}, accAll() {}, icon: () => '', stateHtml: (k) => `<state ${k}>` };
  ctx.render = () => {};
  ctx._username = 'Sai'; ctx._activeTab = 'match-centre'; ctx._state = {};
  vm.createContext(ctx); vm.runInContext(SRC, ctx, { filename: 'mc_hub.js' });
  return {
    ctx, calls: ctx.calls,
    build: () => ctx._buildMatchCentreTab(),
    answer: (call, data) => { call.resolve(data); return new Promise((r) => setImmediate(r)); },
    setComp: (c, event) => { ctx.comp = c; if (event) ctx.dispatch('ipl:comp-changed'); },
  };
}

const IPL = { ok: true, season: { total_pts: 10367, matches_played: 74, avg_per_match: 140, best_pts: 704, best_match: 'Eliminator vs X' },
  weeks: [{ week_no: 1, week_pts: 490, matches_played: 2, total_matches: 2, matches: [{ match_id: 'ipl26_m01', match_no: 'M1', title: 'SRH vs RCB', teams: ['SRH', 'RCB'], date_label: 'Mar 28', status: 'completed', user_match_pts: 300 }] }] };
const SA20 = { ok: true, season: { total_pts: 0, matches_played: 0, avg_per_match: 0, best_pts: 0, best_match: null },
  weeks: [{ week_no: 1, week_pts: 0, matches_played: 0, total_matches: 1, matches: [{ match_id: 'sa20_2027_m01', match_no: 'M1', title: 'SEC vs PC, 1st Match', teams: ['SEC', 'PC'], date_label: 'Jan 17', status: 'upcoming', user_match_pts: 0 }] },
          { week_no: 2, week_pts: 0, matches_played: 0, total_matches: 2, matches: [] }] };
const LOADING = /Loading Match Centre/;

const results = []; let failed = 0;
async function test(name, fn) { try { await fn(load()); results.push('PASS  ' + name); } catch (e) { failed++; results.push('FAIL  ' + name + '\n        ' + String(e.message).split('\n')[0]); } }

(async () => {
  await test('IPL loads, then shows its own totals', async (t) => {
    assert.ok(LOADING.test(t.build()), 'first build is the loading card');
    await t.answer(t.calls[0], IPL);
    const h = t.build();
    assert.ok(h.includes('10367') && h.includes('Total Pts') && h.includes('490 pts'), 'IPL totals and week points are shown');
  });

  await test('THE BUG: switching competition with no event must not show the old competition', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL); assert.ok(t.build().includes('10367'));
    t.setComp('sa20_2027');                                   // the page changed competition; nothing told the Match Centre
    const h = t.build();
    assert.ok(!h.includes('10367') && !h.includes('Total Pts') && !h.includes('490 pts'), 'IPL data must not be shown under SA20');
    assert.ok(LOADING.test(h), 'a loading card is shown instead');
    assert.strictEqual(t.calls.length, 2, 'and the SA20 Match Centre is requested');
    assert.strictEqual(t.calls[1].comp, 'sa20_2027');
  });

  await test('the competition-changed event clears the cache too', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL);
    t.setComp('sa20_2027', true);
    assert.ok(!t.build().includes('10367'), 'IPL data must not be shown after the event');
  });

  await test('before the first match: one pre-season note, no zero totals, no "0 pts"', async (t) => {
    t.setComp('sa20_2027'); t.build(); await t.answer(t.calls[0], SA20);
    const h = t.build();
    assert.ok(h.includes('mc-preseason') && h.includes('hasn’t started'), 'pre-season note');
    assert.ok(h.includes('Jan 17') && h.includes('3 matches over 2 weeks'), 'first match date and the size of the season');
    assert.ok(!h.includes('mc-stats-bar') && !h.includes('Total Pts'), 'no four-zeros stat bar');
    assert.ok(!h.includes('0 pts'), 'weeks with nothing played show a dash, not "0 pts"');
  });

  await test('a late answer for the previous competition is dropped', async (t) => {
    t.build();                                                // request A (IPL) is in flight
    t.setComp('sa20_2027', true); t.build();                  // request B (SA20)
    assert.strictEqual(t.calls.length, 2);
    await t.answer(t.calls[0], IPL);                          // A answers late
    assert.ok(!t.build().includes('10367'), 'the IPL answer must not be shown');
    await t.answer(t.calls[1], SA20);
    const h = t.build(); assert.ok(h.includes('mc-preseason') && !h.includes('10367'));
  });

  await test('new scores refresh quietly: content stays on screen while it is fetched again', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL); assert.ok(t.build().includes('10367'));
    t.ctx.dispatch('ipl:state-updated');
    const h = t.build();
    assert.ok(h.includes('10367') && !LOADING.test(h), 'no spinner flash');
    assert.strictEqual(t.calls.length, 2, 'a refresh was requested');
    await t.answer(t.calls[1], Object.assign({}, IPL, { season: Object.assign({}, IPL.season, { total_pts: 10500 }) }));
    assert.ok(t.build().includes('10500'), 'and the fresh numbers replace the old ones');
  });

  await test('an answer older than a state update is dropped', async (t) => {
    t.build();                                                // request 1 in flight
    t.ctx.dispatch('ipl:state-updated');                      // scores changed after it was made
    t.build();                                                // request 2
    assert.strictEqual(t.calls.length, 2, 'a newer request was made');
    await t.answer(t.calls[0], Object.assign({}, IPL, { season: Object.assign({}, IPL.season, { total_pts: 111 }) }));
    assert.ok(!t.build().includes('111'), 'the older answer must not be shown');
    await t.answer(t.calls[1], IPL); assert.ok(t.build().includes('10367'));
  });

  await test('another member never sees the previous member’s points', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL); assert.ok(t.build().includes('10367'));
    t.ctx._username = 'Moe';
    const h = t.build();
    assert.ok(!h.includes('10367') && LOADING.test(h), 'Sai’s Match Centre is not shown to Moe');
    assert.strictEqual(t.calls[t.calls.length - 1].user, 'Moe');
  });

  await test('switching back shows the right competition again', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL);
    t.setComp('sa20_2027', true); t.build(); await t.answer(t.calls[1], SA20); assert.ok(t.build().includes('mc-preseason'));
    t.setComp('ipl_2026', true); t.build(); await t.answer(t.calls[2], IPL);
    const h = t.build(); assert.ok(h.includes('10367') && !h.includes('mc-preseason'));
  });

  console.log(results.join('\n')); console.log(`\n${results.length - failed} of ${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
