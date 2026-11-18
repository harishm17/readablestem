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
   - The player's whole state is one object (`S`). Its place is the id of an item (the page's
     data-listen-id, made from the element the item came from), never a count or a word. The
     place, speed, voice name, follow and shortcuts are kept in this browser's localStorage,
     under the document's id and result version: ids and settings only, no text of the
     document. A browser that blocks storage still plays, and just remembers nothing.
   - The reader sets the place by clicking an item, by an index link (headings, equations,
     pages), or with Previous and Next. Play starts from the place; Stop keeps it; "Play from
     the beginning" is the restart. Logic reads data-kinds and data-listen-* attributes, never
     the words on the page (the filter's text search is the one place that reads words, and
     it searches what the person typed).
   - The place is shown with aria-current (state, not announced by itself) and a class.
     Focus never moves. The polite status line says only what the person asked for (playing,
     paused, stopped, "Place set to item 47 of 520"), never each item as it is spoken: a
     screen reader user who also hears the voice gets the item once.
   - Play starts at the lesson (past the title, notices and licence) when there is no place.
     The transcript can be filtered; while it is, Previous, Next and playing move through the
     items shown. */
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

  // ---------------------------------------------------------------- what is kept in the browser

  var PREFIX = "readablestem.listen:";
  var INDEX = PREFIX + "places"; // the keys kept, oldest first
  var KEEP = 20; // places of this many documents are kept, the oldest dropped
  var BACK = 8; // ids of the items before the place, for a place that no longer exists

  function storage(job) {
    // Every read and write goes through here: a blocked or full store never stops the player.
    try {
      return job(window.localStorage);
    } catch (e) {
      return null;
    }
  }

  function readJson(key) {
    var raw = storage(function (store) { return store.getItem(key); });
    try {
      var value = raw ? JSON.parse(raw) : null;
      return value && typeof value === "object" ? value : null;
    } catch (e) {
      return null;
    }
  }

  function keyOf(doc, version) {
    return PREFIX + doc + ":" + version;
  }

  function recall(doc, version) {
    // What was kept for this document and version, else for the same document's other
    // version (a review changed it: the ids that still exist still work).
    var own = readJson(keyOf(doc, version));
    if (own) return own;
    var keys = readJson(INDEX);
    var list = keys && Array.isArray(keys.keys) ? keys.keys : [];
    for (var i = list.length - 1; i >= 0; i -= 1) {
      if (typeof list[i] === "string" && list[i].indexOf(PREFIX + doc + ":") === 0) return readJson(list[i]);
    }
    return null;
  }

  function keep(doc, version, value) {
    var key = keyOf(doc, version);
    var index = readJson(INDEX);
    var list = index && Array.isArray(index.keys) ? index.keys : [];
    var drop = [];
    list = list.filter(function (k) {
      if (k === key) return false;
      if (typeof k === "string" && k.indexOf(PREFIX + doc + ":") === 0) { drop.push(k); return false; }
      return typeof k === "string";
    });
    list.push(key);
    while (list.length > KEEP) drop.push(list.shift());
    storage(function (store) {
      store.setItem(key, JSON.stringify(value));
      store.setItem(INDEX, JSON.stringify({ keys: list }));
      drop.forEach(function (k) { store.removeItem(k); });
    });
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
    var follow = root.querySelector("[data-listen-follow]");
    var keys = root.querySelector("[data-listen-keys]");

    // The one object that is the player's state. `at` is the selected item (the place), `mode`
    // is idle, playing or paused. What is kept in the browser is read from it (`remember`).
    var S = {
      mode: "idle",
      at: -1,
      done: false, // the last item has been spoken: the place is cleared, Play starts at the lesson
      rate: 1,
      voiceName: "", // the voice the person chose; "" while the browser's default is in use
      follow: true, // the transcript scrolls to the item being spoken
      keys: true, // single-key shortcuts
    };
    var token = 0;
    var voice = null;
    var voices = [];
    var doc = root.getAttribute("data-listen-doc") || "page:" + window.location.pathname;
    var version = root.getAttribute("data-listen-version") || "page";

    function idOf(i) {
      return items[i].getAttribute("data-listen-id") || items[i].id;
    }

    function indexOfId(id) {
      for (var i = 0; i < items.length; i += 1) if (idOf(i) === id) return i;
      return -1;
    }

    function remember() {
      var back = [];
      for (var i = S.at - 1; i >= 0 && back.length < BACK; i -= 1) back.push(idOf(i));
      keep(doc, version, {
        at: S.at >= 0 ? idOf(S.at) : "",
        back: back,
        rate: S.rate,
        voice: S.voiceName,
        follow: S.follow,
        keys: S.keys,
      });
    }

    function restore() {
      // The place comes back by id; an id that is gone (a new version) falls back to the
      // nearest earlier item that still exists, then to nothing (Play starts at the lesson).
      var saved = recall(doc, version);
      if (!saved) return;
      if (typeof saved.rate === "number" && saved.rate > 0) S.rate = saved.rate;
      if (typeof saved.voice === "string") S.voiceName = saved.voice;
      if (typeof saved.follow === "boolean") S.follow = saved.follow;
      if (typeof saved.keys === "boolean") S.keys = saved.keys;
      var ids = [saved.at].concat(Array.isArray(saved.back) ? saved.back : []);
      for (var i = 0; i < ids.length; i += 1) {
        var at = typeof ids[i] === "string" && ids[i] ? indexOfId(ids[i]) : -1;
        if (at >= 0) {
          S.at = at;
          return;
        }
      }
    }

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
      // One button plays and pauses: its label says what pressing it does now, and with a
      // place kept, where it will start.
      var total = shownCount();
      button.play.textContent = S.mode === "playing"
        ? "Pause"
        : S.mode === "idle" && S.at >= 0
          ? "Resume from item " + (S.at + 1) + " of " + items.length
          : "Play";
      enable(button.play, ready());
      enable(button.stop, S.mode !== "idle");
      ["prev", "next", "heading", "equation", "beginning"].forEach(function (name) {
        if (button[name]) button[name].disabled = !ready();
      });
      position.textContent = S.at >= 0
        ? "Item " + (S.at + 1) + " of " + items.length +
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
      // The main content: past the title, language, notices and licence. The page marks the
      // lesson's first item; a page with no main landmark starts at its first heading.
      var at = -1;
      items.forEach(function (el, i) {
        if (at < 0 && el.hasAttribute("data-listen-lesson")) at = i;
      });
      if (at < 0) {
        items.forEach(function (el, i) {
          if (at < 0 && (el.getAttribute("data-kinds") || "").split(" ")[0] === "heading") at = i;
        });
      }
      return at < 0 ? 0 : at;
    }

    function mark(i, scroll) {
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
      if (!S.follow || scroll === false) return;
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
      S.mode = next;
      paint();
      if (words) say(status, words);
    }

    function opts(i) {
      return { rate: S.rate, voice: voice, lang: voice ? voice.lang : lang(i) };
    }

    function current(t) { return t === token; }

    function speakItem(i, keepGoing) {
      cancel();
      var mine = token;
      S.at = i;
      S.done = false;
      mark(i);
      paint();
      remember();
      speakPieces(
        sayOf(i),
        opts(i),
        mine,
        current,
        function () {
          var after = nextShown(i, 1);
          if (keepGoing && after >= 0) speakItem(after, true);
          else if (keepGoing) {
            S.done = true;
            S.at = -1;
            mark(-1);
            remember();
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
      if (S.mode === "paused") {
        synth.resume();
        setState("playing", "Playing.");
        return;
      }
      var from = placeToPlay();
      if (from < 0) return say(status, "No items match. Clear the filter to play.");
      S.mode = "playing";
      speakItem(from, true);
      say(status, "Playing.");
    }

    function placeToPlay() {
      // From the place when there is one (or the next item shown when the filter hides it),
      // else from the lesson.
      if (S.at >= 0 && !S.done) {
        if (shown(S.at)) return S.at;
        var after = nextShown(S.at, 1);
        if (after >= 0) return after;
      }
      return firstToPlay();
    }

    function firstToPlay() {
      // Play starts at the lesson, or at the next item shown when the filter hides it.
      var at = lessonIndex();
      if (shown(at)) return at;
      var later = nextShown(at, 1);
      return later >= 0 ? later : nextShown(-1, 1);
    }

    function togglePlay() {
      if (S.mode === "playing") pause();
      else play();
    }

    function pause() {
      if (S.mode !== "playing") return;
      synth.pause();
      setState("paused", "Paused.");
    }

    function stop() {
      // Stop keeps the place: Play then resumes from it.
      if (S.mode === "idle") return;
      cancel();
      setState("idle", "Stopped.");
    }

    function go(i, words) {
      if (!ready() || i < 0 || i >= items.length) return false;
      var keepGoing = S.mode !== "idle";
      S.mode = keepGoing ? "playing" : "idle";
      speakItem(i, keepGoing);
      say(status, words || "Item " + (i + 1) + " of " + items.length + ".");
      return true;
    }

    function select(i, scroll) {
      // The reader sets the place (a click, an index link). While playing, the voice moves
      // there; otherwise nothing is spoken and Play starts from it.
      if (i < 0 || i >= items.length) return;
      if (S.mode === "playing" && ready()) {
        S.mode = "playing";
        speakItem(i, true);
      } else {
        if (S.mode === "paused") cancel(); // the held item is not the place any more
        S.mode = "idle";
        S.at = i;
        S.done = false;
        mark(i, scroll);
        paint();
        remember();
      }
      say(status, "Place set to item " + (i + 1) + " of " + items.length + ".");
    }

    function step(by) {
      var from = S.at < 0 ? (by > 0 ? lessonIndex() - 1 : items.length) : S.at;
      var at = nextShown(from, by);
      if (at < 0 || !go(at)) {
        say(status, by > 0 ? "That was the last item." : "That is the first item.");
      }
    }

    function jump(kind, label) {
      for (var i = S.at + 1; i < items.length; i += 1) {
        if (!shown(i)) continue;
        var kinds = (items[i].getAttribute("data-kinds") || "").split(" ");
        if (kinds.indexOf(kind) !== -1) {
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
      S.mode = "playing";
      speakItem(first, true);
      say(status, "Playing from the beginning.");
    });

    // A click on an item sets the place (not when the person is selecting text to copy);
    // an index link scrolls to its item as a link always does, and sets the place too.
    list.addEventListener("click", function (event) {
      var target = event.target;
      if (target && target.closest && target.closest("a, button, input, select, summary")) return;
      var el = target && target.closest ? target.closest("[data-listen-item]") : null;
      var picked = window.getSelection ? String(window.getSelection()) : "";
      if (!el || picked) return;
      select(items.indexOf(el));
    });
    Array.prototype.forEach.call(root.querySelectorAll(".listen-index"), function (index) {
      index.addEventListener("click", function (event) {
        var link = event.target && event.target.closest ? event.target.closest("a[href^='#item-']") : null;
        if (!link) return;
        var at = items.indexOf(root.querySelector("#" + link.getAttribute("href").slice(1)));
        if (at >= 0) select(at, false);
      });
    });

    rateSelect.addEventListener("change", function () {
      S.rate = parseFloat(rateSelect.value) || 1;
      remember();
      if (S.mode === "playing") speakItem(S.at, true);
      say(status, "Speed " + S.rate + ".");
    });

    voiceSelect.addEventListener("change", function () {
      var chosen = voices.filter(function (v) { return v.voiceURI === voiceSelect.value; })[0] || null;
      voice = chosen;
      S.voiceName = chosen ? chosen.name : "";
      remember();
      explain();
      paint();
      if (S.mode === "playing") speakItem(S.at, true);
    });

    if (follow) {
      follow.addEventListener("change", function () {
        S.follow = follow.checked;
        remember();
        say(status, S.follow ? "Following along." : "Not following along.");
      });
    }
    if (keys) {
      keys.addEventListener("change", function () {
        S.keys = keys.checked;
        remember();
        say(status, S.keys ? "Keyboard shortcuts on." : "Keyboard shortcuts off.");
      });
    }

    function explain() {
      // Beside Play, outside the collapsed controls: why Play is off, and what to do.
      var local = localVoices(voices);
      var words = "";
      var pick = false;
      if (!voices.length) {
        words = "No voice on this device.";
      } else if (!local.length && !voice) {
        words = "Only online voices: some browsers send the text to their maker.";
        pick = true;
      } else if (voice && voice.localService !== true) {
        words = "Online voice: your browser may send the text to its maker.";
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
      var keepURI = voice && voice.voiceURI;
      function named(name) {
        return name ? voices.filter(function (v) { return v.name === name; })[0] || null : null;
      }
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
      // The voice the person chose, here or on an earlier visit (an online one was their own
      // choice), else the one in use, else the best on this device.
      var chosen = named(S.voiceName);
      if (chosen) {
        voice = chosen;
      } else if (keepURI && voices.some(function (v) { return v.voiceURI === keepURI; })) {
        voice = voices.filter(function (v) { return v.voiceURI === keepURI; })[0];
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
      if (S.at >= 0 && !shown(S.at) && S.mode !== "idle") stop(); // the place stays
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
    // in a form field, never for a key the page did not ask for, and not at all when the
    // person has turned them off. A screen reader in
    // browse mode keeps its own keys; the buttons do the same things.
    root.addEventListener("keydown", function (event) {
      if (!S.keys || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
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

    restore();
    if (follow) follow.checked = S.follow;
    if (keys) keys.checked = S.keys;
    rateSelect.value = String(S.rate);
    if (rateSelect.value !== String(S.rate)) {
      S.rate = 1; // a speed this menu does not have
      rateSelect.value = "1";
    }
    root.classList.add("is-live");
    controls.hidden = false;
    transport.hidden = false;
    measure();
    // Back on the page: show where the place is, unless the address already points somewhere.
    if (S.at >= 0) mark(S.at, !window.location.hash);
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
