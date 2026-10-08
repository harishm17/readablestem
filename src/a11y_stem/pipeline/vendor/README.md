# Vendored third-party files

## axe-core 4.14.0

`pipeline/html_checks.py` injects `axe.min.js` into headless Chromium to check generated
HTML. It is vendored (not fetched at run time) so checks work offline, give the same
results on every run, and never send a document to a CDN.

| Field | Value |
|---|---|
| Package | `axe-core` 4.14.0 (Deque Systems), latest on npm on 2026-10-07 |
| Licence | MPL-2.0 (`axe-core-LICENSE.txt`); bundled MIT code listed in `axe-core-LICENSE-3RD-PARTY.txt` |
| Source | https://github.com/dequelabs/axe-core (tag `v4.14.0`) |
| Tarball | https://registry.npmjs.org/axe-core/-/axe-core-4.14.0.tgz |
| Tarball SHA-1 (npm `dist.shasum`) | `a9819d3932a8198fbe935d8cbd8afcdfecbcf31d` |
| Tarball integrity (npm `dist.integrity`) | `sha512-9WTZxEjsZ7b13TH8JPmbV2z8CHbl80/2hm3XPEG4JgNdQLK81IBRXmSxHfMAOkSqQeRxT/0dwNDz2GOm3zzpcQ==` |
| `axe.min.js` SHA-256 | `20c09fe157a8a34a30e241aaa1fcdade657734f08ab379ecfbeb7d45cc46e878` |
| `axe.min.js` SRI | `sha256-IMCf4Veoo0ow4kGqofza3mV3NPCKs3ns++t9RcxG6Hg=` (matches `sri-history.json` in the tarball) |

The files are unmodified copies from the tarball (`package/axe.min.js`, `package/LICENSE`,
`package/LICENSE-3RD-PARTY.txt`); the licence header at the top of `axe.min.js` is kept.
`tests/test_assemble_checks.py` fails if the SHA-256 above stops matching the file.

### Updating

```
cd /tmp && npm pack axe-core            # prints shasum and integrity; compare with `npm view axe-core dist`
tar -xzf axe-core-*.tgz
sha256sum package/axe.min.js            # record here and in html_checks.AXE_SHA256
cp package/axe.min.js package/LICENSE package/LICENSE-3RD-PARTY.txt <this dir>   # rename the licences as above
uv run pytest -m browser tests/test_assemble_checks.py
```
