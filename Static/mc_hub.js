/**
 * mc_hub.js — Match Centre Hub Renderer + Box Score Modal  Golden File v1.0
 * Phase 9.3: Replaces the Phase 9.2 stub in ipl_glue.js.
 * Loaded AFTER ipl_glue.js so the unconditional assignment overrides the stub.
 *
 * Data field → UI mapping (hub):
 *   season.total_pts       → TOTAL PTS stat box
 *   season.matches_played  → MATCHES PLAYED stat box
 *   season.avg_per_match   → AVG / MATCH stat box
 *   season.best_pts        → BEST stat box value (gold)
 *   season.best_match      → BEST stat box label (truncated)
 *   week.week_no           → WEEK N group header
 *   week.week_pts          → gold total right of header
 *   week.matches_played / week.total_matches → N/N counter
 *   match.match_no         → left ordinal (M1, M2…)
 *   match.title            → card title
 *   match.venue            → first meta line
 *   match.date_label       → appended to venue
 *   match.result           → second meta line (dimmed)
 *   match.status           → .mc-upcoming class + pill label
 *   match.user_match_pts   → YOUR PTS teal number
 *   match.match_id         → onclick -> _openMatchModal()
 *
 * Data field → UI mapping (box score modal):
 *   d.match_no / d.week_no → teal badge top-left
 *   d.title                → modal headline
 *   d.venue / d.date_label / d.result → subtitle
 *   d.user_pts             → YOUR PTS box (gold)
 *   d.top_scorer.name/.pts → TOP SCORER box (teal)
 *   p.final_pts            → per-player score (teal / dim)
 *   p.name / is_cap / is_vc → name + C/VC badge
 *   p.role / p.team        → grey sub-line
 *   p.multiplier_str       → small annotation e.g. 88x2
 */

