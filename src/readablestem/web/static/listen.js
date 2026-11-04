/* Listen as a student (web/listen.py): plays a listening transcript, or one short reading,
   with the browser's own speech (the Web Speech API). Loaded as a static file, so it runs
   under script-src 'self'; the same file is copied into the showcase site.

   Rules this file keeps:
   - The transcript is plain text in the page and works without this file. Nothing here is
     needed to read it, and nothing here moves focus or takes a key a screen reader needs.
   - Privacy: a voice that runs on this device (localService) is the default. When the browser
     lists only online voices, some browsers send the text to their maker to speak it, so
     nothing is spoken until the person chooses one on purpose (the full player) or presses the
     button a second time (a one-line "Hear this").
   - State changes (playing, paused, stopped, which item) go to a polite status line; the item
     being spoken is not announced again, because the voice is already saying it.
   - Motion follows prefers-reduced-motion. */
(function () {
  "use strict";

  var synth = window.speechSynthesis || null;
  var supported = !!(synth && typeof window.SpeechSynthesisUtterance === "function");
  var motion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var CHUNK = 200; // a long utterance can be cut off by a browser: speak it in pieces

  // ---------------------------------------------------------------- voices

  function voiceList() {
    try {
      return Array.prototype.slice.call(synth.getVoices() || []);
    } catch (e) {
      return [];
    }
  }

  function primary(lang) {
    return String(lang || "").toLowerCase().replace("_", "-").split("-")[0];
  }

  function best(voices, lang) {
    // The voice for `lang` that the browser calls its default, else the first for `lang`,
    // else the default, else the first.
    var want = primary(lang);
    var same = voices.filter(function (v) { return primary(v.lang) === want; });
    var pool = same.length ? same : voices;
    return pool.filter(function (v) { return v.default; })[0] || pool[0] || null;
  }

  function localVoices(voices) {
    return voices.filter(function (v) { return v.localService === true; });
  }

  function whenVoices(done) {
    // Browsers fill the list late: Chrome after "voiceschanged", Safari at once.
    var seen = -1;
    function check(force) {
      var list = voiceList();
      if ((!list.length && !force) || list.length === seen) return;
      seen = list.length;
      done(list);
    }
    if (synth.addEventListener) synth.addEventListener("voiceschanged", function () { check(false); });
    else synth.onvoiceschanged = function () { check(false); };
    check(false);
    window.setTimeout(function () { check(true); }, 1200);
  }

  // ---------------------------------------------------------------- speech in pieces

  function pieces(text) {
    var parts = [];
    var re = /[.!?;:]\s+/g;
    var last = 0;
    var m;
    while ((m = re.exec(text))) {
      parts.push(text.slice(last, m.index + 1));
      last = re.lastIndex;
    }
    parts.push(text.slice(last));
    var out = [];
    var cur = "";
    parts.forEach(function (part) {
      part = part.trim();
      if (!part) return;
      if (cur && (cur + " " + part).length > CHUNK) {
        out.push(cur);
        cur = part;
      } else {
        cur = cur ? cur + " " + part : part;
      }
    });
    if (cur) out.push(cur);
    var final = [];
    out.forEach(function (piece) {
      while (piece.length > CHUNK) {
        var cut = piece.lastIndexOf(" ", CHUNK);
        if (cut < 1) cut = CHUNK;
        final.push(piece.slice(0, cut));
        piece = piece.slice(cut).trim();
      }
      if (piece) final.push(piece);
    });
    return final;
  }

  var holding = null; // the utterance being spoken: some browsers drop it if nothing holds it

  function speakPieces(text, opts, token, isCurrent, onDone, onError) {
    var list = pieces(text);
    var at = 0;
    (function next() {
      if (!isCurrent(token)) return;
      if (at >= list.length) {
        onDone();
        return;
      }
      var u = new window.SpeechSynthesisUtterance(list[at]);
      u.rate = opts.rate;
      if (opts.voice) u.voice = opts.voice;
      if (opts.lang) u.lang = opts.lang;
      u.onend = function () {
        if (!isCurrent(token)) return;
        at += 1;
        next();
      };
      u.onerror = function (event) {
        if (!isCurrent(token)) return;
        var why = event && event.error;
        if (why === "canceled" || why === "interrupted") return;
        onError();
      };
      holding = u;
      synth.speak(u);
    })();
  }

  function say(el, text) {
    // A polite status line: cleared first so the same words twice are spoken twice.
    if (!el) return;
    el.textContent = "";
    window.setTimeout(function () { el.textContent = text; }, 40);
  }

  // ---------------------------------------------------------------- the full player

  function initPlayer(root) {
    var controls = root.querySelector("[data-listen-controls]");
    var list = root.querySelector("[data-listen-transcript]");
    var status = root.querySelector("[data-listen-status]");
    var position = root.querySelector("[data-listen-position]");
    var voiceNote = root.querySelector("[data-listen-voice-note]");
    var voiceSelect = root.querySelector("[data-listen-voice]");
    var rateSelect = root.querySelector("[data-listen-rate]");
    var voiceField = root.querySelector("[data-listen-voice-field]");
    if (!controls || !list) return;
    if (!supported) {
      // The transcript stays as it is; say so once, where the controls would have been.
      var plain = root.querySelector("[data-listen-unsupported]");
      if (plain) plain.hidden = false;
      return;
    }
    var items = Array.prototype.slice.call(list.querySelectorAll("[data-listen-item]"));
    var button = {};
    ["play", "pause", "stop", "prev", "next", "heading", "equation"].forEach(function (name) {
      button[name] = controls.querySelector('[data-listen="' + name + '"]');
    });

    var state = "idle"; // idle, playing, paused
    var index = -1;
    var token = 0;
    var voice = null;
    var voices = [];
    var rate = 1;
    var finished = false;

    function lang(i) {
      var el = items[i];
      return (el && el.closest("[lang]") && el.closest("[lang]").getAttribute("lang")) || "";
    }

    function sayOf(i) {
      var el = items[i];
      var said = el.getAttribute("data-say");
      if (said !== null) return said;
      var text = el.querySelector(".listen-text");
      var role = el.querySelector(".listen-role");
      return ((text && text.textContent) || (role && role.textContent) || "").trim();
    }

    function ready() {
      return !!voice;
    }

    function enable(el, on) {
      // aria-disabled, not disabled: the button just pressed must keep focus (a disabled
      // button drops it), and a screen reader can still find and name it.
      if (on) el.removeAttribute("aria-disabled");
      else el.setAttribute("aria-disabled", "true");
    }

    function paint() {
      enable(button.play, ready() && state !== "playing");
      enable(button.pause, state === "playing");
      enable(button.stop, state !== "idle");
      ["prev", "next", "heading", "equation"].forEach(function (name) {
        button[name].disabled = !ready();
      });
      position.textContent = index >= 0 ? "Item " + (index + 1) + " of " + items.length : "";
    }

    function mark(i) {
      items.forEach(function (el) {
        el.classList.remove("is-current");
        el.removeAttribute("aria-current");
      });
      if (i < 0) return;
      var el = items[i];
      el.classList.add("is-current");
      el.setAttribute("aria-current", "true");
      el.scrollIntoView({
        block: "center",
        behavior: motion && motion.matches ? "auto" : "smooth",
      });
    }

    function cancel() {
      token += 1;
      synth.cancel();
    }

    function setState(next, words) {
      state = next;
      paint();
      if (words) say(status, words);
    }

    function opts(i) {
      return { rate: rate, voice: voice, lang: voice ? voice.lang : lang(i) };
    }

    function current(t) { return t === token; }

    function speakItem(i, keepGoing) {
      cancel();
      var mine = token;
      index = i;
      finished = false;
      mark(i);
      paint();
      speakPieces(
        sayOf(i),
        opts(i),
        mine,
        current,
        function () {
          if (keepGoing && i + 1 < items.length) speakItem(i + 1, true);
          else if (keepGoing) {
            finished = true;
            index = -1;
            mark(-1);
            setState("idle", "Finished.");
          } else setState("idle");
        },
        function () {
          setState("idle", "The voice stopped. Choose another voice, or read the transcript.");
        }
      );
    }

    function play() {
      if (!ready()) return;
      if (state === "paused") {
        synth.resume();
        setState("playing", "Playing.");
        return;
      }
      state = "playing";
      var from = index >= 0 && !finished ? index : 0;
      speakItem(from, true);
      say(status, "Playing.");
    }

    function pause() {
      if (state !== "playing") return;
      synth.pause();
      setState("paused", "Paused.");
    }

    function stop() {
      if (state === "idle" && index < 0) return;
      cancel();
      index = -1;
      mark(-1);
      setState("idle", "Stopped.");
    }

    function go(i, words) {
      if (!ready() || i < 0 || i >= items.length) return false;
      var keep = state !== "idle";
      state = keep ? "playing" : "idle";
      speakItem(i, keep);
      say(status, words || "Item " + (i + 1) + " of " + items.length + ".");
      return true;
    }

    function step(by) {
      var from = index < 0 ? (by > 0 ? -1 : items.length) : index;
      if (!go(from + by)) say(status, by > 0 ? "That was the last item." : "That is the first item.");
    }

    function jump(token_name, label) {
      var from = index;
      for (var i = from + 1; i < items.length; i += 1) {
        var kinds = (items[i].getAttribute("data-kinds") || "").split(" ");
        if (kinds.indexOf(token_name) !== -1) {
          go(i, label + ", item " + (i + 1) + " of " + items.length + ".");
          return;
        }
      }
      say(status, "No more " + label.toLowerCase() + "s.");
    }

    function press(name, action) {
      button[name].addEventListener("click", function () {
        if (button[name].getAttribute("aria-disabled") !== "true") action();
      });
    }
    press("play", play);
    press("pause", pause);
    press("stop", stop);
    button.prev.addEventListener("click", function () { step(-1); });
    button.next.addEventListener("click", function () { step(1); });
    button.heading.addEventListener("click", function () { jump("heading", "Heading"); });
    button.equation.addEventListener("click", function () { jump("equation", "Equation"); });

    rateSelect.addEventListener("change", function () {
      rate = parseFloat(rateSelect.value) || 1;
      if (state === "playing") speakItem(index, true);
      say(status, "Speed " + rate + ".");
    });

    voiceSelect.addEventListener("change", function () {
      var chosen = voices.filter(function (v) { return v.voiceURI === voiceSelect.value; })[0] || null;
      voice = chosen;
      explain();
      paint();
      if (state === "playing") speakItem(index, true);
    });

    function explain() {
      var local = localVoices(voices);
      if (!voices.length) {
        voiceNote.textContent = "No voice on this device. Read the transcript below, or use " +
          "your own screen reader.";
      } else if (!local.length && !voice) {
        voiceNote.textContent = "This browser has only online voices. Some browsers send the " +
          "text to their maker to speak it. Choose a voice to play the transcript this way, " +
          "or read the transcript below.";
      } else if (voice && voice.localService !== true) {
        voiceNote.textContent = "This voice is online: your browser may send the text to its " +
          "maker to speak it.";
      } else {
        voiceNote.textContent = "";
      }
      voiceNote.hidden = !voiceNote.textContent;
    }

    function fill(found) {
      voices = found;
      var local = localVoices(voices);
      var online = voices.filter(function (v) { return v.localService !== true; });
      var keep = voice && voice.voiceURI;
      voiceSelect.textContent = "";
      function group(label, set) {
        if (!set.length) return;
        var g = document.createElement("optgroup");
        g.label = label;
        set.forEach(function (v) {
          var o = document.createElement("option");
          o.value = v.voiceURI;
          o.textContent = v.name + " (" + v.lang + ")";
          g.appendChild(o);
        });
        voiceSelect.appendChild(g);
      }
      if (!local.length && online.length) {
        var none = document.createElement("option");
        none.value = "";
        none.textContent = "Choose a voice";
        voiceSelect.appendChild(none);
      }
      group("On this device", local);
      group("Online (may send the text to the browser's maker)", online);
      if (keep && voices.some(function (v) { return v.voiceURI === keep; })) {
        voice = voices.filter(function (v) { return v.voiceURI === keep; })[0];
      } else if (local.length) {
        voice = best(local, document.documentElement.getAttribute("lang") || "en");
      } else {
        voice = null;
      }
      voiceSelect.value = voice ? voice.voiceURI : "";
      voiceSelect.disabled = !voices.length;
      voiceField.hidden = !voices.length; // an empty menu explains nothing
      explain();
      paint();
    }

    // Shortcuts: only while focus is inside this page's content, never with a modifier, never
    // in a form field, and never for a key the page did not ask for. A screen reader in
    // browse mode keeps its own keys; the buttons do the same things.
    root.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
      var t = event.target;
      if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
      var key = event.key;
      var done = true;
      if (key === "p") (state === "playing" ? pause : play)();
      else if (key === "s" || key === "Escape") stop();
      else if (key === "]") step(1);
      else if (key === "[") step(-1);
      else if (key === "h") jump("heading", "Heading");
      else if (key === "e") jump("equation", "Equation");
      else done = false;
      if (done) event.preventDefault();
    });

    window.addEventListener("pagehide", function () { synth.cancel(); });

    controls.hidden = false;
    rateSelect.value = "1";
    paint();
    whenVoices(fill);
  }

  // ---------------------------------------------------------------- "Hear this" buttons

  function initHear() {
    var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-hear]"));
    if (!buttons.length || !supported) return;
    var token = 0;
    var speaking = null;
    var online = false; // the person has agreed, on this page, to an online voice
    var asked = null; // the button whose press was told about online voices

    function reset() {
      buttons.forEach(function (b) {
        label(b, b.getAttribute("data-hear-idle") || "Hear this");
        b.removeAttribute("aria-pressed");
      });
      speaking = null;
    }

    function label(button, words) {
      var el = button.querySelector("[data-hear-label]");
      if (el) el.textContent = words;
    }

    function note(button, words) {
      var el = button.parentNode.querySelector("[data-hear-note]");
      if (el) say(el, words);
    }

    function lines(button) {
      var out = [];
      [["a", "data-hear-a"], ["b", "data-hear-b"]].forEach(function (pair) {
        var text = button.getAttribute(pair[1]);
        if (!text) return;
        var lead = button.getAttribute(pair[1] + "-lead");
        out.push(lead ? lead + ": " + text : text);
      });
      return out.join(". ");
    }

    function start(button, voice, voiceLang) {
      token += 1;
      var mine = token;
      synth.cancel();
      reset();
      speaking = button;
      label(button, "Stop");
      button.setAttribute("aria-pressed", "true");
      note(button, "Playing.");
      speakPieces(
        lines(button),
        { rate: 1, voice: voice, lang: voice ? voice.lang : voiceLang },
        mine,
        function (t) { return t === token; },
        function () { reset(); note(button, ""); },
        function () { reset(); note(button, "The voice stopped."); }
      );
    }

    buttons.forEach(function (button) {
      button.hidden = false;
      button.addEventListener("click", function () {
        if (speaking === button) {
          token += 1;
          synth.cancel();
          reset();
          note(button, "Stopped.");
          return;
        }
        var voiceLang = button.getAttribute("data-hear-lang") || document.documentElement.getAttribute("lang") || "en";
        var found = voiceList();
        if (!found.length) {
          note(button, "This browser has no voices to speak with.");
          return;
        }
        var local = localVoices(found);
        if (local.length) {
          start(button, best(local, voiceLang), voiceLang);
        } else if (online || asked === button) {
          online = true; // the second press is the person's own choice
          asked = null;
          start(button, best(found, voiceLang), voiceLang);
        } else {
          asked = button;
          note(button, "This browser has only online voices, and some browsers send the text " +
            "to their maker to speak it. Press the button again to hear it anyway.");
        }
      });
    });
    window.addEventListener("pagehide", function () { synth.cancel(); });
  }

  function init() {
    var players = document.querySelectorAll("[data-listen-player]");
    Array.prototype.forEach.call(players, initPlayer);
    initHear();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
