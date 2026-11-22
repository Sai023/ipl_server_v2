// Regression test for the Match Centre cache (Static/mc_hub.js): what is shown must always belong to the competition AND the member
// on screen. Loads the REAL mc_hub.js into a fresh sandbox per case, with a controllable fake API.   node tests/js/mc_hub_cache.test.js
//
// The bug it guards against (seen on a phone, 2026-11-15): after switching IPL -> SA20 the Matches tab kept showing IPL's totals and weeks
// under the SA20 header until the next 60-second poll, because the cache was only cleared by a "state-updated" event.
// MC_HUB_PATH=<file> points the test at another copy (e.g. an older version, to see that the test really catches the bug).
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const SRC = fs.readFileSync(process.env.MC_HUB_PATH || path.join(__dirname, '..', '..', 'Static', 'mc_hub.js'), 'utf8');

function load(opts) {
  opts = opts || {};   // opts.page: behave like the real page, whose own "state-updated" listener RENDERS and was registered BEFORE the hub's (so it runs first)
  const ctx = { calls: [], renders: 0, timers: [] }, listeners = {};
  ctx.window = ctx;
  ctx.addEventListener = (t, f) => { (listeners[t] = listeners[t] || []).push(f); };
  ctx.dispatch = (t, detail) => (listeners[t] || []).forEach((f) => f({ type: t, detail }));
  ctx.setTimeout = (fn, ms) => { ctx.timers.push({ fn, ms, cleared: false }); return ctx.timers.length; };
  ctx.clearTimeout = (id) => { if (ctx.timers[id - 1]) ctx.timers[id - 1].cleared = true; };
  if (opts.page) (listeners['ipl:state-updated'] = listeners['ipl:state-updated'] || []).push(() => ctx.render(ctx._state));
  ctx.document = { querySelectorAll: () => [] };
  ctx.console = { log: console.log, warn() {} };   // a failed request is logged by the module: keep the test output clean
  ctx.comp = 'ipl_2026';
  ctx.IplComp = { get: () => ctx.comp };
  ctx.IplApi = { getMatchCentre: (user) => new Promise((resolve, reject) => ctx.calls.push({ user, comp: ctx.comp, resolve, reject })) };
  ctx.Ui = { accOpen: (id, def) => !!def, accToggle() {}, accAll() {}, icon: () => '', stateHtml: (k, o) => `<state ${k}${o && o.retry ? ' retry=' + o.retry : ''}>` };
  ctx.render = () => { ctx.renders++; if (opts.page && ctx._activeTab === 'match-centre') ctx._buildMatchCentreTab(); };   // the page's render rebuilds the Matches tab when it is the open one
  ctx._username = 'Sai'; ctx._activeTab = 'match-centre'; ctx._state = {};
  vm.createContext(ctx); vm.runInContext(SRC, ctx, { filename: 'mc_hub.js' });
  return {
    ctx, calls: ctx.calls,
    build: () => ctx._buildMatchCentreTab(),
    answer: (call, data) => { call.resolve(data); return new Promise((r) => setImmediate(r)); },
    fail: (call, err) => { call.reject(err || new Error('network')); return new Promise((r) => setImmediate(r)); },
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
async function test(name, fn, opts) { try { await fn(load(opts)); results.push('PASS  ' + name); } catch (e) { failed++; results.push('FAIL  ' + name + '\n        ' + String(e.message).split('\n')[0]); } }

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

  // ---- the stuck spinner (seen on a phone, 2026-11-22, right after the browser sign-in sent the member back to the app) ----
  // The page's "state-updated" listener RENDERS (it starts the Match Centre request, or finds it already in flight); only THEN does the hub hear the same event and
  // cancel that request as out of date. Nothing renders again, so the cancelled request was never made again: the spinner stayed until the member switched tabs.
  const ERROR = /<state error retry=_mcRetry\(\)>/, SLOW = /<state loading retry=_mcRetry\(\)>/;
  await test('THE SPINNER BUG: an update that lands while the first request is in flight must not strand the spinner', async (t) => {
    t.build();                                                  // the first render after sign-in: request 1
    t.ctx.dispatch('ipl:state-updated', {});                    // the glue's first poll answer: the page renders, THEN the hub marks the cache stale
    assert.strictEqual(t.calls.length, 2, 'the cancelled request is made again at once, not at some later render that may never come');
    await t.answer(t.calls[0], Object.assign({}, IPL, { season: Object.assign({}, IPL.season, { total_pts: 111 }) }));
    assert.ok(!t.build().includes('111'), 'the answer that was older than the update is still never shown');
    const before = t.ctx.renders;
    await t.answer(t.calls[1], IPL);
    assert.ok(t.ctx.renders > before, 'the arrival of the data repaints the page');
    const h = t.build(); assert.ok(h.includes('10367') && !LOADING.test(h), 'the Match Centre shows');
  }, { page: true });

  await test('an update while the tab is open refreshes the data on screen at once (the page renders BEFORE the hub hears of it)', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL); assert.ok(t.build().includes('10367'));
    t.ctx.dispatch('ipl:state-updated', {});
    assert.strictEqual(t.calls.length, 2, 'the quiet refresh starts without waiting for the next render');
    assert.ok(t.build().includes('10367'), 'meanwhile the old numbers stay on screen');
    await t.answer(t.calls[1], Object.assign({}, IPL, { season: Object.assign({}, IPL.season, { total_pts: 10500 }) }));
    assert.ok(t.build().includes('10500'), 'and the fresh ones replace them');
  }, { page: true });

  await test('an update while another tab is open waits for the Matches tab (no request nobody looks at)', async (t) => {
    t.ctx._activeTab = 'my-team'; t.build(); await t.answer(t.calls[0], IPL);
    t.ctx.dispatch('ipl:state-updated', {});
    assert.strictEqual(t.calls.length, 1, 'nothing is fetched in the background');
    t.ctx._activeTab = 'match-centre';
    assert.ok(t.build().includes('10367')); assert.strictEqual(t.calls.length, 2, 'it refreshes when the member opens the tab');
  }, { page: true });

  // ---- a request that fails or never answers used to leave the spinner for ever and say nothing ----
  await test('a failed request shows an error card with Try again, and Try again asks once more', async (t) => {
    t.build(); await t.fail(t.calls[0]);
    assert.ok(t.ctx.renders >= 1, 'the failure repaints the page');
    const h = t.build(); assert.ok(ERROR.test(h) && !LOADING.test(h), 'an error card with a retry, not a spinner');
    assert.strictEqual(t.calls.length, 1, 'an ordinary render does not keep hammering a failing server');
    t.ctx._mcRetry();
    assert.strictEqual(t.calls.length, 2, 'Try again asks once more');
    assert.ok(LOADING.test(t.build()), 'and the spinner is back while it does');
    await t.answer(t.calls[1], IPL); assert.ok(t.build().includes('10367'));
  }, { page: true });

  await test('an answer that says not-ok is an error card too', async (t) => {
    t.build(); await t.answer(t.calls[0], { ok: false, error: 'x' });
    assert.ok(ERROR.test(t.build()));
  }, { page: true });

  await test('a request that is slow keeps its spinner but says so and offers Try again after 25 seconds; a late answer still shows', async (t) => {
    t.build();
    assert.ok(LOADING.test(t.build()) && !SLOW.test(t.build()), 'at first it is just the spinner');
    const timer = t.ctx.timers.find((x) => x.ms === 25000 && !x.cleared); assert.ok(timer, 'a 25 second limit is set');
    timer.fn();
    const h = t.build(); assert.ok(SLOW.test(h) && !ERROR.test(h), 'still the spinner (the request is still running) with a message and a button');
    assert.strictEqual(t.calls.length, 1, 'and no second request has been made');
    await t.answer(t.calls[0], IPL);
    assert.ok(t.build().includes('10367'), 'if the answer does come it is shown');
  }, { page: true });

  await test('Try again on a slow request starts a new one and drops the old one', async (t) => {
    t.build(); t.ctx.timers.find((x) => x.ms === 25000 && !x.cleared).fn(); assert.ok(SLOW.test(t.build()));
    t.ctx._mcRetry();
    assert.strictEqual(t.calls.length, 2, 'a new request');
    assert.ok(!SLOW.test(t.build()) && LOADING.test(t.build()), 'the plain spinner again while it runs');
    await t.answer(t.calls[0], Object.assign({}, IPL, { season: Object.assign({}, IPL.season, { total_pts: 111 }) }));
    assert.ok(!t.build().includes('111'), 'the abandoned request is not shown');
    await t.answer(t.calls[1], IPL); assert.ok(t.build().includes('10367'));
  }, { page: true });

  await test('the limit is cleared when the answer comes in time', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL);
    assert.ok(t.ctx.timers.every((x) => x.cleared || x.ms !== 25000), 'no timer is left to turn good data into an error');
  }, { page: true });

  await test('a failed refresh keeps the data on screen: no error card over good numbers', async (t) => {
    t.build(); await t.answer(t.calls[0], IPL);
    t.ctx.dispatch('ipl:state-updated', {}); await t.fail(t.calls[1]);
    const h = t.build(); assert.ok(h.includes('10367') && !ERROR.test(h));
  }, { page: true });

  await test('a new update, or the server coming back, is a new chance for a failed Match Centre', async (t) => {
    t.build(); await t.fail(t.calls[0]); assert.ok(ERROR.test(t.build()));
    t.ctx.dispatch('ipl:recovered');
    assert.strictEqual(t.calls.length, 2, 'the server is back: it asks again by itself');
    await t.fail(t.calls[1]); assert.ok(ERROR.test(t.build()));
    t.ctx.dispatch('ipl:state-updated', {});
    assert.strictEqual(t.calls.length, 3, 'new scores: it asks again by itself');
  }, { page: true });

  await test('another competition or member never sees the error card of the previous one', async (t) => {
    t.build(); await t.fail(t.calls[0]); assert.ok(ERROR.test(t.build()));
    t.setComp('sa20_2027', true);
    assert.ok(LOADING.test(t.build()) && !ERROR.test(t.build()), 'a fresh start for the new competition');
  }, { page: true });

  console.log(results.join('\n')); console.log(`\n${results.length - failed} of ${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
