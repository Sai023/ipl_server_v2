/**
 * ipl_glue.js — Frontend Integration Layer                 Golden File v7.3
 * =========================================================================
 * v7.3 (this release):
 *   • IplApi.getUserMatchPoints(name) — fetches per-match user pts from new endpoint.
 *   • _buildMatchesTab() override — replaces raw Cricbuzz timestamps with clean
 *     match titles + user pts-per-match column (fetched from /api/user-match-points).
 *   • Player picker season_pts injection — after player list renders, injects
 *     each player's season total (from players[].season_pts) next to the price.
 *
 * v7.2: _buildPointsTab() matchCount fix (unique match IDs).
 * v7.1: _buildHistoryTab() weekly chip, _buildLeaderboardCard() per-week columns.
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

  // ── MAINTENANCE OVERLAY ───────────────────────────────────────────────────────

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

  // ── LEADERBOARD NORMALISATION ────────────────────────────────────────────

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

  // ── ROLLOVER SCHEDULER ──────────────────────────────────────────────────

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
    console.info("[IplRollover] Triggering Monday 14:00 rollover …");
    IplApi.rollover(false)
      .then(function (data) {
        if (data && data.rolled) { console.info("[IplRollover] Rollover complete — week: " + data.new_week_no); _lastStateEtag = null; _pollCycle(); }
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

  // ── PUBLIC API ──────────────────────────────────────────────────────────

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

    getPlayers: function () { return _fetchJson("/api/players"); },
    getCurrentWeek: function () { return _fetchJson("/api/current-week"); },
    getHistory: function (name) { return _fetchJson("/api/history/" + encodeURIComponent(name)); },
    getPlayerPoints: function (name) { return _fetchJson("/api/player-points/" + encodeURIComponent(name)); },

    /** v7.3: per-match user points from user_match_points table */
    getUserMatchPoints: function (name) {
      return _fetchJson("/api/user-match-points/" + encodeURIComponent(name));
    },

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

    saveState:   function (p) { return _fetchJson("/api/state", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(p) }); },
    saveMember:  function (n, d) { return _fetchJson("/api/member/"+encodeURIComponent(n), { method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(d) }); },
    saveMatch:   function (m) { return _fetchJson("/api/match", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(m) }); },

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

  // ── 60-SECOND POLLING LOOP ─────────────────────────────────────────────

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

  // ── LIFECYCLE ───────────────────────────────────────────────────────────

  function _init() {
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

  // ── EXPORTS ────────────────────────────────────────────────────────────

  window.IplApi               = IplApi;
  window.IplPolling           = { start: startPolling, stop: stopPolling };
  window.IplConfig            = IplConfig;
  window.IplRollover          = IplRollover;
  window.normaliseLeaderboard = normaliseLeaderboard;

}(window));


// ── v7.1 / v7.2 / v7.3 UI OVERRIDES ────────────────────────────────────────────────

// v7.1: History tab weekly pts chip
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

// v7.1: Leaderboard per-week columns
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
     + ' &nbsp;\u00B7&nbsp; ' + lb.member_count + ' members</p>';
  var allWeeks = [], weekSet = {};
  lb.rankings.forEach(function (r) {
    (r.weekly || []).forEach(function (w) {
      if (!weekSet[w.week_no]) { weekSet[w.week_no] = true; allWeeks.push(w.week_no); }
    });
  });
  allWeeks.sort(function (a, b) { return a - b; });
  h += '<table class="lb-table"><thead><tr><th>#</th><th>Name</th>';
  allWeeks.forEach(function (wk) { h += '<th style="text-align:right;font-size:11px">W' + wk + '</th>'; });
  h += '<th style="text-align:right">Total</th><th>MVP</th></tr></thead><tbody>';
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
    h += '<td class="pts">' + (row.total_pts || 0) + '</td>'
       + '<td class="mvp">' + (row.mvp && row.mvp.player_name ? esc(row.mvp.player_name) + ' (' + row.mvp.pts + ')' : '\u2014') + '</td>'
       + '</tr>';
  });
  h += '</tbody></table></div>';
  return h;
};

