/**
 * ipl_glue.js — Frontend Integration Layer                 Golden File v7.7
 * =========================================================================
 * v7.7 (badge fix):
 *   Root cause of missing Next Week badges identified and fixed.
 *   _buildXiGrid() (inline script) generates card HTML WITHOUT data-pid
 *   attributes. _injectStatsToPicker() used querySelectorAll('[data-pid]')
 *   which found nothing → badges never rendered.
 *
 *   Fix: _patchXiGrid() wraps the original _buildXiGrid at the source.
 *   The wrapper parses the returned HTML, stamps data-pid on each card in
 *   team order (cards and team[] are always co-indexed), and injects the
 *   season_pts badge inline — template injection, not DOM observation.
 *   This fires at render time so there are no timing or observer races.
 *
 *   _injectStatsToPicker() is retained as a secondary pass for the swap/
 *   search picker list (which uses different DOM, does have data-pid).
 *   Mobile blur: unchanged — capture-phase click + ipl:saved hook.
 *
 * v7.6: season_pts badges (MutationObserver approach — broken, see above).
 * v7.5: _checkVersionHandshake(), IplApi.getVersion().
 * v7.4: _playerMap cache, match-by-match totals, leaderboard per-week cols.
 * v7.3: Matches tab + My Pts column.
 * v7.2: _buildPointsTab() matchCount fix.
 * v7.1: History tab chip, Leaderboard per-week columns.
 */

