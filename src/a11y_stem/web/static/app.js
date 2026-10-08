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
 *   view (the window itself does not move);
 * - says so when a page image cannot be loaded;
 * - makes "Save edit" the main button once a field of the decision is changed, so an
 *   edit is less likely to be dropped by pressing Accept out of habit;
 * - offers keyboard shortcuts (N, P, A, E). They are off until the reviewer turns them
 *   on (WCAG 2.1.4: single-key shortcuts must be possible to turn off; speech input can
 *   type letters by accident), and never act while focus is in a field.
 */
(function () {
  "use strict";

  var STORE = "a11y-stem.review-shortcuts";

  function bringMarkIntoView() {
    var region = document.querySelector(".page-scroll");
    var mark = region && region.querySelector(".box.is-current");
    if (!region || !mark) return;
    var r = region.getBoundingClientRect();
    var m = mark.getBoundingClientRect();
    if (m.top >= r.top && m.bottom <= r.bottom && m.left >= r.left && m.right <= r.right) return;
    region.scrollTop += m.top - r.top - region.clientHeight / 3;
    region.scrollLeft += m.left - r.left - region.clientWidth / 3;
  }

  function watchPageImage() {
    var image = document.querySelector(".page-image");
    var note = document.querySelector(".page-failed");
    if (!image || !note) return;
    function failed() {
      note.hidden = false;
    }
    if (image.complete && image.naturalWidth === 0) failed();
    else image.addEventListener("error", failed);
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
  watchPageImage();
  watchEdits();
  shortcuts();
})();
