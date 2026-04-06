/**
 * static/ipl_glue.js — Frontend Integration Layer          Golden File v6
 * =========================================================================
 * Drop this script tag AFTER the app bundle in templates/index.html:
 *
 *   <script src="/static/ipl_glue.js"></script>
 *
 * Wire events:
 *   window.addEventListener("ipl:state-updated",       e => render(e.detail));
 *   window.addEventListener("ipl:leaderboard-updated", e => renderLb(e.detail));
 *   window.addEventListener("ipl:players-updated",     e => setPlayers(e.detail));
 *   window.addEventListener("ipl:error",               e => console.error(e.detail));
 *
 * After writes, dispatch "ipl:saved" to bust the ETag cache:
 *   await window.IplApi.saveNextWeek(name, picks);
 *   window.dispatchEvent(new CustomEvent("ipl:saved"));
 *
 * ── What this file does ────────────────────────────────────────────────────
 *
 * 1. LEADERBOARD NORMALISATION
 *    normaliseLeaderboard() handles all server response shapes.
 *
 * 2. 60-SECOND ETag POLLING
 *    GET /api/poll → { state_etag }. Full fetches fire only on ETag change.
 *    Tab hidden → polling paused. Tab focused → immediate cycle resumes.
 *
 * 3. MAINTENANCE MODE OVERLAY
 *    Any 5xx / network failure schedules overlay after MAINTENANCE_DELAY ms.
 *    On recovery the overlay is removed automatically.
 *
 * 4. window.IplApi
 *    Thin async wrappers over every API endpoint. Includes:
 *      getHistory(name)               → GET  /api/history/<n>
 *      saveNextWeek(name, picks)      → POST /api/save-next-week/<n>
 *      getPlayers()                   → GET  /api/players
 *      getCurrentWeek()               → GET  /api/current-week
 *      rollover(force)                → POST /api/rollover[?force=1]
 *      seedHistory()                  → POST /api/seed-history
 *      resolvePlayer(query, team)     → POST /api/resolve-player  ← NEW v6
 *
 * 5. SEASON / BUDGET CONSTANTS
 *    window.IplConfig.budget    — 100.0 CR (from /api/ping)
 *    window.IplConfig.xi_size   — 11
 *    window.IplConfig.max_weeks — 8
 *
 * 6. MONDAY 14:00 AUTO-ROLLOVER  ← NEW v6
 *    Schedules an automatic /api/rollover call for the next Monday 14:00 UTC.
 *    Fires "ipl:rollover-triggered" event on completion.
 *    Re-schedules itself after each cycle.
 *
 * ── Exported globals ───────────────────────────────────────────────────────
 *   window.IplApi               — API wrapper object
 *   window.IplPolling           — { start(), stop() }
 *   window.IplConfig            — { budget, xi_size, max_weeks }
 *   window.IplRollover          — { scheduleNext(), cancelPending() }
 *   window.normaliseLeaderboard — pure function (exposed for unit tests)
 *
 * CHANGES vs v5:
 *   • resolvePlayer(query, team) → POST /api/resolve-player
 *   • Monday 14:00 UTC auto-rollover scheduler (IplRollover)
 *   • _pollCycle also re-checks current-week after rollover events
 *   • IplApi.rollover() dispatches "ipl:rollover-triggered" on success
 */

