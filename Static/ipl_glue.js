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
  var SAFE_BOOT_MS      = 5000;

  var _lastStateEtag  = null;
  var _overlayTimer   = null;
  var _overlayVisible = false;
  var _pollTimer      = null;
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
        "background:color-mix(in srgb, var(--paper) 92%, transparent);",
        "display:flex;align-items:center;justify-content:center;",
        "animation:ipl-fade-in .3s ease;",
      "}",
      "@keyframes ipl-fade-in{from{opacity:0}to{opacity:1}}",
      ".ipl-mc{",
        "background:var(--sheet);",
        "border:1px solid color-mix(in srgb, var(--gold-line) 40%, transparent);",
        "border-radius:16px;padding:40px 32px;max-width:380px;",
        "text-align:center;color:var(--text);font-family:sans-serif;",
      "}",
      ".ipl-mc-icon{font-size:52px;margin-bottom:12px;}",
      ".ipl-mc h2{color:var(--gold-ink);font-size:22px;margin:0 0 12px;}",
      ".ipl-mc p{color:var(--muted);font-size:14px;line-height:1.7;margin:0 0 16px;}",
      ".ipl-mc .sub{font-size:12px!important;color:var(--dim)!important;}",
      ".ipl-spin{",
        "width:32px;height:32px;margin:0 auto 20px;",
        "border:3px solid color-mix(in srgb, var(--gold-line) 24%, transparent);",
        "border-top-color:var(--gold-line);border-radius:50%;",
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
    // Attach the bearer token to every same-origin /api/* call when logged in.
    // Reads ignore it; writes now require it (server-side SEC-1 auth). Same-origin
    // only, so the token never leaks to a third party.
    if (!headers["Authorization"]) {
      var t = IplAuth.getToken();
      if (t && url.indexOf("/api/") === 0) {
        headers["Authorization"] = "Bearer " + t;
      }
    }
    var sentToken = !!headers["Authorization"];   // a sign-in went with this request
    // Multi-competition: attach ?comp=<slug> to /api/* calls so reads/writes
    // target the selected competition. Harmless on endpoints that ignore it;
    // empty store => server default (the active competition).
    try {
      var _cmp = (window.IplComp && IplComp.get && IplComp.get()) || "";
      if (_cmp && url.indexOf("/api/") === 0 && url.indexOf("comp=") === -1) {
        url += (url.indexOf("?") === -1 ? "?" : "&") + "comp=" + encodeURIComponent(_cmp);
      }
    } catch (e) {}
    return fetch(url, Object.assign({}, options, { headers: headers }))
      .then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) {
          var err = new Error("HTTP " + res.status); err.status = res.status;
          // Any 401 from our API means the session is missing/expired/revoked.
          // Clear the token so the next bootstrap drops to the login card.
          if (res.status === 401 && url.indexOf("/api/") === 0) {
            IplAuth.clearToken();
            // The server refused a token this member was signed in with: their session is over. Tell the page (it says so and lets them sign in
            // again where they were). A wrong passcode at /api/login is a 401 too, but it is not this.
            if (sentToken && !/^\/api\/(login|register)(\?|$)/.test(url)) {
              try { window.dispatchEvent(new CustomEvent("ipl:session-ended", { detail: { url: url } })); } catch (e) {}
            }
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

  // ── ACTIVE COMPETITION STORE (multi-competition) ───────────────────────────
  // Which competition the UI is viewing. Persisted in localStorage and attached
  // as ?comp=<slug> to every /api/* call by _fetchJson. Empty => server default.
  var COMP_KEY = "ipl_active_comp";
  var IplComp = {
    get:   function ()  { try { return localStorage.getItem(COMP_KEY) || ""; } catch (e) { return ""; } },
    set:   function (s) { try { if (s) { if (localStorage.getItem(COMP_KEY) !== s) _lastStateEtag = null; localStorage.setItem(COMP_KEY, s); } } catch (e) {} },   // another competition: never answer "unchanged" with the previous one's etag
    clear: function ()  { try { localStorage.removeItem(COMP_KEY); } catch (e) {} },
  };

  // ── ROLLOVER ────────────────────────────────────────────────────────────
  // 2026-11-19: the browser used to schedule and send the weekly rollover itself (a timer for Monday 14:00 UTC that called the rollover endpoint for whichever
  // competition was selected). It could move a competition that had not started, and it only ever worked for an admin who happened to have the page open.
  // The rollover now belongs to the server alone; nothing in the browser schedules or sends it.

  // ── SERVER CLOCK ────────────────────────────────────────────────────────
  // Locks are decided by the SERVER's clock, so a countdown must follow it, not the phone's (a phone with a wrong clock showed the wrong time
  // left, and a draft that looked open could be locked). The server sends its time with /api/poll (every minute) and /api/state; the offset
  // is kept here and IplClock.now() applies it. Until the server's time is known it is simply the phone's clock.
  var IplClock = (function () {
    var offset = 0, synced = false;
    var MAX_NETWORK_MS = 5000;    // a slower trip (a sleeping server waking up) says nothing about the time of the reply
    var GOOD_NETWORK_MS = 300;    // a trip this quick is accurate to about 150 ms: the latest such reading always wins
    var KEEP_BEST_MS = 600000;    // a worse reading replaces a better one only once that one is this old
    var bestNetwork = Infinity, bestAt = 0;
    var mono = (typeof performance !== "undefined" && performance.now) ? function () { return performance.now(); } : function () { return Date.now(); };
    // The page loads several things at once, and the browser can hold a request back before it is sent; that delay is all on the way out, so a reading
    // from such a trip can be a second or two off. A reading from a quicker trip is better, so the best one seen is kept.
    function take(off, network) {
      var at = mono();
      if (network > GOOD_NETWORK_MS && network > bestNetwork && at - bestAt < KEEP_BEST_MS) return false;
      offset = off; bestNetwork = network; bestAt = at; synced = true;
      return true;
    }
    return {
      now:      function () { return Date.now() + offset; },
      offsetMs: function () { return offset; },
      isSynced: function () { return synced; },
      // serverIso: the server's time when its reply LEFT. sentAt / receivedAt: the phone's clock when the request left and when the reply arrived.
      // serverRecvIso (optional): the server's time when the request ARRIVED. With both server stamps the time the server spent on the request
      // cancels out (as in NTP); without it the server's stamp is assumed to fall half-way through the round trip.
      sync: function (serverIso, sentAt, receivedAt, serverRecvIso) {
        var t2 = Date.parse(serverIso);
        if (!isFinite(t2)) return false;
        if (typeof sentAt === "number" && typeof receivedAt === "number") {
          if (receivedAt < sentAt) return false;
          var t1 = Date.parse(serverRecvIso), network;
          if (isFinite(t1) && t1 <= t2) {
            network = Math.max(0, (receivedAt - sentAt) - (t2 - t1));          // the network part of the trip, without the server's own time
            if (network > MAX_NETWORK_MS) return false;
            return take(((t1 - sentAt) + (t2 - receivedAt)) / 2, network);
          }
          network = receivedAt - sentAt;
          if (network > MAX_NETWORK_MS) return false;
          return take(t2 - (sentAt + receivedAt) / 2, network);
        }
        return take(t2 - Date.now(), 0);
      },
    };
  })();

  // ── PUBLIC API ──────────────────────────────────────────────────────────

  var IplApi = {

    getState: function () {
      var headers = { "Accept": "application/json" };
      if (_lastStateEtag) headers["If-None-Match"] = _lastStateEtag;
      var url = "/api/state";   // always say WHICH competition (the server would otherwise answer with the default one)
      try { var _c = (window.IplComp && IplComp.get && IplComp.get()) || ""; if (_c) url += "?comp=" + encodeURIComponent(_c); } catch (e) {}
      var t0 = Date.now();
      return fetch(url, { headers: headers }).then(function (res) {
        if (res.status === 304) return null;
        if (!res.ok) { var err = new Error("HTTP " + res.status); err.status = res.status; throw err; }
        var etag = res.headers.get("ETag"); if (etag) _lastStateEtag = etag;
        return res.json().then(function (j) { if (j && j.server_now_utc) IplClock.sync(j.server_now_utc, t0, Date.now(), j.server_recv_utc); return j; });
      });
    },

    getLeaderboard: function (weekNo) {
      var url = (weekNo != null) ? ("/api/leaderboard?week=" + weekNo) : "/api/leaderboard";
      return _fetchJson(url).then(normaliseLeaderboard);
    },

    getPlayers:         function () { return _fetchJson("/api/players"); },
    getCompetitions:    function () { return _fetchJson("/api/competitions"); },
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

    // `week` (optional) = the week the page believes is current: the server answers 409 week_changed instead of writing into the wrong week.
    saveNextWeek: function (name, picks, week) {
      var body = (week == null) ? picks : Object.assign({}, picks, { week: week });
      return _fetchJson("/api/save-next-week/" + encodeURIComponent(name), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    },

    // "Keep this XI": confirm the draft the member already has (nothing about the team changes). Same lock and week rules as a save.
    keepNextWeek: function (name, week) {
      return _fetchJson("/api/keep-xi/" + encodeURIComponent(name), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(week == null ? {} : { week: week }),
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
    adminCompetition: function (body) { return _fetchJson("/api/admin/competition", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); },
    adminResetPasscode: function (targetUsername) {
      return _fetchJson("/api/admin/passcode/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_username: targetUsername }),
      });
    },
    logoutClearToken: function () { IplAuth.clearToken(); },


  };

  // ── 60-SECOND POLLING LOOP ─────────────────────────────────────────────

  function _pollCycle() {
    var c0 = (window.IplComp && IplComp.get && IplComp.get()) || "";
    var same = function () { return ((window.IplComp && IplComp.get && IplComp.get()) || "") === c0; };   // the competition can change while a request is in flight
    var t0 = Date.now();
    return _fetchJson("/api/poll")
      .then(function (poll) {
        if (poll && poll.server_now_utc) IplClock.sync(poll.server_now_utc, t0, Date.now(), poll.server_recv_utc);
        _cancelOverlay();
        if (!same()) return;
        var serverEtag = poll && poll.state_etag;
        if (!serverEtag || serverEtag === _lastStateEtag) return;
        // Also refresh /api/players on ETag change — the scraper adds new
        // ext_* players (Washington Sundar = ext_10945 etc) and without
        // this the frontend catalogue stays stuck with the snapshot from
        // initial page load. resolvePlayer() then falls through to its
        // default and the UI displays the raw ID like "ext_10945" with
        // no name, team or role.
        return Promise.all([
          IplApi.getState(),
          IplApi.getLeaderboard(),
          IplApi.getPlayers().catch(function () { return null; }),
        ])
          .then(function (results) {
            if (!same()) return;
            var state = results[0]; var lb = results[1]; var pl = results[2];
            if (pl)    { window.dispatchEvent(new CustomEvent("ipl:players-updated", { detail: pl })); }
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
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stopPolling(); else { startPolling(); _pollCycle(); }
    });
    window.addEventListener("ipl:saved",             function () { _lastStateEtag = null; });
    window.addEventListener("ipl:rollover-triggered", function () { _lastStateEtag = null; _pollCycle(); });

    _safeBootTimer = setTimeout(function () {
      if (window.Ui && window.Ui.boot) return;   // Ui.boot explains a slow start and only fails after ~75 s
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
  window.IplComp              = IplComp;
  window.IplClock             = IplClock;
  window.IplPolling           = { start: startPolling, stop: stopPolling };
  window.IplConfig            = IplConfig;
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
             + (window.Ui ? Ui.stateHtml('empty', { title: 'No scores yet', message: 'Points appear here after the first match is scored.' })
                          : '<p class="empty">No scores yet \u2014 check back after the first match.</p>') + '</div>';
  }
  h += '<h3 class="section-title">\uD83C\uDFC6 Leaderboard' + (lb.week_no ? " \u2014 Week " + lb.week_no : "") + "</h3>";
  if (typeof _buildPodium === 'function') h += _buildPodium(lb.rankings);
  h += '<p style="color:var(--muted);font-size:12px;margin-bottom:12px">'
     + 'Avg: <strong style="color:var(--text)">' + lb.league_avg + '</strong>'
     + ' &nbsp;\u00B7&nbsp; Top: <strong style="color:var(--gold-ink)">' + lb.top_score + '</strong>'
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
  // Wrap in lb-scroll so the table can swipe horizontally on narrow screens
  // without squishing the per-week columns. Sticky #/Name columns + a
  // right-edge fade hint at hidden content. See .lb-scroll in style.css.
  h += '<div class="lb-scroll"><table class="lb-table lb-table-wide"><caption class="ui-sr-only">League standings by week</caption><thead><tr><th scope="col">#</th><th scope="col">Name</th>';
  allWeeks.forEach(function (wk) { h += '<th style="text-align:right;font-size:11px">W' + wk + '</th>'; });
  h += '<th style="text-align:right" title="Cap \xd72 + VC \xd71.5 applied">Total\u00A0\u2605</th><th>MVP</th></tr></thead><tbody>';
  lb.rankings.forEach(function (row) {
    var isMe = row.name === _username;
    var weekMap = {};
    (row.weekly || []).forEach(function (w) { weekMap[w.week_no] = w.pts; });
    h += '<tr' + (isMe ? ' class="me"' : "") + '>'
       + '<td class="rank">' + row.rank + '</td>'
       + '<td>' + esc(row.name) + (isMe ? ' <span style="color:var(--gold-ink);font-size:11px">(you)</span>' : "") + '</td>';
    allWeeks.forEach(function (wk) {
      var pts = weekMap[wk];
      h += '<td style="text-align:right;font-size:12px;color:' + (pts > 0 ? 'var(--teal-ink)' : 'var(--dim)') + '">'
         + (pts != null ? pts : '\u2014') + '</td>';
    });
    h += '<td class="pts">' + (row.total_pts || 0) + '</td>';
    var mvpName = row.mvp && row.mvp.player_name ? esc(row.mvp.player_name) : null;
    var mvpPts  = row.mvp && row.mvp.pts ? row.mvp.pts : null;
    var mvpCell = mvpName ? mvpName + ' <span style="color:var(--teal-ink);font-size:11px">(' + mvpPts + ')</span>' : '\u2014';
    h += '<td class="mvp">' + mvpCell + '</td></tr>';
  });
  h += '</tbody></table></div>';
  h += '<p style="font-size:10px;color:var(--dim);margin:8px 0 0;text-align:right">'
     + '\u2605 Total = sum of per-match pts with Cap(\xd72) and VC(\xd71.5) applied'
     + ' &nbsp;\u00b7&nbsp; <span class="lb-mobile-hint">swipe \u2194 to see all weeks</span></p>';
  h += '</div>';
  return h;
};

// (The Match Centre CSS used to be injected from here; it now lives in Static/screens.css.)


// Note: _mcData / _mcLoading globals and the _buildMatchCentreTab stub
// that used to live here were leftover v7.6 scaffolds. mc_hub.js holds
// its own _mcData / _mcLoading inside its IIFE and always overrides
// window._buildMatchCentreTab on load, so the stub was unreachable.
