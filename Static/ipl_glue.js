/**
 * static/ipl_glue.js — Frontend Integration Layer          Golden File v3
 * =========================================================================
 * Drop this script tag AFTER your existing app bundle in templates/index.html:
 *
 *   <script src="/static/ipl_glue.js"></script>
 *
 * Then wire the events once in your app's initialisation block:
 *
 *   window.addEventListener("ipl:state-updated",       e => render(e.detail));
 *   window.addEventListener("ipl:leaderboard-updated", e => renderLb(e.detail));
 *   window.addEventListener("ipl:error",               e => console.error(e.detail));
 *
 * After writes, signal the glue to invalidate its ETag cache so the next
 * poll triggers a full refresh immediately:
 *
 *   await window.IplApi.saveMember(name, payload);
 *   window.dispatchEvent(new CustomEvent("ipl:saved"));
 *
 * ── What this file does ────────────────────────────────────────────────────
 *
 * 1. LEADERBOARD NORMALISATION
 *    /api/leaderboard now emits both `standings` (new) and `rankings` (legacy).
 *    normaliseLeaderboard() handles either shape, plus any pre-migration cached
 *    response that has only one key, so render() functions work without change.
 *
 * 2. 60-SECOND ETag POLLING
 *    GET /api/poll returns { state_etag } — a single DB meta read.
 *    The glue compares against _lastStateEtag.  Full fetches to /api/state
 *    and /api/leaderboard only fire when the ETag has changed.
 *    Tab hidden → polling paused (visibilitychange).  Tab focused → immediate
 *    poll cycle then resumes at the regular interval.
 *
 * 3. MAINTENANCE MODE OVERLAY
 *    Any 5xx or network failure schedules the overlay after MAINTENANCE_DELAY ms
 *    (avoids flash on transient errors).  On recovery the overlay is removed
 *    automatically without a page reload.
 *
 * 4. window.IplApi
 *    Thin async wrappers over every API endpoint.  Existing render() calls can
 *    migrate incrementally — the events are the primary integration path.
 *
 * ── Exported globals ───────────────────────────────────────────────────────
 *   window.IplApi              — API wrapper object
 *   window.IplPolling          — { start(), stop() }
 *   window.normaliseLeaderboard — pure function (exposed for unit tests)
 */

