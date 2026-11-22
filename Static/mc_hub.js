/**
 * mc_hub.js — Match Centre Hub + Box Score Modal           Golden File v1.2
 * ==========================================================================
 * v1.2 (Phase 9.5 — Final Lock):
 *   • _injectTabStyles() — makes the .tabs nav fully responsive on mobile.
 *     The 6-tab row now scrolls horizontally with no wrapping, hidden
 *     scrollbar, and smooth momentum scrolling on iOS. The Match Centre tab
 *     is highlighted with a teal border so it’s easy to find when scrolled.
 *     Injected once on DOMContentLoaded, idempotent (#ipl-tab-styles guard).
 *
 * v1.1 (Phase 9.4): Box Score Modal with dynamic multiplier rendering.
 *   • Role badge (BAT/BOWL/AR/WK) as coloured pill per player row.
 *   • Multiplier computed client-side (is_cap ×2 gold, is_vc ×1.5 teal).
 *   • Top scorer identified client-side — 2px gold left-border highlight.
 *   • MATCH TOTAL footer = sum(p.final_pts) — independent integrity check.
 *
 * v1.0 (Phase 9.3): Hub list, weekly groups, cache, modal shell.
 *
 * Load order: AFTER ipl_glue.js — overwrites Phase 9.2 stub in ipl_glue.js.
 */

