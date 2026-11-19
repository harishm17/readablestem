/* Error summary links: land on the field with its label, hint and error in view.
 *
 * Following a link to a form control scrolls the control itself to the top of the
 * window, so what explains it (the label, the hint and the error message above it) is
 * cut off. The CSS scroll-margin-top on form controls is the fallback without
 * JavaScript, but no fixed margin is enough on a narrow screen (320 CSS px, or 400%
 * zoom), where those lines wrap to several times their height. As the GOV.UK error
 * summary does, this scrolls the start of the field (its .field wrapper) to the top of
 * the window and then focuses the control without scrolling again.
 *
 * When the whole field is taller than the window, the focused control stays in view
 * (its bottom edge at the bottom of the window) and as much as fits above it shows.
 * The page works the same without this file; it is loaded by base.html (CSP
 * script-src 'self', no inline script).
 */
(function () {
  "use strict";

  var GAP = 8; // CSS px left between the top of the window and the field

  // What should be at the top of the window: the field's wrapper, else its label, else
  // an error message just above the target (the decision buttons of a review card).
  function startOf(target) {
    var field = target.closest(".field");
    if (field) return field;
    var label = target.id
      ? document.querySelector('label[for="' + CSS.escape(target.id) + '"]')
      : null;
    if (label) return label;
    var before = target.previousElementSibling;
    if (before && before.classList.contains("error-message")) return before;
    return target;
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    var link = event.target.closest(".error-summary a[href^='#']");
    if (!link) return;
    var target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (!target) return;
    event.preventDefault();
    var top = startOf(target).getBoundingClientRect().top - GAP;
    var bottom = target.getBoundingClientRect().bottom + GAP;
    // Start of the field at the top, unless that leaves the control below the window.
    window.scrollBy(0, Math.max(top, bottom - window.innerHeight));
    target.focus({ preventScroll: true });
  });
})();

/* Times (UX review MF16). The server writes every time as
 * <time class="local-time" datetime="2026-10-09T23:32:00Z">Oct 9, 2026, 23:32 UTC</time>
 * (the `utc` template filter). This rewrites it in the reader's own time zone, with how
 * far away it is when that is within a week: "Fri, Oct 9, 6:32 PM CDT (in 23 hours)".
 * Without JavaScript the UTC text stays. The pages are in English, so the words are
 * English whatever the browser's language; only the time zone is the reader's. Lists
 * swapped in by htmx are rewritten too.
 */
