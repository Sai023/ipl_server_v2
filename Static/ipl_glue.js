/**
 * ipl_glue.js — Frontend Integration Layer                 Golden File v7.8
 * =========================================================================
 * v7.8 (Phase 9.5 — Final Lock):
 *   • Header bumped to v7.8; full Match Centre feature set live via mc_hub.js.
 *   • Phase 9.2: _injectMCStyles() — --ipl-teal, .match-card, .match-modal.
 *   • Phase 9.3: Hub list + cache + _buildMatchCentreTab() in mc_hub.js.
 *   • Phase 9.4: Box Score modal — role badges, C/VC multiplier annotations,
 *     client-side top-scorer highlight, independent MATCH TOTAL integrity check.
 *   • ipl:state-updated invalidates _mcData so scraper runs auto-refresh hub.
 *
 * v7.6 (Phase 9.2): _injectMCStyles(), CSS vars, scaffold stub.
 * v7.5: Safe Boot — 5-second timeout.
 * v7.4: _playerMap cache, per-week leaderboard columns, match-by-match totals.
 * v7.3: Matches tab with My Pts column.
 */

(function (window) {
  "use strict";

  var POLL_INTERVAL_MS  = 60000;
  var MAINTENANCE_DELAY = 1500;
  var ROLLOVER_HOUR_UTC = 14;
  var ROLLOVER_MIN_UTC  = 0;
  var SAFE_BOOT_MS      = 5000;

  var _lastStateEtag  = null;
  var _overlayTimer   = null;
  var _overlayVisible = false;
  var _pollTimer      = null;
  var _rolloverTimer  = null;
  var _safeBootTimer  = null;

  var IplConfig = {
    budget:       100.0,
    xi_size:      11,
    max_weeks:    10,
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
      league_avg: league_avg, top_score: top_score, member_count: member_count,
      meta: { league_avg: league_avg, top_score: top_score, member_count: member_count },
      rankings: rows, standings: rows,
    };
  }

  // ── HTTP HELPERS ─────────────────────────────────────────────────────────

  function _fetchJson(url, options) {
    options = options || {};
    var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    // Auto-attach bearer token on /api/passcode/*, /api/admin/*, /api/whoami calls.
    if (!headers["Authorization"]) {
      var t = IplAuth.getToken();
      if (t && (url.indexOf("/api/passcode") === 0 || url.indexOf("/api/admin") === 0 || url.indexOf("/api/whoami") === 0)) {
        headers["Authorization"] = "Bearer " + t;
      }
    }
    return fetch(url, Object.assign({}, options, { headers: headers }))
      .then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) {
          var err = new Error("HTTP " + res.status); err.status = res.status;
          // 401 on a passcode/admin endpoint → token expired/revoked. Clear it
          // so the next bootstrap drops to the login card instead of looping.
          if (res.status === 401 && (url.indexOf("/api/passcode") === 0 || url.indexOf("/api/admin") === 0 || url.indexOf("/api/whoami") === 0)) {
            IplAuth.clearToken();
          }
          return res.json().catch(function () { return {}; }).then(function (j) {
            err.serverMessage = j.error || j.detail || ""; err.serverData = j; throw err;
          });
        }
        return res.json();
      });
  }

  // ── AUTH TOKEN STORE (Phase: Passcodes) ────────────────────────────────────
  // Bearer token issued by /api/login or /api/register, persisted in localStorage
  // under `ipl_session_token`. Cleared on logout or 401.

  var TOK_KEY = "ipl_session_token";
  var IplAuth = {
    getToken:   function () { try { return localStorage.getItem(TOK_KEY) || null; } catch (e) { return null; } },
    setToken:   function (t) { try { if (t) localStorage.setItem(TOK_KEY, t); } catch (e) {} },
    clearToken: function ()  { try { localStorage.removeItem(TOK_KEY); } catch (e) {} },
  };

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
    console.info("[IplRollover] Triggering Monday 14:00 rollover \u2026");
    IplApi.rollover(false)
      .then(function (data) {
        if (data && data.rolled) { console.info("[IplRollover] Complete \u2014 week: " + data.new_week_no); _lastStateEtag = null; _pollCycle(); }
        else { console.info("[IplRollover] No-op:", data && data.reason); }
      })
      .catch(function (err) { console.warn("[IplRollover] Failed:", err.message || err); })
      .finally(function () { _scheduleNextRollover(); });
  }

  function _scheduleNextRollover() {
    if (_rolloverTimer) { clearTimeout(_rolloverTimer); _rolloverTimer = null; }
    var ms = Math.min(_msUntilNextRollover(), 7 * 24 * 60 * 60 * 1000);
    console.info("[IplRollover] Next rollover scheduled in " + Math.round(ms / 60000) + " min");
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
      return fetch("/api/state", { headers: headers }).then(function (res) {
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

    getPlayers:         function () { return _fetchJson("/api/players"); },
    getCurrentWeek:     function () { return _fetchJson("/api/current-week"); },
    getHistory:         function (name) { return _fetchJson("/api/history/" + encodeURIComponent(name)); },

    // ── Phase 9: Match Centre endpoints ─────────────────────────────────
    getMatchCentre:  function (name) {
      return _fetchJson("/api/match-centre?user=" + encodeURIComponent(name));
    },
    getMatchDetails: function (matchId, name) {
      return _fetchJson("/api/match-details/" + encodeURIComponent(matchId)
                        + "?user=" + encodeURIComponent(name));
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
        body: JSON.stringify({ query: query, team: team || null }),
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

    saveMember: function (n,d){ return _fetchJson("/api/member/"+encodeURIComponent(n), { method:"PUT",  headers:{"Content-Type":"application/json"}, body:JSON.stringify(d) }); },

    // ── Phase: Passcodes — auth surface ────────────────────────────────────
    register: function (name, passcode) {
      return _fetchJson("/api/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, passcode: passcode }),
      }).then(function (d) { if (d && d.token) IplAuth.setToken(d.token); return d; });
    },
    login: function (name, passcode) {
      return _fetchJson("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, passcode: passcode }),
      }).then(function (d) { if (d && d.token) IplAuth.setToken(d.token); return d; });
    },
    whoami: function () { return _fetchJson("/api/whoami"); },
    changePasscode: function (newPasscode) {
      return _fetchJson("/api/passcode/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ "new": newPasscode }),
      }).then(function (d) { if (d && d.token) IplAuth.setToken(d.token); return d; });
    },
    adminListMembers: function () { return _fetchJson("/api/admin/members"); },
    adminResetPasscode: function (targetUsername) {
      return _fetchJson("/api/admin/passcode/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_username: targetUsername }),
      });
    },
    logoutClearToken: function () { IplAuth.clearToken(); },

    rollover: function (force) {
      var url = force ? "/api/rollover?force=1" : "/api/rollover";
      return _fetchJson(url, { method: "POST" }).then(function (data) {
        if (!data) return data;
        if (data.season_complete) {
          console.warn("[IplApi] Season complete \u2014 all " + IplConfig.max_weeks + " weeks rolled.");
          window.dispatchEvent(new CustomEvent("ipl:season-complete", { detail: data }));
        }
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
    window.addEventListener("ipl:saved",             function () { _lastStateEtag = null; });
    window.addEventListener("ipl:rollover-triggered", function () { _lastStateEtag = null; _pollCycle(); });

    _safeBootTimer = setTimeout(function () {
      var loading = document.getElementById("app-loading");
      if (loading && !loading.classList.contains("hidden")) {
        loading.classList.add("hidden");
        setTimeout(function () { if (loading) loading.remove(); }, 450);
        var banner = document.getElementById("error-banner");
        if (banner) {
          banner.textContent = "\u26a0\ufe0f  Could not connect to server \u2014 please check your connection or refresh the page.";
          banner.classList.add("visible");
        }
        console.warn("[IPL] Safe boot timeout: /api/state did not respond within " + (SAFE_BOOT_MS / 1000) + "s.");
      }
    }, SAFE_BOOT_MS);

    window.addEventListener("ipl:state-updated", function _clearSafeBoot() {
      if (_safeBootTimer) { clearTimeout(_safeBootTimer); _safeBootTimer = null; }
      window.removeEventListener("ipl:state-updated", _clearSafeBoot);
    });

    window.dispatchEvent(new CustomEvent("ipl:ready"));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _init);
  else _init();

  // ── EXPORTS ────────────────────────────────────────────────────────────

  window.IplApi               = IplApi;
  window.IplAuth              = IplAuth;
  window.IplPolling           = { start: startPolling, stop: stopPolling };
  window.IplConfig            = IplConfig;
  window.IplRollover          = IplRollover;
  window.normaliseLeaderboard = normaliseLeaderboard;

}(window));


