/**
 * mc_hub.js — Match Centre Hub + Box Score Modal           Golden File v1.2
 * ==========================================================================
 * v1.2 (Phase 9.5 — Final Lock):
 *   • _injectTabStyles() — makes the .tabs nav fully responsive on mobile.
 *     The 9-tab row now scrolls horizontally with no wrapping, hidden
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
  var _mcData    = null;
  var _mcLoading = false;

  window.addEventListener('ipl:state-updated', function () {
    _mcData    = null;
    _mcLoading = false;
  });

  // ── Responsive tab nav (Phase 9.5) ───────────────────────────────────────
  /**
   * _injectTabStyles()
   * Makes the .tabs row scroll horizontally on narrow screens so all 9 tabs
   * remain accessible without wrapping or clipping.
   *
   * Responsive rules:
   *   .tabs         — overflow-x:auto, no wrapping, hidden scrollbar
   *   .tab-btn      — flex-shrink:0 so buttons never compress below min-width
   *   Match Centre  — teal border accent to aid discovery when scrolled
   *   @media ≤480px — smaller font/padding for tighter screens
   */
  function _injectTabStyles() {
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
        'border-color:rgba(0,210,255,.55)!important;',
        'color:var(--ipl-teal,#00d2ff)!important;',
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
    WK:   { bg: 'rgba(251,191,36,.18)',  txt: '#FBBF24' },
    BAT:  { bg: 'rgba(52,211,153,.18)',  txt: '#34D399' },
    AR:   { bg: 'rgba(139,92,246,.18)',  txt: '#A78BFA' },
    BOWL: { bg: 'rgba(96,165,250,.18)',  txt: '#60A5FA' },
  };

  function _rolePill(role) {
    if (!role) return '';
    var r = role.toUpperCase();
    var c = _ROLE_COLOURS[r];
    if (!c) return '<span style="font-size:9px;color:var(--muted,#5F7A9B)">' + _esc(r) + '</span>';
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

  // ── Hub renderer ────────────────────────────────────────────────────────
  function _renderHub(d) {
    var s = d.season || {};
    var h = '<div id="match-centre-tab">';

    h += '<div class="mc-stats-bar">';
    h += _statBox(s.total_pts     || 0, 'Total Pts',   false);
    h += _statBox(s.matches_played|| 0, 'Matches',     false);
    h += _statBox(s.avg_per_match || 0, 'Avg / Match', false);
    var bestLbl = s.best_match
      ? _esc(s.best_match.replace(/ vs .*/, '') || 'Best')
      : 'Best';
    h += _statBox(s.best_pts || 0, bestLbl, true);
    h += '</div>';

    var weeks = d.weeks || [];
    if (!weeks.length) {
      h += '<p class="empty" style="text-align:center;padding:24px 0">No matches yet.</p>';
    }
    weeks.forEach(function (wk) {
      var played = wk.matches_played || 0;
      var total  = wk.total_matches  || (wk.matches || []).length;

      h += '<div class="mc-week-hdr">';
      h += '<span class="mc-week-lbl">WEEK ' + wk.week_no + '</span>';
      h += '<span class="mc-week-pts">' + (wk.week_pts || 0) + ' pts'
         + '<span style="font-size:10px;color:var(--muted,#5F7A9B);font-weight:400"> '
         + played + '/' + total + '</span></span>';
      h += '</div>';

      (wk.matches || []).forEach(function (m) {
        var upcoming = (m.status || '').toLowerCase() !== 'completed';
        var mid      = m.match_id;
        var pts      = m.user_match_pts;

        var meta1 = [];
        if (m.venue)      meta1.push(_esc(m.venue));
        if (m.date_label) meta1.push(_esc(m.date_label));

        h += '<div class="match-card' + (upcoming ? ' mc-upcoming' : '') + '"'
           + (upcoming ? ''
               : ' onclick="_openMatchModal(\'' + _escAttr(mid) + '\')" tabindex="0"')
           + '>';
        h += '<div class="mc-mno">' + _esc(m.match_no || '') + '</div>';
        h += '<div class="mc-mbody">';
        h += '<div class="mc-mtitle">' + _esc(m.title || mid) + '</div>';
        if (meta1.length)
          h += '<div class="mc-mmeta">' + meta1.join(' \u00B7 ') + '</div>';
        if (m.result)
          h += '<div class="mc-mmeta" style="color:var(--dim,#3D5572)">'
             + _esc(m.result) + '</div>';
        h += '</div>';
        h += '<div class="mc-mright">';
        h += '<span class="mc-mpts' + (pts === 0 && !upcoming ? ' zero' : '') + '">' + (upcoming ? '\u2014' : pts) + '</span>';
        h += '<span class="mc-spill' + (upcoming ? ' upcoming' : '') + '">' + (upcoming ? 'Upcoming' : 'Completed') + '</span>';
        h += '</div>';
        h += '</div>';
      });
    });

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
        var ptsColour   = zero ? 'var(--dim,#3D5572)'
                        : isCap ? '#F5C518'
                        : 'var(--ipl-teal,#00d2ff)';
        var annotColour = isCap ? '#F5C518' : 'var(--ipl-teal,#00d2ff)';
        var rowBorder   = isTop ? 'border-left:2px solid #F5C518;padding-left:6px;margin-left:-6px;' : '';

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
             + (teamStr ? '<span style="color:var(--muted,#5F7A9B)">' + teamStr + '</span>' : '')
             + '</div>';
        }
        h += '</div>';
        // Points + multiplier annotation
        h += '<div class="mm-ppts" style="color:' + ptsColour + '">';
        h += zero ? '0' : final_;
        if (multAnnot) {
          h += '<span class="mm-pmult" style="color:' + annotColour + '">' + _esc(multAnnot) + '</span>';
        }
        h += '</div>';
        h += '</div>';
      });
    }

    // Footer — client-side computed total
    var mismatchHtml = '';
    if (!totalsMatch && players.length > 0) {
      mismatchHtml = ' <span title="Server total: ' + serverTotal + '" '
        + 'style="font-size:10px;color:#FB923C;font-weight:600">\u26A0</span>';
    }
    h += '<div class="mm-total">'
       + '<span class="mm-tlbl">Match Total</span>'
       + '<span class="mm-tval">' + computedTotal + mismatchHtml + '</span>'
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
    if (_mcData) return _renderHub(_mcData);

    if (!_mcLoading) {
      _mcLoading = true;
      IplApi.getMatchCentre(window._username)
        .then(function (d) {
          _mcLoading = false;
          if (d && d.ok) {
            _mcData = d;
            if (window._state && window._activeTab === 'match-centre') {
              render(window._state);
            }
          }
        })
        .catch(function (err) {
          _mcLoading = false;
          console.warn('[MC] fetch failed:', err && err.message ? err.message : err);
        });
    }

    return '<div id="match-centre-tab" class="card" style="min-height:160px">'
         + '<div class="mm-loading">\u23F3 Loading Match Centre\u2026</div>'
         + '</div>';
  };

}());