(function () {
  "use strict";

  var LANG = document.documentElement.lang || "en";
  var WEEK_MS = 7 * 24 * 3600 * 1000;
  var stamp, relative;
  try {
    stamp = new Intl.DateTimeFormat(LANG, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
    relative = new Intl.RelativeTimeFormat(LANG, { numeric: "auto" });
  } catch (e) {
    return; // no Intl: the UTC text stays
  }

  function distance(ms) {
    var minutes = Math.round(ms / 60000);
    if (minutes === 0) return "";
    if (Math.abs(minutes) < 60) return relative.format(minutes, "minute");
    var hours = Math.round(ms / 3600000);
    if (Math.abs(hours) < 48) return relative.format(hours, "hour");
    return relative.format(Math.round(ms / 86400000), "day");
  }

  var TIMES = "time.local-time[datetime]:not([data-local])";

  function rewrite(el) {
    var when = new Date(el.getAttribute("datetime"));
    if (isNaN(when.getTime())) return;
    var ms = when.getTime() - Date.now();
    var away = Math.abs(ms) < WEEK_MS ? distance(ms) : "";
    el.textContent = stamp.format(when) + (away ? " (" + away + ")" : "");
    el.setAttribute("data-local", "");
  }

  // The times inside `root` (the whole page by default), and `root` itself if it is one.
  function localise(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (scope.matches && scope.matches(TIMES)) rewrite(scope);
    scope.querySelectorAll(TIMES).forEach(rewrite);
  }

  localise();
  // Whatever htmx swaps in (the documents list refreshes every 5 seconds) is rewritten the
  // moment it is inserted, before the browser paints it, so a refreshed list never shows
  // UTC where the first render showed the reader's time zone. Only the inserted nodes are
  // looked at, not the whole page again; the text this file writes is a text node and is
  // skipped, so it does not start the observer again.
  if (window.MutationObserver) {
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) localise(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  document.addEventListener("htmx:load", function (event) { localise(event.target); });
})();

/* Upload form (app.html, UX review MF21). Says at once when a chosen file is not a PDF,
 * is empty or is larger than the limit, instead of after sending all of it (35 s at
 * 5 Mbit/s for a refusal the browser can make instantly). The form takes several files
 * (T3-4): too many, or more than the request may carry together, is said the same way.
 * While good files are sent, says so ("Uploading notes.pdf. Keep this tab open."); the one
 * submit rule below keeps a second click from sending them twice. The server checks
 * everything again; without this file the form works the same, only slower to say no.
 */
(function () {
  "use strict";

  var input = document.getElementById("pdf");
  if (!input || !input.hasAttribute("data-max-bytes") || !input.form) return;
  var form = input.form;
  var box = document.getElementById("pdf-check");
  var status = document.getElementById("upload-status");
  var field = input.closest(".field");
  var maxBytes = Number(input.getAttribute("data-max-bytes"));
  var limitBytes = Number(input.getAttribute("data-limit-bytes") || maxBytes);
  var limitMb = input.getAttribute("data-limit-mb") || input.getAttribute("data-max-mb");
  var maxFiles = Number(input.getAttribute("data-max-files") || 1);
  var describedBy = input.getAttribute("aria-describedby") || "";
  var serverError = input.getAttribute("aria-invalid") === "true";
  if (!box || !status || !field) return;

  // A file between the two sizes is allowed: the server keeps it so its pages can be chosen.
  // "docx pptx": the Word and PowerPoint kinds this server converts (data-office, T3-30).
  var office = (input.getAttribute("data-office") || "").split(" ").filter(Boolean);
  // A LaTeX file or a zip of a project (data-latex, FEATURE_LATEX_SOURCE): the server checks it.
  var latex = input.hasAttribute("data-latex");
  // A zip of documents (data-zip, FEATURE_BATCH_ZIP): the server opens it.
  var zip = input.hasAttribute("data-zip");

  function problem(file) {
    var pdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf" ||
      (latex && /\.(tex|zip)$/i.test(file.name)) || (zip && /\.zip$/i.test(file.name));
    var word = office.some(function (kind) {
      return new RegExp("\\." + kind + "$", "i").test(file.name);
    });
    if (!pdf && !word) {
      // The same words as the server's refusal (`data-accepted-*`: web/office_convert.py).
      var note = input.getAttribute("data-accepted-note");
      return file.name + " is not " + input.getAttribute("data-accepted-what") + "." +
        (note ? " " + note : "");
    }
    if (file.size === 0) {
      return file.name + " is empty. Choose a file that has content.";
    }
    if (file.size > limitBytes) {
      return (
        file.name + " is larger than " + limitMb + " MB. Split it into smaller files and " +
        "upload each one."
      );
    }
    return "";
  }

  function problems(files) {
    if (!files || !files.length) return "";
    if (files.length > maxFiles) {
      return (
        "You chose " + files.length + " files. Upload " + maxFiles +
        (maxFiles === 1 ? " file" : " or fewer") + " at a time."
      );
    }
    var total = 0;
    for (var i = 0; i < files.length; i++) {
      var message = problem(files[i]);
      if (message) return message;
      total += files[i].size;
    }
    if (total > limitBytes) {
      return "These files add up to more than " + limitMb + " MB. Upload fewer of them at a time.";
    }
    return "";
  }

  // The same markup as the server's field error (app.html), in a live region.
  function show(message) {
    box.textContent = "";
    if (!message) {
      input.setAttribute("aria-describedby", describedBy);
      if (!serverError) {
        field.classList.remove("field-error");
        input.removeAttribute("aria-invalid");
      }
      return;
    }
    var line = document.createElement("p");
    line.className = "error-message";
    line.id = "pdf-check-error";
    var prefix = document.createElement("span");
    prefix.className = "visually-hidden";
    prefix.textContent = "Error: ";
    line.appendChild(prefix);
    line.appendChild(document.createTextNode(message));
    box.appendChild(line);
    field.classList.add("field-error");
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", (describedBy + " pdf-check-error").trim());
  }

  input.addEventListener("change", function () {
    show(problems(input.files));
  });

  form.addEventListener("submit", function (event) {
    var files = input.files;
    var message = problems(files);
    if (message) {
      event.preventDefault();
      show(message);
      input.focus();
      return;
    }
    if (!files || !files.length) return; // the server answers "Choose ... to upload."
    if (form.hasAttribute("data-submitting")) return; // already sending: the rule below
    // Said here, with the file's name; the one submit rule below then says nothing more.
    status.textContent =
      files.length === 1
        ? "Uploading " + files[0].name + ". Keep this tab open."
        : "Uploading " + files.length + " files. Keep this tab open.";
  });

  // Back to this page from the next one (the browser's page cache): ready again.
  window.addEventListener("pageshow", function () {
    status.textContent = "";
  });
})();

/* Upload form: the "Exam or quiz" hint. When the box is not ticked and a chosen file's name
 * holds an exam word, a line under the box says so (the polite region #exam-hint, filled the
 * way #pdf-check is). The words and the sentence come from the box's data attributes (written
 * once, web/exam.py); a name is read as web.exam.word_in reads it: a camel-case join, digits and
 * punctuation break words. It never ticks the box and never moves focus. Without this file the
 * form is as it was, and the job page says the same after the conversion.
 */
(function () {
  "use strict";

  var input = document.getElementById("pdf");
  var box = document.getElementById("exam");
  var region = document.getElementById("exam-hint");
  if (!input || !box || !region || !box.hasAttribute("data-name-words")) return;
  var words = box.getAttribute("data-name-words").split(" ").filter(Boolean);
  var sentence = box.getAttribute("data-hint") || "";

  function wordIn(name) {
    var spaced = name.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[^A-Za-z]+/g, " ").toLowerCase();
    var found = spaced.split(" ").filter(function (part) { return words.indexOf(part) >= 0; });
    return found.length ? found[0] : "";
  }

  function update() {
    var found = "";
    if (!box.checked && input.files) {
      for (var i = 0; i < input.files.length && !found; i++) found = wordIn(input.files[i].name);
    }
    var line = region.querySelector("p");
    if (!found) {
      if (line) region.textContent = "";
      return;
    }
    var text = sentence.replace("{word}", found);
    if (line && line.textContent === text) return;
    region.textContent = "";
    line = document.createElement("p");
    line.className = "hint";
    line.textContent = text;
    region.appendChild(line);
  }

  input.addEventListener("change", update);
  box.addEventListener("change", update);
  // Back to this page from the browser's page cache: the chosen files may still be there.
  window.addEventListener("pageshow", update);
})();

/* The one submit rule, in the browser (SW43; the server's half is web/forms.py). A form
 * drawn by the form(key) macro (components/forms.html, data-submit-once) is sent once:
 * the pressed button says what is happening ("Saving…", its data-busy-label or the
 * form's), is marked aria-busy and aria-disabled (not disabled: a disabled button leaves
 * the focus order and its name and value would not be sent), a polite live region says
 * the same words, and a second click or Enter sends nothing. Ready again when the page
 * comes back from the browser's page cache, and at the end of an htmx request the form
 * itself made. Another submit handler that stops the send (the upload's file checks)
 * runs first and wins. Without this file a second click is still answered once, by the
 * server: the form's request token.
 */
(function () {
  "use strict";

  var region = null;

  // One polite live region for the page, made when first needed.
  function say(words) {
    if (!region) {
      region = document.createElement("p");
      region.className = "visually-hidden";
      region.setAttribute("aria-live", "polite");
      document.body.appendChild(region);
    }
    region.textContent = "";
    // A moment later, so the same words twice in a row are still announced.
    window.setTimeout(function () { region.textContent = words; }, 50);
  }

  function pressedButton(form, event) {
    var button = event.submitter;
    if (button && button.form === form && button.tagName === "BUTTON") return button;
    return form.querySelector('button[type="submit"], button:not([type])');
  }

  function busy(form, button) {
    form.setAttribute("data-submitting", "");
    if (!button) return "";
    var words =
      button.getAttribute("data-busy-label") || form.getAttribute("data-busy-label") || "";
    button.setAttribute("aria-disabled", "true");
    button.setAttribute("aria-busy", "true");
    button.setAttribute("data-submit-pressed", "");
    // Only a button whose label is plain text takes the busy words in its place.
    if (words && !button.children.length) {
      button.setAttribute("data-label", button.textContent);
      button.textContent = words;
    }
    return words;
  }

  function ready(form) {
    form.removeAttribute("data-submitting");
    var button = form.querySelector("[data-submit-pressed]");
    if (!button) return;
    button.removeAttribute("aria-disabled");
    button.removeAttribute("aria-busy");
    button.removeAttribute("data-submit-pressed");
    if (button.hasAttribute("data-label")) {
      button.textContent = button.getAttribute("data-label");
      button.removeAttribute("data-label");
    }
  }

  // Bubbling to the document: the form's own submit handlers have run by now.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-submit-once")) return;
    if (form.hasAttribute("data-submitting")) {
      event.preventDefault(); // already on its way: the first click's answer will come
      return;
    }
    if (event.defaultPrevented) return; // another handler stopped it (a file check)
    var words = busy(form, pressedButton(form, event));
    var own = form.querySelector('[role="status"]');
    if (words && !(own && own.textContent)) say(words);
  });

  window.addEventListener("pageshow", function () {
    var forms = document.querySelectorAll("form[data-submitting]");
    for (var i = 0; i < forms.length; i++) ready(forms[i]);
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail && event.detail.elt;
    if (form instanceof HTMLFormElement && form.hasAttribute("data-submit-once")) ready(form);
  });

  // A list that refreshes by itself (web/poll.py) leaves a form that is on its way alone: the
  // answer would put a fresh, unpressed button where the busy one is. Nothing is lost: the
  // list still holds the version it had, so the next poll brings the same news.
  document.addEventListener("htmx:beforeSwap", function (event) {
    var detail = event.detail || {};
    var trigger = detail.elt && detail.elt.getAttribute ? detail.elt.getAttribute("hx-trigger") : "";
    var target = detail.target;
    if (!trigger || trigger.indexOf("every") !== 0 || !target || !target.querySelector) return;
    if (target.querySelector("form[data-submitting]")) detail.shouldSwap = false;
  });
})();

