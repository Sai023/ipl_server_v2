/* BCL Fantasy - Google sign-in, the page's half (Phase 3, 2026-11-21)                                   window.IplGoogle
   ============================================================================================================================================
   Google draws its own "Continue with Google" button; when the member taps it Google hands this page a signed ID token (the "credential"); this module sends
   it to the server together with the single-use NONCE the server issued for this attempt (the server checks both: logic/google_identity.py). Nothing here is
   believed by the server just because the page says so.

   Everything is off until the server says Google sign-in is set up (GET /api/auth/config: GOOGLE_CLIENT_ID is set in the environment). A browser that is
   really an app's built-in browser (Instagram, Facebook, the old Android app ...) is never sent to Google (Google refuses it); the member is told to open the
   link in Chrome instead.

   The module reports through DOM events, so the page decides what a result means:
     ipl:google-signed-in   detail = the server's reply for a linked member (name, token, is_admin ...): the page establishes the session
     ipl:google-claimed     detail = the same reply, when the member was just linked by claiming their name (fired right after ipl:google-signed-in: the page may say so)
     ipl:google-linked      detail = the reply of a successful switch (the member's passcode is now retired)
     ipl:google-config      the first answer of /api/auth/config arrived and Google sign-in IS available: the page redraws its sign-in card once
   The DOM work lives in `view` (swapped out by the tests); the logic is everything else. ES5 on purpose, like the other page files. */
