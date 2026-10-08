# Vendored front-end files

Served from our own origin so the Content-Security-Policy can stay `script-src 'self'`
(no CDN). The app works without these files; they only enhance it.

| File | Package | Version | Licence | Source |
|---|---|---|---|---|
| `htmx-2.0.11.min.js` | `htmx.org` (npm) | 2.0.11 | 0BSD (Zero-Clause BSD) | `https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz`, file `package/dist/htmx.min.js` |

Integrity (checked by `tests/test_web_pages.py`):

- SHA-256 of `htmx-2.0.11.min.js`:
  `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`
- SHA-384 (the `integrity` attribute in `templates/base.html`):
  `sha384-2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg`
- npm tarball integrity (verified on download, 2026-10-07):
  `sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`

htmx 1.x was BSD-2-Clause; from 2.0 the package is published under 0BSD (its `LICENSE`
file reads "Zero-Clause BSD"). 0BSD requires no attribution, but it is listed in
`THIRD_PARTY_NOTICES.md` anyway.

## Updating

1. Download the new tarball from the npm registry and check its `dist.integrity`.
2. Copy `package/dist/htmx.min.js` here as `htmx-<version>.min.js`; delete the old file.
3. Update the file name and `integrity` in `templates/base.html`, and this table.
4. Run `uv run pytest tests/test_web_pages.py` (hash check) and the browser tests.

htmx is configured in `base.html` (`<meta name="htmx-config">`) to work under the strict
CSP: no `eval`, no injected `<style>`, no script tags from responses, no history cache
(so no document content is copied into `localStorage`), same-origin requests only.
