"""The finished document as an IMS Common Cartridge 1.3 package (T3-8).

A cartridge is the one file format Canvas, Blackboard, Moodle, D2L and Brightspace all import:
a ZIP with `imsmanifest.xml` at its root, which lists the resources, how they are organised,
and the files each resource holds. This module wraps the Canvas version of the document
(`export_canvas`: styles written into the page, pictures as files) in one:

    imsmanifest.xml
    web_resources/<folder>/index.html
    web_resources/<folder>/images/image-01.png ...

The manifest has the cartridge metadata (schema "IMS Common Cartridge", version 1.3.0, the
document's title and language), one organization (a rooted hierarchy: an empty root item
holding one item, titled as the document, that points at the page), and one resource of type
`webcontent` whose files are the page and its pictures. Nothing else goes in: no quizzes, no
discussions, no links to the web, and no script. Nothing in the document's text, MathML,
alt text, headings, tables or links is changed; the page is `export_canvas`'s `index.html`.

`check_cartridge` checks a package against the rules of the Common Cartridge 1.3 profile that
apply to this content (the list is in its docstring), and the tests run it on every
document the Canvas export is tested on. It is not the IMS validator: the official XML schemas
are not in this repository and nothing here fetches them, so a real import into a Canvas
sandbox (docs/canvas-sandbox-test.md) is what shows how a particular LMS reads the file.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from html import escape
from io import BytesIO
from urllib.parse import quote

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from a11y_stem.pipeline.export_canvas import ZIP_TIME, CanvasExport

NS_CP = "http://www.imsglobal.org/xsd/imsccv1p3/imscp_v1p1"
NS_LOMIMSCC = "http://ltsc.ieee.org/xsd/imsccv1p3/LOM/manifest"
NS_LOM = "http://ltsc.ieee.org/xsd/imsccv1p3/LOM/resource"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = (
    f"{NS_CP} http://www.imsglobal.org/profile/cc/ccv1p3/ccv1p3_imscp_v1p2_v1p0.xsd "
    f"{NS_LOM} http://www.imsglobal.org/profile/cc/ccv1p3/LOM/ccv1p3_lomresource_v1p0.xsd "
    f"{NS_LOMIMSCC} http://www.imsglobal.org/profile/cc/ccv1p3/LOM/ccv1p3_lommanifest_v1p0.xsd"
)
SCHEMA = "IMS Common Cartridge"
SCHEMA_VERSION = "1.3.0"
MANIFEST = "imsmanifest.xml"
WEB_RESOURCES = "web_resources"
RESOURCE_TYPE = "webcontent"
_NCNAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+\Z")


def _id(prefix: str, seed: str) -> str:
    """A stable XML ID: a letter prefix and a hash, so the same document gives the same file."""
    return f"{prefix}_{hashlib.sha256(f'{prefix}:{seed}'.encode()).hexdigest()[:16]}"


def _language(lang: str) -> str:
    return lang if _LANGUAGE.match(lang) else "en"


def manifest_xml(export: CanvasExport, files: list[str]) -> str:
    """`imsmanifest.xml` for the cartridge holding `files` (paths inside the zip, the page
    first)."""
    title = escape(" ".join(export.title.split()) or "Document", quote=True)
    seed = export.folder
    page = files[0]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest identifier="{_id("MANIFEST", seed)}" xmlns="{NS_CP}" '
        f'xmlns:lom="{NS_LOM}" xmlns:lomimscc="{NS_LOMIMSCC}" xmlns:xsi="{NS_XSI}" '
        f'xsi:schemaLocation="{SCHEMA_LOCATION}">',
        "  <metadata>",
        f"    <schema>{SCHEMA}</schema>",
        f"    <schemaversion>{SCHEMA_VERSION}</schemaversion>",
        "    <lomimscc:lom>",
        "      <lomimscc:general>",
        f'        <lomimscc:title><lomimscc:string language="{_language(export.lang)}">'
        f"{title}</lomimscc:string></lomimscc:title>",
        "      </lomimscc:general>",
        "    </lomimscc:lom>",
        "  </metadata>",
        "  <organizations>",
        f'    <organization identifier="{_id("ORG", seed)}" structure="rooted-hierarchy">',
        f'      <item identifier="{_id("ROOT", seed)}">',
        f'        <item identifier="{_id("ITEM", seed)}" identifierref="{_id("RES", seed)}">',
        f"          <title>{title}</title>",
        "        </item>",
        "      </item>",
        "    </organization>",
        "  </organizations>",
        "  <resources>",
        f'    <resource identifier="{_id("RES", seed)}" type="{RESOURCE_TYPE}" '
        f'href="{escape(quote(page), quote=True)}">',
        *(f'      <file href="{escape(quote(name), quote=True)}"/>' for name in files),
        "    </resource>",
        "  </resources>",
        "</manifest>",
        "",
    ]
    return "\n".join(lines)


def build_cartridge(export: CanvasExport) -> bytes:
    """The Common Cartridge zip of an export, in a fixed order with a fixed timestamp (the
    same document gives the same bytes). The page is `export.index_html`; its pictures keep
    their relative addresses, so they sit beside it."""
    base = f"{WEB_RESOURCES}/{export.folder}"
    page = f"{base}/index.html"
    pictures = sorted(f"{base}/{name}" for name in export.images)
    files = [page, *pictures]
    entries: list[tuple[str, bytes]] = [(MANIFEST, manifest_xml(export, files).encode("utf-8"))]
    entries.append((page, export.index_html.encode("utf-8")))
    entries += [(f"{base}/{name}", data) for name, data in sorted(export.images.items())]
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            archive.writestr(info, data)
    return out.getvalue()


# --------------------------------------------------------------------------- the check


def check_cartridge(data: bytes) -> list[str]:
    """The ways `data` fails to be a Common Cartridge 1.3 package of web content; an empty
    list means none found. The rules (CC 1.3 profile, content packaging 1.2):

    - a ZIP with `imsmanifest.xml` at its root, well formed, with no DTD;
    - the root is `manifest` in the CC 1.3 `imscp_v1p1` namespace, with an `identifier`;
    - metadata: schema `IMS Common Cartridge`, schemaversion `1.3.0`, and a LOM general
      title with a `language`;
    - exactly one `organization`, `structure="rooted-hierarchy"`, whose single top item has
      no `identifierref`; every item below it has a title, and an `identifierref` naming a
      resource;
    - resources: each has an identifier, the type `webcontent` (the only type this export
      writes), an `href` that is also one of its `file`s, and files that exist in the zip;
    - every `identifier` in the manifest is a valid XML ID and unique;
    - every path is relative, uses `/`, has no `..` and only plain characters once decoded;
    - every file in the zip other than the manifest is listed by a resource, and each file is
      listed once.
    """
    problems: list[str] = []
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        return ["not a zip file"]
    with archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        if len(set(names)) != len(names):
            problems.append("a file name appears twice in the zip")
        if MANIFEST not in names:
            return [*problems, f"{MANIFEST} is not at the root of the zip"]
        try:
            root = ElementTree.fromstring(archive.read(MANIFEST), forbid_dtd=True)
        except (ElementTree.ParseError, DefusedXmlException, ValueError) as exc:
            return [*problems, f"{MANIFEST} is not well-formed XML ({type(exc).__name__})"]
        problems += _check_manifest(root, set(names) - {MANIFEST})
    return problems


def _check_manifest(root, present: set[str]) -> list[str]:
    from urllib.parse import unquote

    problems: list[str] = []
    if root.tag != f"{{{NS_CP}}}manifest":
        return [f"the root element is {root.tag}, not manifest in {NS_CP}"]

    def child(parent, name: str, namespace: str = NS_CP):
        return [c for c in parent if c.tag == f"{{{namespace}}}{name}"]

    identifiers: list[str] = []
    if not root.get("identifier"):
        problems.append("manifest has no identifier")
    else:
        identifiers.append(root.get("identifier", ""))

    def text_of(parent, name: str) -> str:
        found = child(parent, name)
        return "".join(found[0].itertext()).strip() if found else ""

    metadata = next(iter(child(root, "metadata")), None)
    if metadata is None:
        problems.append("manifest has no metadata")
    else:
        schema, version = text_of(metadata, "schema"), text_of(metadata, "schemaversion")
        if schema != SCHEMA:
            problems.append(f"schema is {schema!r}, not {SCHEMA!r}")
        if version != SCHEMA_VERSION:
            problems.append(f"schemaversion is {version!r}, not {SCHEMA_VERSION!r}")
        strings = list(metadata.iter(f"{{{NS_LOMIMSCC}}}string"))
        titled = [s for s in strings if "".join(s.itertext()).strip()]
        if not titled:
            problems.append("the LOM metadata has no title")
        elif not all(_LANGUAGE.match(s.get("language", "")) for s in titled):
            problems.append("a LOM title has no valid language")

    resources: dict[str, object] = {}
    resource_list = next(iter(child(root, "resources")), None)
    if resource_list is None:
        problems.append("manifest has no resources")
        resource_list = []
    listed: list[str] = []
    for resource in child(resource_list, "resource"):
        rid = resource.get("identifier", "")
        identifiers.append(rid)
        resources[rid] = resource
        if resource.get("type") != RESOURCE_TYPE:
            problems.append(f"resource {rid} has type {resource.get('type')!r}, not webcontent")
        files = [f.get("href", "") for f in child(resource, "file")]
        if not files:
            problems.append(f"resource {rid} lists no files")
        if resource.get("href") is None or unquote(resource.get("href", "")) not in map(
            unquote, files
        ):
            problems.append(f"resource {rid}: href is not one of its files")
        for href in files:
            path = unquote(href)
            listed.append(path)
            if path.startswith("/") or "\\" in path or ".." in path.split("/") or "://" in path:
                problems.append(f"file path {path!r} is not a plain relative path")
            elif not _SAFE_PATH.match(path):
                problems.append(f"file path {path!r} has characters outside A-Z a-z 0-9 . _ - /")
            if path not in present:
                problems.append(f"file {path!r} is listed but not in the zip")
    if len(set(listed)) != len(listed):
        problems.append("a file is listed more than once")
    for path in sorted(present - set(listed)):
        problems.append(f"file {path!r} is in the zip but no resource lists it")

    organizations = next(iter(child(root, "organizations")), None)
    organization_list = child(organizations, "organization") if organizations is not None else []
    if len(organization_list) != 1:
        problems.append(f"{len(organization_list)} organizations, not exactly one")
    for organization in organization_list:
        identifiers.append(organization.get("identifier", ""))
        if organization.get("structure") != "rooted-hierarchy":
            problems.append("the organization's structure is not rooted-hierarchy")
        tops = child(organization, "item")
        if len(tops) != 1:
            problems.append(f"the organization has {len(tops)} top items, not exactly one")
        for top in tops:
            identifiers.append(top.get("identifier", ""))
            if top.get("identifierref") is not None:
                problems.append("the top item points at a resource")
            leaves = list(top.iter(f"{{{NS_CP}}}item"))[1:]
            if not leaves:
                problems.append("the top item holds no items")
            for item in leaves:
                identifiers.append(item.get("identifier", ""))
                ref = item.get("identifierref")
                if ref not in resources:
                    problems.append(f"item {item.get('identifier')} points at no resource")
                if not "".join("".join(t.itertext()) for t in child(item, "title")).strip():
                    problems.append(f"item {item.get('identifier')} has no title")
    for identifier in identifiers:
        if not _NCNAME.match(identifier):
            problems.append(f"identifier {identifier!r} is not a valid XML ID")
    if len(set(identifiers)) != len(identifiers):
        problems.append("an identifier is used twice")
    return problems