(function (window) {
  "use strict";

  var POLL_INTERVAL_MS  = 60000;
  var MAINTENANCE_DELAY = 1500;
  var ROLLOVER_HOUR_UTC = 14;
  var ROLLOVER_MIN_UTC  = 0;

  var _lastStateEtag  = null;
  var _overlayTimer   = null;
  var _overlayVisible = false;
  var _pollTimer      = null;
  var _rolloverTimer  = null;

  var IplConfig = {
    budget:       100.0,
    xi_size:      11,
    max_weeks:    8,
    current_week: 1,
  };

  // ── MAINTENANCE OVERLAY ──────────────────────────────────────────────────

  function _injectOverlayStyles() {
    if (document.getElementById("ipl-overlay-style")) return;
    var s = document.createElement("style");
    s.id = "ipl-overlay-style";
    s.textContent = [
      "#ipl-maintenance-overlay{",
        "position:fixed;inset:0;z-index:9999;",
        "background:rgba(7,17,31,.92);",
        "display:flex;align-items:center;justify-content:center;",
        "animation:ipl-fade-in .3s ease;",
      "}",
      "@keyframes ipl-fade-in{from{opacity:0}to{opacity:1}}",
      ".ipl-mc{",
        "background:#0E1E35;",
        "border:1px solid rgba(245,197,24,.25);",
        "border-radius:16px;padding:40px 32px;max-width:380px;",
        "text-align:center;color:#D8E8F5;font-family:sans-serif;",
      "}",
      ".ipl-mc-icon{font-size:52px;margin-bottom:12px;}",
      ".ipl-mc h2{color:#F5C518;font-size:22px;margin:0 0 12px;}",
      ".ipl-mc p{color:#5F7A9B;font-size:14px;line-height:1.7;margin:0 0 16px;}",
      ".ipl-mc .sub{font-size:12px!important;color:#3D5572!important;}",
      ".ipl-spin{",
        "width:32px;height:32px;margin:0 auto 20px;",
        "border:3px solid rgba(245,197,24,.15);",
        "border-top-color:#F5C518;border-radius:50%;",
        "animation:ipl-spin 1s linear infinite;",
      "}",
      "@keyframes ipl-spin{to{transform:rotate(360deg)}}",
    ].join("");
    document.head.appendChild(s);
  }

  function _showOverlay() {
    if (_overlayVisible || document.getElementById("ipl-maintenance-overlay")) return;
    _injectOverlayStyles();
    var el = document.createElement("div");
    el.id = "ipl-maintenance-overlay";
    el.innerHTML = (
      '<div class="ipl-mc">'
      + '<div class="ipl-mc-icon">&#x1F3CF;</div>'
      + '<h2>Server Unavailable</h2>'
      + '<p>We\'re having trouble reaching the backend.<br>Retrying every 60 seconds&hellip;</p>'
      + '<div class="ipl-spin"></div>'
      + '<p class="sub">Ask the admin to check the server if this persists.</p>'
      + '</div>'
    );
    document.body.appendChild(el);
    _overlayVisible = true;
  }

  function _hideOverlay() {
    var el = document.getElementById("ipl-maintenance-overlay");
    if (el) el.remove();
    _overlayVisible = false;
    if (_overlayTimer) { clearTimeout(_overlayTimer); _overlayTimer = null; }
  }

  function _scheduleOverlay() {
    if (_overlayVisible || _overlayTimer) return;
    _overlayTimer = setTimeout(_showOverlay, MAINTENANCE_DELAY);
  }

  function _cancelOverlay() {
    if (_overlayTimer) { clearTimeout(_overlayTimer); _overlayTimer = null; }
    if (_overlayVisible) _hideOverlay();
  }

  // ── LEADERBOARD NORMALISATION ─────────────────────────────────────────────

  function normaliseLeaderboard(raw) {
    if (!raw || typeof raw !== "object") {
      return { rankings: [], standings: [],
        meta: { league_avg: 0, top_score: 0, member_count: 0 },
        league_avg: 0, top_score: 0, member_count: 0, week_no: null, generated_at: null };
    }
    var rows = Array.isArray(raw.rankings) ? raw.rankings
             : Array.isArray(raw.standings) ? raw.standings : [];
    var m = raw.meta || {};
    var league_avg   = (raw.league_avg   != null) ? raw.league_avg   : (m.league_avg   || 0);
    var top_score    = (raw.top_score    != null) ? raw.top_score    : (m.top_score    || 0);
    var member_count = (raw.member_count != null) ? raw.member_count : (m.member_count || 0);
    return {
      week_no: raw.week_no != null ? raw.week_no : null,
      generated_at: raw.generated_at != null ? raw.generated_at : null,
      league_avg, top_score, member_count,
      meta: { league_avg, top_score, member_count },
      rankings: rows, standings: rows,
    };
  }

  // ── HTTP HELPERS ─────────────────────────────────────────────────────────

  function _fetchJson(url, options) {
    options = options || {};
    var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    return fetch(url, Object.assign({}, options, { headers }))
      .then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) {
          var err = new Error("HTTP " + res.status); err.status = res.status;
          return res.json().catch(function () { return {}; }).then(function (j) {
            err.serverMessage = j.error || j.detail || ""; err.serverData = j; throw err;
          });
        }
        return res.json();
      });
  }

  // ── VERSION HANDSHAKE ────────────────────────────────────────────────────

  function _checkVersionHandshake() {
    _fetchJson("/api/version")
      .then(function (v) {
        if (!v || !v.ok) return;
        console.group(
          "%c\uD83C\uDFCF IPL Fantasy " + v.app_version + " \u2014 Decoupled v2.0 Backend \u2713",
          "color:#F5C518;font-weight:800;font-size:13px"
        );
        console.log("%cAPP_VERSION%c  " + v.app_version, "color:#5F7A9B;font-weight:600", "color:#00D4AA;font-weight:800");
        console.log("%cModule pins:", "color:#5F7A9B;font-weight:600", v.modules);
        console.log("%cMigration map:", "color:#5F7A9B;font-weight:600");
        Object.keys(v.version_map).sort().forEach(function (ver) {
          console.log("  " + ver + " \u2192", v.version_map[ver]);
        });
        console.groupEnd();
        window.dispatchEvent(new CustomEvent("ipl:version-ok", { detail: v }));
      })
      .catch(function (err) {
        console.warn("[IplGlue] /api/version handshake failed \u2014", err.message || err);
      });
  }

  // ── ROLLOVER SCHEDULER ───────────────────────────────────────────────────

  function _msUntilNextRollover() {
    var now = new Date(); var utcDay = now.getUTCDay();
    var daysUntilMon = (utcDay === 1) ? 0 : (8 - utcDay) % 7;
    var candidate = new Date(now);
    candidate.setUTCDate(now.getUTCDate() + daysUntilMon);
    candidate.setUTCHours(ROLLOVER_HOUR_UTC, ROLLOVER_MIN_UTC, 0, 0);
    if (candidate.getTime() - now.getTime() <= 5000) candidate.setUTCDate(candidate.getUTCDate() + 7);
    return candidate.getTime() - now.getTime();
  }

  function _executeRollover() {
    console.info("[IplRollover] Triggering Monday 14:00 UTC rollover \u2026");
    IplApi.rollover(false)
      .then(function (data) {
        if (data && data.rolled) { console.info("[IplRollover] Rollover complete \u2014 week: " + data.new_week_no); _lastStateEtag = null; _pollCycle(); }
        else { console.info("[IplRollover] Rollover no-op:", data && data.reason); }
      })
      .catch(function (err) { console.warn("[IplRollover] Failed:", err.message || err); })
      .finally(function () { _scheduleNextRollover(); });
  }

  function _scheduleNextRollover() {
    if (_rolloverTimer) { clearTimeout(_rolloverTimer); _rolloverTimer = null; }
    var ms = Math.min(_msUntilNextRollover(), 7 * 24 * 60 * 60 * 1000);
    console.info("[IplRollover] Next in " + Math.round(ms / 60000) + " min");
    _rolloverTimer = setTimeout(_executeRollover, ms);
  }

  function _cancelRolloverTimer() {
    if (_rolloverTimer) { clearTimeout(_rolloverTimer); _rolloverTimer = null; }
  }

  var IplRollover = { scheduleNext: _scheduleNextRollover, cancelPending: _cancelRolloverTimer };

  // ── PUBLIC API ───────────────────────────────────────────────────────────

  var IplApi = {

    getState: function () {
      var headers = { "Accept": "application/json" };
      if (_lastStateEtag) headers["If-None-Match"] = _lastStateEtag;
      return fetch("/api/state", { headers }).then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) { var err = new Error("HTTP " + res.status); err.status = res.status; throw err; }
        var etag = res.headers.get("ETag"); if (etag) _lastStateEtag = etag;
        return res.json();
      });
    },

    getLeaderboard: function (weekNo) {
      var url = (weekNo != null) ? ("/api/leaderboard?week=" + weekNo) : "/api/leaderboard";
      return _fetchJson(url).then(normaliseLeaderboard);
    },

    getPlayers:      function () { return _fetchJson("/api/players"); },
    getCurrentWeek:  function () { return _fetchJson("/api/current-week"); },
    getHistory:      function (name) { return _fetchJson("/api/history/" + encodeURIComponent(name)); },
    getPlayerPoints: function (name) { return _fetchJson("/api/player-points/" + encodeURIComponent(name)); },

    getUserMatchPoints: function (name) {
      return _fetchJson("/api/user-match-points/" + encodeURIComponent(name));
    },

    getVersion: function () { return _fetchJson("/api/version"); },

    saveNextWeek: function (name, picks) {
      return _fetchJson("/api/save-next-week/" + encodeURIComponent(name), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(picks),
      });
    },

    resolvePlayer: function (query, team) {
      return _fetchJson("/api/resolve-player", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, team: team || null }),
      });
    },

    ping: function () {
      return _fetchJson("/api/ping").then(function (d) {
        if (d) {
          if (d.budget    != null) IplConfig.budget    = d.budget;
          if (d.xi_size   != null) IplConfig.xi_size   = d.xi_size;
          if (d.max_weeks != null) IplConfig.max_weeks = d.max_weeks;
        }
        return d;
      });
    },

    saveState:  function (p) { return _fetchJson("/api/state", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(p) }); },
    saveMember: function (n, d) { return _fetchJson("/api/member/"+encodeURIComponent(n), { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(d) }); },
    saveMatch:  function (m) { return _fetchJson("/api/match", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(m) }); },

    rollover: function (force) {
      var url = force ? "/api/rollover?force=1" : "/api/rollover";
      return _fetchJson(url, { method: "POST" }).then(function (data) {
        if (!data) return data;
        if (data.season_complete) window.dispatchEvent(new CustomEvent("ipl:season-complete", { detail: data }));
        if (data.rolled) {
          _fetchJson("/api/current-week").then(function (wk) {
            if (wk && wk.week_no != null) {
              IplConfig.current_week = wk.week_no;
              window.dispatchEvent(new CustomEvent("ipl:week-changed", { detail: { week_no: wk.week_no, max_weeks: wk.max_weeks } }));
            }
          }).catch(function () {});
          _lastStateEtag = null;
          window.dispatchEvent(new CustomEvent("ipl:rollover-triggered", { detail: data }));
        }
        return data;
      });
    },

    seedHistory: function () { return _fetchJson("/api/seed-history", { method: "POST" }); },
  };

  // ── 60-SECOND POLLING LOOP ───────────────────────────────────────────────

  function _pollCycle() {
    return _fetchJson("/api/poll")
      .then(function (poll) {
        _cancelOverlay();
        var serverEtag = poll && poll.state_etag;
        if (!serverEtag || serverEtag === _lastStateEtag) return;
        return Promise.all([ IplApi.getState(), IplApi.getLeaderboard() ])
          .then(function (results) {
            var state = results[0]; var lb = results[1];
            if (state) { _lastStateEtag = state._saved || serverEtag; window.dispatchEvent(new CustomEvent("ipl:state-updated", { detail: state })); }
            if (lb)    { window.dispatchEvent(new CustomEvent("ipl:leaderboard-updated", { detail: lb })); }
          });
      })
      .catch(function (err) {
        window.dispatchEvent(new CustomEvent("ipl:error", { detail: err }));
        if ((err.status && err.status >= 500) || !navigator.onLine) _scheduleOverlay();
      });
  }

  function startPolling() { if (_pollTimer) return; _pollCycle(); _pollTimer = setInterval(_pollCycle, POLL_INTERVAL_MS); }
  function stopPolling()  { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }

  // ── LIFECYCLE ────────────────────────────────────────────────────────────

  function _init() {
    _checkVersionHandshake();
    IplApi.ping().catch(function () {});
    IplApi.getCurrentWeek().then(function (wk) {
      if (wk && wk.week_no != null) IplConfig.current_week = wk.week_no;
    }).catch(function () {});
    startPolling();
    _scheduleNextRollover();
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stopPolling(); else { startPolling(); _pollCycle(); }
    });
    window.addEventListener("ipl:saved", function () { _lastStateEtag = null; });
    window.addEventListener("ipl:rollover-triggered", function () { _lastStateEtag = null; _pollCycle(); });
    window.dispatchEvent(new CustomEvent("ipl:ready"));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _init);
  else _init();

  // ── EXPORTS ──────────────────────────────────────────────────────────────

  window.IplApi               = IplApi;
  window.IplPolling           = { start: startPolling, stop: stopPolling };
  window.IplConfig            = IplConfig;
  window.IplRollover          = IplRollover;
  window.normaliseLeaderboard = normaliseLeaderboard;

}(window));


