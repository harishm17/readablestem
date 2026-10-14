# MathML `alttext` from the stored speech (T2-24)

Status: built, **off by default** (`MATHML_ALTTEXT=false`). Do not switch it on for users
until a person has run the manual test below and recorded the result at the end of this
page.

## What the switch does

Each converted equation already has a plain-English reading, made by
`pipeline/speech.py` and stored on the fix (`EquationFix.speech`). The review screen shows
it. The downloaded HTML keeps MathML only, so a screen reader voices the maths itself.

With `MATHML_ALTTEXT=true`, `assemble.render_html` also writes that stored reading as the
`alttext` attribute of the equation's `<math>`:

```html
<math display="block" alttext="x equals y squared">...</math>
```

Only the attribute is added. The MathML inside is unchanged, there is no `aria-label`
(that would hide the structure, see the `speech.py` docstring), and nothing is written
when an equation has no stored reading or the reading is longer than 300 characters (a
cut-short reading would mislead more than none). The text is escaped like every other
attribute. The pipeline and the review screen's re-render both read the setting, so a
downloaded file and a re-rendered one agree.

## Why it is off

`alttext` is a fallback label, and screen readers disagree about when it wins over the
maths structure:

- Readers that voice the MathML (NVDA with MathCAT, JAWS with its math reader, VoiceOver
  in Safari) may ignore it, in which case it is harmless.
- Some readers, and some browsers' accessibility trees, use `alttext` as the whole name
  of the equation. Then the reader hears our sentence and cannot step through the
  equation (exploration, navigation by fraction or root, braille math). That is a loss for
  the users who need the structure most.
- Where the maths does not render at all (old browsers, some LMS viewers), `alttext` is the
  only thing a reader gets. That is the gain.

Which of these happens is a property of the reader and its settings, so it has to be
checked, not assumed. Our readings are also not yet reviewed by a math-fluent screen-reader
user (T2-23 improved matrices, cases, nuclides and units).

## Manual test with NVDA + MathCAT

You need a Windows machine (or VM) with the current NVDA, the MathCAT add-on, and Firefox
and Chrome. Test files are made with the two settings; the same PDF both times.

1. Make two copies of one conversion that has these equations: a fraction, a power, a
   square root, a sum with limits, a 2 by 2 matrix, a piecewise definition (cases), a
   nuclide such as carbon 14, and a number with a unit (5 m/s). One run with
   `MATHML_ALTTEXT=false`, one with `true`. (Offline test runs with `FAKE_MODELS` have
   placeholder equations; use a real conversion or `tests/fixtures`.)
2. Install NVDA, then the MathCAT add-on. In NVDA settings, Math: make sure the math
   reader is MathCAT, speech style ClearSpeak, verbosity Medium. Write down the NVDA and
   MathCAT versions.
3. Open the `false` copy in Firefox. With the browse cursor, move onto each equation.
   Note what NVDA says (this is the baseline). Press NVDA+Alt+Down arrow to enter math
   navigation and check you can step into the parts (numerator, denominator, matrix
   cells).
4. Open the `true` copy and repeat step 3 on the same equations.
5. For each equation, record:
   - what NVDA says without `alttext`, and with it;
   - whether it reads our sentence or its own reading (they should match the baseline);
   - whether you can still enter math navigation and step through the parts;
   - whether braille output (if you have a display or the braille viewer) still shows math
     braille and not our sentence.
6. Repeat 3-5 in Chrome.
7. Optional, if you have them: JAWS in Chrome/Edge, VoiceOver in Safari on macOS or
   iOS, TalkBack with a MathML-capable browser. Same equations, same questions.

### Decision rule

Turn it on by default only if, in NVDA + MathCAT on both browsers:

- the spoken reading with `alttext` is the same as without it (alttext ignored), or
  clearly better where the maths does not render; and
- math navigation still works with it on; and
- none of the equations reads worse than the baseline.

If any reader replaces the structure with the sentence and removes navigation, leave it
off, or offer it only as an option per download ("add text labels to equations") with
that warning. Do not decide from one reader.

## Result log

| Date | Tester | NVDA / MathCAT / browser | Result | Decision |
|---|---|---|---|---|
| | | | not yet tested | off |