/* Upload form option groups (_upload_form.html). Each group is a <details> whose summary
 * line says what is set ("No course · Scanned pages kept as pictures"). The server writes
 * that line for the first view; this rewrites it as the boxes inside change, so a closed
 * group never states a choice that is no longer the one made. Without this file the line
 * stays as the page was sent, and the form still submits what is ticked.
 */
(function () {
  "use strict";

  function describe(group) {
    if (group.getAttribute("data-summary") === "retention") {
      var share = group.querySelector("#share");
      var keep = group.querySelector("#keep");
      var key = share && share.checked ? "share" : keep && keep.checked ? "keep" : "default";
      return group.getAttribute("data-text-" + key) || "";
    }
    var parts = [];
    var course = group.querySelector("select#course");
    if (course && course.selectedIndex >= 0) {
      parts.push(course.value ? course.options[course.selectedIndex].text : "No course");
    }
    group.querySelectorAll("input[type='checkbox'][data-on]").forEach(function (box) {
      parts.push(box.getAttribute(box.checked ? "data-on" : "data-off"));
    });
    return parts.join(" · ");
  }

  document.querySelectorAll("details.option-group[data-summary]").forEach(function (group) {
    var state = group.querySelector("[data-group-state]");
    if (!state) return;
    function update() {
      var text = describe(group);
      if (text && state.textContent.trim() !== text) state.textContent = text;
    }
    group.addEventListener("change", update);
    window.addEventListener("pageshow", update); // the browser may restore ticked boxes
    update();
  });
})();

/* How long a document or batch has been waiting or converting (_wait_note.html). The page
 * says the time as it was when it was sent; this keeps it current every half minute and, once
 * the wait passes what is usual for it, shows the "taking longer than usual" note. The words
 * are the server's (job_page.elapsed_words). Nothing here estimates when it will finish.
 */
(function () {
  "use strict";

  if (!document.querySelector("[data-wait-since]")) return;

  function words(seconds) {
    var minutes = Math.floor(Math.max(seconds, 0) / 60);
    if (minutes < 1) return "less than a minute";
    function plural(n, one) { return n + " " + one + (n === 1 ? "" : "s"); }
    if (minutes < 60) return plural(minutes, "minute");
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    return plural(hours, "hour") + (rest ? " " + plural(rest, "minute") : "");
  }

  function update() {
    // Looked up each time: htmx swaps the batch page's rows, and the job page's status box
    // says "finished" without reloading the page (then there is nothing left to count).
    var finished = !!document.querySelector("#job-status[data-finished]");
    document.querySelectorAll("[data-wait-since]").forEach(function (note) {
      note.hidden = finished;
      var since = Date.parse(note.getAttribute("data-wait-since"));
      if (finished || isNaN(since)) return;
      var seconds = (Date.now() - since) / 1000;
      var text = note.querySelector("[data-wait-text]");
      if (text) {
        text.textContent =
          note.getAttribute("data-wait-lead") + " " + words(seconds) +
          (note.getAttribute("data-wait-tail") || "") + ".";
      }
      var slow = note.querySelector("[data-wait-slow]");
      if (slow && seconds > Number(note.getAttribute("data-wait-slow-after"))) slow.hidden = false;
    });
  }

  window.setInterval(update, 30000);
  document.addEventListener("htmx:afterSwap", update);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) update();
  });
})();

/* A tab nobody is looking at does not poll (web/poll.py). htmx's "every" trigger keeps asking
 * in a background tab, and its trigger filter is an expression (`allowEval` is off here), so
 * the request is cancelled instead. The timer keeps running: the first tick after the tab is
 * shown asks again, within one interval, and nothing is lost meanwhile (the answer is the
 * current state, not a queue of changes).
 */
document.addEventListener("htmx:beforeRequest", function (event) {
  var elt = event.detail && event.detail.elt;
  var trigger = elt && elt.getAttribute ? elt.getAttribute("hx-trigger") : "";
  if (document.hidden && trigger && trigger.indexOf("every") === 0) event.preventDefault();
});

/* "Last checked" (job.html, web/poll.py). A page that polls says when it last asked, whether
 * or not the answer changed anything (a 204 changes nothing, on purpose: a screen reader is not
 * interrupted). The line sits outside the live region, so it is never read out. It goes when the
 * job has finished: the page is the finished one then.
 */
(function () {
  "use strict";

  var line = document.getElementById("last-checked");
  if (!line) return;
  var time = line.querySelector("[data-last-checked]");

  function stamp() {
    if (document.querySelector("#job-status[data-finished]")) {
      line.hidden = true;
      return;
    }
    time.textContent = new Date().toLocaleTimeString([], { timeStyle: "medium" });
    line.hidden = false;
  }

  stamp();
  document.addEventListener("htmx:afterRequest", function (event) {
    var elt = event.detail && event.detail.elt;
    var trigger = elt && elt.getAttribute ? elt.getAttribute("hx-trigger") : "";
    if (trigger && trigger.indexOf("every") === 0 && event.detail.successful) stamp();
  });
  document.addEventListener("htmx:afterSettle", function () {
    if (document.querySelector("#job-status[data-finished]")) line.hidden = true;
  });
})();

/* After a download (_after_download.html, web/feedback.py): the two optional questions
 * are in the page but hidden until the download link is used. Without JavaScript they
 * show on the next visit, and a link leads to them.
 */
(function () {
  "use strict";

  var questions = document.querySelector("[data-after-download]");
  if (!questions) return;
  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    // The HTML file and the other file types (Common Cartridge, Word, EPUB).
    if (event.target.closest('a[href$="/download"], a[href$="/download/cartridge"], a[href$="/download.docx"], a[href$="/download.epub"]')) questions.hidden = false;
  });
  // The Canvas download is a form (a course number can go with it).
  document.addEventListener("submit", function (event) {
    if (event.target instanceof Element && event.target.closest("form.canvas-download")) {
      questions.hidden = false;
    }
  });
})();