// ════════════════════════════════════════════════════════════════════════════
// v7.1 – v7.7  UI OVERRIDES
// ════════════════════════════════════════════════════════════════════════════


// ── Shared player stats cache ─────────────────────────────────────────────────
var _playerMap = {};

function _loadPlayerMap(cb) {
  IplApi.getPlayers().then(function(d) {
    if (d && d.players) {
      _playerMap = {};
      d.players.forEach(function(p, i) {
        _playerMap[p.id] = p;
        _playerMap[p.id]._sort_idx = i;
      });
      if (typeof cb === 'function') cb();
    }
  }).catch(function() {});
}


// ── v7.7: _patchXiGrid — template-time badge injection ───────────────────────
//
// WHY: _buildXiGrid() is defined in the inline script and generates the Next
// Week (and History) card HTML. It does NOT add data-pid to cards, so the
// MutationObserver approach in v7.6 never found anything to badge.
//
// HOW: wrap the original _buildXiGrid so that:
//  1. The returned HTML is parsed into a temporary div.
//  2. Each direct child card is stamped with data-pid=team[i] (cards and the
//     team[] arg are always co-indexed — the inline script renders them in
//     the same order).
//  3. The season_pts badge is appended to the card's name/title element.
//     Source: _state.player_pts[pid] (embedded in /api/state, zero extra
//     fetch). Falls back to _playerMap[pid].season_pts.
//     Only renders when pts > 0. Style: muted teal pill, opacity-75.
//
// This fires at render time — no observer race, no timing dependency.

