"""Which language a text is in: the document's, and passages in another language.

The output's `<html lang>` decides which voice a screen reader uses (and how MathCAT
speaks the maths), and a passage in another language needs its own `lang` (WCAG 3.1.1,
3.1.2). A wrong document language makes a screen reader read the whole file in the wrong
voice, so it is never guessed silently. The order of the sources is in
`text_pass._document_language`: what the person chose at upload, then the text (this
module), then the file's own /Lang when the text is too short to say, else English,
assumed and asked about on the job page.

The text decides in two steps (SW20d). Letters are counted by script, directly, so Chinese,
Japanese and Korean count like any other text. A script that fixes the language decides it:
Hiragana or Katakana (and the Han letters of that text) Japanese, Hangul Korean, Hebrew,
Greek, Thai, Han without kana Chinese. Latin, Cyrillic and Arabic text goes to the
detector, restricted to the languages written in that script, and one table
(`VOICE_TAGS`) turns the detector's labels into tags a voice exists for ("ary", "arz" to
"ar"). The bar is the same for every language. The language holding the most letters is
the document's; when none holds `MIXED_SHARE` of them the document is `mixed`.

A passage is a paragraph or list item whose language differs from the document's, found
with the same rules, block by block, so an English paragraph in an English document is
never marked foreign.

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
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from readablestem.core.types import INLINE_MARKER_RE

DETECTOR = "py3langid"
DEFAULT_LANG = "en"
# The whole document: at least this many letters, and this probability, for any language.
MIN_DOCUMENT_LETTERS = 100
MIN_DOCUMENT_CONFIDENCE = 0.90
# A letter of a script written without spaces (Chinese, Japanese, Korean, Thai) carries
# about this many Latin letters of text: the minimum counts it so, so a page of Chinese is
# not too short for the bar that a page of English clears.
UNSPACED_WEIGHT = 3
# No language holding at least this share of the letters: the document is mixed.
MIXED_SHARE = 0.60
# Never a whole document's language from detection alone (the person can still say so):
# Latin, the constructed languages py3langid knows, and "no linguistic content". A
# one-page list of English terms (reagents, species) reads to the detector as Latin at 0.99.
NOT_DOCUMENT_LANGUAGES = frozenset({"la", "eo", "vo", "zxx"})
# One paragraph or list item in another language than the document: at least this many
# characters of prose, mostly words, and a near-certain answer (a wrong passage language
# makes a screen reader read English with a French voice, so this errs towards none). A
# block in a script that fixes its language needs only `MIN_SCRIPT_LETTERS` letters.
MIN_PASSAGE_CHARS = 40
MIN_PASSAGE_WORD_SHARE = 0.6
MIN_PASSAGE_CONFIDENCE = 0.98
MIN_SCRIPT_LETTERS = 10
# Kana is "in quantity" in a document when it is this share of its kana and Han letters:
# the Han letters are then Japanese, not Chinese.
KANA_SHARE = 0.05
MAX_SAMPLE_LETTERS = 20_000  # from the start of the document

# The detector's labels that name no voice, and the tag a voice exists for.
VOICE_TAGS = {"ary": "ar", "arz": "ar", "grc": "el", "hbo": "he", "nn": "no"}

# A word: two or more letters, with inner apostrophes or hyphens ("l'arithmétique",
# "free-body"). Digits, underscores and symbols make a token maths or code, not a word.
_WORD = re.compile(r"[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*")
_EDGE_PUNCTUATION = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb()[]{}.,;:!?\u2026\u2013\u2014"
_MATH_LETTERS = range(0x1D400, 0x1D800)  # Mathematical Alphanumeric Symbols

# --------------------------------------------------------------------------- scripts

_SCRIPT_RANGES = (
    (0x0041, 0x024F, "latin"),
    (0x1E00, 0x1EFF, "latin"),
    (0x2C60, 0x2C7F, "latin"),
    (0xA720, 0xA7FF, "latin"),
    (0xFF21, 0xFF5A, "latin"),
    (0x0370, 0x03FF, "greek"),
    (0x1F00, 0x1FFF, "greek"),
    (0x0400, 0x052F, "cyrillic"),
    (0x2DE0, 0x2DFF, "cyrillic"),
    (0xA640, 0xA69F, "cyrillic"),
    (0x0590, 0x05FF, "hebrew"),
    (0xFB1D, 0xFB4F, "hebrew"),
    (0x0600, 0x06FF, "arabic"),
    (0x0750, 0x077F, "arabic"),
    (0x08A0, 0x08FF, "arabic"),
    (0xFB50, 0xFDFF, "arabic"),
    (0xFE70, 0xFEFF, "arabic"),
    (0x0E00, 0x0E7F, "thai"),
    (0x1100, 0x11FF, "hangul"),
    (0x3130, 0x318F, "hangul"),
    (0xA960, 0xA97F, "hangul"),
    (0xAC00, 0xD7FF, "hangul"),
    (0xFFA0, 0xFFDC, "hangul"),
    (0x3040, 0x30FF, "kana"),
    (0x31F0, 0x31FF, "kana"),
    (0xFF66, 0xFF9F, "kana"),
    (0x3400, 0x4DBF, "han"),
    (0x4E00, 0x9FFF, "han"),
    (0xF900, 0xFAFF, "han"),
    (0x20000, 0x323AF, "han"),
)
# Scripts whose words are not set off by spaces: their letters are counted directly, not
# through the word pattern, which a sentence's punctuation would cut.
_UNSPACED = frozenset({"han", "kana", "hangul", "thai"})
# The language each of these scripts fixes without the detector (Han: unless kana is near).
_SCRIPT_LANGUAGE = {
    "kana": "ja",
    "hangul": "ko",
    "hebrew": "he",
    "greek": "el",
    "thai": "th",
    "han": "zh",
}
# The detector's labels for each script it is asked about; Latin is every other label.
_ARABIC_LABELS = frozenset({"ar", "ary", "arz", "fa", "ps", "sdh", "ug", "ur", "ku"})
_CYRILLIC_LABELS = frozenset(
    {"ru", "uk", "be", "bg", "mk", "sr", "kk", "ky", "mn", "tg", "tt", "ba"}
)
_OTHER_LABELS = frozenset(
    {"am", "as", "bn", "dz", "gom", "gu", "hi", "hy", "ka", "km", "kn", "lo", "ml"}
    | {"mr", "my", "ne", "or", "pa", "sa", "si", "ta", "te"}
)
_FIXED_LABELS = frozenset({"el", "grc", "he", "hbo", "ja", "ko", "th", "zh", "yue", "wuu"})
_NOT_LATIN = _ARABIC_LABELS | _CYRILLIC_LABELS | _OTHER_LABELS | _FIXED_LABELS


def script_of(char: str) -> str | None:
    """The script of a letter ("latin", "greek", "cyrillic", "hebrew", "arabic", "thai",
    "hangul", "kana", "han", or "other"); None for anything that is not a letter and for
    the letter-like symbols and styled letters maths is written in."""
    if not char.isalpha():
        return None
    code = ord(char)
    if code in _MATH_LETTERS or 0x2100 <= code <= 0x214F:
        return None
    for low, high, name in _SCRIPT_RANGES:
        if low <= code <= high:
            return name
    return "other"


def _asked(label: str, script: str) -> bool:
    """Whether the detector's `label` is a language written in `script`."""
    if script == "arabic":
        return label in _ARABIC_LABELS
    if script == "cyrillic":
        return label in _CYRILLIC_LABELS
    if script == "other":
        return label in _OTHER_LABELS
    return label not in _NOT_LATIN or label == "sr"  # Serbian is written in both