/* Review screen (review_split.html). Everything there works without this; it only:
 *
 * - scrolls the page image, inside its own region, so the selected item's mark is in the
 *   part of the region that is inside the window (the window itself is never moved on load:
 *   side by side, the page pane is sized to the space under the header, see sizePane);
 * - asks once more for an image that could not be made (a render refused for a moment),
 *   then says so when the page image still cannot be loaded;
 * - makes "Save edit" the main button once a field of the decision is changed, so an
 *   edit is less likely to be dropped by pressing Accept out of habit (the server
 *   refuses Accept with a changed field in any case);
 * - offers keyboard shortcuts (N, P, A, E). They are on until the reviewer turns them off
 *   (WCAG 2.1.4: single-key shortcuts must be possible to turn off; speech input can type
 *   letters by accident; the choice is remembered), and never act while focus is in a field.
 *   A key with nothing to act on says so in the status line (#decision-notice).
 */
(function () {
  "use strict";

  var STORE = "readablestem.review-shortcuts";

  // How far to scroll along one axis so a mark at `start` (from the region's visible
  // edge) of length `size` sits a third of the way in, or as far in as it fits.
  function offset(start, size, visible) {
    if (start >= 0 && start + size <= visible) return 0;
    return start - Math.max(0, Math.min(visible / 3, visible - size));
  }

  // Side by side, the page pane is sticky under the window's top edge. Its height is the
  // window minus the space above it (the header, when the window is at the top) minus the
  // sticky offset, so its lower edge is in the window on load; css reads --page-top, which
  // is not set (and the pane is the window minus two offsets) without this script.
  function sizePane() {
    var split = document.querySelector(".split");
    if (!split) return;
    function measure() {
      var top = Math.max(0, Math.round(split.getBoundingClientRect().top + window.scrollY));
      document.documentElement.style.setProperty("--page-top", top + "px");
    }
    measure();
    window.addEventListener("resize", measure);
  }

  function bringMarkIntoView() {
    var region = document.querySelector(".page-scroll");
    var mark = region && region.querySelector(".box.is-current");
    if (!region || !mark) return;
    // Inside the region only, against the part of its box that is in the window: where the
    // window happens to be does not matter, and it is never moved. Stacked, the region
    // starts below the window (a phone, 320 px, 400% zoom): its whole box counts then, and
    // any window term would overshoot.
    var r = region.getBoundingClientRect();
    var m = mark.getBoundingClientRect();
    var box = r.top + region.clientTop;
    var from = 0;
    var to = region.clientHeight;
    var side = window.getComputedStyle(region.closest(".page-area")).position === "sticky";
    if (side) {
      from = Math.max(0, -box);
      to = Math.min(to, window.innerHeight - box);
    }
    if (to - from < 1) {
      from = 0;
      to = region.clientHeight;
    }
    var left = m.left - r.left - region.clientLeft;
    region.scrollTop += offset(m.top - box - from, m.height, to - from);
    region.scrollLeft += offset(left, m.width, region.clientWidth);
  }

  // A render can be refused for a moment (each person has two at a time, and the
  // instance two children): ask once more after a pause, then say it failed.
  var RETRY_MS = 1500;

  function retryOnce(image, gaveUp) {
    var tried = false;
    function failed() {
      if (tried) {
        if (gaveUp) gaveUp();
        return;
      }
      tried = true;
      window.setTimeout(function () {
        var src = image.getAttribute("src");
        image.removeAttribute("src");
        image.setAttribute("src", src);
      }, RETRY_MS);
    }
    image.addEventListener("error", failed);
    if (image.complete && image.naturalWidth === 0 && image.getAttribute("src")) failed();
  }

  function watchImages() {
    var note = document.querySelector(".page-failed");
    document.querySelectorAll(".page-image, .region-image, .cmp-image").forEach(function (image) {
      var isPage = image.classList.contains("page-image");
      retryOnce(image, isPage && note ? function () { note.hidden = false; } : null);
    });
  }

  function watchEdits() {
    document.querySelectorAll(".decide-form").forEach(function (form) {
      // The choices that sit above the form (`data-decide-form` names it) follow it.
      var bars = form.id ? document.querySelectorAll('[data-decide-form="' + form.id + '"]') : [];
      // A card with no proposal: Save edit waits for something typed (data-needs-edit).
      var needs = form.querySelectorAll("[data-needs-edit]");
      needs.forEach(function (button) { button.disabled = true; });
      function typed() {
        return Array.prototype.some.call(
          form.querySelectorAll("textarea[data-edit-field]"),
          function (field) { return field.value.trim() !== ""; }
        );
      }
      function edited() {
        needs.forEach(function (button) { button.disabled = !typed(); });
        form.classList.add("is-edited");
        bars.forEach(function (bar) { bar.classList.add("is-edited"); });
      }
      form.addEventListener("input", function (event) {
        if (event.target.closest("[data-edit-field]")) edited();
      });
      form.addEventListener("change", function (event) {
        if (event.target.closest("[data-edit-field]")) edited();
      });
    });
  }

  function typing(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.isContentEditable ||
        target.closest(
          "input, textarea, select, [contenteditable=''], [contenteditable='true'], " +
            "[contenteditable='plaintext-only'], [role='textbox'], [role='combobox'], [role='searchbox']"
        )
    );
  }

  // The status line is the one the saved notice uses (#decision-notice, role="status"): the
  // page always has it, empty when nothing was saved, so what is put in it is announced.
  // What a key says goes in a span of its own, after the saved notice's own words.
  function say(text) {
    var live = document.getElementById("decision-notice");
    if (!live) return;
    var note = live.querySelector("[data-key-note]");
    if (!note) {
      note = document.createElement("span");
      note.setAttribute("data-key-note", "");
      live.appendChild(note);
    }
    var gap = note.previousSibling ? " " : "";
    note.textContent = "";
    // Emptied first, so the same words said twice are announced twice.
    window.setTimeout(function () { note.textContent = gap + text; }, 60);
  }

  // After n or p the new decision is a new page: focus goes to its heading, as it does
  // after a decision is saved (the server marks that heading autofocus).
  var FOCUS = "readablestem.focus-decision";

  function focusHeadingAfterKey() {
    var asked = false;
    try {
      asked = window.sessionStorage.getItem(FOCUS) === "1";
      window.sessionStorage.removeItem(FOCUS);
    } catch (e) {
      asked = false;
    }
    var heading = document.getElementById("decision-title");
    if (asked && heading) heading.focus();
  }

  // The list of keys names only what this card can do: a key the card cannot act on is
  // left out (a table kept as an image has no Accept; the last decision has no next). The
  // keys still answer in the status line if pressed. Read from the card itself, so a new
  // kind of card needs no second list.
  function listKeysTheCardCanUse(box) {
    var panel = document.getElementById("decision");
    if (!panel) return;
    var accept = panel.getAttribute("data-card") === "page" ? null : panel.querySelector('[data-role="accept"]');
    var can = {
      n: !!panel.querySelector('.decision-nav a[rel="next"]'),
      p: !!panel.querySelector('.decision-nav a[rel="prev"]'),
      a: !!accept && !accept.disabled,
      e: !!panel.querySelector("[data-edit-field], [data-edit-start]"),
    };
    Object.keys(can).forEach(function (key) {
      var row = box.querySelector('[data-key="' + key + '"]');
      if (row) row.hidden = !can[key];
    });
    // A on a button that goes against a check or a suggestion only moves focus to it.
    var word = box.querySelector('[data-key="a"] dd');
    if (word && accept && accept.hasAttribute("data-anyway")) {
      word.textContent = "go to the accept button (it needs a press)";
    }
  }

  function shortcuts() {
    var box = document.getElementById("shortcuts");
    var toggle = document.getElementById("shortcuts-on");
    if (!box || !toggle) return;
    box.hidden = false;
    // On unless this reviewer turned them off: nothing stored is on.
    var on = true;
    try {
      on = window.localStorage.getItem(STORE) !== "off";
    } catch (e) {
      on = true;
    }
    toggle.checked = on;
    listKeysTheCardCanUse(box);
    toggle.addEventListener("change", function () {
      on = toggle.checked;
      try {
        window.localStorage.setItem(STORE, on ? "on" : "off");
      } catch (e) {
        /* private mode: the choice lasts for this page only */
      }
    });
    document.addEventListener("keydown", function (event) {
      if (!on || event.defaultPrevented || event.repeat) return;
      // A key that is part of an input method's composition, or a chord, is not a shortcut.
      if (event.isComposing || event.keyCode === 229) return;
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (typing(event.target)) return;
      // Behind a dialog (the enlarged image) the screen is not what the keys would act on.
      if (document.querySelector("dialog[open]")) return;
      var key = event.key.toLowerCase();
      if (key !== "n" && key !== "p" && key !== "a" && key !== "e") return;
      var panel = document.getElementById("decision");
      if (!panel) return;
      event.preventDefault();
      if (key === "n" || key === "p") {
        var link = panel.querySelector('.decision-nav a[rel="' + (key === "n" ? "next" : "prev") + '"]');
        if (!link) {
          say(key === "n" ? "There is no next decision." : "There is no previous decision.");
          return;
        }
        try {
          window.sessionStorage.setItem(FOCUS, "1");
        } catch (e) {
          /* the page then opens without moving focus */
        }
        link.click();
      } else if (key === "a") {
        if (panel.getAttribute("data-card") === "page") {
          say("A does nothing here: this choice keeps or replaces a whole page, so it needs Tab and a press.");
          return;
        }
        var accept = panel.querySelector('[data-role="accept"]');
        if (!accept || accept.disabled) {
          say("There is nothing to accept here.");
        } else if (accept.hasAttribute("data-anyway")) {
          // Accepting goes against a check, or against what the page suggests for a group of
          // headings: never by a single key. Focus goes to the button.
          accept.focus();
          say(
            (accept.getAttribute("data-anyway") === "advice"
              ? "A does not press this one, because the page suggests the other choice."
              : "A does not accept this one, because accepting goes against a check.") +
              " Focus is on the button: press it if you mean it."
          );
        } else {
          accept.click();
        }
      } else {
        var edit = panel.querySelector(".edit-box");
        // The first field, or where editing starts without being an edit itself (a
        // table's cell list: data-edit-start, which keeps no draft).
        var field = panel.querySelector("[data-edit-field], [data-edit-start]");
        if (!field) {
          say("There is nothing to edit here.");
          return;
        }
        if (edit) edit.open = true;
        field.focus();
      }
    });
  }

  if (!document.querySelector(".split")) return;
  sizePane();
  bringMarkIntoView();
  focusHeadingAfterKey();
  watchImages();
  watchEdits();
  shortcuts();
})();

