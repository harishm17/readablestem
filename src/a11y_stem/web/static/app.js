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
 * <time class="local-time" datetime="2026-10-09T23:32:00Z">9 Oct 2026, 23:32 UTC</time>
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

  function localise() {
    document.querySelectorAll("time.local-time[datetime]:not([data-local])").forEach(function (el) {
      var when = new Date(el.getAttribute("datetime"));
      if (isNaN(when.getTime())) return;
      var ms = when.getTime() - Date.now();
      var away = Math.abs(ms) < WEEK_MS ? distance(ms) : "";
      el.textContent = stamp.format(when) + (away ? " (" + away + ")" : "");
      el.setAttribute("data-local", "");
    });
  }

  localise();
  document.addEventListener("htmx:afterSettle", localise);
})();

/* Upload form (app.html, UX review MF21). Says at once when a chosen file is not a PDF,
 * is empty or is larger than the limit, instead of after sending all of it (35 s at
 * 5 Mbit/s for a refusal the browser can make instantly). The form takes several files
 * (T3-4): too many, or more than the request may carry together, is said the same way.
 * While good files are sent, says so ("Uploading notes.pdf. Keep this tab open.") and keeps
 * a second click from sending them twice. The server checks everything again; without this
 * file the form works the same, only slower to say no.
 */