def scripts_of_language(tag: str) -> frozenset[str]:
    """The scripts a language is written in, for comparing a file's own /Lang with its
    text: every language not listed is Latin."""
    first = primary(tag)
    first = VOICE_TAGS.get(first, first)
    if first == "ja":
        return frozenset({"kana", "han"})
    for script, lang in _SCRIPT_LANGUAGE.items():
        if lang == first:
            return frozenset({script})
    if first in _ARABIC_LABELS:
        return frozenset({"arabic"})
    if first in _CYRILLIC_LABELS:
        return frozenset({"cyrillic"})
    if first in _OTHER_LABELS:
        return frozenset({"other"})
    return frozenset({"latin"})


_lock = threading.Lock()
_identifier: Any = None


def _classifier() -> Any:
    """py3langid's identifier with probabilities, loaded once (it takes ~0.3 s).

    py3langid unpacks its model through a temporary file; when that cannot be written (a
    full or read-only temporary directory) the model is unpacked in memory instead (68
    MB, once). A failure is not kept: the next call tries again."""
    global _identifier
    with _lock:
        if _identifier is None:
            from py3langid.langid import MODEL_FILE, LanguageIdentifier

            try:
                _identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
            except OSError:
                _identifier = _in_memory(MODEL_FILE)
        return _identifier


