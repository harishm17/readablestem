"""The encoded Canvas allowlist and the model of the sanitizer built on it.

The allowlist (`fixtures/canvas/canvas_sanitize_allowlist.json`) is read from Canvas's own
`canvas_sanitize.rb` by `fixtures/canvas/extract_allowlist.py`; these tests pin the facts the
Canvas export depends on, so a re-extraction that changes one shows here, and show that the
model removes what the sanitizer removes.
"""

from __future__ import annotations

from canvas_sanitizer import parse, sanitize, serialize


def kept(html: str) -> str:
    return serialize(sanitize(parse(html)), canonical=True)


def test_an_image_survives_only_with_an_http_or_relative_source():
    assert kept('<img src="images/a.png" alt="A">') == '<img src="images/a.png" alt="A">'
    assert kept('<img src="/courses/1/file_contents/x.png" alt="A">') == (
        '<img src="/courses/1/file_contents/x.png" alt="A">'
    )
    assert kept('<img src="https://x.example/a.png" alt="A">') == (
        '<img src="https://x.example/a.png" alt="A">'
    )
    assert kept('<img src="data:image/png;base64,AAAA" alt="A">') == '<img alt="A">'
    assert kept('<img src="javascript:alert(1)" alt="A">') == '<img alt="A">'
    # a colon after a slash is a path, not a scheme
    assert kept('<img src="images/a:b.png" alt="A">') == '<img src="images/a:b.png" alt="A">'
