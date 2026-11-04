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
   - Motion follows prefers-reduced-motion.
   - Play starts at the lesson (past the title, notices and licence); "Play from the
     beginning" starts at the first item. The transcript can be filtered; while it is,
     Previous, Next and playing move through the items shown. */
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
    var transport = root.querySelector("[data-listen-transport]");
    var list = root.querySelector("[data-listen-transcript]");
    var status = root.querySelector("[data-listen-status]");
    var position = root.querySelector("[data-listen-position]");
    var voiceNote = root.querySelector("[data-listen-voice-note]");
    var voiceText = root.querySelector("[data-listen-voice-text]");
    var readLink = root.querySelector("[data-listen-read-link]");
    var chooseVoice = root.querySelector("[data-listen-choose-voice]");
    var moreControls = root.querySelector(".listen-more");
    var filterBox = root.querySelector("[data-listen-filter]");
    var findInput = root.querySelector("[data-listen-find]");
    var kindSelect = root.querySelector("[data-listen-kind]");
    var filterCount = root.querySelector("[data-listen-filter-count]");
    var voiceSelect = root.querySelector("[data-listen-voice]");
    var rateSelect = root.querySelector("[data-listen-rate]");
    var voiceField = root.querySelector("[data-listen-voice-field]");
    if (!controls || !transport || !list) return;
    if (!supported) {
      // The transcript stays as it is; say so once, where the controls would have been.
      var plain = root.querySelector("[data-listen-unsupported]");
      if (plain) plain.hidden = false;
      return;
    }
    var items = Array.prototype.slice.call(list.querySelectorAll("[data-listen-item]"));
    var button = {};
    ["play", "stop", "prev", "next", "heading", "equation", "beginning"].forEach(function (name) {
      button[name] = root.querySelector('[data-listen="' + name + '"]');
    });

    // "Follow along": the transcript scrolls to the item being spoken. On by default (the person
    // can turn it off, and the choice is kept in this browser); with prefers-reduced-motion
    // the scroll is a jump, not an animation.
    var follow = root.querySelector("[data-listen-follow]");
    var FOLLOW_KEY = "readablestem.listen.follow";
    if (follow) {
      try {
        if (window.localStorage.getItem(FOLLOW_KEY) === "off") follow.checked = false;
      } catch (e) {
        /* storage blocked: the default stays */
      }
      follow.addEventListener("change", function () {
        try {
          window.localStorage.setItem(FOLLOW_KEY, follow.checked ? "on" : "off");
        } catch (e) {
          /* not kept */
        }
        say(status, follow.checked ? "Following along." : "Not following along.");
      });
    }

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
      // One button plays and pauses: its label says what pressing it does now.
      button.play.textContent = state === "playing" ? "Pause" : "Play";
      enable(button.play, ready());
      enable(button.stop, state !== "idle");
      ["prev", "next", "heading", "equation", "beginning"].forEach(function (name) {
        if (button[name]) button[name].disabled = !ready();
      });
      var total = shownCount();
      position.textContent = index >= 0
        ? "Item " + (index + 1) + " of " + items.length +
          (total < items.length ? " (" + total + " shown)" : "")
        : "";
    }

    // ------------------------------------------------------------ what is shown (the filter)

    function shown(i) {
      return !items[i].hidden;
    }

    function shownCount() {
      return items.filter(function (el) { return !el.hidden; }).length;
    }

    function nextShown(from, by) {
      // The next item shown after `from` (in direction `by`), or -1.
      for (var i = from + by; i >= 0 && i < items.length; i += by) if (shown(i)) return i;
      return -1;
    }

    function lessonIndex() {
      // The main content: past the title, language, notices and licence.
      var at = -1;
      items.forEach(function (el, i) {
        var role = el.querySelector(".listen-role");
        if (at < 0 && role && /^Main landmark/.test(role.textContent.trim())) at = i;
      });
      if (at < 0) {
        items.forEach(function (el, i) {
          if (at < 0 && (el.getAttribute("data-kinds") || "").split(" ")[0] === "heading") at = i;
        });
      }
      return at < 0 ? 0 : at;
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
      var folded = el.closest("details");
      if (folded) folded.open = true; // the items before the lesson are folded away
      if (follow && !follow.checked) return;
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
          var after = nextShown(i, 1);
          if (keepGoing && after >= 0) speakItem(after, true);
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
      var from = index >= 0 && !finished ? index : firstToPlay();
      if (from < 0) return say(status, "No items match. Clear the filter to play.");
      state = "playing";
      speakItem(from, true);
      say(status, "Playing.");
    }

    function firstToPlay() {
      // Play starts at the lesson, or at the next item shown when the filter hides it.
      var at = lessonIndex();
      if (shown(at)) return at;
      var later = nextShown(at, 1);
      return later >= 0 ? later : nextShown(-1, 1);
    }

    function togglePlay() {
      if (state === "playing") pause();
      else play();
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
      var from = index < 0 ? (by > 0 ? lessonIndex() - 1 : items.length) : index;
      var at = nextShown(from, by);
      if (at < 0 || !go(at)) {
        say(status, by > 0 ? "That was the last item." : "That is the first item.");
      }
    }

    function jump(token_name, label) {
      var from = index;
      for (var i = from + 1; i < items.length; i += 1) {
        if (!shown(i)) continue;
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
    press("play", togglePlay);
    press("stop", stop);
    button.prev.addEventListener("click", function () { step(-1); });
    button.next.addEventListener("click", function () { step(1); });
    button.heading.addEventListener("click", function () { jump("heading", "Heading"); });
    button.equation.addEventListener("click", function () { jump("equation", "Equation"); });
    button.beginning.addEventListener("click", function () {
      var first = nextShown(-1, 1);
      if (first < 0) return say(status, "No items match. Clear the filter to play.");
      state = "playing";
      speakItem(first, true);
      say(status, "Playing from the beginning.");
    });

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
      // Beside Play, outside the collapsed controls: why Play is off, and what to do.
      var local = localVoices(voices);
      var words = "";
      var pick = false;
      if (!voices.length) {
        words = "Play is off: this device has no voice to speak with.";
      } else if (!local.length && !voice) {
        words = "Play is off: this browser has only online voices, and some browsers send " +
          "the text to their maker to speak it.";
        pick = true;
      } else if (voice && voice.localService !== true) {
        words = "This voice is online: your browser may send the text to its maker to speak it.";
      }
      voiceText.textContent = words;
      if (chooseVoice) chooseVoice.hidden = !pick;
      if (readLink) readLink.hidden = !(words && !ready());
      voiceNote.hidden = !words;
      transport.classList.toggle("has-note", !!words);
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

    if (chooseVoice) {
      chooseVoice.addEventListener("click", function () {
        if (moreControls) moreControls.open = true;
        voiceSelect.focus();
      });
    }

    // The filter: a text search and a kind. Items that do not match are hidden (hidden
    // attribute, so they leave the reading order too); the count is said politely.
    function applyFilter() {
      var words = findInput ? findInput.value.trim().toLowerCase() : "";
      var kind = kindSelect ? kindSelect.value : "";
      items.forEach(function (el) {
        var okKind = !kind || (el.getAttribute("data-kinds") || "").split(" ").indexOf(kind) !== -1;
        var okText = !words || el.textContent.toLowerCase().indexOf(words) !== -1;
        el.hidden = !(okKind && okText);
      });
      // A folded group with nothing shown in it is not worth opening.
      Array.prototype.forEach.call(root.querySelectorAll("[data-listen-before]"), function (box) {
        box.hidden = !box.querySelector("[data-listen-item]:not([hidden])");
      });
      var total = shownCount();
      if (filterCount) {
        filterCount.textContent = words || kind
          ? "Showing " + total + " of " + items.length + (items.length === 1 ? " item." : " items.")
          : "";
      }
      if (index >= 0 && !shown(index)) stop();
      paint();
    }
    if (filterBox && findInput && kindSelect) {
      var typing = 0;
      findInput.addEventListener("input", function () {
        window.clearTimeout(typing);
        typing = window.setTimeout(applyFilter, 150);
      });
      kindSelect.addEventListener("change", applyFilter);
      filterBox.hidden = false;
    }

    // The sticky transport can wrap to several lines (a narrow screen, larger text, a longer
    // note), so the room items need above them when scrolled into view is its real height,
    // measured, not a fixed guess. listen.css reads --listen-sticky.
    function measure() {
      var sticky = window.getComputedStyle(transport).position === "sticky";
      var height = sticky ? Math.ceil(transport.getBoundingClientRect().height) : 0;
      root.style.setProperty("--listen-sticky", height + "px");
    }
    if (window.ResizeObserver) new window.ResizeObserver(measure).observe(transport);
    window.addEventListener("resize", measure);

    // Shortcuts: only while focus is inside this page's content, never with a modifier, never
    // in a form field, and never for a key the page did not ask for. A screen reader in
    // browse mode keeps its own keys; the buttons do the same things.
    root.addEventListener("keydown", function (event) {
      if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
      var t = event.target;
      if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
      var key = event.key;
      var done = true;
      if (key === "p") togglePlay();
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
    transport.hidden = false;
    measure();
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
