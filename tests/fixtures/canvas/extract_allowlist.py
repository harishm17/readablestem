"""Write `canvas_sanitize_allowlist.json` from Canvas's own sanitizer configuration.

    uv run python tests/fixtures/canvas/extract_allowlist.py PATH/TO/canvas_sanitize.rb

The input is `gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb` of
instructure/canvas-lms (AGPL-3.0, so the Ruby file is not copied into this repository; the
JSON only records which element, attribute, protocol and CSS property names it allows).
Fetch the file at the commit named in the JSON's `source`:

    gh api "repos/instructure/canvas-lms/contents/gems/canvas_sanitize/lib/canvas_sanitize/\
canvas_sanitize.rb?ref=<commit>" --jq .content | base64 -d

The script checks the file's git blob SHA against the one recorded in `source`, so a
different version of the file is refused rather than silently re-encoded.

What the Ruby does that a plain list read would miss, and this script repeats:
- `:data` in an attribute list allows any `data-*` attribute whose name matches
  `REGEX_DATA_ATTR` (not `data-xml`, `data-method` and a few Canvas hooks);
- an allowed element with no explicit protocol rule gets the default (http, https,
  relative) on the `href`, `src` and `cite` attributes it allows;
- the CSS property list is built with `map`s, expanded here.

The `remove_contents` list is not in the Canvas file: it is the default of the `sanitize`
gem (v7.0.0, lib/sanitize/config/default.rb) that Canvas merges its configuration into.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "canvas_sanitize_allowlist.json"

SOURCE = {
    "repo": "instructure/canvas-lms",
    "path": "gems/canvas_sanitize/lib/canvas_sanitize/canvas_sanitize.rb",
    "commit": "c4912103269a6afa9e6957460b6d2bc6a1d8605f",
    "commit_note": (
        "last commit that changed the file (2026-03-19); the file is identical on master at "
        "1c9f0bb8013ed69c4f2efe11fd483025469b7e6c (2026-10-14)"
    ),
    "git_blob_sha": "1c70bcfb964cfc3cbdd1d6fe6ce46ece8ea2b337",
    "licence": "AGPL-3.0",
}
SANITIZE_GEM = {
    "repo": "rgrove/sanitize",
    "version": "7.0.0",
    "tag_commit": "19ee751d1b1c1e9d0335c0438fdb6b389544c45c",
    "path": "lib/sanitize/config/default.rb",
    "note": "Canvas pins sanitize ~> 7.0 (Gemfile.d/app.rb)",
}
# sanitize 7.0.0, config/default.rb: elements that are not allowed are removed together
# with their contents when listed here; any other removed element leaves its contents.
REMOVE_CONTENTS = [
    "iframe",
    "math",
    "noembed",
    "noframes",
    "noscript",
    "plaintext",
    "script",
    "style",
    "svg",
    "xmp",
]
DEFAULT_PROTOCOLS = ["http", "https", ":relative"]


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def strip_comments(text: str) -> str:
    return re.sub(r"(?m)#(?!\{).*$", "", text)


def block(text: str, opener: str) -> str:
    """The text between the bracket that `opener` ends in and its match."""
    start = text.index(opener) + len(opener) - 1
    pairs = {"[": "]", "{": "}", "(": ")"}
    close, depth = pairs[text[start]], 0
    for i in range(start, len(text)):
        if text[i] == text[start]:
            depth += 1
        elif text[i] == close:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    raise ValueError(f"unclosed {opener}")


def words(body: str) -> list[str]:
    """The members of a Ruby list: quoted strings, `%w[...]` words, `:symbols`."""
    out: list[str] = []
    for m in re.finditer(r'"([^"]*)"|(:\w+)', body):
        out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


def attribute_lists(body: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    pattern = re.compile(
        r'(?:"(?P<el>[\w-]+)"|:(?P<all>all))\s*=>\s*(?:%w\[(?P<w>[^\]]*)\]|\[(?P<q>[^\]]*)\])\.freeze'
    )
    for m in pattern.finditer(body):
        key = "*" if m.group("all") else m.group("el")
        items = m.group("w").split() if m.group("w") is not None else words(m.group("q"))
        result[key] = sorted(set(items), key=items.index)
    return result


def protocol_rules(body: str) -> dict[str, dict[str, list[str]]]:
    rules: dict[str, dict[str, list[str]]] = {}
    for m in re.finditer(r'"([\w-]+)"\s*=>\s*\{(.*?)\}', body, re.S):
        element, inner = m.group(1), m.group(2)
        attrs: dict[str, list[str]] = {}
        for pair in re.finditer(r'"([\w-]+)"\s*=>\s*(DEFAULT_PROTOCOLS|\[[^\]]*\])', inner):
            value = pair.group(2)
            attrs[pair.group(1)] = (
                list(DEFAULT_PROTOCOLS) if value == "DEFAULT_PROTOCOLS" else words(value)
            )
        rules[element] = attrs
    return rules


def css_properties(body: str) -> list[str]:
    plain = set(re.search(r"%w\[(.*?)\]\s*\+", body, re.S).group(1).split())  # type: ignore[union-attr]
    plain |= {
        f"grid-{i}"
        for i in [
            "area",
            "auto-columns",
            "auto-flow",
            "auto-rows",
            "column",
            "gap",
            "row",
            "template",
        ]
    }
    plain |= {f"grid-template-{i}" for i in ["areas", "columns", "rows"]}
    plain |= {f"grid-column-{i}" for i in ["end", "gap", "start"]}
    plain |= {f"grid-row-{i}" for i in ["end", "gap", "start"]}
    plain |= {f"background-{i}" for i in ["attachment", "color", "image", "position", "repeat"]}
    plain |= {f"background-position-{i}" for i in ["x", "y"]}
    plain |= {
        f"border-{i}"
        for i in [
            "bottom",
            "collapse",
            "color",
            "left",
            "right",
            "spacing",
            "style",
            "top",
            "width",
        ]
    }
    plain |= {
        f"border-{side}-{part}"
        for side in ["bottom", "left", "right", "top"]
        for part in ["color", "style", "width"]
    }
    plain |= {f"font-{i}" for i in ["family", "size", "stretch", "style", "variant", "width"]}
    plain |= {f"list-style-{i}" for i in ["image", "position", "type"]}
    plain |= {f"margin-{i}" for i in ["bottom", "left", "right", "top", "offset"]}
    plain |= {f"padding-{i}" for i in ["bottom", "left", "right", "top"]}
    return sorted(plain)


def main(path: Path) -> None:
    raw = path.read_bytes()
    sha = git_blob_sha(raw)
    if sha != SOURCE["git_blob_sha"]:
        sys.exit(f"{path}: blob {sha} is not the recorded {SOURCE['git_blob_sha']}")
    text = strip_comments(raw.decode("utf-8"))
    sanitize = text[text.index("SANITIZE = {") :]

    elements = words(block(sanitize, "elements: ["))
    attributes = attribute_lists(block(sanitize, "attributes: {"))
    protocols = protocol_rules(block(sanitize, "protocols: {"))
    # "Any allowed elements for which we don't explicitly declare a protocol ..."
    for element in elements:
        if element in protocols:
            continue
        found = {
            a: list(DEFAULT_PROTOCOLS)
            for a in ("href", "src", "cite")
            if a in attributes.get(element, [])
        }
        if found:
            protocols[element] = found
    properties = css_properties(block(sanitize, "properties: ("))
    data = {
        "source": SOURCE,
        "sanitize_gem": SANITIZE_GEM,
        "note": (
            "Generated by extract_allowlist.py. Attribute names in '*' apply to every allowed "
            "element; ':data' allows data-* attributes (see data_attribute_regex). Protocol "
            "lists name the URL schemes an attribute may use; ':relative' is a URL with no "
            "scheme (a relative or root-relative path). A style attribute keeps only "
            "css_properties, and url() values in it only keep css_protocols."
        ),
        "elements": sorted(elements),
        "remove_contents_when_removed": REMOVE_CONTENTS,
        "attributes": dict(sorted(attributes.items())),
        "data_attribute_regex": (
            r"\Adata-(?!xml|kyle-menu|turn-into-dialog|flash-message|popup-within|"
            r"html-tooltip-title|method)[a-z_][\w.à-öø-ſǝ-ʯ-]*\z"
        ),
        "protocols": dict(sorted(protocols.items())),
        "css_properties": properties,
        "css_protocols": list(DEFAULT_PROTOCOLS),
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(elements)} elements, {len(properties)} css properties")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