var _xiGridPatched = false;

function _patchXiGrid() {
  if (_xiGridPatched) return;
  if (typeof window._buildXiGrid !== 'function') {
    // Inline script not yet executed — retry shortly
    setTimeout(_patchXiGrid, 150);
    return;
  }
  _xiGridPatched = true;
  var _orig = window._buildXiGrid;

  window._buildXiGrid = function(team, cap, vc) {
    var html = _orig(team, cap, vc);
    if (!html || !team || !team.length) return html;

    // Parse into a temporary container so we can walk the DOM
    var tmp = document.createElement('div');
    tmp.innerHTML = html;

    // The outer element returned by the original (grid wrapper or the first
    // child if the function wraps in a fragment)
    var grid = (tmp.children.length === 1) ? tmp.firstElementChild : tmp;
    var cards = Array.from(grid.children);

    // Fallback: if child count doesn't match team (e.g. grid has a header),
    // find card-like children that have sub-elements (avatar + name)
    if (cards.length !== team.length) {
      cards = Array.from(grid.querySelectorAll(':scope > *')).filter(function(el) {
        return el.children.length >= 2;
      });
    }

    cards.forEach(function(card, i) {
      var pid = team[i];
      if (!pid) return;

      // Stamp data-pid so the MutationObserver + picker sort also work
      card.setAttribute('data-pid', pid);

      // Skip if badge already present (re-render guard)
      if (card.querySelector('.ipl-sp-badge')) return;

      // season_pts lookup — prefer _state.player_pts (no extra fetch)
      var pts = 0;
      if (window._state && window._state.player_pts) {
        pts = window._state.player_pts[pid] || 0;
      }
      if (!pts && _playerMap[pid]) {
        pts = _playerMap[pid].season_pts || 0;
      }
      if (!pts) return;

      // Build badge element
      var badge = document.createElement('span');
      badge.className = 'ipl-sp-badge';
      badge.setAttribute('data-for', pid);
      badge.title = 'Season base pts (no cap/vc multiplier)';
      badge.style.cssText = (
        'display:inline-block;' +
        'margin-left:4px;' +
        'padding:1px 5px;' +
        'border-radius:99px;' +
        'background:rgba(0,212,170,0.13);' +
        'color:#00D4AA;' +
        'font-size:9px;font-weight:700;' +
        'opacity:0.75;' +
        'vertical-align:middle;' +
        'white-space:nowrap;' +
        'position:relative;z-index:1;' +   // clears C/VC button stacking
        'pointer-events:none;'             // never intercepts C/VC taps
      );
      badge.textContent = pts + ' Pts';

      // Inject after the player name element (first text-heavy child)
      // Fallback: append to card so it always appears somewhere
      var nameEl = (
        card.querySelector('[class*="name"],[class*="title"],[class*="label"]') ||
        card.firstElementChild
      );
      if (nameEl) {
        nameEl.appendChild(badge);
      } else {
        card.appendChild(badge);
      }
    });

    return tmp.innerHTML;
  };
}

// Patch immediately if inline script already ran, else retry after DOMContentLoaded
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _patchXiGrid);
} else {
  _patchXiGrid();
}


// ── v7.6: Mobile keyboard dismiss ────────────────────────────────────────────
// blur() runs inside requestAnimationFrame so DOM update lands first.
function _dismissKeyboard() {
  requestAnimationFrame(function () {
    try {
      if (document.activeElement && typeof document.activeElement.blur === 'function') {
        document.activeElement.blur();
      }
    } catch (e) {}
  });
}

