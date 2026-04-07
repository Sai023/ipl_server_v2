/**
 * ipl_glue.js — Frontend Integration Layer                 Golden File v7
 * =========================================================================
 * CHANGES vs v6:
 *   • IplConfig gains current_week (hydrated on init + after every rollover)
 *   • IplApi.rollover() dispatches "ipl:week-changed" with { week_no, max_weeks }
 *     after a successful roll, so any UI panel can update without page refresh.
 *   • IplApi.rollover() dispatches "ipl:season-complete" when the season cap
 *     is reached (season_complete === true).
 *   • _init() calls IplApi.getCurrentWeek() to prime IplConfig.current_week.
 *
 * Wire events:
 *   window.addEventListener("ipl:state-updated",       e => render(e.detail));
 *   window.addEventListener("ipl:leaderboard-updated", e => renderLb(e.detail));
 *   window.addEventListener("ipl:players-updated",     e => setPlayers(e.detail));
 *   window.addEventListener("ipl:rollover-triggered",  e => onRollover(e.detail));
 *   window.addEventListener("ipl:week-changed",        e => onWeekChange(e.detail));  // NEW v7
 *   window.addEventListener("ipl:season-complete",     e => onSeasonEnd(e.detail));   // NEW v7
 *   window.addEventListener("ipl:error",               e => console.error(e.detail));
 *
 * ── Exported globals ───────────────────────────────────────────────────────
 *   window.IplApi               — API wrapper object
 *   window.IplPolling           — { start(), stop() }
 *   window.IplConfig            — { budget, xi_size, max_weeks, current_week }
 *   window.IplRollover          — { scheduleNext(), cancelPending() }
 *   window.normaliseLeaderboard — pure function (exposed for unit tests)
 */