(function (window) {
  "use strict";

  var GIS_SRC = "https://accounts.google.com/gsi/client";
  var GIS_TIMEOUT_MS = 12000;
  var NONCE_MAX_AGE_MS = 8 * 60 * 1000;          // the server keeps a nonce 10 minutes: ask for a fresh one a little before
  var LS_LINKED = "ipl_linked";

  var cfg = null, cfgP = null;                    // what /api/auth/config said
  var account = null;                             // {linked, emailMasked, pinEnabled} of the signed-in member
  var nonce = null, nonceAt = 0, nonceP = null;   // the unspent nonce, when we got it, and a request in flight
  var gisP = null;                                // loading Google's library
  var current = null;                             // the mount Google's callback reports to
  var offered = false;                            // the switch sheet is offered once per launch
  var ticker = null;

  // Sign-in from the installed app (see "sign-in from the app" below)
  var LS_VERIFIER = "ipl_handoff_v";              // the app's secret half, kept in the app's own storage: JSON {v, t}
  var SS_CHALLENGE = "ipl_handoff_c";             // the browser's copy of the challenge it was opened with: JSON {c, t} (survives a reload of the tab)
  var HANDOFF_MAX_AGE_MS = 15 * 60 * 1000;
  var APP_SCHEME = "bclfantasy", APP_PACKAGE = "com.iplfantasy.app";
  var CHALLENGE_RE = /^[A-Za-z0-9_-]{43}$/, CODE_RE = /^[A-Za-z0-9_-]{16,128}$/;
  var pendingChallenge = null;                    // browser: the app's challenge, until the member has been offered the way back or said not now
  var pendingCode = null;                         // app: a code that came back from the browser, until it has been redeemed
  var redeeming = false;                          // app: the code is being redeemed (the sign-in card says "signing you in" instead of offering the button)
  var handoffSheet = null;                        // browser: the open "Return to the app" sheet

  function api() { return window.IplApi; }
  function fire(name, detail) { try { window.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (e) {} }
  function hasGis() { return !!(window.google && window.google.accounts && window.google.accounts.id); }
  function inDom(el) { try { return !!(el && document.body && document.body.contains(el)); } catch (e) { return false; } }
  function errText(e) {
    if (window.Ui && Ui.errorMessage) return Ui.errorMessage(e, "Something went wrong. Please try again.");
    return (e && (e.serverMessage || e.message)) || "Something went wrong. Please try again.";
  }

  // ── what the server says is set up ─────────────────────────────────────────────────────────────────────────────────────────────────────
  // {enabled, clientId, pinEnabled}. Cached; a failed request is not cached, so the next try asks again.
  function config() {
    if (cfg) return Promise.resolve(cfg);
    if (cfgP) return cfgP;
    cfgP = api().authConfig().then(function (d) {
      var g = (d && d.google) || {};
      cfg = { enabled: !!(g.enabled && g.client_id), clientId: g.client_id || null, pinEnabled: !(d && d.pin_enabled === false) };
      cfgP = null;
      if (cfg.enabled) fire("ipl:google-config", cfg);
      return cfg;
    }, function (e) { cfgP = null; throw e; });
    return cfgP;
  }
  function forgetConfig() { cfg = null; }        // after a switch: whether anybody still has a passcode may have changed

  // ── the browser we are in ──────────────────────────────────────────────────────────────────────────────────────────────────────────
  // Google refuses sign-in inside the built-in browser of other apps and inside an Android WebView (the old BCL Fantasy app is one). {blocked, app}
  function embeddedBrowser(ua) {
    ua = String(ua != null ? ua : ((window.navigator && navigator.userAgent) || ""));
    var known = [[/FBAN|FBAV|FB_IAB|FBIOS|FB4A/i, "Facebook"], [/Instagram/i, "Instagram"], [/\bLine\//i, "LINE"], [/MicroMessenger/i, "WeChat"],
                 [/Snapchat/i, "Snapchat"], [/TikTok|musical_ly|BytedanceWebview/i, "TikTok"], [/Twitter/i, "X"], [/LinkedInApp/i, "LinkedIn"],
                 [/Pinterest/i, "Pinterest"], [/Telegram/i, "Telegram"]];
    for (var i = 0; i < known.length; i++) if (known[i][0].test(ua)) return { blocked: true, app: known[i][1] };
    if (/Android/i.test(ua) && /; wv\)/i.test(ua)) return { blocked: true, app: "this app" };      // an Android WebView
    return { blocked: false, app: "" };
  }

  // ── Google's library, and the nonce ────────────────────────────────────────────────────────────────────────────────────────────────────
  function loadGis() {
    if (hasGis()) return Promise.resolve();
    if (gisP) return gisP;
    gisP = new Promise(function (resolve, reject) {
      var s = document.createElement("script"), t = null, done = false;
      function fail(why) { if (done) return; done = true; clearTimeout(t); gisP = null; try { if (s.parentNode) s.parentNode.removeChild(s); } catch (e) {} reject(new Error("gis_" + why)); }
      s.src = GIS_SRC; s.async = true; s.defer = true;
      s.onload = function () { if (done) return; if (hasGis()) { done = true; clearTimeout(t); resolve(); } else fail("missing"); };
      s.onerror = function () { fail("blocked"); };
      t = setTimeout(function () { fail("timeout"); }, GIS_TIMEOUT_MS);
      document.head.appendChild(s);
    });
    return gisP;
  }
  // The nonce for the next attempt: the unspent one while it is fresh, else a new one (never two requests at once).
  function ensureNonce(force) {
    if (!force && nonce && Date.now() - nonceAt < NONCE_MAX_AGE_MS) return Promise.resolve(nonce);
    if (nonceP) return nonceP;
    nonceP = api().authNonce().then(function (d) {
      nonceP = null;
      if (!d || !d.nonce) throw new Error("no nonce");
      nonce = d.nonce; nonceAt = Date.now();
      return nonce;
    }, function (e) { nonceP = null; throw e; });
    return nonceP;
  }

  // ── mounting the button ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  // mode "signin": the credential goes to /api/auth/google. mode "link": to /api/auth/link (a signed-in member switching). done(reply) runs on success.
  function mount(host, mode, done) {
    var ctx = { mode: mode, host: host, done: done || null, nonce: null };
    return config().then(function (c) {
      if (!c.enabled || !inDom(host)) return;
      var eb = embeddedBrowser();
      if (eb.blocked) {
        // The BCL Fantasy app (1.5 and later) hands the Google step to the phone's browser and takes the member back; every other built-in browser is told to open the link elsewhere.
        if (mode === "signin" && appShell().inApp && handoffSupported()) { view.appSignIn(host, function () { startAppSignIn(ctx); }); return; }
        view.blocked(host, eb.app);
        return;
      }
      view.status(host, "", "");
      return loadGis().then(function () { return ensureNonce(false); }).then(function (n) {
        if (!inDom(host)) return;
        ctx.nonce = n; arm(ctx); keepFresh();
      });
    }).catch(function (e) {
      view.trouble(host, /^gis_/.test((e && e.message) || "") ? "Google sign-in could not load. Check your connection and try again." : errText(e), function () { mount(host, mode, done); });
    });
  }
  // (Re)start Google's side with this attempt's nonce and draw the button.
  function arm(ctx) {
    current = ctx;
    window.google.accounts.id.initialize({
      client_id: cfg.clientId, nonce: ctx.nonce, auto_select: false, cancel_on_tap_outside: true,
      callback: function (resp) { onCredential(ctx, resp); }
    });
    view.renderButton(ctx.host, window.google.accounts.id);
  }
  function rearm(ctx) {
    if (!inDom(ctx.host)) return;
    ensureNonce(true).then(function (n) { if (inDom(ctx.host)) { ctx.nonce = n; arm(ctx); } }, function () { /* the next tap says why */ });
  }
  // While a button is on screen its nonce is renewed before the server forgets it (the member may sit on the sign-in card for a long time).
  function keepFresh() {
    if (ticker) return;
    ticker = setInterval(function () {
      if (!current || !inDom(current.host)) { clearInterval(ticker); ticker = null; return; }
      if (Date.now() - nonceAt > NONCE_MAX_AGE_MS) rearm(current);
    }, 60000);
  }

  // Google handed over a credential (or did not).
  function onCredential(ctx, resp) {
    var cred = resp && resp.credential;
    if (!cred) { view.status(ctx.host, "Google did not send a sign-in. Please try again.", "error"); rearm(ctx); return; }
    var n = ctx.nonce;
    if (nonce === n) nonce = null;                     // from now on it is spent, whatever the answer
    view.busy(ctx.host, true);
    var call = ctx.mode === "link" ? api().authLink : api().authGoogle;
    call(cred, n).then(function (d) {
      view.busy(ctx.host, false);
      if (ctx.mode === "signin" && !(d && d.linked)) {   // a good Google account that nobody has linked yet: not an error, and not a dead end either
        offerClaim(ctx, d);
        return;
      }
      remember(true);
      account = { linked: true, emailMasked: (d && d.email_masked) || null, pinEnabled: false };
      forgetConfig();
      fire(ctx.mode === "link" ? "ipl:google-linked" : "ipl:google-signed-in", d);
      if (ctx.done) ctx.done(d);
    }, function (err) {
      view.busy(ctx.host, false);
      view.status(ctx.host, errText(err), "error");
      rearm(ctx);
    });
  }

  // A good Google account that nobody has linked yet. The server remembered WHICH account it verified (a claim ticket) and sent the list of members; the member
  // picks their name and enters their passcode ONCE, and the server links the account, retires the passcode and signs them in: no second trip to Google. Without
  // a claim (a server that does not know about them) the old explanation is shown instead.
  function offerClaim(ctx, d) {
    var c = d && d.claim;
    if (!c || !c.ticket) { view.notLinked(ctx.host, d && d.email_masked); rearm(ctx); return; }
    var sheet = view.claimSheet({
      emailMasked: c.email_masked || (d && d.email_masked) || "",
      members: c.members || [],
      join: c.join || null,                              // {allowed, why}: may a NEW member join with this Google account? (absent on an older server)
      submit: function (name, passcode) { return submitClaim(ctx, c, sheet, name, passcode); },
      submitJoin: function (name) { return submitJoin(ctx, c, sheet, name); },
      cancel: function () { rearm(ctx); }                // the member backed out: the button gets a fresh nonce for the next attempt
    });
    return sheet;
  }
  function submitClaim(ctx, c, sheet, name, passcode) {
    sheet.busy(true);
    return api().authClaim(c.ticket, name, passcode).then(function (d) {
      sheet.close();
      remember(true);
      account = { linked: true, emailMasked: (d && d.email_masked) || null, pinEnabled: false };
      forgetConfig();
      fire("ipl:google-signed-in", d);
      fire("ipl:google-claimed", d);
      if (ctx.done) ctx.done(d);
    }, function (err) {
      sheet.busy(false);
      var reason = (window.Ui && Ui.errorReason) ? Ui.errorReason(err) : "";
      var data = (err && err.serverData) || {};
      if (reason === "claim_expired" || reason === "claim_locked") {      // the ticket is gone (too old, used up, or too many wrong passcodes): start again from Google
        sheet.close();
        view.status(ctx.host, errText(err), "error");
        rearm(ctx);
        return;
      }
      if (reason === "bad_credentials") {
        var left = data.attempts_left;
        sheet.fail("That passcode is not right." + (typeof left === "number" ? " " + left + (left === 1 ? " try" : " tries") + " left." : ""), { clear: true });
        return;
      }
      if (reason === "member_taken" || reason === "temp_passcode") { sheet.fail(errText(err), { clear: true, notFree: name }); return; }   // that name cannot be picked: it is shown as such
      sheet.fail(errText(err), { clear: true });
    });
  }

  // A NEW member joins with this Google account: the display name they chose and nothing else (no passcode exists). The reply is a sign-in.
  function submitJoin(ctx, c, sheet, name) {
    sheet.busy(true);
    return api().authJoin(c.ticket, name).then(function (d) {
      sheet.close();
      remember(true);
      account = { linked: true, emailMasked: (d && d.email_masked) || null, pinEnabled: false };
      forgetConfig();
      fire("ipl:google-signed-in", d);
      fire("ipl:google-joined", d);
      if (ctx.done) ctx.done(d);
    }, function (err) {
      sheet.busy(false);
      var reason = (window.Ui && Ui.errorReason) ? Ui.errorReason(err) : "";
      if (reason === "claim_expired") {                   // the ticket is gone: start again from Google
        sheet.close();
        view.status(ctx.host, errText(err), "error");
        rearm(ctx);
        return;
      }
      if (reason === "joining_closed" || reason === "members_full") { sheet.fail(errText(err), { noJoin: true }); return; }   // nobody new can join: say so, and stop offering it
      sheet.fail(errText(err), { keepName: true });      // a name somebody has, or not a usable name: stay on the name box and try another
    });
  }

  // ── what this phone knows about the member ─────────────────────────────────────────────────────────────────────────────────────────────
  function remember(on) { try { if (on) localStorage.setItem(LS_LINKED, "1"); else localStorage.removeItem(LS_LINKED); } catch (e) {} }
  function wasLinked() { try { return localStorage.getItem(LS_LINKED) === "1"; } catch (e) { return false; } }
  function setAccount(a) {
    account = a ? { linked: !!a.linked, emailMasked: a.emailMasked || null, pinEnabled: a.pinEnabled !== false } : null;
    if (account) remember(account.linked);
  }

  // ── sign-in from the app ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
  // Google refuses to sign anybody in inside an app's WebView, and the BCL Fantasy Android app (1.5 and later) is one. So the app hands the Google step to the phone's
  // own browser and the browser hands the member back: the pattern native apps use (RFC 8252, with the PKCE proof of RFC 7636).
  //   in the APP      the member taps "Sign in with Google in your browser". The page makes a random VERIFIER, keeps it in the app's own storage and sends the browser only its
  //                   hash, the CHALLENGE, by "loading" /app-signin?c=<challenge>: the app intercepts that address and opens the browser instead of loading it.
  //   in the BROWSER  the page opens with ?handoff=<challenge>. Once the member is signed in there (Google, a passcode: anything) it asks the server for a one-time CODE for
  //                   that challenge and offers a "Return to the app" link (an Android intent for bclfantasy://auth?code=...), which the app handles.
  //   in the APP      loaded with ?handoff_code=<code>: the page gives the server the code AND the verifier and gets the app's OWN session (the browser's is untouched).
  // A code that falls into the wrong hands (another app can register the same link) is useless without the verifier, which never left the app.
  // The app tells the page it is the app with a user-agent token, BCLFantasyApp/<version> (APK 1.5 and later); the old app has none and keeps the "open in Chrome" notice.
  function appShell(ua) {
    ua = String(ua != null ? ua : ((window.navigator && navigator.userAgent) || ""));
    var m = /BCLFantasyApp\/([\d.]+)/.exec(ua);
    return { inApp: !!m, version: m ? m[1] : "" };
  }
  function handoffSupported() {
    try { return !!(window.crypto && window.crypto.subtle && window.crypto.getRandomValues && typeof window.TextEncoder !== "undefined" && typeof window.btoa === "function" && window.localStorage); } catch (e) { return false; }
  }
  function b64url(bytes) {                       // bytes: a Uint8Array or an ArrayBuffer; base64url without padding
    var b = new Uint8Array(bytes), s = "", i;
    for (i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return window.btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function randomVerifier() { var a = new Uint8Array(32); window.crypto.getRandomValues(a); return b64url(a); }              // 32 random bytes: 43 characters
  function challengeOf(verifier) { return window.crypto.subtle.digest("SHA-256", new window.TextEncoder().encode(verifier)).then(b64url); }
  function storeVerifier(v) { try { localStorage.setItem(LS_VERIFIER, JSON.stringify({ v: v, t: Date.now() })); } catch (e) {} }
  function readVerifier() {
    try { var o = JSON.parse(localStorage.getItem(LS_VERIFIER) || "null"); if (o && typeof o.v === "string" && Date.now() - o.t < HANDOFF_MAX_AGE_MS) return o.v; } catch (e) {}
    return null;
  }
  function clearVerifier() { try { localStorage.removeItem(LS_VERIFIER); } catch (e) {} }

  // The member tapped the button in the app: keep a new secret, send the browser its hash.
  function startAppSignIn(ctx) {
    var v = randomVerifier();
    return challengeOf(v).then(function (ch) {
      storeVerifier(v);
      view.status(ctx.host, "Finish signing in in your browser, then tap “Return to the app”.", "info");
      window.location.href = ((window.location && location.origin) || "") + "/app-signin?c=" + ch;
    }, function () { view.status(ctx.host, "This phone could not start the browser sign-in. Open the link in Chrome instead.", "error"); });
  }

  function queryParams() {
    var out = {}, s = String((window.location && location.search) || "").replace(/^\?/, "");
    if (!s) return out;
    s.split("&").forEach(function (kv) {
      var i = kv.indexOf("="), k = i < 0 ? kv : kv.slice(0, i);
      try { out[decodeURIComponent(k)] = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " ")); } catch (e) {}
    });
    return out;
  }
  function stripParams(names) {                  // the address bar never keeps a challenge or a code (a reload must not use one twice)
    try {
      var rest = String(location.search || "").replace(/^\?/, "").split("&").filter(function (kv) { return kv && names.indexOf(kv.split("=")[0]) < 0; }).join("&");
      window.history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + (location.hash || ""));
    } catch (e) {}
  }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  // At load: pick up ?handoff=<challenge> (browser) and ?handoff_code=<code> (app) from the address, and take them out of it.
  function takeParams() {
    var p = queryParams(), names = [];
    var android = /Android/i.test(String((window.navigator && navigator.userAgent) || ""));
    if (has(p, "handoff")) {
      names.push("handoff");
      if (CHALLENGE_RE.test(p.handoff) && android && !appShell().inApp) {
        pendingChallenge = p.handoff;
        try { sessionStorage.setItem(SS_CHALLENGE, JSON.stringify({ c: p.handoff, t: Date.now() })); } catch (e) {}
      }
    }
    if (has(p, "handoff_code")) { names.push("handoff_code"); if (CODE_RE.test(p.handoff_code)) pendingCode = p.handoff_code; }
    if (names.length) stripParams(names);
    if (!pendingChallenge && android && !appShell().inApp) {                  // the tab was reloaded after the address was cleaned: the challenge it was opened with
      try { var o = JSON.parse(sessionStorage.getItem(SS_CHALLENGE) || "null"); if (o && CHALLENGE_RE.test(o.c) && Date.now() - o.t < HANDOFF_MAX_AGE_MS) pendingChallenge = o.c; } catch (e) {}
    }
  }
  function forgetChallenge() { pendingChallenge = null; try { sessionStorage.removeItem(SS_CHALLENGE); } catch (e) {} }

  // APP: the browser sent the member back with a code. Give the server the code and the secret; the reply is the app's own session, a sign-in like any other.
  function redeemPending() {
    var code = pendingCode; pendingCode = null;
    var v = readVerifier();
    if (!code) return Promise.resolve();
    if (!v) { fire("ipl:app-handoff", { ok: false, message: "That browser sign-in did not start in this app. Tap Sign in with Google in your browser to start again." }); return Promise.resolve(); }
    redeeming = true;
    return api().authHandoffRedeem(code, v).then(function (d) {
      redeeming = false; clearVerifier();
      remember(!!(d && d.linked));
      account = { linked: !!(d && d.linked), emailMasked: (d && d.email_masked) || null, pinEnabled: !(d && d.linked) };
      fire("ipl:google-signed-in", d);           // the page signs the member in exactly as for a Google sign-in
      fire("ipl:app-handoff", { ok: true, name: d && d.name });
    }, function (err) {
      redeeming = false; clearVerifier();
      fire("ipl:app-handoff", { ok: false, message: errText(err) });
    });
  }

  // BROWSER: the member is signed in here and the app is waiting. Ask for a one-time code and offer the way back (the code lives two minutes: after that the button asks again).
  function appLink(code) {
    var back = ((window.location && location.origin) || "") + "/";
    return "intent://auth?code=" + encodeURIComponent(code) + "#Intent;scheme=" + APP_SCHEME + ";package=" + APP_PACKAGE + ";S.browser_fallback_url=" + encodeURIComponent(back) + ";end";
  }
  function offerHandoff(name) {
    if (!pendingChallenge || handoffSheet || appShell().inApp) return false;
    var challenge = pendingChallenge;
    var sheet = handoffSheet = view.handoffSheet({
      name: name || "",
      again: function () { ask(); },
      sent: function () { if (handoffSheet === sheet) forgetChallenge(); },      // the way back was taken: the page stops remembering the app's challenge (a new code can still be asked for with this sheet)
      cancel: function () { if (handoffSheet === sheet) handoffSheet = null; forgetChallenge(); }
    });
    function ask() {
      sheet.waiting();
      return api().authHandoff(challenge).then(function (d) {
        if (handoffSheet !== sheet) return;
        sheet.ready(appLink(d.code), Math.max(10, ((d && d.expires_in) || 120) - 10) * 1000);
      }, function (err) { if (handoffSheet === sheet) sheet.fail(errText(err)); });
    }
    ask();
    return true;
  }
  // The sign-in card in the browser says why the member is here.
  function handoffNoteHtml() {
    return pendingChallenge ? '<p class="handoff-note">You are signing in to the BCL Fantasy app. Sign in here, then tap <b>Return to the app</b>.</p>' : "";
  }

  // ── the screens ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  function loginBlockHtml() { return (cfg && cfg.enabled) ? view.loginBlockHtml() : ""; }
  // After every draw of the page: put the button into the sign-in card (the card is rebuilt on each draw, so the button is drawn again; its nonce is reused).
  function afterRender() {
    if (!cfg) { if (!cfgP) config().then(function () {}, function () {}); return; }
    if (!cfg.enabled) return;
    var host = document.getElementById("google-signin");
    if (host) mount(host, "signin", null);
  }
  // A signed-in member with a passcode switches to Google.
  function openSwitch() {
    var sheet = view.sheet({ title: "Switch to Google sign-in", message: "From now on you sign in with Google: no passcode to remember. Two taps. Your passcode is retired once it works, and the old Android app (which cannot sign in with Google) will need Chrome instead.", secondary: "Not now" });
    mount(sheet.slot, "link", function (d) {
      sheet.finish("Done: " + ((d && d.email_masked) || "your Google account") + ". Your passcode is retired. On a new phone, just sign in with Google.");
    });
    return sheet;
  }
  // The offer made once per launch to a member who has not switched. opts.nearLock: a lock is close, so the member is not interrupted.
  function offerSwitch(opts) {
    if (offered || !cfg || !cfg.enabled || !account || account.linked || (opts && opts.nearLock) || embeddedBrowser().blocked || pendingChallenge) return false;   // (a member in the browser for the app's sake is not interrupted)
    offered = true;
    openSwitch();
    return true;
  }
  function offerSwitchWhenReady(opts) { return config().then(function () { return offerSwitch(opts); }, function () { return false; }); }
  // A member whose session ended (or whose passcode is retired) signs in again.
  // opts.onCancel runs when the member dismisses the sheet without signing in (the page then shows its "signed out" sheet again).
  function signInAgain(name, opts) {
    var sheet = view.sheet({ title: "Sign in with Google", message: name ? "Welcome back, " + name + "." : "Sign in to carry on.", secondary: "Cancel", onCancel: opts && opts.onCancel });
    mount(sheet.slot, "signin", function () { sheet.close(); });
    return sheet;
  }
  function closeSheets() { handoffSheet = null; view.closeSheets(); }      // (a member who is still on their way back to the app is offered the way back again)

  // ── the DOM part ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  var openSheets = [];
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function wrapOf(host) { var w = host && host.parentNode; return (w && w.classList && w.classList.contains("google-slot-wrap")) ? w : null; }
  var view = {
    loginBlockHtml: function () {
      return '<div class="google-block"><div class="google-slot-wrap"><div id="google-signin" class="google-slot"></div>' +
             '<div class="google-status" role="status" aria-live="polite"></div></div><div class="google-or"><span>or use your passcode</span></div></div>';
    },
    renderButton: function (host, g) {
      var w = 320; try { w = Math.max(200, Math.min(380, Math.round(host.getBoundingClientRect().width) || 320)); } catch (e) {}
      host.innerHTML = "";
      g.renderButton(host, { type: "standard", theme: "outline", size: "large", text: "continue_with", shape: "rectangular", logo_alignment: "left", width: w });
    },
    status: function (host, text, kind) {
      var w = wrapOf(host), s = w && w.querySelector(".google-status");
      if (s) { s.textContent = text || ""; s.className = "google-status" + (kind ? " " + kind : ""); }
    },
    busy: function (host, on) {
      var w = wrapOf(host); if (w) w.classList.toggle("busy", !!on);
      if (on) view.status(host, "Signing in…", "");
    },
    notLinked: function (host, masked) {
      view.status(host, "No member is linked to " + (masked || "that Google account") + " yet. Already a member? Pick your name, sign in with your passcode, then choose Switch to Google sign-in.", "info");
    },
    blocked: function (host, app) {
      var link = (window.location && location.origin) || "";
      var how = app === "this app"      // our own old Android app is a WebView: it has no menu to open the page elsewhere, so say what to do instead
        ? "Open the link below in Chrome on your phone and sign in there."
        : "Open this page in Chrome (or Safari): tap the menu in the corner and choose “Open in browser”, or copy the link.";
      host.innerHTML = '<div class="google-blocked"><b>Google does not allow sign-in inside ' + esc(app) + '.</b><span>' + esc(how) + '</span>' +
        '<code>' + esc(link) + '</code><button type="button" class="btn btn-ghost btn-sm">Copy link</button></div>';
      var b = host.querySelector("button");
      if (b) b.onclick = function () {
        function ok() { if (window.Ui) Ui.success("Link copied."); }
        try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(link).then(ok, function () { if (window.Ui) Ui.info(link); }); return; } } catch (e) {}
        if (window.Ui) Ui.info(link);
      };
    },
    // Inside the BCL Fantasy app: Google refuses the app's own browser view, so the member signs in with Google in the phone's browser and is brought back (see "sign-in from the app").
    appSignIn: function (host, onTap) {
      host.innerHTML = '<button type="button" class="btn btn-teal google-app-btn">Sign in with Google in your browser</button>';
      var b = host.querySelector("button"); if (b) b.onclick = onTap;
      view.status(host, "Opens your browser, then brings you back here.", "info");
    },
    // In the browser, for the app: a bottom sheet with ONE link, an Android intent that opens the app with the one-time code. {waiting(), ready(href, ms), fail(text), close(), dismiss()}.
    handoffSheet: function (o) {
      var bd = document.createElement("div"); bd.className = "ui-backdrop ui-sheet-bd ui-sheet-blocking";
      var who = o.name ? esc(o.name) : "you";
      bd.innerHTML = '<div class="ui-sheet google-sheet handoff-sheet" role="dialog" aria-modal="true" aria-label="Sign in to the app"><div class="ui-sheet-grip" aria-hidden="true"></div>' +
        '<h2 class="ui-sheet-title has-sub">Sign in to the app</h2>' +
        '<p class="ui-sheet-sub">' + (o.name ? "You are signed in as <b>" + who + "</b> in this browser. " : "") + "Tap the button to go back to the BCL Fantasy app: it signs in as " + who + " too.</p>" +
        '<div class="handoff-body"><a class="btn btn-teal handoff-go" role="button" aria-disabled="true">Getting your sign-in ready…</a><div class="google-status handoff-hint" role="status" aria-live="polite"></div></div>' +
        '<div class="ui-sheet-actions"><button type="button" class="btn btn-ghost" data-r="secondary">Not now</button></div></div>';
      document.body.appendChild(bd);
      var go = bd.querySelector(".handoff-go"), hint = bd.querySelector(".handoff-hint"), timer = null, self = {};
      function say(text, kind) { hint.textContent = text || ""; hint.className = "google-status handoff-hint" + (kind ? " " + kind : ""); }
      function stop() { if (timer) { clearTimeout(timer); timer = null; } }
      function again(label, text) {                    // the link is not usable any more (it timed out, or asking failed): the button asks for a new one
        stop(); go.removeAttribute("href"); go.setAttribute("aria-disabled", "false"); go.textContent = label; say(text, "error");
        go.onclick = function (e) { e.preventDefault(); o.again(); };
      }
      function onKey(e) { if (e.key === "Escape") { e.preventDefault(); self.dismiss(); } }
      self.waiting = function () { stop(); go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); go.textContent = "Getting your sign-in ready…"; go.onclick = function (e) { e.preventDefault(); }; say("", ""); };
      self.ready = function (href, ms) {
        stop(); go.setAttribute("href", href); go.setAttribute("aria-disabled", "false"); go.textContent = "Return to the app";
        say("If nothing happens, check that the BCL Fantasy app (version 1.5 or later) is installed.", "info");
        timer = setTimeout(function () { again("Get a new sign-in", "That sign-in timed out."); }, ms);
        go.onclick = function () {                     // the browser follows the link: the code is used up by the app, so it is not offered twice
          stop(); go.setAttribute("aria-disabled", "true"); go.textContent = "Opening the app…"; say("", "");
          if (o.sent) o.sent();
          timer = setTimeout(function () { again("Open the app again", "Still here? The app may not have opened."); }, 5000);
        };
      };
      self.fail = function (text) { again("Try again", text); };
      self.close = function () { stop(); document.removeEventListener("keydown", onKey, true); if (bd.parentNode) bd.parentNode.removeChild(bd); var i = openSheets.indexOf(self); if (i >= 0) openSheets.splice(i, 1); };
      self.dismiss = function () { self.close(); if (o.cancel) o.cancel(); };
      bd.querySelector('[data-r="secondary"]').onclick = self.dismiss;
      bd.addEventListener("mousedown", function (e) { if (e.target === bd) self.dismiss(); });
      document.addEventListener("keydown", onKey, true);
      openSheets.push(self);
      if (go.focus) go.focus();
      return self;
    },
    trouble: function (host, msg, retry) {
      if (!inDom(host)) return;
      host.innerHTML = '<div class="google-trouble"><span>' + esc(msg) + '</span><button type="button" class="btn btn-ghost btn-sm">Try again</button></div>';
      var b = host.querySelector("button"); if (b) b.onclick = retry;
    },
    // a bottom sheet with a slot for the Google button: {slot, close(), finish(text)}
    sheet: function (o) {
      // (the "-blocking" class is only the layout: a bottom sheet on a phone, a centred dialog on a wide screen. This sheet can be dismissed, see dismiss() below)
      var bd = document.createElement("div"); bd.className = "ui-backdrop ui-sheet-bd ui-sheet-blocking";
      bd.innerHTML = '<div class="ui-sheet google-sheet" role="dialog" aria-modal="true" aria-label="' + esc(o.title) + '"><div class="ui-sheet-grip" aria-hidden="true"></div>' +
        '<h2 class="ui-sheet-title has-sub">' + esc(o.title) + '</h2><p class="ui-sheet-sub">' + esc(o.message) + '</p>' +
        '<div class="google-slot-wrap"><div class="google-slot"></div><div class="google-status" role="status" aria-live="polite"></div></div>' +
        '<div class="ui-sheet-actions"><button type="button" class="btn btn-ghost" data-r="secondary">' + esc(o.secondary || "Close") + '</button></div></div>';
      document.body.appendChild(bd);
      var self = { el: bd, slot: bd.querySelector(".google-slot") };
      function onKey(e) { if (e.key === "Escape") { e.preventDefault(); self.dismiss(); } }
      self.close = function () {                       // closes quietly (after a success, or when the page closes every sheet)
        document.removeEventListener("keydown", onKey, true);
        if (bd.parentNode) bd.parentNode.removeChild(bd);
        var i = openSheets.indexOf(self); if (i >= 0) openSheets.splice(i, 1);
      };
      self.dismiss = function () { self.close(); if (o.onCancel) o.onCancel(); };     // the member backed out: Escape, a tap outside, or the secondary button
      self.finish = function (text) {
        var box = bd.querySelector(".ui-sheet");
        box.innerHTML = '<div class="ui-sheet-grip" aria-hidden="true"></div><h2 class="ui-sheet-title has-sub">All set</h2><p class="ui-sheet-sub">' + esc(text) + '</p>' +
          '<div class="ui-sheet-actions"><button type="button" class="btn btn-teal" data-r="ok">OK</button></div>';
        var ok = box.querySelector("button"); if (ok) { ok.onclick = self.close; ok.focus(); }
      };
      document.addEventListener("keydown", onKey, true);
      bd.addEventListener("mousedown", function (e) { if (e.target === bd) self.dismiss(); });
      bd.querySelector('[data-r="secondary"]').onclick = self.dismiss;
      openSheets.push(self);
      return self;
    },
    // The "Which member are you?" sheet for a Google account nobody has linked yet. o = {emailMasked, members: [{name, status}], join: {allowed, why} | null,
    // submit(name, passcode), submitJoin(name), cancel()}. Two ways forward: an existing member picks their name and enters their passcode once (only members whose status
    // is "free" can be picked; the others are shown with their reason: a name that has a Google account can never be picked again), or a NEW member chooses "I'm new here"
    // and types a display name (no passcode exists). Returns {close(), dismiss(), busy(on), fail(text, {clear, notFree, noJoin, keepName})}; the fourth passcode digit sends it.
    claimSheet: function (o) {
      var bd = document.createElement("div"); bd.className = "ui-backdrop ui-sheet-bd ui-sheet-blocking";
      var chips = "", free = 0;
      (o.members || []).forEach(function (m) {
        var can = m.status === "free"; if (can) free++;
        var why = m.status === "linked" ? "has Google" : (m.status === "temp" ? "temporary passcode" : "");
        chips += '<button type="button" class="login-chip claim-chip" data-name="' + esc(m.name) + '"' + (can ? ' aria-pressed="false"' : " disabled") + ">" + esc(m.name) + (why ? "<small>" + why + "</small>" : "") + "</button>";
      });
      var canJoin = !!(o.join && o.join.allowed), joinNote = "";
      if (o.join && !o.join.allowed) joinNote = o.join.why === "full" ? "The league is full." : "New members can\u2019t join right now. Ask the admin.";
      var who = esc(o.emailMasked || "this Google account");
      var sub = free && canJoin ? "Google says you are <b>" + who + "</b>. Already a member? Pick your name and enter your passcode once to link it. New here? Choose \u201cI\u2019m new here\u201d."
              : free ? "Google says you are <b>" + who + "</b>. Pick your name and enter your passcode once to link it: from then on you just tap Continue with Google." + (joinNote ? " Not on the list? " + joinNote : "")
              : canJoin ? "Google says you are <b>" + who + "</b>. No existing member is free to link (a member who already has a Google account cannot be picked again). New here? Choose \u201cI\u2019m new here\u201d."
              : "Google says you are <b>" + who + "</b>, but no member is free to link. A member who already has a Google account cannot be picked again: ask the admin to remove that link if it is wrong." + (joinNote ? " " + joinNote : "");
      var pinHtml = (window.Ui && Ui.pin) ? Ui.pin({ id: "claim-passcode", label: "4-digit passcode", describedby: "claim-status" })
        : '<input id="claim-passcode" class="pin-input" type="tel" inputmode="numeric" maxlength="4" autocomplete="off" aria-label="4-digit passcode" aria-describedby="claim-status">';
      var can = free || canJoin;
      bd.innerHTML = '<div class="ui-sheet google-sheet claim-sheet" role="dialog" aria-modal="true" aria-label="Link your Google account">' +
        '<div class="ui-sheet-grip" aria-hidden="true"></div>' +
        '<h2 class="ui-sheet-title has-sub">Which member are you?</h2><p class="ui-sheet-sub">' + sub + "</p>" +
        '<div class="claim-members" role="group" aria-label="Members">' + chips + "</div>" +
        (canJoin ? '<div class="claim-or"><span>or</span></div><div class="claim-members claim-new-wrap"><button type="button" class="login-chip claim-chip claim-new" aria-pressed="false">I\u2019m new here</button></div>' : "") +
        '<div class="claim-pin" hidden><label class="claim-pin-label" for="claim-passcode">Passcode for <b class="claim-who"></b></label>' + pinHtml + "</div>" +
        (canJoin ? '<div class="claim-join" hidden><label class="claim-pin-label" for="claim-newname">Your name</label>' +
                   '<input id="claim-newname" class="claim-name-input" type="text" maxlength="30" autocomplete="nickname" autocapitalize="words" enterkeyhint="done" placeholder="e.g. Sam" aria-describedby="claim-status">' +
                   '<p class="claim-hint">This is how you appear in the league. You sign in with Google: there is no passcode to choose.</p></div>' : "") +
        '<div id="claim-status" class="google-status" role="status" aria-live="polite"></div>' +
        '<div class="ui-sheet-actions">' + (can ? '<button type="button" class="btn btn-teal" data-r="primary" disabled>Link and sign in</button>' : "") +
        '<button type="button" class="btn btn-ghost" data-r="secondary">' + (can ? "Not me" : "OK") + "</button></div></div>";
      document.body.appendChild(bd);
      var box = bd.querySelector(".claim-sheet"), pinWrap = bd.querySelector(".claim-pin"), whoEl = bd.querySelector(".claim-who"), inp = bd.querySelector(".pin-input"),
          joinWrap = bd.querySelector(".claim-join"), nameInp = bd.querySelector(".claim-name-input"), newChip = bd.querySelector(".claim-new"),
          status = bd.querySelector(".google-status"), go = bd.querySelector('[data-r="primary"]'), selected = null, joining = false, working = false, self = {};
      function say(text, kind) { status.textContent = text || ""; status.className = "google-status" + (kind ? " " + kind : ""); }
      function digits() { return String(inp ? inp.value : "").replace(/\D/g, ""); }
      function newName() { return String(nameInp ? nameInp.value : "").replace(/\s+/g, " ").trim(); }
      function sync() {
        if (!go) return;
        go.disabled = working || (joining ? newName().length < 1 : (!selected || digits().length !== 4));
        go.textContent = joining ? "Join the league" : "Link and sign in";
      }
      function marks() {
        [].forEach.call(bd.querySelectorAll(".claim-chip"), function (b) {
          var isNew = b.classList.contains("claim-new"), on = isNew ? joining : (!joining && b.getAttribute("data-name") === selected);
          b.classList.toggle("on", on); if (!b.disabled) b.setAttribute("aria-pressed", on ? "true" : "false");
        });
      }
      function pick(name) {
        joining = false; selected = name; whoEl.textContent = name; pinWrap.hidden = false; if (joinWrap) joinWrap.hidden = true; say("", ""); marks();
        if (window.Ui && Ui.pinReset) Ui.pinReset(inp, false); else if (inp) { inp.value = ""; inp.focus(); }
        sync();
      }
      function pickNew() {
        joining = true; selected = null; pinWrap.hidden = true; if (joinWrap) joinWrap.hidden = false; say("", ""); marks();
        if (nameInp) nameInp.focus();
        sync();
      }
      function submit() {
        if (working) return;
        if (joining) { var n = newName(); if (n.length >= 1) o.submitJoin(n); return; }
        if (!selected || digits().length !== 4) return;
        o.submit(selected, digits());
      }
      function onKey(e) { if (e.key === "Escape") { e.preventDefault(); self.dismiss(); } }
      self.close = function () {
        document.removeEventListener("keydown", onKey, true);
        if (bd.parentNode) bd.parentNode.removeChild(bd);
        var i = openSheets.indexOf(self); if (i >= 0) openSheets.splice(i, 1);
      };
      self.dismiss = function () { self.close(); if (o.cancel) o.cancel(); };
      self.busy = function (on) { working = !!on; box.classList.toggle("busy", working); if (on) say(joining ? "Joining\u2026" : "Linking\u2026", ""); sync(); };
      self.fail = function (text, opts) {
        opts = opts || {};
        say(text, "error");
        if (opts.noJoin) {                                   // nobody new can join any more (closed, or full): stop offering it
          canJoin = false; joining = false;
          var w = bd.querySelector(".claim-or"); if (w && w.parentNode) w.parentNode.removeChild(w);
          if (newChip && newChip.parentNode) newChip.parentNode.parentNode.removeChild(newChip.parentNode);
          if (joinWrap) joinWrap.hidden = true;
          if (!free && go && go.parentNode) go.parentNode.removeChild(go);
          go = bd.querySelector('[data-r="primary"]');
        } else if (opts.notFree) {                           // that name turned out not to be free (somebody was linked a moment ago): show it as taken, back to choosing
          [].forEach.call(bd.querySelectorAll(".claim-chip"), function (b) {
            if (b.getAttribute("data-name") !== opts.notFree) return;
            b.disabled = true; b.removeAttribute("aria-pressed"); b.classList.remove("on");
            if (!b.querySelector("small")) b.insertAdjacentHTML("beforeend", "<small>not available</small>");
          });
          selected = null; pinWrap.hidden = true;
        } else if (opts.keepName) {                          // a name somebody has: stay on the name box
          if (nameInp) { nameInp.focus(); if (nameInp.select) nameInp.select(); }
        } else if (opts.clear && window.Ui && Ui.pinReset) Ui.pinReset(inp, true);
        sync();
      };
      bd.querySelector(".claim-members").addEventListener("click", function (e) {
        var b = e.target && e.target.closest ? e.target.closest(".claim-chip") : null;
        if (b && !b.disabled && !working && !b.classList.contains("claim-new")) pick(b.getAttribute("data-name"));
      });
      if (newChip) newChip.addEventListener("click", function () { if (!working) pickNew(); });
      if (inp) {
        inp.addEventListener("input", function () { sync(); if (!joining && digits().length === 4) submit(); });
        inp.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); submit(); } });
      }
      if (nameInp) {
        nameInp.addEventListener("input", function () { sync(); });
        nameInp.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); submit(); } });
      }
      if (go) go.onclick = submit;
      bd.querySelector('[data-r="secondary"]').onclick = function () { self.dismiss(); };
      bd.addEventListener("mousedown", function (e) { if (e.target === bd) self.dismiss(); });
      document.addEventListener("keydown", onKey, true);
      openSheets.push(self);
      var first = bd.querySelector(".claim-chip:not([disabled])") || bd.querySelector('[data-r="secondary"]');
      if (first && first.focus) first.focus();
      return self;
    },
    closeSheets: function () { openSheets.slice().forEach(function (s) { s.close(); }); }
  };

  window.IplGoogle = {
    config: config, forgetConfig: forgetConfig,
    enabled: function () { return !!(cfg && cfg.enabled); },
    pinEnabled: function () { return !cfg || cfg.pinEnabled !== false; },
    embeddedBrowser: embeddedBrowser,
    account: function () { return account; },
    setAccount: setAccount, wasLinked: wasLinked, remember: remember,
    loginBlockHtml: loginBlockHtml, afterRender: afterRender,
    mount: mount, openSwitch: openSwitch, offerSwitch: offerSwitch, offerSwitchWhenReady: offerSwitchWhenReady,
    signInAgain: signInAgain, closeSheets: closeSheets,
    appShell: appShell, offerHandoff: offerHandoff, handoffNoteHtml: handoffNoteHtml, redeeming: function () { return redeeming; },
    _test: { setView: function (v) { view = v; }, getView: function () { return view; }, reset: function () { cfg = cfgP = account = nonce = nonceP = gisP = current = null; nonceAt = 0; offered = false; pendingChallenge = pendingCode = handoffSheet = null; redeeming = false; if (ticker) { clearInterval(ticker); ticker = null; } } }
  };

  // At load: the address may carry the way into (or back from) the browser sign-in for the app. Never allowed to break the page.
  try { takeParams(); if (pendingCode) redeemPending(); } catch (e) { redeeming = false; }
}(window));