document.addEventListener('click', function (e) {
  var t = e.target;
  if (!t) return;
  var isAction = t.closest
    ? t.closest('.pick-btn,.swap-btn,.add-btn,.select-btn,[data-action="pick"],[data-action="swap"]')
    : false;
  if (isAction) _dismissKeyboard();
}, true /* capture phase */);

window.addEventListener('ipl:saved', function () { _dismissKeyboard(); });


// ── v7.1: History tab ────────────────────────────────────────────────────────
_buildHistoryTab = function () {
  if (!_historyData || !_historyData.weeks || _historyData.weeks.length === 0) {
    return (_username && !_historyData)
      ? '<div class="card"><p class="empty">History loading\u2026</p></div>'
      : '<div class="card"><div class="history-empty"><strong>No history yet</strong>Your weekly XIs will appear here once you\'ve set and locked a team.</div></div>';
  }
  var weeks  = _historyData.weeks;
  var viewWk = (_historyViewWk === null) ? _currentWeek : _historyViewWk;
  var h = '<div class="history-bar"><label>\uD83D\uDCD6 Browse:</label>'
        + '<select onchange="_selectHistoryWeek(parseInt(this.value))">';
  weeks.forEach(function (w) {
    var lbl = "Week " + w.week_no + (w.week_no === 0 ? " (Pre-season)" : w.is_current ? " (Current)" : " (Archive)");
    h += '<option value="' + w.week_no + '"' + (w.week_no === viewWk ? " selected" : "") + ">" + esc(lbl) + "</option>";
  });
  h += "</select>";
  var selRow = null;
  for (var i = 0; i < weeks.length; i++) { if (weeks[i].week_no === viewWk) { selRow = weeks[i]; break; } }
  if (selRow && !selRow.is_current) h += '<span class="ro-note">\uD83D\uDD12 Read-only archive</span>';
  h += "</div>";
  if (!selRow) return h + '<div class="card"><p class="empty">No data for selected week.</p></div>';
  var tw = selRow.this_week;
  h += '<div class="card' + (selRow.is_current ? "" : " card-locked") + '">'
     + '<div class="week-label">Week ' + selRow.week_no
     + (selRow.week_no === 0 ? " \u2014 Pre-season" : selRow.is_current ? "" : ' &nbsp;<span class="badge-lock">Archive</span>')
     + '</div><h3 class="section-title">\u26A1 This Week\'s XI</h3>';
  h += '<div style="display:inline-flex;align-items:center;gap:10px;background:rgba(0,212,170,.1);'
     + 'border:1px solid rgba(0,212,170,.25);border-radius:8px;padding:6px 14px;margin-bottom:12px;">'
     + '<span style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em">'
     + 'Week ' + selRow.week_no + ' Points</span>'
     + '<span style="font-size:20px;font-weight:900;color:var(--teal)">' + (selRow.week_pts || 0) + '</span>'
     + '</div>';
  if (tw && tw.team && tw.team.length > 0) {
    h += _buildXiGrid(tw.team, tw.cap, tw.vc);
    h += '<div class="cap-hint"><span><span class="badge badge-c">C</span> \xd72 pts</span>'
       + '<span><span class="badge badge-vc">VC</span> \xd71.5 pts</span></div>';
  } else {
    h += '<p class="empty">No XI recorded for this week.</p>';
  }
  return h + "</div>";
};


// ── v7.4: Leaderboard ────────────────────────────────────────────────────────
_buildLeaderboardCard = function (lb) {
  var h = '<div class="card">';
  if (!lb || !lb.rankings || lb.rankings.length === 0) {
    return h + '<h3 class="section-title">\uD83C\uDFC6 Leaderboard</h3>'
             + '<p class="empty">No scores yet \u2014 check back after the first match.</p></div>';
  }
  h += '<h3 class="section-title">\uD83C\uDFC6 Leaderboard' + (lb.week_no ? " \u2014 Week " + lb.week_no : "") + "</h3>";
  h += '<p style="color:var(--muted);font-size:12px;margin-bottom:12px">'
     + 'Avg: <strong style="color:var(--text)">' + lb.league_avg + '</strong>'
     + ' &nbsp;\u00B7&nbsp; Top: <strong style="color:var(--gold)">' + lb.top_score + '</strong>'
     + ' &nbsp;\u00B7&nbsp; ' + lb.member_count + ' members'
     + ' &nbsp;\u00B7&nbsp; <span style="color:var(--muted);font-size:11px" title="Points include Captain \xd72 and Vice-Captain \xd71.5 multipliers">'
     + '\u2139\uFE0F Cap/VC weighted</span></p>';
  var allWeeks = [], weekSet = {};
  lb.rankings.forEach(function (r) {
    (r.weekly || []).forEach(function (w) {
      if (!weekSet[w.week_no]) { weekSet[w.week_no] = true; allWeeks.push(w.week_no); }
    });
  });
  allWeeks.sort(function (a, b) { return a - b; });
  h += '<table class="lb-table"><thead><tr><th>#</th><th>Name</th>';
  allWeeks.forEach(function (wk) { h += '<th style="text-align:right;font-size:11px">W' + wk + '</th>'; });
  h += '<th style="text-align:right" title="Cap \xd72 + VC \xd71.5 applied">Total\u00A0\u2605</th><th>MVP</th></tr></thead><tbody>';
  lb.rankings.forEach(function (row) {
    var isMe = row.name === _username;
    var weekMap = {};
    (row.weekly || []).forEach(function (w) { weekMap[w.week_no] = w.pts; });
    h += '<tr' + (isMe ? ' class="me"' : "") + '>'
       + '<td class="rank">' + row.rank + '</td>'
       + '<td>' + esc(row.name) + (isMe ? ' <span style="color:var(--gold);font-size:11px">(you)</span>' : "") + '</td>';
    allWeeks.forEach(function (wk) {
      var pts = weekMap[wk];
      h += '<td style="text-align:right;font-size:12px;color:' + (pts > 0 ? 'var(--teal)' : 'var(--dim)') + '">'
         + (pts != null ? pts : '\u2014') + '</td>';
    });
    h += '<td class="pts">' + (row.total_pts || 0) + '</td>';
    var mvpName = row.mvp && row.mvp.player_name ? esc(row.mvp.player_name) : null;
    var mvpPts  = row.mvp && row.mvp.pts ? row.mvp.pts : null;
    var mvpCell = mvpName
      ? mvpName + ' <span style="color:var(--teal);font-size:11px">(' + mvpPts + ')</span>'
      : '\u2014';
    h += '<td class="mvp">' + mvpCell + '</td></tr>';
  });
  h += '</tbody></table>';
  h += '<p style="font-size:10px;color:var(--dim);margin:8px 0 0;text-align:right">'
     + '\u2605 Total = sum of per-match pts with Cap(\xd72) and VC(\xd71.5) applied</p>';
  h += '</div>';
  return h;
};


