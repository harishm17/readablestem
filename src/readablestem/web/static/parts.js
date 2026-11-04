/* Choosing pages in parts (templates/upload_parts.html, web/routes/parts.py).

   The form has always been one text box with a part on each line. This file adds the easier
   way in: two page fields and "Add part", which write one line into that box, and "Remove" on
   each row of the preview, which takes one line out. The box stays the single source of what
   is sent; the server reads it again and refuses anything wrong. Without this file the box is
   open and the form works as before. htmx (on the box, see the template) refreshes the preview
   as the box changes; this file only sends it the "input" event when it changes the box. */
(function () {
  "use strict";

  var builder = document.querySelector("[data-parts-builder]");
  var box = document.getElementById("parts");
  if (!builder || !box) return;

  var from = document.getElementById("part-from");
  var to = document.getElementById("part-to");
  var add = builder.querySelector("[data-add-part]");
  var note = builder.querySelector("[data-builder-note]");
  var advanced = document.querySelector("[data-parts-advanced]");
  var total = parseInt(builder.getAttribute("data-total"), 10) || 0;

  function entries() {
    return box.value.split(/[\n;]+/).map(function (e) { return e.trim(); }).filter(Boolean);
  }

  function write(list) {
    box.value = list.join("\n");
    box.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function say(words) {
    // A polite status line, cleared first so the same words twice are spoken twice.
    note.textContent = "";
    window.setTimeout(function () { note.textContent = words; }, 40);
  }

  function whole(field) {
    var text = field.value.trim();
    return /^\d{1,5}$/.test(text) ? parseInt(text, 10) : NaN;
  }

  add.addEventListener("click", function () {
    var first = whole(from);
    var last = to.value.trim() === "" ? first : whole(to);
    if (isNaN(first) || isNaN(last)) {
      say("Enter the first page, and the last page if the part has more than one.");
      (isNaN(first) ? from : to).focus();
      return;
    }
    if (first < 1 || last < first || (total && last > total)) {
      say("The pages must run from 1" + (total ? " to " + total : "") + ", the first no later than the last.");
      from.focus();
      return;
    }
    var list = entries();
    list.push(first === last ? String(first) : first + "-" + last);
    write(list);
    from.value = "";
    to.value = "";
    say("Part " + list.length + " added.");
    from.focus();
  });

  // Enter in a page field adds the part (it would otherwise send the whole form).
  [from, to].forEach(function (field) {
    field.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      add.click();
    });
  });

  // "Remove" on a row of the preview (the rows are drawn again after every change).
  document.addEventListener("click", function (event) {
    var target = event.target;
    var remove = target && target.closest ? target.closest("[data-remove-part]") : null;
    if (!remove) return;
    var at = parseInt(remove.getAttribute("data-remove-part"), 10) - 1;
    var kept = entries();
    if (at < 0 || at >= kept.length) return;
    kept.splice(at, 1);
    write(kept);
    say("Part " + (at + 1) + " removed.");
    from.focus();
  });

  function showRemoveButtons() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-remove-part]"), function (b) {
      b.hidden = false;
    });
  }
  document.addEventListener("htmx:afterSwap", showRemoveButtons);
  document.addEventListener("htmx:load", showRemoveButtons);
  showRemoveButtons();

  // Script is here: show the page fields. The box is folded away unless something needs fixing.
  builder.hidden = false;
  if (advanced && !advanced.hasAttribute("data-has-error")) advanced.open = false;
})();
