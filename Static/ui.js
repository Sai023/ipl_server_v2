/* BCL Fantasy - shared UI layer v1.0  (window.Ui)
   One consistent way to talk to the user:
     Ui.toast(msg, {type, duration, action})   Ui.success/error/warn/info(msg)   Ui.fail(err, prefix)
     Ui.confirm({title,message,confirmText,danger})  -> Promise<boolean>
     Ui.prompt({title,message,label,validate})        -> Promise<string|null>
     Ui.notice({title,message})                       -> Promise<void>
     Ui.errorMessage(err, fallback)  plain-language text for any failed request (uses the server's machine `reason` when we have a sentence for it; an unexpected
                                     server failure also shows its short request id as "(ref ...)")
     Ui.errorFor(reason, fallback) / Ui.errorReason(err) / Ui.errorRef(err)  the sentence for a server `reason`, the reason carried by a failed request, and its request id
     Ui.stateHtml(kind, {title,message,retry})  loading | empty | error | offline cards
     Ui.connection  one slim banner for any trouble reaching the server (health from every /api/ call, quiet probe, backoff, Retry now; Phase 1.7)
     Ui.boot  staged loading messages for a slow first load (server waking up), with a real failure screen + Retry
   Replaces every browser alert()/confirm()/prompt() and the bare red error banner. */