(function () {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────
  var _mcData    = null;
  var _mcLoading = false;

  // Expose to ipl:state-updated listener (already set in ipl_glue.js)
  // We piggyback on it by overwriting _mcData via the global reference.
  window.__mc_invalidate = function () { _mcData = null; };

  // Patch the existing listener to also call us
  window.addEventListener('ipl:state-updated', function () {
    _mcData    = null;
    _mcLoading = false;
  });

  // ── Helpers (safe fallbacks if ipl_glue globals aren't ready) ───────────
  function _esc(s) {
    return typeof esc === 'function' ? esc(s)
      : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
  function _escAttr(s) {
    return typeof escAttr === 'function' ? escAttr(s)
      : String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  }
  function _avCls(team) {
    return typeof _avClass === 'function' ? _avClass(team) : '';
  }
  function _ini(name) {
    return typeof _initials === 'function' ? _initials(name)
      : (name||'?').substring(0,2).toUpperCase();
  }

  // ── Season stat box ─────────────────────────────────────────────────────
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

    // Season stats bar
    h += '<div class="mc-stats-bar">';
    h += _statBox(s.total_pts || 0, 'Total Pts', false);
    h += _statBox(s.matches_played || 0, 'Matches', false);
    h += _statBox(s.avg_per_match || 0, 'Avg / Match', false);
    var bestLbl = s.best_match
      ? _esc((s.best_match).replace(/ vs .*/,'') || 'Best')
      : 'Best';
    h += _statBox(s.best_pts || 0, bestLbl, true);
    h += '</div>';

    // Week groups
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
           + (upcoming ? '' : ' onclick="_openMatchModal(\'' + _escAttr(mid) + '\')",tabindex="0"')
           + '>';
        h += '<div class="mc-mno">' + _esc(m.match_no || '') + '</div>';
        h += '<div class="mc-mbody">';
        h += '<div class="mc-mtitle">' + _esc(m.title || mid) + '</div>';
        if (meta1.length) h += '<div class="mc-mmeta">' + meta1.join(' \u00B7 ') + '</div>';
        if (m.result)     h += '<div class="mc-mmeta" style="color:var(--dim,#3D5572)">'
                              + _esc(m.result) + '</div>';
        h += '</div>';
        h += '<div class="mc-mright">';
        h += '<span class="mc-mpts' + (pts === 0 && !upcoming ? ' zero' : '') + '">' + (upcoming ? '\u2014' : pts) + '</span>';
        h += '<span class="mc-spill' + (upcoming ? ' upcoming' : '') + '">' + (upcoming ? 'Upcoming' : 'Completed') + '</span>';
        h += '</div>';
        h += '</div>'; // .match-card
      });
    });

    h += '</div>'; // #match-centre-tab
    return h;
  }

  // ── Box Score modal builder ──────────────────────────────────────────────
  function _buildBoxScore(d) {
    var h = '';
    h += '<button class="mm-close" onclick="_closeMatchModal()">' + '\u00D7' + '</button>';

    var badge = [(d.match_no || ''), d.week_no ? 'Week ' + d.week_no : ''].filter(Boolean).join(' \u00B7 ');
    if (badge) h += '<div class="mm-badge">' + _esc(badge) + '</div>';
    h += '<div class="mm-title">' + _esc(d.title || d.match_id) + '</div>';

    var sub = [d.venue, d.date_label, d.result].filter(Boolean).map(_esc).join(' \u00B7 ');
    h += '<div class="mm-sub">' + sub + '</div>';

    // Score boxes
    h += '<div class="mm-scores">';
    h += '<div class="mm-sbox"><div class="mm-slbl">Your Pts</div>'
       + '<div class="mm-sval">' + (d.user_pts || 0) + '</div></div>';
    var ts = d.top_scorer;
    h += '<div class="mm-sbox top"><div class="mm-slbl">Top Scorer</div>'
       + '<div class="mm-sval">' + (ts ? ts.pts : '\u2014') + '</div>'
       + (ts ? '<div class="mm-sname">' + _esc(ts.name) + '</div>' : '')
       + '</div>';
    h += '</div>'; // .mm-scores

    // Player list
    var players = d.players || [];
    if (!players.length) {
      h += '<div class="mm-loading">No player data yet for this match.</div>';
    } else {
      h += '<div class="mm-xi-lbl">Your XI \u00B7 Points This Match</div>';
      players.forEach(function (p, idx) {
        var zero = !p.final_pts;
        var cBdg  = p.is_cap ? '<span class="badge badge-c" style="font-size:9px;padding:1px 5px">C</span>' : '';
        var vcBdg = p.is_vc  ? '<span class="badge badge-vc" style="font-size:9px;padding:1px 5px">VC</span>' : '';
        var sub2  = [p.role, p.team].filter(Boolean).join(' \u00B7 ');
        h += '<div class="mm-prow">';
        h += '<div class="mm-pnum">' + (idx + 1) + '</div>';
        h += '<div class="mm-pav ' + _avCls(p.team) + '">' + _esc(_ini(p.name || p.player_id)) + '</div>';
        h += '<div class="mm-pinfo">';
        h += '<div class="mm-pname">' + _esc(p.name || p.player_id) + cBdg + vcBdg + '</div>';
        if (sub2) h += '<div class="mm-psub">' + _esc(sub2) + '</div>';
        h += '</div>';
        h += '<div class="mm-ppts' + (zero ? ' zero' : '') + '">' + (zero ? 0 : p.final_pts);
        if (p.multiplier_str) h += '<span class="mm-pmult">' + _esc(p.multiplier_str) + '</span>';
        h += '</div>';
        h += '</div>'; // .mm-prow
      });
    }

    h += '<div class="mm-total">';
    h += '<span class="mm-tlbl">Match Total</span>';
    h += '<span class="mm-tval">' + (d.user_pts || 0) + '</span>';
    h += '</div>';
    return h;
  }

  // ── Public: open modal ───────────────────────────────────────────────────
  window._openMatchModal = function (matchId) {
    if (!matchId) return;
    var user = window._username;
    if (!user) return;
    _closeMatchModal();

    var backdrop = document.createElement('div');
    backdrop.id = 'mc-modal-backdrop';
    backdrop.className = 'match-modal-backdrop';
    backdrop.onclick = function (e) { if (e.target === backdrop) _closeMatchModal(); };
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
          modal.innerHTML = '<button class="mm-close" onclick="_closeMatchModal()">\u00D7</button>'
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

  // ── Public: hub tab builder (overrides Phase 9.2 stub) ───────────────────
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
