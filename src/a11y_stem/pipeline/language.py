"""Which language a text is in: the document's, and passages in another language.

The output's `<html lang>` decides which voice a screen reader uses (and how MathCAT
speaks the maths), and a passage in another language needs its own `lang` (WCAG 3.1.1,
3.1.2). The PDF's /Lang is used when it has one; most PDFs do not (none of the test
documents does), so the text pass detects the language from the body text with
py3langid (`detect`).

The detector (decided 2026-10-09, docs/layout.md "The text pass"): py3langid 0.4
(BSD-3-Clause; a maintained fork of langid.py, Lui and Baldwin 2012), a naive Bayes
model of byte n-grams for 97 languages in a 4.5 MB file, about 0.3 s to load and well
under a millisecond per paragraph. It needs NumPy. lingua-py (Apache-2.0) is more
accurate on very short text but its wheel installs about 290 MB of models; fastText's
lid.176 model is CC BY-SA 3.0, a share-alike licence this project does not take on. On
a check of 24 short academic sentences (English with maths, French, German, Spanish,
Italian, Portuguese, Dutch, a Latin tag) py3langid placed all 24, lingua 23.

Only words are classified (`prose`): inline equations, numbers, single letters (mostly
variables), symbols and code-like tokens are left out, so maths does not look like a
language of its own. Short or maths-heavy text gives no answer rather than a guess.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from typing import Any

from a11y_stem.core.types import INLINE_MARKER_RE

DETECTOR = "py3langid"
DEFAULT_LANG = "en"
# The whole document: at least this many letters of prose, and this probability.
MIN_DOCUMENT_LETTERS = 100
MIN_DOCUMENT_CONFIDENCE = 0.90
# ... and for any language but the default, more prose and a near-certain answer: a
# one-page list of English terms (reagents, species, element names) reads to the
# detector as Latin at 0.99, and a wrong document language makes a screen reader read
# the whole file in the wrong voice, while the default only asks for a check
# (`lang_assumed`) the instructor confirms on the job page.
MIN_OTHER_DOCUMENT_LETTERS = 250
MIN_OTHER_DOCUMENT_CONFIDENCE = 0.99
# Never a whole document's language from detection alone (a PDF's /Lang or the
# instructor can still say so): Latin, and the constructed languages py3langid knows.
NOT_DOCUMENT_LANGUAGES = frozenset({"la", "eo", "vo"})
# One paragraph or list item in another language than the document: at least this many
# characters of prose, mostly words, and a near-certain answer (a wrong passage language
# makes a screen reader read English with a French voice, so this errs towards none).
MIN_PASSAGE_CHARS = 40
MIN_PASSAGE_WORD_SHARE = 0.6
MIN_PASSAGE_CONFIDENCE = 0.98
MAX_SAMPLE_CHARS = 20_000  # of prose, from the start of the document

# A word: two or more letters, with inner apostrophes or hyphens ("l'arithmétique",
# "free-body"). Digits, underscores and symbols make a token maths or code, not a word.
_WORD = re.compile(r"[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*")
_EDGE_PUNCTUATION = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb()[]{}.,;:!?\u2026\u2013\u2014"
_MATH_LETTERS = range(0x1D400, 0x1D800)  # Mathematical Alphanumeric Symbols

_lock = threading.Lock()
_identifier: Any = None


def _classifier() -> Any:
    """py3langid's identifier with probabilities, loaded once (it takes ~0.3 s)."""
    global _identifier
    with _lock:
        if _identifier is None:
            from py3langid.langid import MODEL_FILE, LanguageIdentifier

            _identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
        return _identifier


def prose(text: str) -> tuple[str, int, int]:
    """(the words of `text` joined by spaces, how many words, how many tokens).

    Inline-equation markers count as tokens that are not words, so a sentence that is
    mostly maths has a low share of words."""
    tokens = INLINE_MARKER_RE.sub(" # ", text).split()
    words = []
    for token in tokens:
        core = token.strip(_EDGE_PUNCTUATION)
        if _WORD.fullmatch(core) and not any(ord(c) in _MATH_LETTERS for c in core):
            words.append(core)
    return " ".join(words), len(words), len(tokens)


def detect(words: str) -> tuple[str, float] | None:
    """(ISO 639-1 code, probability) for a text of words, or None for no words."""
    if not words.strip():
        return None
    code, probability = _classifier().classify(words)
    return str(code), float(probability)