(function (window) {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  var POLL_INTERVAL_MS  = 60000;   // 60 seconds
  var MAINTENANCE_DELAY = 1500;    // ms before overlay appears
  var ROLLOVER_HOUR_UTC = 14;      // Monday 14:00 UTC
  var ROLLOVER_MIN_UTC  = 0;


  // ── Module state ──────────────────────────────────────────────────────────
  var _lastStateEtag    = null;
  var _overlayTimer     = null;
  var _overlayVisible   = false;
  var _pollTimer        = null;
  var _rolloverTimer    = null;

  // ── Season/budget config (hydrated from /api/ping) ────────────────────────
  var IplConfig = {
    budget:    100.0,
    xi_size:   11,
    max_weeks: 8,
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

  /**
   * Returns the milliseconds until the next Monday 14:00:00 UTC.
   * If we are past Monday 14:00 UTC this week, targets next week's Monday.
   */
  function _msUntilNextRollover() {
    var now   = new Date();
    var dayMs = 24 * 60 * 60 * 1000;

    // 0=Sun 1=Mon … 6=Sat (UTC)
    var utcDay   = now.getUTCDay();       // 0-6
    var utcH     = now.getUTCHours();
    var utcM     = now.getUTCMinutes();
    var utcS     = now.getUTCSeconds();
    var utcMs    = now.getUTCMilliseconds();

    // Days until next Monday
    var daysUntilMon = (utcDay === 1) ? 0 : (8 - utcDay) % 7;

    // Build the candidate Monday 14:00 UTC
    var candidate = new Date(now);
    candidate.setUTCDate(now.getUTCDate() + daysUntilMon);
    candidate.setUTCHours(ROLLOVER_HOUR_UTC, ROLLOVER_MIN_UTC, 0, 0);

    // If candidate is in the past (or within 5 s), push to next Monday
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
          window.dispatchEvent(new CustomEvent("ipl:rollover-triggered", { detail: data }));
          // Bust ETag so the UI refreshes
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
        // Always schedule the next cycle regardless of outcome
        _scheduleNextRollover();
      });
  }

  function _scheduleNextRollover() {
    if (_rolloverTimer) {
      clearTimeout(_rolloverTimer);
      _rolloverTimer = null;
    }
    var ms = _msUntilNextRollover();
    // Cap at 7 days to handle setTimeout integer overflow
    var capped = Math.min(ms, 7 * 24 * 60 * 60 * 1000);
    console.info(
      "[IplRollover] Next rollover scheduled in " +
      Math.round(capped / 60000) + " min"
    );
    _rolloverTimer = setTimeout(_executeRollover, capped);
  }

  function _cancelRolloverTimer() {
    if (_rolloverTimer) {
      clearTimeout(_rolloverTimer);
      _rolloverTimer = null;
    }
  }

  var IplRollover = {
    scheduleNext:  _scheduleNextRollover,
    cancelPending: _cancelRolloverTimer,
  };


  // ──────────────────────────────────────────────────────────────────────────
  // PUBLIC API  —  window.IplApi
  // ──────────────────────────────────────────────────────────────────────────

  var IplApi = {

    /** GET /api/state — full legacy league state. ETag-aware. */
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

    /** GET /api/leaderboard[?week=N] */
    getLeaderboard: function (weekNo) {
      var url = (weekNo != null) ? ("/api/leaderboard?week=" + weekNo) : "/api/leaderboard";
      return _fetchJson(url).then(normaliseLeaderboard);
    },

    /** GET /api/players → { players, by_id, by_name, ok } */
    getPlayers: function () {
      return _fetchJson("/api/players");
    },

    /** GET /api/current-week → { week_no, max_weeks, ok } */
    getCurrentWeek: function () {
      return _fetchJson("/api/current-week");
    },

    /**
     * GET /api/history/<n>
     * Returns { name, current_week, weeks:[{week_no, is_current, this_week, next_week}], ok }
     */
    getHistory: function (name) {
      return _fetchJson("/api/history/" + encodeURIComponent(name));
    },

    /**
     * POST /api/save-next-week/<n>
     * Saves ONLY the nw_* columns. Never touches the locked this_week.
     * Server validates XI_SIZE=11 and budget ≤ 100.0 CR.
     * Server v8 also resolves name strings → canonical IDs before saving.
     * Rejects with 422 if budget exceeded or wrong player count.
     *
     * @param {string} name
     * @param {{ team: string[], cap: string|null, vc: string|null }} picks
     * @returns Promise<{ ok, week_no, total_cost, resolution_log }|null>
     */
    saveNextWeek: function (name, picks) {
      return _fetchJson("/api/save-next-week/" + encodeURIComponent(name), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(picks),
      });
    },

    /**
     * POST /api/resolve-player  (NEW — v6)
     * Resolves a player name, shorthand, or ID to the canonical Players record.
     *
     * Tier 1: exact ID match       ("r01")
     * Tier 2: exact name + team    ("Virat Kohli" + "RCB")
     * Tier 3: exact name           ("Phil Salt")
     * Tier 4: semantic shorthand   ("VK" → "Virat Kohli")
     * Tier 5: fuzzy token-set      ("V. Kohli" → "Virat Kohli")
     * Tier 6: last-name suffix     ("Pandya" → best candidate)
     *
     * @param {string}      query  Player name, ID, or shorthand
     * @param {string|null} team   Optional team code hint (e.g. "MI", "RCB")
     * @returns Promise<{ ok, input, match_tier, resolved:{id,name,team,role,price} }|null>
     */
    resolvePlayer: function (query, team) {
      return _fetchJson("/api/resolve-player", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, team: team || null }),
      });
    },

    /** GET /api/ping — also hydrates IplConfig */
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

    /** POST /api/state — legacy full merge save */
    saveState: function (payload) {
      return _fetchJson("/api/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },

    /** PUT /api/member/<n> — legacy upsert */
    saveMember: function (name, data) {
      return _fetchJson("/api/member/" + encodeURIComponent(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    },

    /** POST /api/match */
    saveMatch: function (matchObj) {
      return _fetchJson("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(matchObj),
      });
    },

    /**
     * POST /api/rollover[?force=1]
     * @param {boolean} force  true → bypass Monday deadline gate (dev/testing)
     * Returns { ok, rolled, new_week_no, season_complete, reason? }
     * Dispatches "ipl:rollover-triggered" when rolled === true.
     */
    rollover: function (force) {
      var url = force ? "/api/rollover?force=1" : "/api/rollover";
      return _fetchJson(url, { method: "POST" }).then(function (data) {
        if (data && data.season_complete) {
          console.warn("[IplApi] Season complete — all " + IplConfig.max_weeks + " weeks rolled.");
        }
        if (data && data.rolled) {
          window.dispatchEvent(new CustomEvent("ipl:rollover-triggered", { detail: data }));
        }
        return data;
      });
    },

    /** POST /api/seed-history — idempotent W0/W1 seed */
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

    startPolling();

    // Start the Monday 14:00 auto-rollover scheduler
    _scheduleNextRollover();

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stopPolling();
      } else {
        startPolling();
        // Immediately check if a rollover happened while tab was hidden
        _pollCycle();
      }
    });

    window.addEventListener("ipl:saved", function () {
      _lastStateEtag = null;
    });

    // Re-poll after any rollover to pick up new week state
    window.addEventListener("ipl:rollover-triggered", function () {
      _lastStateEtag = null;
      _pollCycle();
    });

    // Signal to the app script (index.html) that IplApi is now available.
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
