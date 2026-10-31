/* Product analytics loader (web/analytics.py documents what is and is not captured).
 *
 * Loads PostHog from this site's own /m/ path (a reverse proxy in the app), so the
 * Content-Security-Policy (script-src 'self') is unchanged and nothing is requested from
 * another host. base.html includes it on app pages when ANALYTICS_ENABLED is on; converted
 * documents, the demo and downloads never carry it. The pages work the same without it.
 *
 * It does nothing unless the host is the production domain, the browser is not automated,
 * and the visitor has not opted out (?notrack=1 turns it off in this browser, ?notrack=0
 * turns it back on).
 *
 * Captured: page views, page leaves and the funnel events the pages announce:
 *   <span hidden data-ph-event="signup_completed" data-ph-props='{"provider":"google"}'>
 *   <a href="..." data-ph-event="result_opened">          (sent when the link is clicked)
 * plus a click on any link to /demo (demo_opened). Properties are counts, booleans and short
 * categories. No autocapture, no session replay, no heatmaps, no copied text, no element
 * text, no form values; nobody is identified.
 */
(function () {
  "use strict";

  var HOSTS = ["readablestem.com", "www.readablestem.com"];
  var TOKEN = "phc_nT2JxCxLcAiSQGP4d4iJ2mWef6k3gNa6ptFaEJmz5cJ9";
  var SITE = "readablestem";
  var PERSONAL_SITE = "harishmanoharan.com";

  if (HOSTS.indexOf(location.hostname) === -1 || navigator.webdriver) return;

  var params = new URLSearchParams(location.search);
  try {
    if (params.has("notrack")) {
      if (params.get("notrack") === "0") localStorage.removeItem("hm_notrack");
      else localStorage.setItem("hm_notrack", "1");
    }
    if (localStorage.getItem("hm_notrack")) return;
  } catch (e) {
    /* storage blocked: carry on without an opt-out */
  }

  // PostHog's official loader stub (posthog.com/docs/libraries/js): queues calls until
  // array.js arrives. With api_host "<origin>/m" it fetches "<origin>/m/static/array.js".
  !(function (t, e) {
    var o, n, p, r;
    e.__SV || (window.posthog && window.posthog.__loaded) || ((window.posthog = e), (e._i = []), (e.init = function (i, s, a) {
      function g(t, e) {
        var o = e.split(".");
        2 == o.length && ((t = t[o[0]]), (e = o[1])),
          (t[e] = function () {
            t.push([e].concat(Array.prototype.slice.call(arguments, 0)));
          });
      }
      p ||
        (((p = t.createElement("script")).type = "text/javascript"),
        (p.crossOrigin = "anonymous"),
        (p.async = !0),
        (p.src = s.api_host.replace(".i.posthog.com", "-assets.i.posthog.com") + "/static/array.js"),
        (p.onerror = function () {
          p = null;
        }),
        (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(p, r));
      var u = e;
      for (
        void 0 !== a ? (u = e[a] = []) : (a = "posthog"),
          u.people = u.people || [],
          Object.defineProperty(u, "toString", {
            configurable: !0,
            enumerable: !0,
            writable: !0,
            value: function (t) {
              var e = "posthog";
              return "posthog" !== a && (e += "." + a), t || (e += " (stub)"), e;
            },
          }),
          Object.defineProperty(u.people, "toString", {
            configurable: !0,
            enumerable: !0,
            writable: !0,
            value: function () {
              return u.toString(1) + ".people (stub)";
            },
          }),
          o = "init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),
          n = 0;
        n < o.length;
        n++
      )
        g(u, o[n]);
      e._i.push([i, s, a]);
    }), (e.__SV = 1));
  })(document, window.posthog || []);

  var ref = (params.get("ref") || "").trim().toLowerCase().slice(0, 80);
  var hmId = params.get("hm_id");
  if (!hmId || !/^[A-Za-z0-9_-]{8,64}$/.test(hmId)) hmId = "";

  posthog.init(TOKEN, {
    api_host: location.origin + "/m",
    ui_host: "https://us.posthog.com",
    defaults: "2026-08-30",
    person_profiles: "identified_only",
    // The visitor id lives in localStorage: this loader sets no cookie.
    persistence: "localStorage",
    // The personal site hands its visitor id over in hm_id, so the journey reads as one person.
    bootstrap: hmId ? { distinctID: hmId } : {},
    autocapture: false,
    capture_pageview: true,
    capture_pageleave: true,
    disable_session_recording: true,
    enable_heatmaps: false,
    capture_dead_clicks: false,
    capture_exceptions: false,
    disable_surveys: true,
    capture_performance: false,
    advanced_disable_feature_flags: true,
    // Runs before the first $pageview, so these are on it.
    loaded: function (ph) {
      ph.register({ site: SITE });
      if (hmId) ph.register_once({ ref: PERSONAL_SITE });
      if (ref) {
        ph.register({ ref: ref });
        ph.register_once({ first_ref: ref });
      }
    },
  });

  // Once the first page view is sent, drop the tags from the address bar so a shared link
  // does not carry them.
  var TAGS = ["hm_id", "ref", "notrack"];
  var dirty = false;
  params.forEach(function (_, key) {
    if (TAGS.indexOf(key) !== -1 || key.indexOf("utm_") === 0) dirty = true;
  });
  if (dirty) {
    setTimeout(function () {
      var url = new URL(location.href);
      Array.from(url.searchParams.keys()).forEach(function (key) {
        if (TAGS.indexOf(key) !== -1 || key.indexOf("utm_") === 0) url.searchParams.delete(key);
      });
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    }, 1500);
  }

  // ---- funnel events announced by the page

  // Only counts, booleans and short categories get through, whatever a page says.
  function cleanProps(raw) {
    var out = {};
    var data;
    try {
      data = JSON.parse(raw || "{}");
    } catch (e) {
      return out;
    }
    Object.keys(data).forEach(function (key) {
      var value = data[key];
      if (!/^[a-z_]{1,32}$/.test(key)) return;
      if (typeof value === "number" || typeof value === "boolean") out[key] = value;
      else if (typeof value === "string" && value.length <= 24) out[key] = value;
    });
    return out;
  }

  // Events with a "once" key (one per job) are remembered in this browser.
  function firstTime(key) {
    if (!key) return true;
    try {
      var seen = JSON.parse(localStorage.getItem("rs_ph_once") || "[]");
      if (seen.indexOf(key) !== -1) return false;
      seen.push(key);
      localStorage.setItem("rs_ph_once", JSON.stringify(seen.slice(-50)));
    } catch (e) {
      /* no storage: send it */
    }
    return true;
  }

  function scan() {
    document.querySelectorAll("span[data-ph-event]:not([data-ph-done])").forEach(function (marker) {
      marker.setAttribute("data-ph-done", "");
      if (firstTime(marker.getAttribute("data-ph-once"))) {
        posthog.capture(marker.getAttribute("data-ph-event"), cleanProps(marker.getAttribute("data-ph-props")));
      }
    });
  }
  scan();
  // Status boxes swapped in by htmx (a conversion finishing) carry markers too.
  document.addEventListener("htmx:afterSettle", scan);

  document.addEventListener(
    "click",
    function (event) {
      if (!(event.target instanceof Element)) return;
      var link = event.target.closest("a[href]");
      if (!link) return;
      var name = link.getAttribute("data-ph-event");
      if (!name && link.getAttribute("href") === "/demo") name = "demo_opened";
      if (name) posthog.capture(name, cleanProps(link.getAttribute("data-ph-props")), { transport: "sendBeacon" });
    },
    true
  );
})();