/* Drafts of a decision's fields (UX review MF19), on the review screen and the list view.
 *
 * A session can end while a reviewer is typing (signed out on another device, or after
 * seven days): the decision posted then is refused, and what was typed was lost. Now what
 * is typed into a decision's fields ([data-edit-field]) is kept in this tab's
 * sessionStorage, under the form's address (ids only), and put back when that decision
 * is shown again; signing in comes back to it (web/return_to.py). A line under the field
 * says so, and the field's description includes it. When the browser has no room left for a
 * draft, a status line under the form's first field says "This draft could not be saved on
 * this device." and the older draft is dropped, so nothing claims protection that is not
 * there.
 *
 * sessionStorage belongs to this browser tab: it is never sent to the server and is gone
 * when the tab is closed. A draft is also forgotten when the decision is saved (the
 * notice after it names the item: data-saved), when the page is drawn with that text
 * anyway, and when Sign out is chosen in this tab. Without JavaScript nothing is kept
 * (the browser's Back button usually still has the text).
 *
 * The privacy page says so ("Cookies": unsaved review text is kept in the browser tab until
 * it is saved, the tab is closed, or you sign out there).
 */
(function () {
  "use strict";

  var PREFIX = "readablestem.draft:";
  var store = null;
  try {
    store = window.sessionStorage;
    store.getItem(PREFIX); // throws where storage is blocked
  } catch (e) {
    store = null;
  }
  if (!store) return;

  // A form's draft is kept under its action, plus its data-draft-key where two forms
  // post to one place (a table's cell form and its headers form), so neither restores,
  // removes or overwrites the other's.
  function key(form) {
    var own = form.getAttribute("data-draft-key");
    return PREFIX + form.getAttribute("action") + (own ? "#" + own : "");
  }

  // Every draft kept for one action (all of its forms).
  function forget(action) {
    var base = PREFIX + action;
    for (var i = store.length - 1; i >= 0; i--) {
      var name = store.key(i);
      if (name === base || (name && name.indexOf(base + "#") === 0)) store.removeItem(name);
    }
  }

  function kept(action) {
    var base = PREFIX + action;
    for (var i = 0; i < store.length; i++) {
      var name = store.key(i);
      if (name === base || (name && name.indexOf(base + "#") === 0)) return true;
    }
    return false;
  }

  function valueOf(field) {
    return field.type === "checkbox" ? (field.checked ? "on" : "") : field.value;
  }

  function defaultOf(field) {
    if (field.type === "checkbox") return field.defaultChecked ? "on" : "";
    if (field.tagName === "SELECT") {
      for (var i = 0; i < field.options.length; i++) {
        if (field.options[i].defaultSelected) return field.options[i].value;
      }
      return field.options.length ? field.options[0].value : "";
    }
    return field.defaultValue;
  }

  function fieldsOf(form) {
    return Array.prototype.slice.call(form.querySelectorAll("[data-edit-field][name]"));
  }

  function read(form) {
    try {
      var draft = JSON.parse(store.getItem(key(form)) || "null");
      return draft && typeof draft === "object" ? draft : null;
    } catch (e) {
      return null;
    }
  }

  function save(form) {
    var draft = {};
    var changed = false;
    fieldsOf(form).forEach(function (field) {
      var value = valueOf(field);
      if (value !== defaultOf(field)) {
        draft[field.name] = value;
        changed = true;
      }
    });
    var failed = false;
    try {
      if (changed) store.setItem(key(form), JSON.stringify(draft));
      else store.removeItem(key(form));
    } catch (e) {
      // Storage is full: this draft is not kept, and an older one under the same key would
      // be put back as if it were the latest, so it goes too. Say so, once, in words.
      failed = true;
      try { store.removeItem(key(form)); } catch (e2) { /* nothing more can be done */ }
    }
    unsaved(form, failed);
  }

  // One short status under the first field of a form whose draft could not be stored; it goes
  // when a later change is stored. It is added empty and filled a moment later so a screen
  // reader announces it as a status.
  var UNSAVED = "This draft could not be saved on this device.";
  function unsaved(form, failed) {
    var line = form.querySelector("[data-draft-failed]");
    if (!failed) {
      if (line) line.remove();
      return;
    }
    if (line) return;
    var first = fieldsOf(form)[0];
    if (!first) return;
    line = document.createElement("p");
    line.className = "kept-draft draft-failed";
    line.setAttribute("role", "status");
    line.setAttribute("data-draft-failed", "");
    (first.closest(".field") || first).insertAdjacentElement("afterend", line);
    window.setTimeout(function () { line.textContent = UNSAVED; }, 50);
  }

  function note(field) {
    var id = "kept-draft-" + (field.id || field.name);
    if (document.getElementById(id)) return;
    var line = document.createElement("p");
    line.className = "kept-draft";
    line.id = id;
    line.textContent = "What you typed before is back here. It is not saved yet.";
    var place = field.closest(".field") || field;
    place.insertAdjacentElement("afterend", line);
    var described = field.getAttribute("aria-describedby");
    field.setAttribute("aria-describedby", described ? described + " " + id : id);
  }

  function restore(form) {
    var draft = read(form);
    if (!draft) return;
    var restored = [];
    fieldsOf(form).forEach(function (field) {
      if (!Object.prototype.hasOwnProperty.call(draft, field.name)) return;
      var value = String(draft[field.name]);
      // What the server drew: saved since, or never different. (The browser itself may
      // have put the text back on a reload: still a draft, so still said.)
      if (value === defaultOf(field)) return;
      if (valueOf(field) !== value) {
        if (field.type === "checkbox") field.checked = value === "on";
        else field.value = value;
      }
      if (valueOf(field) === value) restored.push(field);
    });
    if (!restored.length) {
      store.removeItem(key(form));
      return;
    }
    form.classList.add("is-edited");
    if (form.id) {
      document.querySelectorAll('[data-decide-form="' + form.id + '"]').forEach(function (bar) {
        bar.classList.add("is-edited");
      });
    }
    for (var open = form.closest("details"); open; open = open.parentElement.closest("details")) {
      open.open = true;
    }
    var inner = restored[0].closest("details");
    if (inner) inner.open = true;
    restored.forEach(function (field) {
      note(field);
      // As if typed: the live check (htmx) and "Save edit" as the main button follow.
      field.dispatchEvent(new Event(field.tagName === "TEXTAREA" ? "input" : "change", { bubbles: true }));
    });
  }

  function start() {
    var saved = document.querySelector("[data-saved]");
    if (saved) {
      forget(saved.getAttribute("data-saved"));
      // Other forms the same save settled (the page tools' text and region forms).
      (saved.getAttribute("data-saved-also") || "").split(" ").forEach(function (action) {
        if (action) forget(action);
      });
    }
    var forms = [];
    document.querySelectorAll("form [data-edit-field]").forEach(function (field) {
      var form = field.form;
      if (form && form.getAttribute("action") && forms.indexOf(form) < 0) forms.push(form);
    });
    forms.forEach(function (form) {
      restore(form);
      form.addEventListener("input", function () { save(form); });
      form.addEventListener("change", function () { save(form); });
    });
    // The page for a form refused because the session ended: say the text is kept.
    document.querySelectorAll("[data-draft-for]").forEach(function (hint) {
      if (kept(hint.getAttribute("data-draft-for"))) hint.hidden = false;
    });
    // Signing out in this tab forgets every draft (a shared computer).
    document.querySelectorAll('form[action="/logout"]').forEach(function (form) {
      form.addEventListener("submit", function () {
        for (var i = store.length - 1; i >= 0; i--) {
          var name = store.key(i);
          if (name && name.indexOf(PREFIX) === 0) store.removeItem(name);
        }
      });
    });
  }

  // After htmx has set up the page (it starts on DOMContentLoaded too, and was loaded
  // first), so a restored LaTeX field is checked as if it had been typed.
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();

/* When a job finishes (job.html, _job_status.html). The status box is polled by htmx; the
 * server's answer for a finished job carries data-finished, and this:
 *
 * - sets the tab's title to the new status ("Converted: notes.pdf"), so a tab left in the
 *   background shows it;
 * - offers an opt-in browser notice ("Notify me when it finishes"). The Notification API is
 *   asked only after the button is used; where it is missing, blocked, or the page is
 *   already in front, nothing else happens. Nothing is sent to the server.
 *
 * The announcement itself is the polite live region the status box sits in
 * (#job-status-region): it reads the new status. A page
 * opened after the job finished says nothing more. Without this file the page is the same,
 * only without the title change and the notice.
 */
(function () {
  "use strict";

  var region = document.getElementById("job-status-region");
  if (!region) return;
  var first = document.getElementById("job-status");
  if (!first || first.hasAttribute("data-finished")) return;

  var box = document.getElementById("notify-box");
  var button = document.getElementById("notify-button");
  var state = document.getElementById("notify-state");
  var supported = typeof window.Notification === "function";

  function say(text) {
    if (state) state.textContent = text;
  }

  function showState() {
    if (!supported || !box || !button) return;
    var permission = window.Notification.permission;
    if (permission === "denied") {
      box.hidden = true; // blocked in the browser: nothing to offer
      return;
    }
    box.hidden = false;
    if (permission === "granted") {
      button.setAttribute("aria-disabled", "true");
      button.textContent = "Notice is on";
      say("You will get a notice when it finishes, if this tab is in the background.");
    }
  }

  if (supported && button) {
    button.addEventListener("click", function () {
      if (button.getAttribute("aria-disabled") === "true") return;
      var done = function () {
        var permission = window.Notification.permission;
        if (permission === "granted") {
          showState();
        } else if (permission === "denied") {
          say("Your browser blocked notices. This page's title still changes when it finishes.");
          button.setAttribute("aria-disabled", "true");
        } else {
          say("No notice: you did not allow it.");
        }
      };
      try {
        var asked = window.Notification.requestPermission(done);
        if (asked && typeof asked.then === "function") asked.then(done, done);
      } catch (e) {
        say("Your browser could not ask for notices.");
      }
    });
    showState();
  }

  var finished = false;
  // The finished answer replaces the part of the page the button is in (#job-body), so where
  // the keyboard was is noted before the swap takes the button away.
  var wasInBox = false;
  document.addEventListener("htmx:beforeSwap", function () {
    wasInBox = !!box && box.contains(document.activeElement);
  });
  document.addEventListener("htmx:afterSettle", function () {
    var now = document.getElementById("job-status");
    if (finished || !now || !now.hasAttribute("data-finished")) return;
    finished = true;
    var title = now.getAttribute("data-title");
    if (title) document.title = title + " | ReadableSTEM";
    if (box) {
      // Keep the keyboard's place: focus on the button moves to the status box.
      if (wasInBox || box.contains(document.activeElement)) {
        region.setAttribute("tabindex", "-1");
        region.focus();
      }
      box.hidden = true;
    }
    if (!supported || window.Notification.permission !== "granted") return;
    if (!document.hidden && document.hasFocus()) return; // the page is in front: it shows
    try {
      var note = new window.Notification("ReadableSTEM", {
        body: now.getAttribute("data-summary") || "Your document has finished.",
      });
      note.onclick = function () {
        window.focus();
        note.close();
      };
    } catch (e) {
      /* some browsers allow notices only from a service worker: the title still changed */
    }
  });
})();

/* The main navigation is a <details> ("Menu"), closed in the markup. A wide screen shows the
 * links whatever its state (CSS ::details-content); this opens it there for a browser that does
 * not support that, and closes it again when the window narrows (the Menu button is hidden on a
 * wide screen). */
(function () {
  "use strict";
  var menu = document.querySelector(".nav-menu");
  if (!menu || !window.matchMedia) return;
  var narrow = window.matchMedia("(max-width: 39.99rem)");
  function sync() { menu.open = !narrow.matches; }
  sync();
  if (narrow.addEventListener) narrow.addEventListener("change", sync);
})();

/* The review list's compact rows (review.html, more than three waiting): opening one editor
 * (its summary pressed or clicked) closes the others in its list, so the page stays one editor
 * long. Rows stay <details>, so the keyboard and a link to a decision work as before; opening
 * rows by a link or by script leaves the others alone, and without this file all can be open. */
(function () {
  "use strict";
  document.addEventListener("click", function (event) {
    var summary = event.target instanceof Element ? event.target.closest("details.compact-item > summary") : null;
    if (!summary) return;
    var row = summary.parentElement;
    if (row.open) return; // it is being closed
    var list = row.closest("ol");
    if (!list) return;
    list.querySelectorAll("details.compact-item[open]").forEach(function (other) {
      if (other !== row) other.open = false;
    });
  });
})();

/* ==== ux2-lane-b: the review screens ======================================================
 * Everything here is an addition to markup that works without it.
 *
 * - Symbols (review_inventory.html): "Change all" waits until a replacement is chosen and a place
 *   is ticked, and a status line under it says, in words, how many places will change and to
 *   what. The line is a live region, so a screen reader hears the count change as places are
 *   ticked or unticked; before a replacement is chosen, a visually hidden status says the
 *   ticked count when it changes. Without this file the button is always there and the server
 *   says what is missing.
 * - "Keep without a description" (figure card): its button waits for the box that says what
 *   it means for students. (The server refuses it without the box in any case.)
 * - "Enlarge the PDF image": the picture in a dialog, for one tap on a phone. Without this file
 *   the link opens the picture itself.
 * - A phone's Decision / Page bar (review_split.html): goes to either, and remembers where you
 *   were in each, so going back lands on the same place. "Fit the page to the screen width"
 *   takes away the sideways scrolling of the page image (the choice is kept for the tab).
 */
(function () {
  "use strict";

  document.querySelectorAll("form.symbol-form").forEach(function (form) {
    var select = form.querySelector("[data-symbol-to]");
    var line = form.querySelector("[data-symbol-affect]");
    var button = form.querySelector('button[data-role="relabel"]');
    if (!select || !line || !button) return;
    var total = Number(form.getAttribute("data-total")) || 0;
    var summary = form.querySelector(".symbol-places > summary");
    var changeable = Number(form.getAttribute("data-changeable")) || 0;
    var said = "";
    // The count in the "Places (n of m checked)" summary changes silently; this hidden status
    // says it, politely, when it changes. With a replacement chosen the status line under it
    // already says the count, so this one stays quiet then.
    var counter = form.querySelector("[data-symbol-count]");
    var counted = -1;

    function ticked() { return form.querySelectorAll('input[name="include"]:checked').length; }

    function say(text) {
      if (text === said) return;
      said = text;
      line.textContent = text;
    }

    function update() {
      var n = ticked();
      var chosen = select.value !== "";
      var target = chosen ? select.options[select.selectedIndex].text : "";
      if (summary) summary.textContent = "Places (" + n + " of " + changeable + " checked)";
      if (counter && n !== counted) {
        // The first call (counted is -1) only sets the count: nothing is announced on load.
        var changed = counted !== -1;
        counted = n;
        counter.textContent = changed && !chosen
          ? n + " of " + changeable + (changeable === 1 ? " place" : " places") + " checked."
          : "";
      }
      var ready = chosen && n > 0;
      if (ready) button.removeAttribute("aria-disabled");
      else button.setAttribute("aria-disabled", "true");
      if (!chosen) {
        say("Choose a symbol first.");
      } else if (n === 0) {
        say("No place is checked. Check at least one.");
      } else {
        say(n + " of " + total + " places change to " + target + ".");
      }
    }

    select.addEventListener("change", update);
    form.addEventListener("change", function (event) {
      if (event.target && event.target.name === "include") update();
    });
    button.addEventListener("click", function (event) {
      if (button.getAttribute("aria-disabled") !== "true") return;
      event.preventDefault();
      if (select.value === "") select.focus();
      else { var first = form.querySelector('input[name="include"]'); if (first) first.focus(); }
    });
    update();
  });

  document.querySelectorAll("[data-needs-tick]").forEach(function (button) {
    var box = document.getElementById(button.getAttribute("data-needs-tick"));
    if (!box) return;
    function sync() {
      if (box.checked) button.removeAttribute("aria-disabled");
      else button.setAttribute("aria-disabled", "true");
    }
    box.addEventListener("change", sync);
    button.addEventListener("click", function (event) {
      if (button.getAttribute("aria-disabled") !== "true") return;
      event.preventDefault();
      box.focus();
    });
    sync();
  });

  // A table finding's "Change the table" opens the folded place where it is changed.
  function openCorrection(hash) {
    var target = hash && hash.indexOf("#table-correct-") === 0 ? document.getElementById(hash.slice(1)) : null;
    if (target && target.tagName === "DETAILS") target.open = true;
  }
  document.addEventListener("click", function (event) {
    var link = event.target instanceof Element ? event.target.closest('a[href^="#table-correct-"]') : null;
    if (link) openCorrection(link.getAttribute("href"));
  });
  openCorrection(window.location.hash);

  // The picture of the PDF, larger, in a dialog (Escape or Close puts you back where you were).
  var dialog = null;
  var opener = null;
  function enlargeDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "enlarge-dialog";
    dialog.setAttribute("aria-label", "The PDF image, larger");
    var body = document.createElement("div");
    body.className = "enlarge-body";
    body.tabIndex = 0; // it scrolls, so the keyboard can scroll it
    body.setAttribute("role", "group");
    body.setAttribute("aria-label", "The PDF image, larger: scroll to see all of it");
    var image = document.createElement("img");
    image.className = "enlarge-image";
    body.appendChild(image);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "button";
    close.textContent = "Close";
    close.addEventListener("click", function () { dialog.close(); });
    dialog.appendChild(body);
    dialog.appendChild(close);
    dialog.addEventListener("click", function (event) { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener("close", function () { if (opener) opener.focus(); });
    document.body.appendChild(dialog);
    return dialog;
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target instanceof Element ? event.target.closest("a[data-enlarge]") : null;
    if (!link || typeof HTMLDialogElement === "undefined") return;
    event.preventDefault();
    var box = enlargeDialog();
    var original = link.parentElement ? link.parentElement.querySelector("img") : null;
    var image = box.querySelector("img");
    image.src = link.href;
    image.alt = original ? original.alt : "";
    opener = link;
    box.showModal();
  });

  var split = document.querySelector(".split");
  var toggle = document.querySelector(".view-toggle");
  var decision = document.getElementById("decision");
  var pageView = document.getElementById("page-view");
  if (!split || !toggle || !decision || !pageView || !window.matchMedia) return;

  var KEEP = "readablestem.fit-width";
  var fitButton = document.querySelector("[data-fit-width]");
  var region = document.querySelector(".page-scroll");
  if (fitButton && region) {
    fitButton.hidden = false;
    var fitOn = false;
    try { fitOn = window.sessionStorage.getItem(KEEP) === "on"; } catch (e) { fitOn = false; }
    var applyFit = function () {
      region.classList.toggle("fit-width", fitOn);
      fitButton.setAttribute("aria-pressed", fitOn ? "true" : "false");
      fitButton.textContent = fitOn ? "Show the page at full size" : "Fit the page to the screen width";
    };
    fitButton.addEventListener("click", function () {
      fitOn = !fitOn;
      try { window.sessionStorage.setItem(KEEP, fitOn ? "on" : "off"); } catch (e) { /* kept for this page only */ }
      applyFit();
    });
    applyFit();
  }

  var narrow = window.matchMedia("(max-width: 63.99rem)");
  var where = {};
  var links = {
    decision: toggle.querySelector('[data-view="decision"]'),
    page: toggle.querySelector('[data-view="page"]'),
  };

  function active() {
    var top = pageView.getBoundingClientRect().top;
    return top < window.innerHeight * 0.5 ? "page" : "decision";
  }

  function mark() {
    var now = active();
    Object.keys(links).forEach(function (view) {
      if (!links[view]) return;
      if (view === now) links[view].setAttribute("aria-current", "true");
      else links[view].removeAttribute("aria-current");
    });
  }

  var queued = false;
  window.addEventListener("scroll", function () {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(function () { queued = false; mark(); });
  }, { passive: true });
  window.addEventListener("resize", mark);

  toggle.addEventListener("click", function (event) {
    var link = event.target instanceof Element ? event.target.closest("a[data-view]") : null;
    if (!link || !narrow.matches) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var view = link.getAttribute("data-view");
    var from = active();
    if (view === from) { event.preventDefault(); return; }
    event.preventDefault();
    where[from] = window.scrollY;
    var target = view === "page" ? pageView : decision;
    var heading = view === "page" ? pageView : document.getElementById("decision-title");
    var top = where[view] !== undefined
      ? where[view]
      : target.getBoundingClientRect().top + window.scrollY - 8;
    window.scrollTo(0, Math.max(top, 0));
    if (heading) heading.focus({ preventScroll: true });
    mark();
  });
  mark();
})();
/* ==== end ux2-lane-b ======================================================================== */

/* The review's decision bar sticks to the bottom of a wide, tall window (app.css). Its height
 * depends on the item (one or two rows of choices, up to three notes), so the room a focused
 * control needs to stay above it is measured, not guessed: app.css reads --decision-bar-height
 * as the page's scroll padding. Without this file the padding is a fixed two rows. */
(function () {
  "use strict";
  var bar = document.querySelector(".decision-area .decision-bar");
  if (!bar) return;
  function measure() {
    var sticky = window.getComputedStyle(bar).position === "sticky";
    var height = sticky ? Math.ceil(bar.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty("--decision-bar-height", height + "px");
  }
  if (window.ResizeObserver) new window.ResizeObserver(measure).observe(bar);
  window.addEventListener("resize", measure);
  measure();
})();

/* Pictures of the PDF (templates/_crop.html) are shown at their own size, so one can be wider
 * than its column: it scrolls inside its box. The box is a tab stop only while there is
 * something to scroll to (axe: scrollable-region-focusable; a page of 25 crops is not 25 tab
 * stops), the line under it appears while the picture is wider than the box, and the box
 * carries an edge shadow (data-more) until its right-hand end is in view. Without this file
 * every box stays a tab stop and says nothing, and still scrolls. */
(function () {
  "use strict";
  function update(box) {
    var scroller = box.querySelector(".crop-scroll");
    if (!scroller) return;
    var wide = scroller.scrollWidth > scroller.clientWidth + 1;
    var atEnd = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 1;
    var more = box.querySelector(".crop-more");
    if (more) more.hidden = !wide;
    if (wide) scroller.setAttribute("tabindex", "0");
    else scroller.removeAttribute("tabindex");
    if (wide && !atEnd) box.setAttribute("data-more", "");
    else box.removeAttribute("data-more");
  }
  var observer = window.ResizeObserver
    ? new window.ResizeObserver(function (entries) {
        entries.forEach(function (entry) {
          update(entry.target.closest(".crop-box"));
        });
      })
    : null;
  function attach(box) {
    var scroller = box.querySelector(".crop-scroll");
    if (!scroller || box.hasAttribute("data-crop-ready")) return;
    box.setAttribute("data-crop-ready", "");
    scroller.addEventListener("scroll", function () {
      update(box);
    });
    if (observer) observer.observe(scroller);
    update(box);
  }
  function attachAll(root) {
    root.querySelectorAll(".crop-box").forEach(attach);
  }
  attachAll(document);
  window.addEventListener("resize", function () {
    document.querySelectorAll(".crop-box").forEach(update);
  });
  if (window.MutationObserver) {
    new window.MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType === 1) attachAll(node);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