(function () {
  "use strict";

  var input = document.getElementById("pdf");
  if (!input || !input.hasAttribute("data-max-bytes") || !input.form) return;
  var form = input.form;
  var box = document.getElementById("pdf-check");
  var status = document.getElementById("upload-status");
  var button = form.querySelector('button[type="submit"]');
  var field = input.closest(".field");
  var maxBytes = Number(input.getAttribute("data-max-bytes"));
  var limitBytes = Number(input.getAttribute("data-limit-bytes") || maxBytes);
  var limitMb = input.getAttribute("data-limit-mb") || input.getAttribute("data-max-mb");
  var maxFiles = Number(input.getAttribute("data-max-files") || 1);
  var describedBy = input.getAttribute("aria-describedby") || "";
  var serverError = input.getAttribute("aria-invalid") === "true";
  if (!box || !status || !button || !field) return;

  // A file between the two sizes is allowed: the server keeps it so its pages can be chosen.
  function problem(file) {
    var pdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
    if (!pdf) return file.name + " is not a PDF. Choose a PDF file.";
    if (file.size === 0) return file.name + " is empty. Choose a PDF that has content.";
    if (file.size > limitBytes) {
      return (
        file.name + " is larger than " + limitMb + " MB. Split it into smaller PDFs and " +
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
    if (!files || !files.length) return; // the server answers "Choose a PDF file to upload."
    if (button.getAttribute("aria-disabled") === "true") {
      event.preventDefault(); // already sending
      return;
    }
    status.textContent =
      files.length === 1
        ? "Uploading " + files[0].name + ". Keep this tab open."
        : "Uploading " + files.length + " files. Keep this tab open.";
    button.setAttribute("aria-disabled", "true");
  });

  // Back to this page from the next one (the browser's page cache): ready again.
  window.addEventListener("pageshow", function () {
    status.textContent = "";
    button.removeAttribute("aria-disabled");
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
    if (event.target.closest('a[href$="/download"]')) questions.hidden = false;
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
 * - scrolls the page image, inside its own region, so the selected item's mark is in
 *   the region's visible box (side by side, also the window, only as far as keeps the
 *   decision's heading in view; stacked, never the window);
 * - asks once more for an image that could not be made (a render refused for a moment),
 *   then says so when the page image still cannot be loaded;
 * - makes "Save edit" the main button once a field of the decision is changed, so an
 *   edit is less likely to be dropped by pressing Accept out of habit (the server
 *   refuses Accept with a changed field in any case);
 * - offers keyboard shortcuts (N, P, A, E). They are off until the reviewer turns them
 *   on (WCAG 2.1.4: single-key shortcuts must be possible to turn off; speech input can
 *   type letters by accident), and never act while focus is in a field.
 */
(function () {
  "use strict";

  var STORE = "a11y-stem.review-shortcuts";

  // How far to scroll along one axis so a mark at `start` (from the region's visible
  // edge) of length `size` sits a third of the way in, or as far in as it fits.
  function offset(start, size, visible) {
    if (start >= 0 && start + size <= visible) return 0;
    return start - Math.max(0, Math.min(visible / 3, visible - size));
  }

  function bringMarkIntoView() {
    var region = document.querySelector(".page-scroll");
    var mark = region && region.querySelector(".box.is-current");
    if (!region || !mark) return;
    // Inside the region only, against its own visible box: where the window happens to
    // be does not matter (stacked, the region starts below the window on a phone, at
    // 320 px and at 400% zoom, and any window term would overshoot).
    var r = region.getBoundingClientRect();
    var m = mark.getBoundingClientRect();
    var top = m.top - r.top - region.clientTop;
    var left = m.left - r.left - region.clientLeft;
    region.scrollTop += offset(top, m.height, region.clientHeight);
    region.scrollLeft += offset(left, m.width, region.clientWidth);
    // Side by side (wide screens), the page pane starts below the header, so a mark near
    // the bottom of a page can stay below the window. Move the window just enough, and
    // never so far that the decision's heading leaves the top of the window.
    var heading = document.getElementById("decision-title");
    var side = window.getComputedStyle(region.closest(".page-area")).position === "sticky";
    m = mark.getBoundingClientRect();
    if (side && heading && m.bottom > window.innerHeight) {
      var room = heading.getBoundingClientRect().top - 8;
      window.scrollBy(0, Math.min(m.bottom - window.innerHeight + 24, Math.max(room, 0)));
    }
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
      form.addEventListener("input", function (event) {
        if (event.target.closest("[data-edit-field]")) form.classList.add("is-edited");
      });
      form.addEventListener("change", function (event) {
        if (event.target.closest("[data-edit-field]")) form.classList.add("is-edited");
      });
    });
  }

  function typing(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")
    );
  }

  function shortcuts() {
    var box = document.getElementById("shortcuts");
    var toggle = document.getElementById("shortcuts-on");
    if (!box || !toggle) return;
    box.hidden = false;
    var on = false;
    try {
      on = window.localStorage.getItem(STORE) === "on";
    } catch (e) {
      on = false;
    }
    toggle.checked = on;
    toggle.addEventListener("change", function () {
      on = toggle.checked;
      try {
        window.localStorage.setItem(STORE, on ? "on" : "off");
      } catch (e) {
        /* private mode: the choice lasts for this page only */
      }
    });
    document.addEventListener("keydown", function (event) {
      if (!on || event.defaultPrevented || event.repeat || event.isComposing) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (typing(event.target)) return;
      var key = event.key.toLowerCase();
      var panel = document.getElementById("decision");
      if (!panel) return;
      var target = null;
      if (key === "n") target = panel.querySelector('.decision-nav a[rel="next"]');
      else if (key === "p") target = panel.querySelector('.decision-nav a[rel="prev"]');
      else if (key === "a") target = panel.querySelector('[data-role="accept"]');
      else if (key === "e") {
        var edit = panel.querySelector(".edit-box");
        if (edit) edit.open = true;
        // The first field, or where editing starts without being an edit itself (a
        // table's cell list: data-edit-start, which keeps no draft).
        target = panel.querySelector("[data-edit-field], [data-edit-start]");
        if (target) {
          event.preventDefault();
          target.focus();
        }
        return;
      }
      if (!target) return;
      event.preventDefault();
      target.click();
    });
  }

  if (!document.querySelector(".split")) return;
  bringMarkIntoView();
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
 * says so, and the field's description includes it.
 *
 * sessionStorage belongs to this browser tab: it is never sent to the server and is gone
 * when the tab is closed. A draft is also forgotten when the decision is saved (the
 * notice after it names the item: data-saved), when the page is drawn with that text
 * anyway, and when Sign out is chosen in this tab. Without JavaScript nothing is kept
 * (the browser's Back button usually still has the text).
 *
 * TODO(Harish): decide whether the privacy page should say that unsaved review text is
 * kept in the browser tab (sessionStorage) until it is saved, the tab is closed, or you
 * sign out there.
 */
(function () {
  "use strict";

  var PREFIX = "a11y-stem.draft:";
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
    try {
      if (changed) store.setItem(key(form), JSON.stringify(draft));
      else store.removeItem(key(form));
    } catch (e) {
      /* storage full: the draft is not kept */
    }
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