(function (window) {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  var POLL_INTERVAL_MS  = 60000;   // 60 seconds
  var MAINTENANCE_DELAY = 1500;    // ms before overlay appears (avoids flash)

  // ── Module state ──────────────────────────────────────────────────────────
  var _lastStateEtag  = null;
  var _overlayTimer   = null;
  var _overlayVisible = false;
  var _pollTimer      = null;


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

  /**
   * Accepts any /api/leaderboard response shape and returns a canonical object:
   *
   *   {
   *     rankings  : [ {rank, name, total_pts, matches_counted, mvp} ],  // primary
   *     standings : <same array>,
   *     meta      : { league_avg, top_score, member_count },
   *     league_avg, top_score, member_count,                             // flat
   *     week_no, generated_at
   *   }
   *
   * Handles:
   *   • New server (both `rankings` and `standings` present)
   *   • Legacy server (only `rankings`)
   *   • leaderboard_route.py shape (only `standings` + `meta`)
   *   • Empty / null responses
   */
  function normaliseLeaderboard(raw) {
    if (!raw || typeof raw !== "object") {
      return {
        rankings: [], standings: [],
        meta: { league_avg: 0, top_score: 0, member_count: 0 },
        league_avg: 0, top_score: 0, member_count: 0,
        week_no: null, generated_at: null,
      };
    }

    // Prefer `rankings` (legacy primary), fall back to `standings` (new primary)
    var rows = Array.isArray(raw.rankings)
      ? raw.rankings
      : Array.isArray(raw.standings)
        ? raw.standings
        : [];

    // Benchmark values: prefer flat keys, fall back to meta sub-object
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
      meta:     { league_avg: league_avg, top_score: top_score, member_count: member_count },
      rankings: rows,
      standings: rows,   // same reference — no copy cost
    };
  }


  // ──────────────────────────────────────────────────────────────────────────
  // HTTP HELPERS
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Wraps fetch() with Accept/Content-Type headers and basic error handling.
   * Returns parsed JSON or null (304 Not Modified).
   * Throws an Error with .status set on HTTP errors.
   */
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
            throw err;
          });
        }
        return res.json();
      });
  }


  // ──────────────────────────────────────────────────────────────────────────
  // PUBLIC API  —  window.IplApi
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * Thin async wrappers over every Flask endpoint.
   * All methods return Promises.
   */
  var IplApi = {

    /**
     * GET /api/state — full league state.
     * Sends If-None-Match header; returns null on 304 (nothing changed).
     * Updates _lastStateEtag from ETag response header.
     */
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

    /**
     * GET /api/leaderboard[?week=N] — normalised leaderboard.
     * @param {number|null} weekNo  Pass integer for weekly view, null/undefined for global.
     */
    getLeaderboard: function (weekNo) {
      var url = (weekNo != null) ? ("/api/leaderboard?week=" + weekNo) : "/api/leaderboard";
      return _fetchJson(url).then(normaliseLeaderboard);
    },

    /** GET /api/ping */
    ping: function () {
      return _fetchJson("/api/ping");
    },

    /**
     * POST /api/state — full merge save.
     * After calling this, dispatch "ipl:saved" to bust the ETag cache.
     */
    saveState: function (payload) {
      return _fetchJson("/api/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },

    /**
     * PUT /api/member/<name> — upsert one member's picks.
     * After calling this, dispatch "ipl:saved" to bust the ETag cache.
     *
     * @param {string} name    Display name (max 30 chars)
     * @param {object} data    { this_week:{team,cap,vc}, next_week:{team,cap,vc} }
     *                         or legacy { team:[...], cap:str, vc:str }
     */
    saveMember: function (name, data) {
      return _fetchJson("/api/member/" + encodeURIComponent(name), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    },

    /**
     * POST /api/match — upsert one match + scores.
     */
    saveMatch: function (matchObj) {
      return _fetchJson("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(matchObj),
      });
    },

    /**
     * POST /api/rollover — trigger Monday 14:00 UTC promotion.
     * Idempotent; returns { ok, rolled }.
     */
    rollover: function () {
      return _fetchJson("/api/rollover", { method: "POST" });
    },
  };


  // ──────────────────────────────────────────────────────────────────────────
  // 60-SECOND POLLING LOOP
  // ──────────────────────────────────────────────────────────────────────────

  /**
   * One poll cycle:
   *  1. GET /api/poll  → { state_etag }   (single meta-row read, very cheap)
   *  2. Compare ETag. If unchanged → return (no further fetches).
   *  3. If changed → fire /api/state + /api/leaderboard in parallel.
   *  4. Dispatch ipl:state-updated and ipl:leaderboard-updated custom events.
   *  5. On any 5xx or network failure → schedule maintenance overlay.
   *  6. On recovery (2xx after error) → cancel overlay, refresh.
   */
  function _pollCycle() {
    return _fetchJson("/api/poll")
      .then(function (poll) {
        _cancelOverlay();

        var serverEtag = poll && poll.state_etag;
        if (!serverEtag || serverEtag === _lastStateEtag) return;  // nothing new

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
    if (_pollTimer) return;                    // already running
    _pollCycle();                              // immediate first cycle
    _pollTimer = setInterval(_pollCycle, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
  }


  // ──────────────────────────────────────────────────────────────────────────
  // LIFECYCLE
  // ──────────────────────────────────────────────────────────────────────────

  function _init() {
    startPolling();

    // Pause polling when tab is hidden; resume (with immediate cycle) on focus
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        stopPolling();
      } else {
        startPolling();
      }
    });

    // Any write operation should bust the ETag so the next poll refreshes
    window.addEventListener("ipl:saved", function () {
      _lastStateEtag = null;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _init);
  } else {
    _init();
  }


  // ──────────────────────────────────────────────────────────────────────────
  // EXPORTS
  // ──────────────────────────────────────────────────────────────────────────

  window.IplApi                = IplApi;
  window.IplPolling            = { start: startPolling, stop: stopPolling };
  window.normaliseLeaderboard  = normaliseLeaderboard;   // exposed for unit tests

}(window));