// ── v7.4: Points tab ─────────────────────────────────────────────────────────
_buildPointsTab = function () {
  var h = '<div class="card">';
  h += '<div class="user-header-bar" style="margin-bottom:14px">';
  h += '<h3 class="section-title" style="margin:0">\uD83D\uDCCA My Points Breakdown</h3>';
  h += '<button class="btn btn-ghost btn-sm" onclick="_ptsData=null;_loadPoints();if(_state)render(_state)" title="Reload points">\u27F3 Reload</button>';
  h += '</div>';

  if (_ptsLoading) return h + '<div class="pts-loading">\u23F3 Loading\u2026</div></div>';
  if (!_ptsData || !_ptsData.players || _ptsData.players.length === 0)
    return h + '<div class="pts-loading">No points data yet. Run scraper then Reload.</div></div>';

  var d = _ptsData;
  var _ms = {};
  d.players.forEach(function(p) { (p.matches||[]).forEach(function(m) { _ms[m.match_id] = true; }); });
  var matchCount = Object.keys(_ms).length;

  h += '<div class="pts-summary">';
  h += '<div class="pts-stat"><div class="val">' + d.total_pts + '</div><div class="lbl">Total Pts</div></div>';
  h += '<div class="pts-stat"><div class="val">' + d.players.length + '</div><div class="lbl">Players</div></div>';
  h += '<div class="pts-stat"><div class="val">' + matchCount + '</div><div class="lbl">Matches</div></div>';
  if (matchCount > 0) h += '<div class="pts-stat"><div class="val">' + Math.round(d.total_pts/matchCount) + '</div><div class="lbl">Avg/Match</div></div>';
  h += '</div>';

  h += '<table class="pts-table"><thead><tr>'
     + '<th>Player</th><th>Team</th><th>Role</th>'
     + '<th style="text-align:right">Pts\u00A0(cap/vc)</th>'
     + '<th style="text-align:right;font-size:11px;color:var(--muted)">Base</th>'
     + '<th></th></tr></thead><tbody>';

  d.players.forEach(function(p, idx) {
    var rowId  = "pts-row-" + idx;
    var badge  = p.is_cap ? '<span class="badge badge-c" style="margin-left:4px">C</span>'
               : p.is_vc  ? '<span class="badge badge-vc" style="margin-left:4px">VC</span>' : '';
    var mult   = p.is_cap ? " (\xd72)" : p.is_vc ? " (\xd71.5)" : "";
    var avC    = _avClass(p.team);
    var pInfo  = _playerMap[p.id] || {};
    var basePts = pInfo.season_pts != null ? pInfo.season_pts : '\u2014';

    h += '<tr>';
    h += '<td><div style="display:flex;align-items:center;gap:8px">'
       + '<div class="prow-avatar ' + avC + '">' + esc(_initials(p.name||p.id)) + '</div>'
       + '<div><div style="font-size:13px;font-weight:600">' + esc(p.name||p.id) + badge + '</div>'
       + '<div style="font-size:10px;color:var(--muted)">' + esc(p.id) + '</div></div></div></td>';
    h += '<td style="font-size:11px;color:var(--muted)">' + esc(p.team||'\u2014') + '</td>';
    h += '<td>' + _roleBadge(p.role) + '</td>';
    h += '<td class="pts-cell">' + p.total_pts + esc(mult) + '</td>';
    h += '<td style="text-align:right;font-size:11px;color:var(--muted)">' + basePts + '</td>';
    h += '<td>';
    if (p.matches && p.matches.length > 0)
      h += '<button class="pts-expand-btn" onclick="(function(){var el=document.getElementById(\'' + rowId + '\');el.classList.toggle(\'open\');})()" >Details</button>';
    h += '</td></tr>';
    if (p.matches && p.matches.length > 0) {
      h += '<tr><td colspan="6" style="padding:0 8px 8px"><div class="pts-matches" id="' + rowId + '">';
      p.matches.forEach(function(m) {
        var multStr = m.multiplier > 1 ? (m.multiplier === 2
          ? '<span class="m-mult">\xd72</span>'
          : '<span class="m-mult">\xd71.5</span>') : '';
        h += '<div class="pts-match-row">'
           + '<div class="m-title">' + esc(m.title||m.match_id) + '</div>'
           + '<div style="display:flex;align-items:center">'
           + '<span style="font-size:10px;color:var(--muted);margin-right:6px">W' + m.week_no + '</span>'
           + multStr + '<span class="m-pts">' + m.final_pts + '</span>'
           + '</div></div>';
      });
      h += '</div></td></tr>';
    }
  });
  h += '</tbody></table>';

  var matchTitleMap = {};
  if (_state && _state.matches) {
    _state.matches.forEach(function(m) { matchTitleMap[m.id] = m.title || m.id; });
  }

  if (_historyData && _historyData.weeks && _historyData.weeks.length > 0) {
    var rows = [];
    _historyData.weeks.forEach(function(wk) {
      var ppm = wk.points_per_match || {};
      Object.keys(ppm).forEach(function(mid) {
        rows.push({ week_no: wk.week_no, match_id: mid, pts: ppm[mid] });
      });
    });
    rows.sort(function(a, b) {
      return a.week_no !== b.week_no ? a.week_no - b.week_no : a.match_id.localeCompare(b.match_id);
    });

    if (rows.length > 0) {
      h += '<h3 class="section-title" style="margin-top:20px">\uD83D\uDCC8 Match-by-Match Team Totals</h3>';
      h += '<p style="font-size:11px;color:var(--muted);margin-bottom:8px">Your XI\u2019s combined score per game including Cap(\xd72) and VC(\xd71.5).</p>';
      h += '<table style="width:100%;border-collapse:collapse;font-size:12px">';
      h += '<thead><tr>'
         + '<th style="text-align:left;padding:5px 8px;color:var(--muted)">Match</th>'
         + '<th style="text-align:center;padding:5px 4px;color:var(--muted)">Wk</th>'
         + '<th style="text-align:right;padding:5px 8px;color:var(--teal)">XI Total</th>'
         + '</tr></thead><tbody>';
      var runningTotal = 0;
      rows.forEach(function(r) {
        runningTotal += r.pts;
        var title = matchTitleMap[r.match_id] || r.match_id;
        var color = r.pts > 0 ? 'var(--teal)' : 'var(--dim)';
        h += '<tr style="border-top:1px solid rgba(255,255,255,.04)">'
           + '<td style="padding:6px 8px;color:var(--text)">' + esc(title) + '</td>'
           + '<td style="text-align:center;padding:6px 4px;color:var(--muted)">W' + r.week_no + '</td>'
           + '<td style="text-align:right;padding:6px 8px;font-weight:700;color:' + color + '">' + r.pts + '</td>'
           + '</tr>';
      });
      h += '<tr style="border-top:2px solid rgba(255,255,255,.12)">'
         + '<td colspan="2" style="padding:7px 8px;font-weight:700;color:var(--text)">Season Total</td>'
         + '<td style="text-align:right;padding:7px 8px;font-weight:900;color:var(--gold)">' + runningTotal + '</td>'
         + '</tr>';
      h += '</tbody></table>';
    }
  }

  return h + '</div>';
};