def _in_memory(model_file: str) -> Any:
    """py3langid's identifier from its model file, unpacked without a temporary file
    (as `py3langid.modelio.load_model` does, through memory)."""
    import io
    import lzma
    from pathlib import Path

    import numpy as np
    from py3langid.langid import MODEL_DIR, LanguageIdentifier
    from py3langid.modelio import _to_array

    path = Path(model_file)
    path = path if path.is_absolute() else MODEL_DIR / path
    with lzma.open(path) as source:
        buffer = io.BytesIO(source.read())
    with np.load(buffer, allow_pickle=False) as data:
        return LanguageIdentifier(
            np.asarray(data["ptc"]),
            np.asarray(data["pc"]),
            data["classes"].tolist(),
            _to_array(data["nextmove"]),
            data["out_feat"].tolist(),
            norm_probs=True,
            tk_row=_to_array(data["nextmove_row"]),
        )


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


def _detect_in(words: str, script: str) -> tuple[str, float] | None:
    """(the most probable language of `words` among those written in `script`, its share
    of their probability). Labels that name one voice ("ary", "arz", "ar") are added up
    under it (`VOICE_TAGS`)."""
    if not words.strip():
        return None
    shares: dict[str, float] = {}
    for label, probability in _classifier().rank(words):
        if _asked(str(label), script):
            tag = VOICE_TAGS.get(str(label), str(label))
            shares[tag] = shares.get(tag, 0.0) + float(probability)
    total = sum(shares.values())
    if total <= 0:
        return None
    tag = max(shares, key=lambda k: shares[k])
    return tag, shares[tag] / total


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class _Block:
    """One block's letters by script, and its words by the script they start in."""

    letters: Counter[str]
    words: dict[str, list[str]]
    word_count: int
    token_count: int

    @property
    def main(self) -> str | None:
        """The script holding the most letters (None for no letters)."""
        return self.letters.most_common(1)[0][0] if self.letters else None


def _block(text: str) -> _Block:
    joined, word_count, token_count = prose(text)
    letters: Counter[str] = Counter()
    words: dict[str, list[str]] = {}
    for word in joined.split():
        first = None
        for char in word:
            script = script_of(char)
            if script is None or script in _UNSPACED:
                continue
            letters[script] += 1
            first = first or script
        if first is not None:
            words.setdefault(first, []).append(word)
    for char in INLINE_MARKER_RE.sub(" ", text):
        if (script := script_of(char)) in _UNSPACED:
            letters[script] += 1
    return _Block(letters, words, word_count, token_count)


def _same_language(a: str, b: str) -> bool:
    first, second = primary(a), primary(b)
    return VOICE_TAGS.get(first, first) == VOICE_TAGS.get(second, second)