(function (window) {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  var POLL_INTERVAL_MS  = 60000;
  var MAINTENANCE_DELAY = 1500;
  var ROLLOVER_HOUR_UTC = 14;
  var ROLLOVER_MIN_UTC  = 0;

  // ── Module state ──────────────────────────────────────────────────────────
  var _lastStateEtag  = null;
  var _overlayTimer   = null;
  var _overlayVisible = false;
  var _pollTimer      = null;
  var _rolloverTimer  = null;

  // ── Season/budget config (hydrated from /api/ping + /api/current-week) ───
  var IplConfig = {
    budget:       100.0,
    xi_size:      11,
    max_weeks:    8,
    current_week: 1,
  };


  // ──────────────────────────────────────────────────────────────────────────
  // MAINTENANCE OVERLAY
  // ──────────────────────────────────────────────────────────────────────────

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
      '<div class="ipl-mc">' +
        '<div class="ipl-mc-icon">&#x1F3CF;</div>' +
        '<h2>Server Unavailable</h2>' +
        '<p>We\'re having trouble reaching the backend.<br>' +
        'Retrying automatically every 60 seconds&hellip;</p>' +
        '<div class="ipl-spin"></div>' +
        '<p class="sub">If this persists, ask the admin to check the server.</p>' +
      '</div>'
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


  // ──────────────────────────────────────────────────────────────────────────
  // LEADERBOARD NORMALISATION
  // ──────────────────────────────────────────────────────────────────────────

  function normaliseLeaderboard(raw) {
    if (!raw || typeof raw !== "object") {
      return {
        rankings: [], standings: [],
        meta: { league_avg: 0, top_score: 0, member_count: 0 },
        league_avg: 0, top_score: 0, member_count: 0,
        week_no: null, generated_at: null,
      };
    }
    var rows = Array.isArray(raw.rankings)
      ? raw.rankings
      : Array.isArray(raw.standings)
        ? raw.standings
        : [];
    var m            = raw.meta || {};
    var league_avg   = (raw.league_avg   != null) ? raw.league_avg   : (m.league_avg   || 0);
    var top_score    = (raw.top_score    != null) ? raw.top_score    : (m.top_score    || 0);
    var member_count = (raw.member_count != null) ? raw.member_count : (m.member_count || 0);
    return {
      week_no:      raw.week_no      != null ? raw.week_no      : null,
      generated_at: raw.generated_at != null ? raw.generated_at : null,
      league_avg:   league_avg,
      top_score:    top_score,
      member_count: member_count,
      meta:      { league_avg: league_avg, top_score: top_score, member_count: member_count },
      rankings:  rows,
      standings: rows,
    };
  }


  // ──────────────────────────────────────────────────────────────────────────
  // HTTP HELPERS
  // ──────────────────────────────────────────────────────────────────────────

  function _fetchJson(url, options) {
    options = options || {};
    var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    return fetch(url, Object.assign({}, options, { headers: headers }))
      .then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) {
          var err = new Error("HTTP " + res.status);
          err.status = res.status;
          return res.json().catch(function () { return {}; }).then(function (j) {
            err.serverMessage = j.error || j.detail || "";
            err.serverData    = j;
            throw err;
          });
        }
        return res.json();
      });
  }


  // ──────────────────────────────────────────────────────────────────────────
  // MONDAY 14:00 UTC AUTO-ROLLOVER SCHEDULER
  // ──────────────────────────────────────────────────────────────────────────

  function _msUntilNextRollover() {
    var now  = new Date();
    var utcDay = now.getUTCDay();
    var daysUntilMon = (utcDay === 1) ? 0 : (8 - utcDay) % 7;
    var candidate = new Date(now);
    candidate.setUTCDate(now.getUTCDate() + daysUntilMon);
    candidate.setUTCHours(ROLLOVER_HOUR_UTC, ROLLOVER_MIN_UTC, 0, 0);
    if (candidate.getTime() - now.getTime() <= 5000) {
      candidate.setUTCDate(candidate.getUTCDate() + 7);
    }
    return candidate.getTime() - now.getTime();
  }

  function _executeRollover() {
    console.info("[IplRollover] Triggering Monday 14:00 rollover …");
    IplApi.rollover(false)
      .then(function (data) {
        if (data && data.rolled) {
          console.info("[IplRollover] Rollover complete — new week: " + data.new_week_no);
          _lastStateEtag = null;
          _pollCycle();
        } else {
          console.info("[IplRollover] Rollover no-op:", data && data.reason);
        }
      })
      .catch(function (err) {
        console.warn("[IplRollover] Rollover failed:", err.message || err);
      })
      .finally(function () {
        _scheduleNextRollover();
      });
  }

  function _scheduleNextRollover() {
    if (_rolloverTimer) { clearTimeout(_rolloverTimer); _rolloverTimer = null; }
    var ms     = _msUntilNextRollover();
    var capped = Math.min(ms, 7 * 24 * 60 * 60 * 1000);
    console.info("[IplRollover] Next rollover scheduled in " + Math.round(capped / 60000) + " min");
    _rolloverTimer = setTimeout(_executeRollover, capped);
  }

  function _cancelRolloverTimer() {
    if (_rolloverTimer) { clearTimeout(_rolloverTimer); _rolloverTimer = null; }
  }

  var IplRollover = {
    scheduleNext:  _scheduleNextRollover,
    cancelPending: _cancelRolloverTimer,
  };


  // ──────────────────────────────────────────────────────────────────────────
  // PUBLIC API  —  window.IplApi
  // ──────────────────────────────────────────────────────────────────────────

  var IplApi = {

    getState: function () {
      var headers = { "Accept": "application/json" };
      if (_lastStateEtag) headers["If-None-Match"] = _lastStateEtag;
      return fetch("/api/state", { headers: headers }).then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) {
          var err = new Error("HTTP " + res.status);
          err.status = res.status;
          throw err;
        }
        var etag = res.headers.get("ETag");
        if (etag) _lastStateEtag = etag;
        return res.json();
      });
    },

    getLeaderboard: function (weekNo) {
      var url = (weekNo != null) ? ("/api/leaderboard?week=" + weekNo) : "/api/leaderboard";
      return _fetchJson(url).then(normaliseLeaderboard);
    },

    getPlayers: function () {
      return _fetchJson("/api/players");
    },

    getCurrentWeek: function () {
      return _fetchJson("/api/current-week");
    },

    getHistory: function (name) {
      return _fetchJson("/api/history/" + encodeURIComponent(name));
    },

    saveNextWeek: function (name, picks) {
      return _fetchJson("/api/save-next-week/" + encodeURIComponent(name), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(picks),
      });
    },

    resolvePlayer: function (query, team) {
      return _fetchJson("/api/resolve-player", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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

    saveState: function (payload) {
      return _fetchJson("/api/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },

    saveMember: function (name, data) {
      return _fetchJson("/api/member/" + encodeURIComponent(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    },

    saveMatch: function (matchObj) {
      return _fetchJson("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(matchObj),
      });
    },

    /**
     * POST /api/rollover[?force=1]
     *
     * On success (rolled === true):
     *   1. Fetches /api/current-week and updates IplConfig.current_week.
     *   2. Dispatches "ipl:week-changed"   { week_no, max_weeks }
     *   3. Dispatches "ipl:rollover-triggered" { ...server response }
     *   4. Busts ETag so next poll fetches fresh state + leaderboard.
     *
     * On season_complete === true:
     *   Dispatches "ipl:season-complete" in addition to the above.
     */
    rollover: function (force) {
      var url = force ? "/api/rollover?force=1" : "/api/rollover";
      return _fetchJson(url, { method: "POST" }).then(function (data) {
        if (!data) return data;

        if (data.season_complete) {
          console.warn("[IplApi] Season complete — all " + IplConfig.max_weeks + " weeks rolled.");
          window.dispatchEvent(new CustomEvent("ipl:season-complete", { detail: data }));
        }

        if (data.rolled) {
          // Re-fetch current week from server and update IplConfig
          _fetchJson("/api/current-week").then(function (wk) {
            if (wk && wk.week_no != null) {
              IplConfig.current_week = wk.week_no;
              window.dispatchEvent(new CustomEvent("ipl:week-changed", {
                detail: { week_no: wk.week_no, max_weeks: wk.max_weeks }
              }));
            }
          }).catch(function () {});

          // Bust ETag → next poll cycle will pull fresh state + leaderboard
          _lastStateEtag = null;
          window.dispatchEvent(new CustomEvent("ipl:rollover-triggered", { detail: data }));
        }

        return data;
      });
    },

    seedHistory: function () {
      return _fetchJson("/api/seed-history", { method: "POST" });
    },
  };


  // ──────────────────────────────────────────────────────────────────────────
  // 60-SECOND POLLING LOOP
  // ──────────────────────────────────────────────────────────────────────────

  function _pollCycle() {
    return _fetchJson("/api/poll")
      .then(function (poll) {
        _cancelOverlay();
        var serverEtag = poll && poll.state_etag;
        if (!serverEtag || serverEtag === _lastStateEtag) return;

        return Promise.all([
          IplApi.getState(),
          IplApi.getLeaderboard(),
        ]).then(function (results) {
          var state = results[0];
          var lb    = results[1];
          if (state) {
            _lastStateEtag = state._saved || serverEtag;
            window.dispatchEvent(new CustomEvent("ipl:state-updated", { detail: state }));
          }
          if (lb) {
            window.dispatchEvent(new CustomEvent("ipl:leaderboard-updated", { detail: lb }));
          }
        });
      })
      .catch(function (err) {
        window.dispatchEvent(new CustomEvent("ipl:error", { detail: err }));
        if ((err.status && err.status >= 500) || !navigator.onLine) {
          _scheduleOverlay();
        }
      });
  }

  function startPolling() {
    if (_pollTimer) return;
    _pollCycle();
    _pollTimer = setInterval(_pollCycle, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }


  // ──────────────────────────────────────────────────────────────────────────
  // LIFECYCLE
  // ──────────────────────────────────────────────────────────────────────────

  function _init() {
    // Hydrate IplConfig from server
    IplApi.ping().catch(function () {});

    // Prime current_week synchronously for components that read IplConfig early
    IplApi.getCurrentWeek().then(function (wk) {
      if (wk && wk.week_no != null) {
        IplConfig.current_week = wk.week_no;
      }
    }).catch(function () {});

    startPolling();
    _scheduleNextRollover();

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stopPolling();
      } else {
        startPolling();
        _pollCycle();
      }
    });

    window.addEventListener("ipl:saved", function () {
      _lastStateEtag = null;
    });

    window.addEventListener("ipl:rollover-triggered", function () {
      _lastStateEtag = null;
      _pollCycle();
    });

    window.dispatchEvent(new CustomEvent("ipl:ready"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }


  // ──────────────────────────────────────────────────────────────────────────
  // EXPORTS
  // ──────────────────────────────────────────────────────────────────────────

  window.IplApi               = IplApi;
  window.IplPolling           = { start: startPolling, stop: stopPolling };
  window.IplConfig            = IplConfig;
  window.IplRollover          = IplRollover;
  window.normaliseLeaderboard = normaliseLeaderboard;

}(window));