(function () {
  'use strict';

  // ── Module state ──────────────────────────────────────────────────────
  // The Match Centre answer is cached per competition: _mcComp says which one _mcData belongs to, and _mcSeq numbers the requests so an
  // answer that arrives after a reset or a competition switch is dropped instead of being shown under the wrong competition.
  var _mcData    = null;
  var _mcComp    = '';
  var _mcUser    = '';
  var _mcStale   = false;   // true: _mcData is still shown while a newer copy is fetched in the background
  var _mcLoading = false;   // a request is in flight
  var _mcFailed  = false;   // the last request failed (or said "not ok"): no automatic retry from an ordinary render; with nothing on screen an error card offers Try again
  var _mcSlow    = false;   // the request in flight has not answered for MC_SLOW_MS and there is nothing to show: the spinner says so and offers Try again
  var _mcSeq     = 0;
  var MC_SLOW_MS = 25000;   // a sleeping server can take most of a minute: that is "slow", not "failed"; the answer, if it still comes, is shown

  function _curComp() { try { return (window.IplComp && IplComp.get && IplComp.get()) || ''; } catch (e) { return ''; } }
  function _mcReset()     { _mcData = null; _mcComp = ''; _mcUser = ''; _mcStale = false; _mcLoading = false; _mcFailed = false; _mcSlow = false; _mcSeq++; }
  function _mcShowing()   { return !!(window._state && window._activeTab === 'match-centre'); }
  function _mcRepaint()   { if (_mcShowing()) render(window._state); }

  // Ask the server for the Match Centre of the competition and member on screen (never two requests at once). The answer repaints the page.
  function _mcFetch() {
    var user = window._username;
    if (!user || _mcLoading) return;
    var comp = _curComp();
    _mcLoading = true; _mcFailed = false; _mcSlow = false;
    var seq = ++_mcSeq;
    var slow = setTimeout(function () {                           // no answer yet and nothing to show: say so, keep waiting
      if (seq === _mcSeq && _mcLoading && !_mcData) { _mcSlow = true; _mcRepaint(); }
    }, MC_SLOW_MS);
    function failed(why) {
      clearTimeout(slow);
      if (seq !== _mcSeq) return;
      if (why) console.warn('[MC] fetch failed:', why);
      _mcLoading = false; _mcSlow = false; _mcFailed = true; _mcComp = comp; _mcUser = user;   // with numbers on screen this stays quiet (no error card over good data)
      _mcRepaint();
    }
    IplApi.getMatchCentre(user).then(function (d) {
      clearTimeout(slow);
      if (seq !== _mcSeq) return;                                 // reset, competition switch or a newer update since this request was made: the answer is stale
      if (!(d && d.ok)) { failed('not ok'); return; }
      _mcLoading = false; _mcSlow = false; _mcFailed = false;
      _mcData = d; _mcComp = comp; _mcUser = user; _mcStale = false;
      _mcRepaint();
    }, function (err) { failed(err && err.message ? err.message : String(err)); });
  }

  // New scores or a refresh: keep showing what we have (no spinner flash) and fetch it again quietly. The page's own "state-updated" listener runs BEFORE this one and RENDERS, which
  // may have started (or found in flight) a request: that request is older than this update, so it is cancelled here and must be made again NOW. Waiting for "the next render"
  // left the spinner for good (seen on a phone, 2026-11-22). With another tab open nothing is fetched: opening the Matches tab renders, and renders ask.
  function _mcMarkStale() {
    _mcStale = true; _mcFailed = false; _mcSlow = false;
    if (_mcLoading) { _mcLoading = false; _mcSeq++; }
    if (_mcShowing()) _mcFetch();
  }
  // The server is back after trouble: a Match Centre that failed asks again by itself.
  function _mcRecovered() {
    if (!_mcFailed) return;
    _mcFailed = false;
    if (_mcShowing()) { _mcFetch(); _mcRepaint(); }
  }
  // "Try again" on the error card or on a slow spinner: abandon whatever is running and ask once more.
  window._mcRetry = function () {
    if (_mcLoading) { _mcLoading = false; _mcSeq++; }
    _mcFailed = false; _mcSlow = false;
    _mcFetch(); _mcRepaint();
  };

  window.addEventListener('ipl:state-updated', _mcMarkStale);
  window.addEventListener('ipl:recovered',     _mcRecovered);
  window.addEventListener('ipl:comp-changed',  _mcReset);   // switching competition must never leave the previous one's Match Centre on screen

  // ── Responsive tab nav (Phase 9.5) ───────────────────────────────────────
  /**
   * _injectTabStyles()
   * Makes the .tabs row scroll horizontally on narrow screens so all 6 tabs
   * remain accessible without wrapping or clipping.
   *
   * Responsive rules:
   *   .tabs         — overflow-x:auto, no wrapping, hidden scrollbar
   *   .tab-btn      — flex-shrink:0 so buttons never compress below min-width
   *   Match Centre  — teal border accent to aid discovery when scrolled
   *   @media ≤480px — smaller font/padding for tighter screens
   */
  function _injectTabStyles() {
    return;   // tab bar is restyled in Static/screens.css (nav-tabs / bottom-nav)
    if (document.getElementById('ipl-tab-styles')) return;
    var s = document.createElement('style');
    s.id  = 'ipl-tab-styles';
    s.textContent = [
      /* Make .tabs horizontally scrollable on any screen width */
      '.tabs{',
        'display:flex;',
        'flex-wrap:nowrap;',                    /* prevent wrapping */
        'overflow-x:auto;',                     /* horizontal scroll */
        '-webkit-overflow-scrolling:touch;',    /* iOS momentum scroll */
        'scrollbar-width:none;',                /* Firefox hidden scrollbar */
        'gap:4px;',
        'padding-bottom:2px;',                  /* room for focus ring */
      '}',
      /* Hide WebKit scrollbar */
      '.tabs::-webkit-scrollbar{display:none;}',
      /* Prevent tab buttons from shrinking below their natural width */
      '.tab-btn{flex-shrink:0;}',
      /* Match Centre tab — teal accent border so it stands out when scrolled */
      '.tab-btn.active[onclick*="match-centre"]{',
        'border-color:color-mix(in srgb, var(--teal-line) 88%, transparent)!important;',
        'color:var(--ipl-teal)!important;',
      '}',
      /* Narrow screen: slightly tighter tab buttons */
      '@media(max-width:480px){',
        '.tab-btn{font-size:11px;padding:5px 9px;}',
      '}',
    ].join('');
    document.head.appendChild(s);
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', _injectTabStyles);
  else
    _injectTabStyles();

  // ── Safe fallbacks for globals in index.html inline script ──────────────
  function _esc(s) {
    return typeof esc === 'function' ? esc(s)
      : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
                 .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function _escAttr(s) {
    return typeof escAttr === 'function' ? escAttr(s)
      : String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  }
  function _avCls(team) {
    return typeof _avClass === 'function' ? _avClass(team) : '';
  }
  function _ini(name) {
    if (typeof _initials === 'function') return _initials(name);
    var parts = (name || '?').split(' ');
    return parts.length === 1
      ? name.substring(0,2).toUpperCase()
      : (parts[0][0] + parts[parts.length-1][0]).toUpperCase();
  }

  // ── Role badge pill ──────────────────────────────────────────────────────────
  var _ROLE_COLOURS = {
    WK:   { bg: 'color-mix(in srgb, var(--teal-wash) 42%, transparent)',   txt: 'var(--teal-ink)' },
    BAT:  { bg: 'color-mix(in srgb, var(--gold-wash) 42%, transparent)',   txt: 'var(--gold-ink)' },
    AR:   { bg: 'color-mix(in srgb, var(--violet-wash) 42%, transparent)', txt: 'var(--violet-ink)' },
    BOWL: { bg: 'color-mix(in srgb, var(--danger-wash) 42%, transparent)', txt: 'var(--danger-ink)' },
  };

  function _rolePill(role) {
    if (!role) return '';
    var r = role.toUpperCase();
    var c = _ROLE_COLOURS[r];
    if (!c) return '<span style="font-size:9px;color:var(--muted)">' + _esc(r) + '</span>';
    return '<span style="display:inline-block;padding:1px 6px;border-radius:4px;'
         + 'font-size:9px;font-weight:700;letter-spacing:.03em;'
         + 'background:' + c.bg + ';color:' + c.txt + '">' + _esc(r) + '</span>';
  }

  // ── Season stat box ────────────────────────────────────────────────────────
  function _statBox(val, lbl, gold) {
    return '<div class="mc-stat-box">'
         + '<div class="mc-stat-val' + (gold ? ' gold' : '') + '">' + val + '</div>'
         + '<div class="mc-stat-lbl">' + lbl + '</div>'
         + '</div>';
  }

  window._mcToggleAll = function (btn) {
    var all = document.querySelectorAll('details[data-acc^="mcw-"]'), anyClosed = false;
    all.forEach(function (d) { if (!d.open) anyClosed = true; });
    Ui.accAll('details[data-acc^="mcw-"]', anyClosed);
    if (btn) btn.textContent = anyClosed ? 'Collapse all' : 'Expand all';
  };

  // ── Hub renderer ────────────────────────────────────────────────────────
  function _renderHub(d) {
    var s = d.season || {};
    var weeks = d.weeks || [];
    var h = '<div id="match-centre-tab">';

    // Before the first match there is nothing to total up: say so, instead of four zeros.
    var started = (s.matches_played || 0) > 0 || weeks.some(function (w) { return (w.matches_played || 0) > 0; });
    if (started) {
      h += '<div class="mc-stats-bar">';
      h += _statBox(s.total_pts     || 0, 'Total Pts',   false);
      h += _statBox(s.matches_played|| 0, 'Matches',     false);
      h += _statBox(s.avg_per_match || 0, 'Avg / Match', false);
      var bestLbl = s.best_match
        ? 'Best: ' + _esc(s.best_match.replace(/ vs .*/, '') || 'match')
        : 'Best match';
      h += _statBox(s.best_pts || 0, bestLbl, true);
      h += '</div>';
    } else if (weeks.length) {
      var nMatches = 0, firstDate = '';
      weeks.forEach(function (w) {
        nMatches += w.total_matches || (w.matches || []).length;
        if (!firstDate && w.matches && w.matches.length && w.matches[0].date_label) firstDate = w.matches[0].date_label;
      });
      h += '<div class="mc-preseason"><h3>The season hasn\u2019t started</h3><p>'
         + (firstDate ? 'First match <b>' + _esc(firstDate) + '</b> \u00B7 ' : '')
         + nMatches + ' matches over ' + weeks.length + (weeks.length === 1 ? ' week' : ' weeks')
         + '. Your points will appear here as matches are played.</p></div>';
    }

    if (!weeks.length) {
      h += (window.Ui ? Ui.stateHtml('empty', {title: 'No matches yet', message: 'Fixtures will appear here as soon as they are added.'}) : '<p class="empty">No matches yet.</p>');
    }
    // The week that is in play (some matches played, some not) opens by default; else the last week; before the season, the first.
    var openWk = null;
    weeks.forEach(function (w) { var pl = w.matches_played || 0, tt = w.total_matches || (w.matches || []).length; if (openWk === null && pl > 0 && pl < tt) openWk = w.week_no; });
    if (openWk === null) { var lastPlayed = null; weeks.forEach(function (w) { if ((w.matches_played || 0) > 0) lastPlayed = w.week_no; }); openWk = lastPlayed !== null ? lastPlayed : (weeks[0] ? weeks[0].week_no : 0); }
    if (weeks.length > 1) h += '<div class="mc-toolbar"><h3 class="mc-tb-title">Weeks</h3><button type="button" class="btn btn-ghost btn-sm" onclick="_mcToggleAll(this)">' + (weeks.every(function (w) { return Ui.accOpen('mcw-' + w.week_no, w.week_no === openWk); }) ? 'Collapse all' : 'Expand all') + '</button></div>';
    h += '<div class="mc-weeks">';
    weeks.forEach(function (wk) {
      var played = wk.matches_played || 0;
      var total  = wk.total_matches  || (wk.matches || []).length;

      var open = Ui.accOpen('mcw-' + wk.week_no, wk.week_no === openWk);
      h += '<details class="acc mc-week" data-acc="mcw-' + wk.week_no + '" data-def="' + (wk.week_no === openWk ? 1 : 0) + '" ontoggle="Ui.accToggle(this)"' + (open ? ' open' : '') + '>';
      h += '<summary class="mc-week-hdr">';
      h += '<span class="mc-week-lbl">Week ' + wk.week_no + '</span>';
      h += '<span class="mc-week-meta"><span class="mc-week-pts">' + (played ? (wk.week_pts || 0) + ' pts' : '\u2014') + '</span>'
         + '<span class="mc-week-prog">' + played + '/' + total + ' played</span>' + Ui.icon('down', 18) + '</span>';
      h += '</summary><div class="mc-week-body">';

      (wk.matches || []).forEach(function (m) {
        var upcoming = (m.status || '').toLowerCase() !== 'completed';
        var mid      = m.match_id;
        var pts      = m.user_match_pts;

        h += '<div class="match-card' + (upcoming ? ' mc-upcoming' : '') + '"'
           + (upcoming ? ''
               : ' onclick="_openMatchModal(\'' + _escAttr(mid) + '\')" tabindex="0"')
           + '>';
        h += '<div class="mc-mno">' + _esc(m.match_no || '') + '</div>';
        h += '<div class="mc-mbody">';
        h += '<div class="mc-mtitle">' + _esc(m.title || mid) + '</div>';
        // Team matchup pills: SRH vs RCB
        var teams = m.teams || [];
        if (teams.length >= 2) {
          h += '<div class="mc-teams-row">'
             + '<span class="mc-team-tag ' + _avCls(teams[0]) + '">' + _esc(teams[0]) + '</span>'
             + '<span class="mc-vs">vs</span>'
             + '<span class="mc-team-tag ' + _avCls(teams[1]) + '">' + _esc(teams[1]) + '</span>'
             + '</div>';
        }
        var meta2 = [];
        if (m.date_label) meta2.push(_esc(m.date_label));
        if (m.venue)      meta2.push(_esc(m.venue));
        if (meta2.length)
          h += '<div class="mc-mmeta">' + meta2.join(' \u00B7 ') + '</div>';
        if (m.result)
          h += '<div class="mc-mmeta mc-result">'
             + _esc(m.result) + '</div>';
        h += '</div>';
        h += '<div class="mc-mright">';
        h += '<span class="mc-mpts' + (pts === 0 && !upcoming ? ' zero' : '') + '">' + (upcoming ? '\u2014' : pts) + '</span>';
        h += '<span class="mc-spill' + (upcoming ? ' upcoming' : '') + '">' + (upcoming ? 'Upcoming' : 'Completed') + '</span>';
        h += '</div>';
        h += '</div>';
      });
      h += '</div></details>';
    });
    h += '</div>';

    h += '</div>';
    return h;
  }

  // ── Box Score modal builder ──────────────────────────────────────────────
  function _buildBoxScore(d) {
    var players = d.players || [];

    // Client-side top scorer (max final_pts)
    var topIdx = -1, topPts = -1;
    players.forEach(function (p, i) {
      if ((p.final_pts || 0) > topPts) { topPts = p.final_pts || 0; topIdx = i; }
    });

    // Client-side match total (independent integrity check)
    var computedTotal = 0;
    players.forEach(function (p) { computedTotal += (p.final_pts || 0); });
    var serverTotal  = d.user_pts || 0;
    var totalsMatch  = (computedTotal === serverTotal);

    // Detect "no player breakdown yet" — scraper hasn't run for this match
    var noBreakdown = (computedTotal === 0 && serverTotal > 0 && players.length > 0);

    var h = '';
    h += '<button class="mm-close" onclick="_closeMatchModal()">' + '\u00D7' + '</button>';

    var badge = [d.match_no || '', d.week_no ? 'Week ' + d.week_no : ''].filter(Boolean).join(' \u00B7 ');
    if (badge) h += '<div class="mm-badge">' + _esc(badge) + '</div>';
    h += '<div class="mm-title">' + _esc(d.title || d.match_id || '') + '</div>';
    var sub = [d.venue, d.date_label, d.result].filter(Boolean).map(_esc).join(' \u00B7 ');
    if (sub) h += '<div class="mm-sub">' + sub + '</div>';

    // Score boxes
    h += '<div class="mm-scores">';
    h += '<div class="mm-sbox"><div class="mm-slbl">Your Pts</div>'
       + '<div class="mm-sval">' + serverTotal + '</div></div>';
    var tsName = (topIdx >= 0 && topPts > 0) ? players[topIdx].name : null;
    var tsPts  = (topIdx >= 0 && topPts > 0) ? topPts : null;
    if (!tsName && d.top_scorer) { tsName = d.top_scorer.name; tsPts = d.top_scorer.pts; }
    h += '<div class="mm-sbox top"><div class="mm-slbl">Top Scorer</div>'
       + '<div class="mm-sval">' + (tsPts != null ? tsPts : '\u2014') + '</div>'
       + (tsName ? '<div class="mm-sname">' + _esc(tsName) + '</div>' : '')
       + '</div>';
    h += '</div>';

    // Player list
    if (!players.length) {
      h += '<div class="mm-loading">No player data yet for this match.</div>';
    } else {
      h += '<div class="mm-xi-lbl">Your XI \u00B7 Points This Match</div>';
      if (noBreakdown) {
        h += '<div style="font-size:11px;color:var(--muted);margin-bottom:8px;font-style:italic">'
           + 'Per-player breakdown available after the scraper runs for this match.</div>';
      }
      players.forEach(function (p, idx) {
        var isCap  = !!p.is_cap;
        var isVc   = !!p.is_vc;
        var isTop  = (idx === topIdx && topPts > 0);
        var base   = p.base_pts  || 0;
        var final_ = p.final_pts || 0;
        var zero   = final_ === 0;

        // Multiplier annotation
        var multAnnot = '';
        if      (isCap && base > 0)       multAnnot = base + ' \u00D7 2';
        else if (isVc  && base > 0)       multAnnot = base + ' \u00D7 1.5';
        else if (p.multiplier_str)         multAnnot = p.multiplier_str;

        // Colour rules
        var ptsColour   = noBreakdown ? 'var(--muted)'
                        : zero        ? 'var(--dim)'
                        : isCap       ? 'var(--gold-ink)'
                        : 'var(--ipl-teal)';
        var annotColour = isCap ? 'var(--gold-ink)' : 'var(--ipl-teal)';
        var rowBorder   = isTop ? 'border-left:2px solid var(--gold-line);padding-left:6px;margin-left:-6px;' : '';

        var cBdg  = isCap ? '<span class="badge badge-c" style="font-size:9px;padding:1px 5px">C</span>'  : '';
        var vcBdg = isVc  ? '<span class="badge badge-vc" style="font-size:9px;padding:1px 5px">VC</span>' : '';

        h += '<div class="mm-prow" style="' + rowBorder + '">';
        h += '<div class="mm-pnum">' + (idx + 1) + '</div>';
        h += '<div class="mm-pav ' + _avCls(p.team) + '">' + _esc(_ini(p.name || p.player_id || '')) + '</div>';
        h += '<div class="mm-pinfo">';
        h += '<div class="mm-pname">' + _esc(p.name || p.player_id || '') + cBdg + vcBdg + '</div>';
        // Role pill + team sub-line
        var rolePillHtml = _rolePill(p.role);
        var teamStr      = p.team ? _esc(p.team) : '';
        if (rolePillHtml || teamStr) {
          h += '<div class="mm-psub" style="display:flex;align-items:center;gap:5px;margin-top:2px">'
             + (rolePillHtml || '')
             + (teamStr ? '<span style="color:var(--muted)">' + teamStr + '</span>' : '')
             + '</div>';
        }
        h += '</div>';
        // Points + multiplier annotation
        h += '<div class="mm-ppts" style="color:' + ptsColour + '">';
        if (noBreakdown) {
          h += isCap ? '<span style="font-size:10px">×2</span>—'
             : isVc  ? '<span style="font-size:10px">×1.5</span>—'
             : '—';
        } else {
          h += zero ? '0' : final_;
          if (multAnnot) {
            h += '<span class="mm-pmult" style="color:' + annotColour + '">' + _esc(multAnnot) + '</span>';
          }
        }
        h += '</div>';
        h += '</div>';
      });
    }

    // Footer — use server total when per-player breakdown unavailable
    var displayTotal = noBreakdown ? serverTotal : computedTotal;
    var mismatchHtml = '';
    if (!noBreakdown && !totalsMatch && players.length > 0) {
      mismatchHtml = ' <span title="Server total: ' + serverTotal + '" '
        + 'style="font-size:10px;color:var(--orange-ink);font-weight:600">⚠</span>';
    }
    h += '<div class="mm-total">'
       + '<span class="mm-tlbl">Match Total</span>'
       + '<span class="mm-tval">' + displayTotal + mismatchHtml + '</span>'
       + '</div>';

    return h;
  }

  // ── Public: open modal ──────────────────────────────────────────────────
  window._openMatchModal = function (matchId) {
    if (!matchId) return;
    var user = window._username;
    if (!user) return;
    _closeMatchModal();

    var backdrop = document.createElement('div');
    backdrop.id        = 'mc-modal-backdrop';
    backdrop.className = 'match-modal-backdrop';
    backdrop.onclick   = function (e) { if (e.target === backdrop) _closeMatchModal(); };
    backdrop.innerHTML =
      '<div class="match-modal">'
      + '<button class="mm-close" onclick="_closeMatchModal()">\u00D7</button>'
      + '<div class="mm-loading">\u23F3 Loading match details\u2026</div>'
      + '</div>';
    document.body.appendChild(backdrop);

    IplApi.getMatchDetails(matchId, user)
      .then(function (d) {
        var modal = backdrop.querySelector('.match-modal');
        if (!modal) return;
        if (!d || !d.ok) {
          modal.innerHTML =
            '<button class="mm-close" onclick="_closeMatchModal()">\u00D7</button>'
            + '<div class="mm-loading">\u26A0 Could not load match details.</div>';
          return;
        }
        modal.innerHTML = _buildBoxScore(d);
      })
      .catch(function () {
        var modal = backdrop.querySelector('.match-modal');
        if (modal) modal.innerHTML =
          '<button class="mm-close" onclick="_closeMatchModal()">\u00D7</button>'
          + '<div class="mm-loading">\u26A0 Network error \u2014 try again.</div>';
      });
  };

  // ── Public: close modal ──────────────────────────────────────────────────
  window._closeMatchModal = function () {
    var el = document.getElementById('mc-modal-backdrop');
    if (el) el.remove();
  };

  // ── Public: hub tab builder (final — overrides all previous stubs) ────────
  window._buildMatchCentreTab = function () {
    if (!window._username) {
      return '<div id="match-centre-tab" class="card">'
           + '<p class="empty">Log in to view your Match Centre.</p></div>';
    }
    var comp = _curComp(), user = window._username;
    if ((_mcData || _mcFailed) && (_mcComp !== comp || _mcUser !== user)) _mcReset();   // another competition or member: never show it (or its error)
    if (_mcData && !_mcStale) return _renderHub(_mcData);

    if (!_mcLoading && !_mcFailed) _mcFetch();   // (a failed request is not asked again by every render: that would hammer a server that is in trouble)

    if (_mcData) return _renderHub(_mcData);   // a stale copy for this competition and member stays on screen while the fresh one loads (or after a refresh failed)
    if (_mcFailed) {
      return '<div id="match-centre-tab" class="card" style="min-height:160px">'
           + (window.Ui ? Ui.stateHtml('error', { title: 'Could not load the Match Centre', message: 'Check your connection and try again.', retry: '_mcRetry()', retryLabel: 'Try again' })
                        : '<p class="empty">Could not load the Match Centre. <button type="button" onclick="_mcRetry()">Try again</button></p>')
           + '</div>';
    }
    if (_mcSlow) {   // still waiting after a long time (a sleeping server): the spinner stays, because the request is still running, and says why it is taking long
      return '<div id="match-centre-tab" class="card" style="min-height:160px">'
           + (window.Ui ? Ui.stateHtml('loading', { title: 'Still loading the Match Centre\u2026', message: 'The server may be waking up. This can take up to a minute.', retry: '_mcRetry()', retryLabel: 'Try again' })
                        : '<p class="empty">Still loading the Match Centre\u2026 <button type="button" onclick="_mcRetry()">Try again</button></p>')
           + '</div>';
    }
    return '<div id="match-centre-tab" class="card" style="min-height:160px">'
         + '<div class="mm-loading"><div class="ui-spinner" aria-hidden="true"></div>Loading Match Centre\u2026</div>'
         + '</div>';
  };

}());
