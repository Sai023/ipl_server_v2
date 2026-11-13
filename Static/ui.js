/* BCL Fantasy - shared UI layer v1.0  (window.Ui)
   One consistent way to talk to the user:
     Ui.toast(msg, {type, duration, action})   Ui.success/error/warn/info(msg)   Ui.fail(err, prefix)
     Ui.confirm({title,message,confirmText,danger})  -> Promise<boolean>
     Ui.prompt({title,message,label,validate})        -> Promise<string|null>
     Ui.notice({title,message})                       -> Promise<void>
     Ui.errorMessage(err, fallback)  plain-language text for any failed request
     Ui.stateHtml(kind, {title,message,retry})  loading | empty | error | offline cards
     Ui.connection.set('offline'|'reconnecting', msg) / .clear()
     Ui.boot  staged loading messages for a slow first load (server waking up), with a real failure screen + Retry
   Replaces every browser alert()/confirm()/prompt() and the bare red error banner. */
(function () {
  "use strict";
  var Ui = window.Ui = {};

  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  Ui.esc = esc;

  /* ---------- plain-language error text ---------- */
  Ui.errorMessage = function (err, fallback) {
    fallback = fallback || "Something went wrong. Please try again.";
    if (!err) return fallback;
    if (typeof err === "string") return err;
    if (err.serverMessage) return err.serverMessage;               // the server's own readable explanation wins
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

  /* ---------- status cards ---------- */
  Ui.stateHtml = function (kind, o) {
    o = o || {};
    var ico = { loading: "", empty: "🏏", error: "⚠️", offline: "📵" }[kind] || "";
    var h = '<div class="ui-state ui-state-' + kind + '" role="' + (kind === "error" ? "alert" : "status") + '">';
    h += kind === "loading" ? '<div class="ui-spinner" aria-hidden="true"></div>' : '<div class="ui-state-ico" aria-hidden="true">' + ico + "</div>";
    if (o.title) h += "<h3>" + esc(o.title) + "</h3>";
    if (o.message) h += "<p>" + esc(o.message) + "</p>";
    if (o.retry) h += '<button type="button" class="btn btn-teal btn-sm" onclick="' + o.retry + '">' + esc(o.retryLabel || "Try again") + "</button>";
    return h + "</div>";
  };

  /* ---------- connection banner ---------- */
  var conn = null;
  function getConn() {
    if (conn && document.body.contains(conn)) return conn;
    conn = document.createElement("div"); conn.id = "ui-conn"; conn.setAttribute("role", "status"); conn.setAttribute("aria-live", "polite");
    document.body.appendChild(conn); return conn;
  }
  Ui.connection = {
    set: function (kind, msg) {
      var c = getConn(); c.className = "show" + (kind === "offline" ? " offline" : "");
      c.innerHTML = "<span>" + esc(msg || (kind === "offline" ? "You're offline. Reconnect to see the latest scores." : "Having trouble reaching the server — retrying…")) + "</span>";
      var b = document.createElement("button"); b.type = "button"; b.textContent = "Retry now";
      b.onclick = function () { window.dispatchEvent(new CustomEvent("ipl:retry")); };
      c.appendChild(b);
    },
    clear: function () { if (conn) conn.className = ""; }
  };
  window.addEventListener("offline", function () { Ui.connection.set("offline"); });
  window.addEventListener("online", function () { Ui.connection.clear(); Ui.success("Back online.", { duration: 2500 }); });

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
          if (p) p.textContent = "We couldn't reach the server. Check your connection, then try again.";
          var b = document.createElement("button"); b.type = "button"; b.className = "btn btn-teal ui-boot-fail"; b.textContent = "Try again";
          b.onclick = function () { location.reload(); };
          cur.appendChild(b); b.focus();
        }
      }, 500);
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", Ui.boot.start); else Ui.boot.start();
})();