def document_language(texts: Iterable[str]) -> str | None:
    """The language of the document's body text, or None when unsure (too little prose,
    or no language probable enough; for a language other than English, a stricter bar,
    and never Latin or a constructed language)."""
    sample: list[str] = []
    size = 0
    for text in texts:
        words, _, _ = prose(text)
        if words:
            sample.append(words)
            size += len(words) + 1
            if size >= MAX_SAMPLE_CHARS:
                break
    joined = " ".join(sample)
    letters = sum(c.isalpha() for c in joined)
    if letters < MIN_DOCUMENT_LETTERS:
        return None
    found = detect(joined)
    if found is None or found[1] < MIN_DOCUMENT_CONFIDENCE:
        return None
    code, probability = found
    if primary(code) != DEFAULT_LANG and (
        code in NOT_DOCUMENT_LANGUAGES
        or letters < MIN_OTHER_DOCUMENT_LETTERS
        or probability < MIN_OTHER_DOCUMENT_CONFIDENCE
    ):
        return None
    return code


def passage_language(text: str, document_lang: str) -> str | None:
    """The language of one paragraph or list item when it is confidently not the
    document's, else None."""
    words, word_count, token_count = prose(text)
    if len(words) < MIN_PASSAGE_CHARS or word_count < MIN_PASSAGE_WORD_SHARE * token_count:
        return None
    found = detect(words)
    if found is None or found[1] < MIN_PASSAGE_CONFIDENCE:
        return None
    code = found[0]
    return None if primary(code) == primary(document_lang) else code


# --------------------------------------------------------------------------- tags

# A well-formed BCP 47 language tag (RFC 5646 section 2.1; the grandfathered irregular
# tags such as "i-klingon" are left out): language (with extended subtags), script,
# region, variants, extensions, private use; or a private-use tag alone.
BCP47 = re.compile(
    r"(?:(?:[a-z]{2,3}(?:-[a-z]{3}){0,3}|[a-z]{4,8})"
    r"(?:-[a-z]{4})?"
    r"(?:-(?:[a-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*"
    r"(?:-[0-9a-wyz](?:-[a-z0-9]{2,8})+)*"
    r"(?:-x(?:-[a-z0-9]{1,8})+)?"
    r"|x(?:-[a-z0-9]{1,8})+)",
    re.IGNORECASE,
)


def is_language_tag(value: str) -> bool:
    return bool(value) and len(value) <= 64 and BCP47.fullmatch(value) is not None


def normalise_tag(value: str) -> str | None:
    """`value` as a BCP 47 tag in its usual case ("EN_us" -> "en-US", "zh-hant" ->
    "zh-Hant"), or None when it is not one."""
    tag = value.strip().replace("_", "-")
    if not is_language_tag(tag):
        return None
    parts = tag.lower().split("-")
    out = [parts[0]]
    extension = len(parts[0]) == 1  # a private-use tag ("x-...") stays as it is
    for part in parts[1:]:
        extension = extension or len(part) == 1  # from a singleton ("u", "x") on: as is
        if not extension and len(part) == 4 and part.isalpha():
            out.append(part.title())  # script
        elif not extension and len(part) == 2 and part.isalpha():
            out.append(part.upper())  # region
        else:
            out.append(part)
    return "-".join(out)


# Primary subtags that name no language: undetermined, multiple, no linguistic content,
# uncoded. A document needs a real one for a screen reader to pick a voice.
_NO_LANGUAGE = frozenset({"und", "mul", "zxx", "mis"})


def document_tag(value: str) -> str | None:
    """`value` as a tag a whole document can carry: a well-formed BCP 47 tag whose
    language is a two- or three-letter code that names a language ("fr", "pt-BR"), or
    the English name of a language in `LANGUAGE_NAMES` ("French" -> "fr"). None when it
    is neither, e.g. "x-none", "und" or a misspelt name."""
    named = {name.casefold(): code for code, name in LANGUAGE_NAMES.items()}
    if (code := named.get(value.strip().casefold())) is not None:
        return code
    tag = normalise_tag(value)
    if tag is None:
        return None
    first = primary(tag)
    if not (2 <= len(first) <= 3 and first.isalpha()) or first in _NO_LANGUAGE:
        return None
    return tag


def primary(tag: str) -> str:
    """The language subtag ("pt-BR" -> "pt")."""
    return tag.split("-", 1)[0].lower()


# English names of the languages a course document is most likely to be in, for the job
# page's control. Any other valid tag works too; `language_name` then gives the tag.
LANGUAGE_NAMES: dict[str, str] = {
    "ar": "Arabic",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "la": "Latin",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese",
}


def language_name(tag: str) -> str:
    """ "French" for "fr" or "fr-CA"; the tag itself for a language not in the list."""
    return LANGUAGE_NAMES.get(primary(tag), tag)
