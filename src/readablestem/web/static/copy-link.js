/* Copy link (share.html). The document's address is a read-only text field that works without
   this file: it can be selected and copied by hand. With script, a "Copy link" button, hidden
   until now, copies it and says so in a polite status line. Static file, so it runs under
   script-src 'self'.

   Rules this file keeps:
   - Focus stays on the button; only the status line changes. The one exception is a refused
     clipboard: the address is then selected, and focus is on it so the copy keys work.
   - The status says what happened in words ("Link copied." or what to do instead). When the
     browser refuses the clipboard, the address is selected and the status says to press the
     copy keys, so the person is never left with a button that seems to have done nothing.
   - The address is never sent anywhere and never written to a log. */
(function () {
  "use strict";

  var button = document.querySelector("[data-copy-target]");
  if (!button) return;
  var field = document.getElementById(button.getAttribute("data-copy-target"));
  var state = document.getElementById(button.getAttribute("data-copy-state"));
  if (!field || !state) return;

  var timer = 0;

  function say(text) {
    window.clearTimeout(timer);
    // An empty region then the text, so the same words are announced again on a second press.
    state.textContent = "";
    timer = window.setTimeout(function () {
      state.textContent = text;
    }, 50);
  }

  function selectField() {
    field.focus({ preventScroll: true });
    field.select();
    field.setSelectionRange(0, field.value.length);
  }

  function fallback() {
    // Older browsers and insecure pages have no async clipboard: select, then try the
    // command, and say what to do if that is refused too.
    selectField();
    var done = false;
    try {
      done = document.execCommand("copy");
    } catch (e) {
      done = false;
    }
    if (done) {
      button.focus({ preventScroll: true });
      say("Link copied.");
    } else {
      say("Could not copy automatically. The address is selected: press Ctrl+C (Command+C on a Mac).");
    }
  }

  button.hidden = false;
  button.addEventListener("click", function () {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(field.value).then(
        function () {
          say("Link copied.");
        },
        function () {
          fallback();
        }
      );
    } else {
      fallback();
    }
  });
})();
