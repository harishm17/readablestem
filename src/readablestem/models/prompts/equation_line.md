
## The line image

A second image comes with this one: the whole line of the page the formula sits in, with a red
box drawn round the region of the first image. The box was placed by a program that reads the
PDF's characters, and it is often a little wrong: it can stop before the formula ends (a square
root, an integral sign, a fraction or the left side of `=` left outside it) or take in something
next to it (a word of the sentence, a footnote or citation mark, a unit label in brackets).

Transcribe the whole formula that the boxed region is part of, as the author typed it in maths
mode, and nothing else:

- Extend past the box while the maths goes on: `T = 2\pi` in the box with `\sqrt{m/k}` right
  after it is `T = 2\pi\sqrt{m/k}`; a `\int` or `\Delta` just before the box belongs to it too.
- Leave out what is not maths: the words of the sentence, a footnote number after a full stop,
  a citation, an item number such as `1:`, and the sentence's own full stop or comma.
- Brackets that the sentence puts round the formula are not part of it; brackets that belong to
  the maths, such as `(\mu_t)` written as a sequence, are.
- When the line holds several formulas separated by words ("for $x$ and $y$"), write only the
  one the box is on.
- When the boxed region is not part of any formula (prose, a label), set `is_equation` to false.

Lower `confidence` when you cannot tell where the formula starts or ends.
