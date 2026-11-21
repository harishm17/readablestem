"""The terms and action labels that more than one template or module says.

One word names one concept on every screen:

- check: an automatic check the tool ran on the file. What a person may look at is an item
  (`ITEM`); the verb in advice is "look at" or "compare".
- item: one thing in the document that the tool handled and a person can decide. A part is
  only a page range of a large upload.
- post: put the finished file where students get it. Share: give named people access inside
  ReadableSTEM.
- sample: the practice document in the app. Demo: the public page `/demo`.
- document, never "job". Notification is the browser's; notes are the list on the document
  page.

The words that already have one home are read from there and not copied here: the state
words (`web.states`), the sign-in words, the exam words, the refusals, the dates, the
allowance phrase and the pages rule (`web.quota`), and the message catalog (`web.messages`).

Templates read this module as the `strings` global (`{{ strings.KEEP_AS_IMAGE }}`).
"""

# The one label for keeping the original picture, and how the record and the item's state say
# it afterwards.
KEEP_AS_IMAGE = "Keep as image"
KEPT_AS_IMAGE = "Kept as image (your choice)"
# A page that could not be drawn has no image to keep: its note is what stays.
KEEP_AS_NOTE = "Keep the note"
KEPT_AS_NOTE = "Kept as a note (your choice)"
KEEP_ORIGINAL = "Keep the original"
KEPT_ORIGINAL = "Kept the original (your choice)"
KEEP_WITHOUT_DESCRIPTION = "Keep without a description"
KEPT_WITHOUT_DESCRIPTION = "Kept without a description (your choice)"
LEAVE_AS_WRITTEN = "Leave as written"
LEFT_AS_WRITTEN = "Left as written (your choice)"
# A heading a person rejected is text again, bold where its line is bold: the item's state and
# the record say it in the same words.
MADE_BOLD_TEXT = "Made bold text by you"
MADE_PLAIN_TEXT = "Made plain text by you"

# What a person may look at, and the tool's own word for the lowest severity.
ITEM = "item"
ITEMS = "items"
OPTIONAL_ITEM = "Optional item"
OPTIONAL_ITEMS = "Optional items"
WORTH_A_LOOK = "Worth a look"

# Converting what was not converted, and asking the model to read something again.
CONVERT_MISSING_ITEMS = "Convert the missing items"
READ_AGAIN = "Ask the model to read it again"
MODEL_READS_TODAY = "Model reads today"

# The consent choice on the upload form: the phrase as a clause ("if you let ReadableSTEM
# look at the results") and as the label of the box.
OPT_IN = "let ReadableSTEM look at the results"
OPT_IN_LABEL = f"Also {OPT_IN}"

# The practice document, and the public demo.
OPEN_SAMPLE = "Open the sample document"
SEE_DEMO = "See the demo first (no sign-in)"