// ════════════════════════════════════════════════════════════════════════════
// v7.4  UI OVERRIDES
// ════════════════════════════════════════════════════════════════════════════


// ── v7.4: Leaderboard — per-week columns + cap/vc note ───────────────────
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
    var mvpCell = mvpName ? mvpName + ' <span style="color:var(--teal);font-size:11px">(' + mvpPts + ')</span>' : '\u2014';
    h += '<td class="mvp">' + mvpCell + '</td></tr>';
  });
  h += '</tbody></table>';
  h += '<p style="font-size:10px;color:var(--dim);margin:8px 0 0;text-align:right">'
     + '\u2605 Total = sum of per-match pts with Cap(\xd72) and VC(\xd71.5) applied</p>';
  h += '</div>';
  return h;
};

// ════════════════════════════════════════════════════════════════════════════
// v7.6  MATCH CENTRE — CSS Injection + Scaffold   (Phase 9.2)
// ════════════════════════════════════════════════════════════════════════════

function _injectMCStyles() {
  if (document.getElementById('ipl-mc-styles')) return;
  var s = document.createElement('style');
  s.id = 'ipl-mc-styles';
  s.textContent = [
    ':root{--ipl-teal:#00d2ff;--ipl-teal-soft:rgba(0,210,255,.13);}',
    '.mc-stats-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:18px;}',
    '.mc-stat-box{background:#0E1E35;border:1px solid rgba(0,210,255,.14);border-radius:10px;padding:11px 6px;text-align:center;}',
    '.mc-stat-val{font-size:21px;font-weight:900;color:var(--ipl-teal);line-height:1;}',
    '.mc-stat-val.gold{color:#F5C518;}',
    '.mc-stat-lbl{font-size:9px;color:var(--muted,#5F7A9B);text-transform:uppercase;letter-spacing:.06em;margin-top:3px;}',
    '.mc-week-hdr{display:flex;align-items:center;justify-content:space-between;',
    'padding:10px 0 6px;border-bottom:1px solid rgba(0,210,255,.1);margin-bottom:8px;}',
    '.mc-week-lbl{font-size:11px;font-weight:700;color:var(--ipl-teal);text-transform:uppercase;letter-spacing:.07em;}',
    '.mc-week-pts{font-size:13px;font-weight:800;color:#F5C518;}',
    '.match-card{display:flex;align-items:center;gap:10px;padding:11px 12px;',
    'background:rgba(0,210,255,.04);border:1px solid rgba(0,210,255,.08);border-radius:10px;',
    'margin-bottom:6px;cursor:pointer;transition:background .16s,border-color .16s;',
    '-webkit-tap-highlight-color:transparent;}',
    '.match-card:hover,.match-card:active{background:var(--ipl-teal-soft);border-color:rgba(0,210,255,.28);}',
    '.match-card.mc-upcoming{opacity:.55;cursor:default;pointer-events:none;}',
    '.mc-mno{font-size:10px;font-weight:700;color:var(--muted,#5F7A9B);min-width:24px;text-align:center;}',
    '.mc-mbody{flex:1;min-width:0;}',
    '.mc-mtitle{font-size:13px;font-weight:700;color:var(--text,#D8E8F5);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '.mc-mmeta{font-size:10px;color:var(--muted,#5F7A9B);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '.mc-mright{display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0;}',
    '.mc-mpts{font-size:18px;font-weight:900;color:var(--ipl-teal);line-height:1;}',
    '.mc-mpts.zero{color:var(--dim,#3D5572);}',
    '.mc-spill{display:inline-block;padding:2px 7px;border-radius:99px;font-size:9px;font-weight:700;',
    'text-transform:uppercase;letter-spacing:.04em;background:rgba(0,212,170,.14);color:#00D4AA;}',
    '.mc-spill.upcoming{background:rgba(95,122,155,.14);color:#5F7A9B;}',
    '.mc-teams-row{display:flex;align-items:center;gap:6px;margin:3px 0 2px;}',
    '.mc-team-tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;}',
    '.mc-vs{font-size:9px;font-weight:600;color:var(--muted,#5F7A9B);text-transform:uppercase;}',
    '.mc-result{color:var(--dim,#3D5572)!important;font-style:italic;}',
    '.match-modal-backdrop{position:fixed;inset:0;z-index:1000;background:rgba(7,17,31,.88);',
    'display:flex;align-items:flex-end;justify-content:center;animation:mcFade .2s ease;}',
    '@keyframes mcFade{from{opacity:0}to{opacity:1}}',
    '.match-modal{background:#0E1E35;border:1px solid rgba(0,210,255,.18);border-radius:18px 18px 0 0;',
    'width:100%;max-width:560px;max-height:90vh;overflow-y:auto;padding:20px 16px 36px;',
    'animation:mcSlide .24s ease;position:relative;}',
    '@keyframes mcSlide{from{transform:translateY(36px);opacity:0}to{transform:translateY(0);opacity:1}}',
    '.mm-close{position:absolute;top:14px;right:14px;background:rgba(255,255,255,.07);',
    'border:none;border-radius:50%;width:28px;height:28px;',
    'color:var(--muted,#5F7A9B);font-size:14px;cursor:pointer;',
    'display:flex;align-items:center;justify-content:center;}',
    '.mm-badge{display:inline-block;font-size:10px;font-weight:700;color:var(--ipl-teal);',
    'background:var(--ipl-teal-soft);border-radius:4px;padding:2px 7px;margin-bottom:5px;}',
    '.mm-title{font-size:16px;font-weight:800;color:var(--text,#D8E8F5);margin:0 0 2px;}',
    '.mm-sub{font-size:11px;color:var(--muted,#5F7A9B);margin-bottom:14px;}',
    '.mm-scores{display:flex;gap:10px;margin-bottom:16px;}',
    '.mm-sbox{flex:1;background:rgba(0,0,0,.2);border:1px solid rgba(255,255,255,.06);border-radius:10px;padding:11px 13px;}',
    '.mm-slbl{font-size:9px;font-weight:700;color:var(--muted,#5F7A9B);text-transform:uppercase;letter-spacing:.07em;margin-bottom:3px;}',
    '.mm-sval{font-size:26px;font-weight:900;color:#F5C518;line-height:1;}',
    '.mm-sbox.top .mm-sval{font-size:20px;color:var(--ipl-teal);}',
    '.mm-sname{font-size:12px;font-weight:700;color:var(--text,#D8E8F5);margin-top:2px;}',
    '.mm-xi-lbl{font-size:10px;font-weight:700;color:var(--muted,#5F7A9B);text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;}',
    '.mm-prow{display:flex;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.05);}',
    '.mm-pnum{font-size:11px;color:var(--dim,#3D5572);min-width:16px;text-align:center;}',
    '.mm-pav{width:32px;height:32px;border-radius:50%;background:#1A3050;',
    'display:flex;align-items:center;justify-content:center;',
    'font-size:11px;font-weight:700;color:var(--text,#D8E8F5);flex-shrink:0;}',
    '.mm-pinfo{flex:1;min-width:0;}',
    '.mm-pname{font-size:13px;font-weight:600;color:var(--text,#D8E8F5);display:flex;align-items:center;gap:4px;}',
    '.mm-psub{font-size:10px;color:var(--muted,#5F7A9B);margin-top:1px;}',
    '.mm-ppts{font-size:16px;font-weight:900;color:var(--ipl-teal);flex-shrink:0;text-align:right;}',
    '.mm-ppts.zero{color:var(--dim,#3D5572);font-size:14px;}',
    '.mm-pmult{font-size:10px;color:var(--muted,#5F7A9B);display:block;}',
    '.mm-total{display:flex;justify-content:space-between;align-items:center;',
    'padding:12px 0 0;margin-top:4px;border-top:2px solid rgba(0,210,255,.15);}',
    '.mm-tlbl{font-size:12px;font-weight:700;color:var(--muted,#5F7A9B);text-transform:uppercase;letter-spacing:.06em;}',
    '.mm-tval{font-size:22px;font-weight:900;color:#F5C518;}',
    '.mm-loading{text-align:center;padding:32px 16px;color:var(--muted,#5F7A9B);font-size:13px;}',
  ].join('');
  document.head.appendChild(s);
}

if (document.readyState === 'loading')
  document.addEventListener('DOMContentLoaded', _injectMCStyles);
else
  _injectMCStyles();


// Note: _mcData / _mcLoading globals and the _buildMatchCentreTab stub
// that used to live here were leftover v7.6 scaffolds. mc_hub.js holds
// its own _mcData / _mcLoading inside its IIFE and always overrides
// window._buildMatchCentreTab on load, so the stub was unreachable.
