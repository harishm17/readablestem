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
        target = panel.querySelector("[data-edit-field]");
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

  function key(form) {
    return PREFIX + form.getAttribute("action");
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
    if (saved) store.removeItem(PREFIX + saved.getAttribute("data-saved"));
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
      if (store.getItem(PREFIX + hint.getAttribute("data-draft-for"))) hint.hidden = false;
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