def _block_language(block: _Block, document_lang: str | None) -> str | None:
    """The block's language when it is certain: its script fixes it, or the detector is
    sure among the languages of its script. None otherwise (too short, too much maths)."""
    main = block.main
    if main is None:
        return None
    if main in ("han", "kana"):
        if block.letters["han"] + block.letters["kana"] < MIN_SCRIPT_LETTERS:
            return None
        # Han letters are Japanese where kana is near them or the document is Japanese.
        if block.letters["kana"] or (document_lang and primary(document_lang) == "ja"):
            return "ja"
        return "zh"
    if main in _SCRIPT_LANGUAGE:
        return _SCRIPT_LANGUAGE[main] if block.letters[main] >= MIN_SCRIPT_LETTERS else None
    words = " ".join(block.words.get(main, ()))
    if (
        len(words) < MIN_PASSAGE_CHARS
        or block.word_count < MIN_PASSAGE_WORD_SHARE * block.token_count
    ):
        return None
    found = _detect_in(words, main)
    if found is None or found[1] < MIN_PASSAGE_CONFIDENCE:
        return None
    return found[0]


def passage_language(text: str, document_lang: str) -> str | None:
    """The language of one paragraph or list item when it is confidently not the
    document's, else None."""
    found = _block_language(_block(text), document_lang)
    return None if found is None or _same_language(found, document_lang) else found


# --------------------------------------------------------------------------- document


@dataclass(frozen=True)
class DocumentLanguage:
    """The language of a document's text: `lang`, and `mixed` when no language holds
    `MIXED_SHARE` of its letters (`lang` is then the largest)."""

    lang: str
    mixed: bool = False


def document_language(texts: Iterable[str]) -> DocumentLanguage | None:
    """The language of the document's body text, or None when unsure: too little text,
    or no language probable enough, and never Latin or a constructed language."""
    blocks: list[_Block] = []
    size = 0
    for text in texts:
        block = _block(text)
        if block.letters:
            blocks.append(block)
            size += sum(block.letters.values())
            if size >= MAX_SAMPLE_LETTERS:
                break
    totals: Counter[str] = Counter()
    for block in blocks:
        totals.update(block.letters)
    kana = totals["kana"]
    japanese = kana >= 3 and kana >= KANA_SHARE * (kana + totals["han"])
    # Each script's language: fixed by the script, or the detector's over all its words. A
    # script with too little text or no sure answer has none, and its letters are left out.
    of_script = dict(_SCRIPT_LANGUAGE)
    if japanese:
        of_script["han"] = "ja"
    for script in ("latin", "cyrillic", "arabic", "other"):
        words = " ".join(w for block in blocks for w in block.words.get(script, ()))
        if sum(c.isalpha() for c in words) < MIN_DOCUMENT_LETTERS:
            continue
        found = _detect_in(words, script)
        if found is None or found[1] < MIN_DOCUMENT_CONFIDENCE:
            continue
        if found[0] not in NOT_DOCUMENT_LANGUAGES:
            of_script[script] = found[0]
    # The letters of each language. A block the detector is sure is in a language of its
    # script (a French paragraph in a Spanish document) counts for that one, so a document
    # of two languages is mixed even when no single answer is sure for all its words.
    held: Counter[str] = Counter()
    weighed = 0
    for block in blocks:
        own = _block_language(block, None)
        if own in NOT_DOCUMENT_LANGUAGES:
            own = None
        for script, count in block.letters.items():
            if script == block.main and script in ("latin", "cyrillic", "arabic") and own:
                lang = own
            else:
                lang = of_script.get(script)
            if lang is not None:
                held[lang] += count
                weighed += count * (UNSPACED_WEIGHT if script in _UNSPACED else 1)
    total = sum(held.values())
    if weighed < MIN_DOCUMENT_LETTERS:
        return None
    top, letters = held.most_common(1)[0]
    return DocumentLanguage(top, mixed=letters < MIXED_SHARE * total)


def fits_text(tag: str, texts: Iterable[str]) -> bool:
    """Whether a file's own language (its /Lang) is written in the script of its text, or
    the text has no letters to say otherwise. For a text too short for `document_language`."""
    totals: Counter[str] = Counter()
    for text in texts:
        totals.update(_block(text).letters)
    return not totals or totals.most_common(1)[0][0] in scripts_of_language(tag)


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