(function () {
  "use strict";
  var Ui = window.Ui = {};

  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  Ui.esc = esc;

  /* ---------- plain-language error text ---------- */
  /* The server tags many errors with a machine `reason`. These are the sentences we show for them (the client owns the wording);
     a reason that is not listed here (or no reason at all) falls back to the server's own readable text. */
  var REASON_COPY = {
    auth_required:   "You're signed out. Sign in to continue.",
    session_ended:   "You've been signed out. Sign in again to continue.",
    admin_only:      "Only an admin can do that.",
    not_your_team:   "You can only change your own team.",
    rate_limited:    "Too many tries. Wait a minute and try again.",
    locked:          "Picks are locked right now. Your last saved XI is the one that counts.",
    name_taken:      "Someone already has that name. Try another.",
    bad_credentials: "Wrong name or passcode.",
    not_found:       "That could not be found.",
    too_large:       "That is too large to send.",
    bad_request:     "That request was not understood. Please try again.",
    google_not_configured: "Google sign-in isn't available right now.",
    google_unreachable:    "Couldn't reach Google. Try again in a moment.",
    token_invalid:         "Google sign-in couldn't be verified. Please try again.",
    token_expired:         "That took too long. Please try again.",
    nonce_invalid:         "That sign-in expired. Please try again.",
    email_unverified:      "Google says that email address isn't verified.",
    google_account_in_use: "That Google account already belongs to another member.",
    already_linked:        "You already have a Google account linked. Ask the admin to unlink it first.",
    pin_retired:           "This account signs in with Google.",
    claim_expired:         "That sign-in expired. Tap Continue with Google to start again.",
    claim_locked:          "Too many wrong passcodes. Try again in about 10 minutes.",
    member_taken:          "That member already has a Google account linked.",
    temp_passcode:         "That member has a temporary passcode. Sign in with it first, choose your own, then switch to Google.",
    register_closed:       "New members join with Google. Tap Continue with Google.",
    joining_closed:        "New members can't join right now. Ask the admin.",
    members_full:          "The league is full.",
    server_error:    "The server had a problem. Please try again in a moment."
  };
  Ui.errorFor = function (reason, fallback) { return Object.prototype.hasOwnProperty.call(REASON_COPY, reason) ? REASON_COPY[reason] : (fallback || ""); };
  Ui.errorReason = function (err) { var d = err && typeof err === "object" ? err.serverData : null; return (d && typeof d.reason === "string") ? d.reason : ""; };
  /* An unexpected server failure carries a short request id (the same id is in the server log next to the real error). Showing it as "(ref 3fa9c2d1)" lets a
     member who sends a screenshot give us the exact failure. Only a plain short id is shown, never anything else the server might have put there. */
  Ui.errorRef = function (err) { var d = err && typeof err === "object" ? err.serverData : null, id = d && d.request_id; return (typeof id === "string" && /^[A-Za-z0-9]{4,16}$/.test(id)) ? " (ref " + id + ")" : ""; };
  Ui.errorMessage = function (err, fallback) {
    fallback = fallback || "Something went wrong. Please try again.";
    if (!err) return fallback;
    if (typeof err === "string") return err;
    var byReason = Ui.errorFor(Ui.errorReason(err));
    if (byReason) return byReason + Ui.errorRef(err);              // a reason we have a sentence for
    if (err.serverMessage) return err.serverMessage + Ui.errorRef(err);   // otherwise the server's own readable explanation wins
    var s = err.status;
    if (s === 401) return "Your session has expired. Please sign in again.";
    if (s === 403) return "You don't have permission to do that.";
    if (s === 404) return "That could not be found.";
    if (s === 409) return "That conflicts with the current data. Refresh and try again.";
    if (s === 413) return "That file is too large.";
    if (s === 423) return "Changes are locked right now.";
    if (s === 429) return "Too many requests. Please wait a moment and try again.";
    if (s >= 500)  return "The server had a problem. Please try again in a moment.";
    if (s >= 400)  return fallback;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return "You're offline. Check your connection and try again.";
    if (err.name === "TypeError" || /failed to fetch|networkerror|load failed|network/i.test(err.message || "")) return "Can't reach the server. Check your connection and try again.";
    if (err.message && !/^HTTP \d+$/.test(err.message)) return err.message;
    return fallback;
  };

  /* ---------- toasts ---------- */
  var ICONS = { success: "✓", error: "⚠", warn: "⚠", info: "ℹ" };
  var host = null;
  function getHost() {
    if (host && document.body.contains(host)) return host;
    host = document.createElement("div");
    host.id = "ui-toasts";
    host.setAttribute("aria-live", "polite");
    host.setAttribute("aria-relevant", "additions");
    document.body.appendChild(host);
    return host;
  }
  Ui.toast = function (msg, opts) {
    opts = opts || {};
    var type = opts.type || "info";
    var h = getHost();
    for (var i = 0; i < h.children.length; i++) { if (h.children[i].getAttribute("data-msg") === msg) return h.children[i]; }  // no duplicates
    while (h.children.length >= 3) h.removeChild(h.firstChild);
    var t = document.createElement("div");
    t.className = "ui-toast ui-toast-" + type;
    t.setAttribute("role", type === "error" ? "alert" : "status");
    t.setAttribute("data-msg", msg);
    t.innerHTML = '<span class="ui-ico" aria-hidden="true">' + ICONS[type] + '</span><span class="ui-msg">' + esc(msg) + "</span>";
    function close() { if (!t.parentNode) return; t.classList.add("out"); setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 200); }
    if (opts.action && opts.action.label) {
      var a = document.createElement("button"); a.type = "button"; a.className = "ui-act"; a.textContent = opts.action.label;
      a.onclick = function () { close(); if (opts.action.onClick) opts.action.onClick(); };
      t.appendChild(a);
    }
    var x = document.createElement("button"); x.type = "button"; x.className = "ui-x"; x.setAttribute("aria-label", "Dismiss"); x.innerHTML = "&times;"; x.onclick = close;
    t.appendChild(x);
    h.appendChild(t);
    var ms = opts.duration != null ? opts.duration : (type === "error" ? 8000 : 4500);
    if (ms > 0) setTimeout(close, ms);
    return t;
  };
  ["success", "error", "warn", "info"].forEach(function (k) { Ui[k] = function (m, o) { return Ui.toast(m, Object.assign({ type: k }, o || {})); }; });
  Ui.fail = function (err, prefix) {
    var m = Ui.errorMessage(err);
    return Ui.toast(prefix ? prefix + ": " + m : m, { type: "error" });
  };

  /* ---------- dialogs (focus-trapped, Esc to cancel, restores focus) ---------- */
  function openDialog(o) {
    return new Promise(function (resolve) {
      var prev = document.activeElement, id = "ui-dlg-" + Date.now();
      var bd = document.createElement("div"); bd.className = "ui-backdrop";
      var withInput = o.input === true;
      bd.innerHTML =
        '<div class="ui-dialog" role="dialog" aria-modal="true" aria-labelledby="' + id + '-t"' + (o.message ? ' aria-describedby="' + id + '-b"' : "") + ">" +
        '<h2 id="' + id + '-t">' + esc(o.title || "Are you sure?") + "</h2>" +
        (o.message ? '<div class="ui-body" id="' + id + '-b">' + esc(o.message) + "</div>" : "") +
        (withInput ? '<label for="' + id + '-i">' + esc(o.label || "") + '</label><input type="text" id="' + id + '-i" autocomplete="off" placeholder="' + esc(o.placeholder || "") + '"><div class="ui-err" role="alert"></div>' : "") +
        '<div class="ui-actions">' + (o.cancelText === false ? "" : '<button type="button" class="btn btn-ghost" data-r="cancel">' + esc(o.cancelText || "Cancel") + "</button>") +
        '<button type="button" class="btn ' + (o.danger ? "btn-danger" : "btn-teal") + '" data-r="ok">' + esc(o.confirmText || "OK") + "</button></div></div>";
      document.body.appendChild(bd);
      var dlg = bd.firstChild, input = withInput ? dlg.querySelector("input") : null, err = dlg.querySelector(".ui-err");
      function done(val) { document.removeEventListener("keydown", onKey, true); if (bd.parentNode) bd.parentNode.removeChild(bd); try { if (prev && prev.focus) prev.focus(); } catch (e) {} resolve(val); }
      function ok() {
        if (!withInput) return done(true);
        var v = input.value.trim(), bad = o.validate ? o.validate(v) : (v ? "" : "Please enter a value.");
        if (bad) { err.textContent = bad; input.focus(); return; }
        done(v);
      }
      function cancel() { done(withInput ? null : false); }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancel(); }
        else if (e.key === "Enter" && withInput && e.target === input) { e.preventDefault(); ok(); }
        else if (e.key === "Tab") {
          var f = dlg.querySelectorAll("button,input"); if (!f.length) return;
          var first = f[0], last = f[f.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
      document.addEventListener("keydown", onKey, true);
      bd.addEventListener("mousedown", function (e) { if (e.target === bd && !o.persistent) cancel(); });
      dlg.querySelector('[data-r="ok"]').onclick = ok;
      var c = dlg.querySelector('[data-r="cancel"]'); if (c) c.onclick = cancel;
      (input || (o.danger && c ? c : dlg.querySelector('[data-r="ok"]'))).focus();   // destructive: start on Cancel
    });
  }
  Ui.confirm = function (o) { return openDialog(Object.assign({}, o)); };
  Ui.prompt = function (o) { return openDialog(Object.assign({}, o, { input: true })); };
  Ui.notice = function (o) { return openDialog(Object.assign({ cancelText: false }, o)).then(function () {}); };

  /* ---------- icons (inline SVG, inherit colour) ---------- */
  var ICON_PATHS = {
    matches: '<rect x="3" y="4" width="18" height="17" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/><path d="m9 15 2 2 4-4"/>',
    team: '<path d="M12 3 4 6v5c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V6l-8-3Z"/><path d="m9 12 2 2 4-4"/>',
    pick: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    table: '<path d="M8 21h8M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0V4Z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>',
    members: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 20a6.5 6.5 0 0 0-3-5.4"/>',
    admin: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5v14Z"/><path d="M20 17v4H6.5A2.5 2.5 0 0 1 4 19.5"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    down: '<path d="m6 9 6 6 6-6"/>', right: '<path d="m9 6 6 6-6 6"/>', back: '<path d="m15 6-6 6 6 6"/>', plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="m5 12 5 5 9-10"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    tools: '<path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.5 2.5-2.4-.6-.6-2.4Z"/>', search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
  };
  Ui.icon = function (name, size) {
    var n = size || 20;
    return '<svg class="ui-icon" width="' + n + '" height="' + n + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + (ICON_PATHS[name] || "") + "</svg>";
  };

  /* ---------- sheet / menu: bottom sheet on phones, dropdown card on desktop ---------- */
  Ui.sheet = function (o) {
    var prev = document.activeElement;
    var bd = document.createElement("div"); bd.className = "ui-backdrop ui-sheet-bd";
    /* items: {label, hint, icon, danger, checked, divider, onClick}  plus, for a radio choice: {radio:true, desc, badge}
       (a radio item shows a round marker, a second line of description and an optional C / VC style badge) */
    var html = '<div class="ui-sheet" role="dialog" aria-modal="true" aria-label="' + esc(o.title || "Menu") + '">' +
      '<div class="ui-sheet-grip" aria-hidden="true"></div>' + (o.title ? '<h2 class="ui-sheet-title' + (o.subtitle ? " has-sub" : "") + '">' + esc(o.title) + "</h2>" : "") +
      (o.subtitle ? '<p class="ui-sheet-sub">' + esc(o.subtitle) + "</p>" : "") + '<div class="ui-sheet-list"' + (o.items && o.items.some(function (x) { return x.radio; }) ? ' role="menu"' : "") + ">";
    (o.items || []).forEach(function (it, i) {
      if (it.divider) { html += '<div class="ui-sheet-div"></div>'; return; }
      var rad = it.radio ? ' role="menuitemradio" aria-checked="' + (it.checked ? "true" : "false") + '"' : "";
      html += '<button type="button" class="ui-sheet-item' + (it.danger ? " danger" : "") + (it.radio ? " radio" + (it.checked ? " on" : "") : "") + '" data-i="' + i + '"' + rad + ">" +
        (it.radio ? '<span class="rd" aria-hidden="true"></span>' : (it.icon ? Ui.icon(it.icon, 20) : "")) +
        '<span class="tx"><span class="t">' + esc(it.label) + "</span>" + (it.desc ? '<span class="d">' + esc(it.desc) + "</span>" : "") + "</span>" +
        (it.badge ? '<span class="bdg bdg-' + esc(String(it.badge).toLowerCase()) + '" aria-hidden="true">' + esc(it.badge) + "</span>" : "") +
        (it.hint ? '<span class="h">' + esc(it.hint) + "</span>" : "") +
        (!it.radio && it.checked ? '<span class="ck" aria-label="current">' + Ui.icon("check", 18) + "</span>" : "") + "</button>";
    });
    bd.innerHTML = html + "</div></div>";
    document.body.appendChild(bd);
    function close() { document.removeEventListener("keydown", onKey, true); if (bd.parentNode) bd.parentNode.removeChild(bd); try { if (prev && prev.focus) prev.focus(); } catch (e) {} }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {         // menu keys: move between the choices
        var items = [].slice.call(bd.querySelectorAll(".ui-sheet-item")), at = items.indexOf(document.activeElement);
        if (!items.length) return; e.preventDefault();
        items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length].focus();
      }
      else if (e.key === "Tab") {
        var f = bd.querySelectorAll("button"); if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    }
    document.addEventListener("keydown", onKey, true);
    bd.addEventListener("mousedown", function (e) { if (e.target === bd) close(); });
    bd.querySelectorAll(".ui-sheet-item").forEach(function (b) {
      b.onclick = function () { var it = o.items[+b.getAttribute("data-i")]; close(); if (it && it.onClick) it.onClick(); };
    });
    var first = bd.querySelector(".ui-sheet-item.on") || bd.querySelector(".ui-sheet-item"); if (first) first.focus();   // start on the current choice
    return close;
  };

  /* ---------- blocking sheet: something that must be answered before the app can go on (e.g. "You've been signed out") ----------
     Ui.sheetBlocking({title, message, primary:{label, onClick, keepOpen}, secondary:{label, onClick, keepOpen}}) -> {close, el}
     No close button; Escape and a tap outside do nothing; Tab stays inside; announced as an alert; focus starts on the primary button.
     A button closes the sheet before running its onClick unless keepOpen is set. */
  Ui.sheetBlocking = function (o) {
    var prev = document.activeElement, id = "ui-blk-" + Date.now();
    var bd = document.createElement("div"); bd.className = "ui-backdrop ui-sheet-bd ui-sheet-blocking";
    bd.innerHTML =
      '<div class="ui-sheet" role="alertdialog" aria-modal="true" aria-labelledby="' + id + '-t"' + (o.message ? ' aria-describedby="' + id + '-b"' : "") + ">" +
      '<div class="ui-sheet-grip" aria-hidden="true"></div>' +
      '<h2 class="ui-sheet-title has-sub" id="' + id + '-t">' + esc(o.title) + "</h2>" +
      (o.message ? '<p class="ui-sheet-sub" id="' + id + '-b">' + esc(o.message) + "</p>" : "") +
      '<div class="ui-sheet-actions"><button type="button" class="btn btn-teal" data-r="primary">' + esc(o.primary.label) + "</button>" +
      (o.secondary ? '<button type="button" class="btn btn-ghost" data-r="secondary">' + esc(o.secondary.label) + "</button>" : "") + "</div></div>";
    document.body.appendChild(bd);
    function close() { document.removeEventListener("keydown", onKey, true); if (bd.parentNode) bd.parentNode.removeChild(bd); try { if (prev && prev.focus) prev.focus(); } catch (e) {} }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); }       // it cannot be dismissed: it has to be answered
      else if (e.key === "Tab") {
        var f = bd.querySelectorAll("button"); if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    }
    document.addEventListener("keydown", onKey, true);
    function wire(sel, spec) { var b = bd.querySelector(sel); if (!b || !spec) return; b.onclick = function () { if (!spec.keepOpen) close(); if (spec.onClick) spec.onClick(); }; }
    wire('[data-r="primary"]', o.primary); wire('[data-r="secondary"]', o.secondary);
    var first = bd.querySelector('[data-r="primary"]'); if (first) first.focus();
    return { close: close, el: bd };
  };

  /* ---------- accordion state: <details> sections remember what the user opened across re-renders ---------- */
  var _acc = {};
  Ui.accOpen = function (id, def) { return Object.prototype.hasOwnProperty.call(_acc, id) ? _acc[id] : !!def; };
  Ui.accToggle = function (el) { var id = el.getAttribute("data-acc"), def = el.getAttribute("data-def") === "1"; if (el.open === def) delete _acc[id]; else _acc[id] = el.open; };
  Ui.accAll = function (selector, open) { document.querySelectorAll(selector).forEach(function (d) { d.open = open; Ui.accToggle(d); }); };
  Ui.accClear = function (prefix) { Object.keys(_acc).forEach(function (k) { if (k.indexOf(prefix) === 0) delete _acc[k]; }); };   // forget what was opened under a prefix (e.g. when the competition changes)

  /* ---------- status cards ---------- */
  Ui.stateHtml = function (kind, o) {
    o = o || {};
    var ico = { loading: "", empty: "🏏", error: "⚠", offline: "📵" }[kind] || "";
    var h = '<div class="ui-state ui-state-' + kind + '" role="' + (kind === "error" ? "alert" : "status") + '">';
    h += kind === "loading" ? '<div class="ui-spinner" aria-hidden="true"></div>'
      : (kind === "empty" ? '<img class="ui-state-mark" src="/static/brand/mark-paper.svg" width="84" height="84" alt="" aria-hidden="true">' : '<div class="ui-state-ico" aria-hidden="true">' + ico + "</div>");
    if (o.title) h += "<h3>" + esc(o.title) + "</h3>";
    if (o.message) h += "<p>" + esc(o.message) + "</p>";
    if (o.retry) h += '<button type="button" class="btn btn-teal btn-sm" onclick="' + o.retry + '">' + esc(o.retryLabel || "Try again") + "</button>";
    return h + "</div>";
  };

  /* ---------- connection health: ONE slim banner for every kind of trouble reaching the server (2026-11-21, Phase 1.7) ----------
     Evidence comes from every /api/ call (Ui.connection.watch wraps fetch once): an answer below 500 means the server is fine; a 5xx, or no answer, is trouble.
     One failure shows nothing: a probe of /api/health follows 3 s later and only a second failure raises the banner, so a single blip never flashes.
     While in trouble the probe backs off (3, 5, 10, 20, 40, then every 60 s, paused while the tab is hidden); the first good answer clears the banner and fires
     "ipl:recovered" so the page refreshes at once. The banner sits in the flow ABOVE the app (never over the app bar) and blocks nothing: what happened, what is safe,
     what to do (Retry now). Offline outranks server trouble, which outranks "can't confirm your sign-in". */
  var PROBE_DELAYS = [3000, 5000, 10000, 20000, 40000, 60000];
  var SEVERE_AFTER = 90000;
  var health = { fails: 0, since: 0, lastOk: 0, probes: 0, timer: null, ticker: null, shownServer: false, offline: false, signin: "" };
  var rawFetch = null;
  var conn = null;
  function nowMs() { return Date.now(); }
  function relAgo(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    if (s < 45) return "just now";
    var m = Math.round(s / 60);
    if (m < 60) return (m < 2 ? 1 : m) + " min ago";
    var h = Math.round(m / 60);
    return h + (h === 1 ? " hour ago" : " hours ago");
  }
  function loaderVisible() {
    var el = document.getElementById("app-loading");
    return !!(el && el.classList && !el.classList.contains("hidden"));   // the boot screen already explains a slow start
  }
  function getConn() {
    if (conn && conn.parentNode) return conn;
    conn = document.createElement("div"); conn.id = "ui-conn"; conn.setAttribute("role", "status"); conn.setAttribute("aria-live", "polite");
    document.body.insertBefore(conn, document.body.firstChild);
    return conn;
  }
  // What the banner should say right now, or null for nothing.
  function compose() {
    var t = nowMs(), upd = health.lastOk ? " (updated " + relAgo(t - health.lastOk) + ")" : "";
    if (health.offline) {
      return loaderVisible()    // before the first screen there is no data to show yet; say what will happen instead
        ? { kind: "offline", msg: "You're offline. We'll load your league as soon as you're back.", ago: "" }
        : { kind: "offline", msg: "You're offline. Showing the last data we have.", ago: upd };
    }
    if (health.fails >= 2 && !loaderVisible()) {
      return (t - health.since > SEVERE_AFTER)
        ? { kind: "severe", msg: "Still can't reach the server. Showing the last data we have. Your picks are kept on this phone.", ago: upd }
        : { kind: "server", msg: "The server is having a moment. Retrying…", ago: "" };
    }
    if (health.signin && !loaderVisible()) return { kind: "signin", msg: health.signin, ago: "" };
    return null;
  }
  function paint() {
    var c = compose();
    if (!c) {
      if (conn) { conn.className = ""; conn.setAttribute("data-msg", ""); }   // a banner that comes back later is rebuilt, so it is announced again
      if (health.ticker) { clearInterval(health.ticker); health.ticker = null; }
      return;
    }
    var el = getConn();
    if (el.getAttribute("data-msg") !== c.msg) {          // a changed message is announced; the little "updated ... ago" text is not (aria-hidden)
      el.setAttribute("data-msg", c.msg);
      el.innerHTML = '<span class="msg">' + esc(c.msg) + '</span><span class="ago" aria-hidden="true"></span>';
      var b = document.createElement("button"); b.type = "button"; b.textContent = "Retry now";
      b.onclick = function () { Ui.connection.retryNow(); };
      el.appendChild(b);
    }
    el.className = "show" + ((c.kind === "offline" || c.kind === "severe") ? " offline" : "");
    var ag = el.querySelector(".ago"); if (ag) ag.textContent = c.ago ? " " + c.ago : "";
    if (c.kind === "server" || c.kind === "severe") health.shownServer = true;
    if (!health.ticker) health.ticker = setInterval(paint, 30000);   // the time since the last good answer, and the move to the longer wording, follow the clock
  }
  function probe() {
    health.probes++;
    var f = rawFetch || (typeof window.fetch === "function" ? window.fetch : null);
    if (!f) return;
    f.call(window, "/api/health", { cache: "no-store", headers: { "Accept": "application/json" } })   // one SELECT 1 on the server (/api/ping costs five statements)
      .then(function (r) { Ui.connection.report(r.status); }, function () { Ui.connection.report(0); });
  }
  function scheduleProbe() {
    if (health.timer || health.fails === 0 || health.offline) return;      // offline: wait for the online event instead of knocking on a closed door
    var base = PROBE_DELAYS[Math.min(health.probes, PROBE_DELAYS.length - 1)];
    health.timer = setTimeout(function () {
      health.timer = null;
      if (document.hidden) return;                                          // not looking: no point (visibilitychange probes as soon as they are)
      probe();
    }, Math.round(base * (0.8 + 0.4 * Math.random())));
  }
  function markOk() {
    health.lastOk = nowMs();
    if (health.offline) { health.offline = false; paint(); }   // an answer proves the network works: never stay "offline" on a stale flag (a missed "online" event)
    if (health.fails === 0) return;
    var showed = health.shownServer;
    health.fails = 0; health.since = 0; health.probes = 0; health.shownServer = false;
    if (health.timer) { clearTimeout(health.timer); health.timer = null; }
    paint();
    if (showed) Ui.success("Connected again.", { duration: 2500 });
    try { window.dispatchEvent(new CustomEvent("ipl:recovered")); } catch (e) {}   // the page refreshes what it missed
  }
  Ui.connection = {
    // Health evidence: the HTTP status of an /api/ answer, or 0 / null when the request never got one.
    report: function (status) {
      if (!status || status >= 500) {
        if (health.offline) return;                                         // failures while the phone is offline say nothing about the server
        health.fails++;
        if (health.fails === 1) { health.since = nowMs(); health.probes = 0; }
        paint(); scheduleProbe();
      } else markOk();
    },
    // A named reason that is not about the network (today only "signin": a member who could not be checked stays signed in). "" clears it.
    reason: function (name, msg) { if (name === "signin") { health.signin = msg || ""; paint(); } },
    // Check right now (the Retry now button); the page refetches through "ipl:retry".
    retryNow: function () {
      if (health.timer) { clearTimeout(health.timer); health.timer = null; }
      probe();
      try { window.dispatchEvent(new CustomEvent("ipl:retry")); } catch (e) {}
    },
    // Wrap fetch once so every same-origin /api/ call is evidence. The caller still gets the very same promise (results and failures unchanged).
    watch: function () {
      if (rawFetch || typeof window.fetch !== "function") return;
      rawFetch = window.fetch;
      window.fetch = function (input) {
        var url = typeof input === "string" ? input : ((input && input.url) || "");
        var p = rawFetch.apply(this, arguments);
        if (url.indexOf("/api/") === 0) p.then(function (r) { Ui.connection.report(r.status); }, function (e) { if (!e || e.name !== "AbortError") Ui.connection.report(0); });
        return p;
      };
    },
    // The boot screen has gone: trouble that was held back while it explained a slow start may show now.
    refresh: function () { paint(); },
    state: function () { var c = compose(); return { kind: c ? c.kind : null, msg: c ? c.msg : "", fails: health.fails, since: health.since, lastOk: health.lastOk, offline: health.offline, signin: health.signin }; }
  };
  health.offline = (typeof navigator !== "undefined" && navigator.onLine === false);
  window.addEventListener("offline", function () { health.offline = true; paint(); });
  window.addEventListener("online", function () {
    var was = health.offline;
    health.offline = false; health.fails = 0; health.since = 0; health.probes = 0;   // what failed while offline was the phone, not the server
    if (health.timer) { clearTimeout(health.timer); health.timer = null; }
    paint();
    if (was) Ui.success("Back online.", { duration: 2500 });
    probe();                                                                           // and check straight away
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && health.fails > 0 && !health.offline && !health.timer) probe();   // back on screen while in trouble: do not wait for the next timer
  });
  Ui.connection.watch();

  /* ---------- boot: a slow first load is normal (the free server sleeps), so explain it instead of failing at 5 s ---------- */
  var STAGES = [
    { at: 0,     msg: "Loading your league…" },
    { at: 6000,  msg: "Still connecting — the server may be waking up. This can take up to a minute." },
    { at: 25000, msg: "Almost there… thanks for your patience." }
  ];
  var FAIL_AT = 75000;
  Ui.boot = {
    started: false,
    start: function () {
      if (Ui.boot.started) return; Ui.boot.started = true;
      var el = document.getElementById("app-loading"); if (!el) return;
      var p = el.querySelector("p"); if (p) p.className = "ui-boot-msg";
      var t0 = Date.now(), shown = 0;
      var iv = setInterval(function () {
        var cur = document.getElementById("app-loading");
        if (!cur || cur.classList.contains("hidden")) { clearInterval(iv); return; }     // app rendered: loader is going away
        var age = Date.now() - t0;
        for (var i = STAGES.length - 1; i > shown; i--) { if (age >= STAGES[i].at) { shown = i; if (p) p.textContent = STAGES[i].msg; break; } }
        if (age >= FAIL_AT) {
          clearInterval(iv);
          var sp = cur.querySelector(".spinner"); if (sp) sp.style.display = "none";
          if (p) p.textContent = "We couldn't reach the server. Check your connection, then try again. Your picks are kept on this phone.";
          var b = document.createElement("button"); b.type = "button"; b.className = "btn btn-teal ui-boot-fail"; b.textContent = "Try again";
          b.onclick = function () { location.reload(); };
          cur.appendChild(b); b.focus();
        }
      }, 500);
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", Ui.boot.start); else Ui.boot.start();

  /* ---------- more icons ---------- */
  ICON_PATHS.trash = '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>';
  ICON_PATHS.eye = '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>';
  ICON_PATHS.eyeoff = '<path d="M3 3l18 18"/><path d="M6.6 6.6C3.7 8.4 2 12 2 12s4 7 10 7c1.7 0 3.2-.5 4.5-1.2M10 5.1A9 9 0 0 1 12 5c6 0 10 7 10 7a16 16 0 0 1-2.5 3.4"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>';
  ICON_PATHS.sliders = '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>';
  ICON_PATHS.close = '<path d="M6 6l12 12M18 6 6 18"/>';
  ICON_PATHS.swap = '<path d="M7 7h12l-3-3M17 17H5l3 3"/>';
  ICON_PATHS.undo = '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>';

  /* ---------- segmented control: a radio group drawn as joined buttons; arrow keys move the choice ----------
     Ui.seg({id, label, value, options:[{v, l, n, cls, title}], onSelect:"fnName", cls}) -> html. onSelect is called with the option's value (a string).
     Ui.segSet(el, value) changes the choice in place (the focused button stays where it is). */
  Ui.seg = function (o) {
    var h = '<div class="seg' + (o.cls ? " " + o.cls : "") + '"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ' role="radiogroup" aria-label="' + esc(o.label || "") + '">';
    (o.options || []).forEach(function (op) {
      var on = String(op.v) === String(o.value);
      h += '<button type="button" role="radio" class="seg-opt' + (on ? " on" : "") + (op.cls ? " " + op.cls : "") + '" aria-checked="' + (on ? "true" : "false") + '" tabindex="' + (on ? 0 : -1) + '" data-v="' + esc(op.v) + '"' +
        (op.title ? ' title="' + esc(op.title) + '"' : "") + ' onclick="' + o.onSelect + "(this.getAttribute('data-v'))\"><span class=\"seg-l\">" + esc(op.l) + "</span>" + (op.n != null ? '<span class="seg-n">' + esc(op.n) + "</span>" : "") + "</button>";
    });
    return h + "</div>";
  };
  Ui.segSet = function (el, value) {
    if (!el) return;
    [].forEach.call(el.querySelectorAll(".seg-opt"), function (b) { var on = b.getAttribute("data-v") === String(value); b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
  };
  document.addEventListener("keydown", function (e) {
    var b = e.target; if (!b || !b.classList || !b.classList.contains("seg-opt")) return;
    var step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }, opts = [].slice.call(b.parentNode.querySelectorAll(".seg-opt")), at = opts.indexOf(b), to = -1;
    if (step[e.key]) to = (at + step[e.key] + opts.length) % opts.length; else if (e.key === "Home") to = 0; else if (e.key === "End") to = opts.length - 1;
    if (to < 0) return; e.preventDefault(); opts[to].focus(); opts[to].click();
  });

  /* ---------- toggle chips: a multi-choice group (several can be on at once; none on = no restriction, e.g. "all teams") ----------
     Ui.chips({id, label, values:[...on...], onToggle:"fnName", allLabel:"All", options:[{v, l, dot}]}) -> html.
     onToggle is called with the option's value (a string); the leading "All" chip (drawn when allLabel is given) calls it with "" and means "clear the group".
     dot = a class (e.g. a team colour class) that paints a small colour dot before the label.
     Ui.chipsSet(el, values) changes what is on in place (the focused chip stays where it is). */
  Ui.chips = function (o) {
    var vals = o.values || [], h = '<div class="tchips"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ' role="group" aria-label="' + esc(o.label || "") + '">';
    function chip(v, l, dot, on) {
      return '<button type="button" class="tchip' + (on ? " on" : "") + '" aria-pressed="' + (on ? "true" : "false") + '" data-v="' + esc(v) + '" onclick="' + o.onToggle + "(this.getAttribute('data-v'))\">" +
        (dot ? '<i class="tchip-dot ' + esc(dot) + '" aria-hidden="true"></i>' : "") + "<span>" + esc(l) + "</span></button>";
    }
    if (o.allLabel) h += chip("", o.allLabel, "", vals.length === 0);
    (o.options || []).forEach(function (op) { h += chip(op.v, op.l == null ? op.v : op.l, op.dot, vals.indexOf(op.v) !== -1); });
    return h + "</div>";
  };
  Ui.chipsSet = function (el, values) {
    if (!el) return;
    [].forEach.call(el.querySelectorAll(".tchip"), function (b) {
      var v = b.getAttribute("data-v"), on = v === "" ? values.length === 0 : values.indexOf(v) !== -1;
      b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  };

  /* ---------- range slider: the filled part of the track follows the thumb (CSS reads --p) ---------- */
  Ui.rangeFill = function (el) { var mn = +el.min || 0, mx = +el.max || 100, v = +el.value; el.style.setProperty("--p", (mx > mn ? (v - mn) / (mx - mn) * 100 : 0) + "%"); };
  document.addEventListener("input", function (e) { var t = e.target; if (t && t.classList && t.classList.contains("range")) Ui.rangeFill(t); }, true);

  /* ---------- passcode boxes: N boxes drawn over ONE real input, so paste, backspace, autofill and the numeric keypad all just work ----------
     Ui.pin({id, label, length, describedby}) -> html; the real <input id> keeps its value, so callers read it as before.
     Ui.pinReset(input, shake) clears it (and shakes the boxes on a wrong code). */
  Ui.pin = function (o) {
    var n = o.length || 4, cells = ""; for (var i = 0; i < n; i++) cells += '<span class="pin-cell"></span>';
    return '<div class="pin" data-len="' + n + '"><div class="pin-box">' + cells +
      '<input id="' + esc(o.id) + '" class="pin-input" type="tel" inputmode="numeric" pattern="\\d{' + n + '}" maxlength="' + n + '" autocomplete="off" aria-label="' + esc(o.label || "Passcode") + '"' + (o.describedby ? ' aria-describedby="' + esc(o.describedby) + '"' : "") + "></div>" +
      '<button type="button" class="pin-eye" aria-label="Show digits" aria-pressed="false">' + Ui.icon("eye", 20) + "</button></div>";
  };
  function pinSync(inp) {
    var p = inp.closest(".pin"); if (!p) return;
    var v = inp.value.replace(/\D/g, "").slice(0, +p.getAttribute("data-len")); if (v !== inp.value) inp.value = v;
    if (v) p.classList.remove("shake");                                  // typing again clears the error look
    var reveal = p.classList.contains("reveal"), cells = p.querySelectorAll(".pin-cell"), focused = document.activeElement === inp;
    /* masked digits are drawn as dots by CSS; the digit itself only appears while "show" is on */
    [].forEach.call(cells, function (c, i) { c.textContent = v.charAt(i) && reveal ? v.charAt(i) : ""; c.classList.toggle("filled", !!v.charAt(i)); c.classList.toggle("cur", focused && i === Math.min(v.length, cells.length - 1)); });
  }
  Ui.pinSync = pinSync;
  Ui.pinReset = function (inp, shake) {
    if (!inp) return; inp.value = ""; pinSync(inp);
    if (shake) { var p = inp.closest(".pin"); if (p) { p.classList.remove("shake"); void p.offsetWidth; p.classList.add("shake"); } }
    inp.focus();
  };
  ["input", "focusin", "focusout"].forEach(function (ev) { document.addEventListener(ev, function (e) { if (e.target && e.target.classList && e.target.classList.contains("pin-input")) pinSync(e.target); }, true); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".pin-eye"); if (!b) return;
    var p = b.closest(".pin"), on = !p.classList.contains("reveal"), inp = p.querySelector(".pin-input");
    p.classList.toggle("reveal", on); b.setAttribute("aria-pressed", on ? "true" : "false"); b.setAttribute("aria-label", on ? "Hide digits" : "Show digits"); b.innerHTML = Ui.icon(on ? "eyeoff" : "eye", 20);
    pinSync(inp); inp.focus();
  });
})();