// ── v7.3: Matches tab ────────────────────────────────────────────────────────
var _umpData = null;

function _loadUserMatchPoints() {
  if (!window._username) return;
  IplApi.getUserMatchPoints(window._username)
    .then(function(d) {
      if (d && d.ok) {
        _umpData = {};
        (d.matches || []).forEach(function(m) { _umpData[m.match_id] = m.pts; });
      }
    }).catch(function() {});
}

_buildMatchesTab = function () {
  if (_umpData === null) _loadUserMatchPoints();

  if (!_state || !_state.matches || _state.matches.length === 0)
    return '<div class="card"><p class="empty">No match data yet.</p></div>';

  var h = '<div class="card"><h3 class="section-title">\uD83D\uDCCB Matches</h3>';
  h += '<table style="width:100%;border-collapse:collapse;font-size:13px">';
  h += '<thead><tr>'
     + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600">Match</th>'
     + '<th style="text-align:center;padding:6px 4px;color:var(--muted);font-weight:600">Wk</th>'
     + '<th style="text-align:center;padding:6px 4px;color:var(--muted);font-weight:600">Status</th>'
     + '<th style="text-align:right;padding:6px 8px;color:var(--teal);font-weight:600">My Pts</th>'
     + '</tr></thead><tbody>';

  _state.matches.forEach(function(m) {
    var title  = m.title || m.id;
    var status = (m.status || "").toLowerCase();
    var badgeColor = status === 'completed' ? '#00D4AA' : status === 'live' ? '#F5C518' : '#5F7A9B';
    var badgeLabel = status === 'completed' ? 'Completed' : status === 'live' ? 'Live' : 'Upcoming';
    var myPts  = (_umpData && _umpData[m.id] != null) ? _umpData[m.id] : null;
    var ptsStr = (myPts != null)
      ? '<span style="color:var(--teal);font-weight:700">' + myPts + '</span>'
      : '<span style="color:var(--dim)">\u2014</span>';
    h += '<tr style="border-top:1px solid rgba(255,255,255,.05)">';
    h += '<td style="padding:8px 8px;font-weight:500">' + esc(title) + '</td>';
    h += '<td style="text-align:center;padding:8px 4px;color:var(--muted);font-size:11px">W' + (m.wk||m.week_no||'?') + '</td>';
    h += '<td style="text-align:center;padding:8px 4px">';
    h += '<span style="display:inline-block;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;'
       + 'background:' + badgeColor + '22;color:' + badgeColor + '">' + badgeLabel + '</span>';
    h += '</td>';
    h += '<td style="text-align:right;padding:8px 8px">' + ptsStr + '</td>';
    h += '</tr>';
  });

  h += '</tbody></table></div>';
  return h;
};


