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

## RapidTable 3.0.2 (SLANet-plus pre- and post-processing)

`pipeline/tables.py` runs the SLANet-plus table-structure model the way RapidTable does.
The code around the model is vendored, with changes, as `rapid_table.py`.

| Field | Value |
|---|---|
| Package | `rapid_table` 3.0.2 (RapidAI; the code derives from PaddleOCR's PP-Structure, PaddlePaddle Authors) |
| Licence | Apache-2.0 (`rapid_table-LICENSE.txt`); notice: `rapid_table-NOTICE.txt` |
| Source | https://github.com/RapidAI/RapidTable (tag `v3.0.2`), files `rapid_table/table_structure/pp_structure/pre_process.py`, `.../post_process.py`, `rapid_table/table_matcher/main.py` |
| What changed | listed in the header of `rapid_table.py`: Pillow instead of OpenCV for the resize, one image at a time, cell boxes kept in order (an all-zero box is `None`), bounded span parsing, type hints; the HTML matcher, OCR and engine wrapper are not used |

The model file itself is not vendored: it is downloaded (Docker build, `make table-model`)
and checked against the SHA-256 that RapidTable 3.0.2's `default_models.yaml` gives for
`slanet_plus` (`pipeline/table_model.py`, which records the URL).