// v7.2: Points tab matchCount fix
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
  h += '<table class="pts-table"><thead><tr><th>Player</th><th>Team</th><th>Role</th><th style="text-align:right">Pts</th><th></th></tr></thead><tbody>';
  d.players.forEach(function(p, idx) {
    var rowId  = "pts-row-" + idx;
    var badge  = p.is_cap ? '<span class="badge badge-c" style="margin-left:4px">C</span>'
               : p.is_vc  ? '<span class="badge badge-vc" style="margin-left:4px">VC</span>' : '';
    var mult   = p.is_cap ? " (\xd72)" : p.is_vc ? " (\xd71.5)" : "";
    var avC    = _avClass(p.team);
    h += '<tr>';
    h += '<td><div style="display:flex;align-items:center;gap:8px">'
       + '<div class="prow-avatar ' + avC + '">' + esc(_initials(p.name||p.id)) + '</div>'
       + '<div><div style="font-size:13px;font-weight:600">' + esc(p.name||p.id) + badge + '</div>'
       + '<div style="font-size:10px;color:var(--muted)">' + esc(p.id) + '</div></div></div></td>';
    h += '<td style="font-size:11px;color:var(--muted)">' + esc(p.team||'\u2014') + '</td>';
    h += '<td>' + _roleBadge(p.role) + '</td>';
    h += '<td class="pts-cell">' + p.total_pts + esc(mult) + '</td>';
    h += '<td>';
    if (p.matches && p.matches.length > 0)
      h += '<button class="pts-expand-btn" onclick="(function(){var el=document.getElementById(\'' + rowId + '\');el.classList.toggle(\'open\');})()">Details</button>';
    h += '</td></tr>';
    if (p.matches && p.matches.length > 0) {
      h += '<tr><td colspan="5" style="padding:0 8px 8px"><div class="pts-matches" id="' + rowId + '">';
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
  return h + '</div>';
};


// ── v7.3: Matches tab override ────────────────────────────────────────────────
// Shows clean match titles, Wk labels (no raw Cricbuzz timestamps),
// and a “My Pts” column populated from /api/user-match-points.

var _umpData = null;  // cached user match points

function _loadUserMatchPoints() {
  if (!window._username) return;
  IplApi.getUserMatchPoints(window._username)
    .then(function(d) {
      if (d && d.ok) { _umpData = {}; (d.matches || []).forEach(function(m) { _umpData[m.match_id] = m.pts; }); }
    }).catch(function() {});
}

// Called whenever the Matches tab is shown
_buildMatchesTab = function () {
  // Load user match pts on first view
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
    var myPts = (_umpData && _umpData[m.id] != null) ? _umpData[m.id] : null;
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


// ── v7.3: Player picker season_pts injection ───────────────────────────────
// After the Next Week draft renders, inject season_pts next to each player's
// price so you can see form when picking. Uses players.season_pts from /api/players.

var _playerSeasonPts = {};  // id -> season_pts

function _loadPlayerSeasonPts() {
  IplApi.getPlayers().then(function(d) {
    if (d && d.players) {
      _playerSeasonPts = {};
      d.players.forEach(function(p) { _playerSeasonPts[p.id] = p.season_pts || 0; });
      _injectSeasonPtsIntoPicker();
    }
  }).catch(function() {});
}

function _injectSeasonPtsIntoPicker() {
  // Find player rows in the picker — they have a data-pid attribute or a price span.
  // The inline script builds rows with class 'prow' or similar containing the player id.
  var rows = document.querySelectorAll('[data-pid], .prow, .player-row, .pick-row');
  rows.forEach(function(row) {
    var pid = row.getAttribute('data-pid') || row.dataset.pid;
    if (!pid) {
      // Try to extract from child element with class prow-id or similar
      var idEl = row.querySelector('.prow-id, [class*="prow-id"], .player-id');
      if (idEl) pid = idEl.textContent.trim();
    }
    if (!pid) return;
    var pts = _playerSeasonPts[pid];
    if (pts == null) return;
    // Only inject if not already done
    if (row.querySelector('.ipl-season-pts')) return;
    var badge = document.createElement('span');
    badge.className = 'ipl-season-pts';
    badge.title = 'Season points';
    badge.style.cssText = 'display:inline-block;margin-left:6px;padding:1px 6px;'
      + 'border-radius:99px;background:rgba(0,212,170,.15);color:#00D4AA;'
      + 'font-size:10px;font-weight:700;vertical-align:middle';
    badge.textContent = pts + 'pts';
    // Inject next to the price element
    var priceEl = row.querySelector('[class*="price"], .prow-price, .player-price');
    if (priceEl) priceEl.parentNode.insertBefore(badge, priceEl.nextSibling);
    else row.appendChild(badge);
  });
}

// Watch for Next Week tab to become active and inject pts
var _pickerObserver = null;
function _setupPickerObserver() {
  if (_pickerObserver) return;
  var target = document.querySelector('.tab-content, #app, main, body');
  if (!target) { setTimeout(_setupPickerObserver, 500); return; }
  _loadPlayerSeasonPts();
  _pickerObserver = new MutationObserver(function() { _injectSeasonPtsIntoPicker(); });
  _pickerObserver.observe(target, { childList: true, subtree: true });
}

// Also reload user match pts cache whenever state updates
window.addEventListener('ipl:state-updated', function() {
  _umpData = null;  // force reload on next Matches tab view
});

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _setupPickerObserver);
else setTimeout(_setupPickerObserver, 800);