// ── v7.6/7.7: Picker/swap list badges (secondary — for the search list) ───────
//
// This handles the SWAP LIST (below the XI grid) which uses a different
// DOM structure from the XI cards above. These rows typically DO have
// data-pid on them or are .prow elements. _patchXiGrid handles the XI
// grid cards; this handles the picker/swap rows.

function _injectStatsToPicker() {
  // Badge injection on any [data-pid] element (swap list rows)
  var chips = document.querySelectorAll('[data-pid]');
  chips.forEach(function (chip) {
    if (chip.querySelector('.ipl-sp-badge')) return;
    var pid = chip.getAttribute('data-pid');
    if (!pid) return;
    var pts = 0;
    if (window._state && window._state.player_pts) {
      pts = window._state.player_pts[pid] || 0;
    }
    if (!pts && _playerMap[pid]) pts = _playerMap[pid].season_pts || 0;
    if (!pts) return;

    var badge = document.createElement('span');
    badge.className = 'ipl-sp-badge';
    badge.setAttribute('data-for', pid);
    badge.title = 'Season base pts (no cap/vc)';
    badge.style.cssText = (
      'display:inline-block;margin-left:4px;padding:1px 5px;' +
      'border-radius:99px;background:rgba(0,212,170,0.13);color:#00D4AA;' +
      'font-size:9px;font-weight:700;opacity:0.75;vertical-align:middle;' +
      'white-space:nowrap;pointer-events:none;'
    );
    badge.textContent = pts + ' Pts';

    var anchor = chip.querySelector('.prow-name,.player-name,[class*="name"]') || chip.firstElementChild;
    if (anchor) anchor.appendChild(badge);
    else chip.appendChild(badge);
  });

  // Sort swap/picker lists by season_pts DESC
  var lists = document.querySelectorAll(
    '.picker-list,.swap-list,.search-results,.player-list,' +
    '[class*="picker"] ul,[class*="search"] ul'
  );
  lists.forEach(function (list) {
    if (list.getAttribute('data-ipl-sorted')) return;
    var items = Array.from(list.querySelectorAll('[data-pid]'));
    if (items.length < 2) return;
    var hasData = items.some(function (el) {
      var p = _playerMap[el.getAttribute('data-pid')];
      return p && p.season_pts > 0;
    });
    if (!hasData) return;
    list.setAttribute('data-ipl-sorted', '1');
    items.sort(function (a, b) {
      var pa = _playerMap[a.getAttribute('data-pid')] || {};
      var pb = _playerMap[b.getAttribute('data-pid')] || {};
      var diff = (pb.season_pts || 0) - (pa.season_pts || 0);
      return diff !== 0 ? diff : (pa._sort_idx || 0) - (pb._sort_idx || 0);
    });
    items.forEach(function (el) { list.appendChild(el); });
  });
}

// ── MutationObserver (secondary / swap list) ──────────────────────────────────
var _pickerObserver = null;

function _setupPickerObserver() {
  if (_pickerObserver) return;
  var target = document.querySelector('.tab-content, #app, main, body');
  if (!target) { setTimeout(_setupPickerObserver, 500); return; }

  _loadPlayerMap(function () { _injectStatsToPicker(); });

  _pickerObserver = new MutationObserver(function () { _injectStatsToPicker(); });
  _pickerObserver.observe(target, { childList: true, subtree: true });
}

window.addEventListener('ipl:state-updated', function () {
  _umpData = null;
  // Re-apply _patchXiGrid in case the inline script replaced _buildXiGrid
  _xiGridPatched = false;
  _patchXiGrid();
  _loadPlayerMap(function () { _injectStatsToPicker(); });
});

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _setupPickerObserver);
else setTimeout(_setupPickerObserver, 800);
