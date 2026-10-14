# Addendum 8: the product was renamed; the registered text carries its new name

Written 2026-10-14, after the run and after Addendum 7. It registers nothing new and changes no
number, no draw, no seed and no recorded answer. `PREREGISTRATION.md` (version 2) and Addenda 1
to 7 stand.

## 1. What happened

On 2026-10-14 the product was renamed ReadableSTEM (the first line of `README.md` gives the name
it had before). In every text file of `proof/` and in `PROOF.md` the old name was replaced by the
new one, and nothing else was changed except the SHA-256 values that name a file which the
replacement changed (section 3). The registered text, the code that ran and the recorded numbers
are the same as before; only the name in them differs.

Registered files kept their old name until then because their hashes had been written down. They
now carry the new name for the same reason the rest of the repository does, and the hashes are
written down again. This addendum and its script are how you can check that nothing else moved.

## 2. How to check it

The tag `proof-heldout-2026-10` is not changed. It still holds the files exactly as registered,
and CI still replays the recorded run there. From a clone that has the tag:

```
python3 proof/verify-rename.py            # prints OK, or the files that differ
python3 proof/verify-rename.py --diff     # the same, and the name changes as a unified diff
```

The script reads every file of `proof/` and `PROOF.md` at the tag, replaces the old name by the
rules below, renews the hashes of changed files, and compares the result byte for byte with this
working tree. It prints `OK` only if every file is identical to the rebuilt one, no file is
missing, and the only files that are not in the tag are this addendum, the script and
`proof/original-hashes/`. It uses the standard library and `git`, writes nothing unless
`--write` is given, and has no network access. The rules are the script's `rename_text` and
`replace_hashes`: the import name and any command, path or package name become `readablestem`;
the name in prose becomes `ReadableSTEM`.

## 3. The hashes

A SHA-256 (in full, or abbreviated to 8 or more characters) that names a file whose text changed
is replaced by the hash of the changed file, and again until nothing changes. That regenerates
`PREREG-v2.sha256` and `PREREG-v1.sha256` (the lines of files that changed), `PREREG-after-review-v1.sha256`,
`selection/rules-before-draw.sha256`, the bundle's `bundle.json`, the set files and the places
where the registration's hash is quoted. The hashes of version 1 of the registration and of the
parser plan, which are not in this repository, are not touched.

**The original hash lists are kept.** `proof/original-hashes/` holds the four hash lists as they
were at the tag, byte for byte. `cd proof/original-hashes && git show proof-heldout-2026-10:proof/PREREGISTRATION.md | sha256sum`
gives the first line of `PREREG-v2.sha256` there, and so on for every file at the tag. This
addendum and `verify-rename.py` are hashed in the current `PREREG-v2.sha256` (the last two lines),
as Addenda 6 and 7 were.

Hashed files outside `proof/` that changed in the same way (their hashes are quoted in
reports and in the documentation):

| File | SHA-256 before | SHA-256 after |
|---|---|---|
| `data/gold/manifest.json` | `63b5de054889b73fe1b15b6c092aca6d16ae09f886fac62adfc9d39ac7fc1cc6` | `b74e1fa4a67bd67ff37c048e992fdf518308ad0e063ed4522129aa43a82981de` |
| `data/gold/tables/manifest.json` | `c994c016627d6f288520c1990cad1d8320dd1f6f29a1d595f7cbfe1a331e1b15` | `218fa7cbfdedf88b6a8a5bebfefda41a011cb82a312f263d1dd6344dedb0f946` |
| `data/dev-v2/dev/manifest.json` | `8156ea5e66776bc5172ca8d2713046faf31ea592eb47cde6f146e289bf71f268` | `df760b3f59cdebe6c7eda3d6c13663d7ae5535881503f0b12da92fc60ecdf9c6` |

## 4. What was not changed, and why

- **The tag, and git history.** They are frozen.
- **Binary recorded material.** The rendered page PDFs and the page images of the held-out
  sections (`proof/bundles/*/media/`) and the images the models were shown print the old name in
  their footers. They are binary and are the same bytes as at the tag. Text that
  the systems under test wrote about those pages (`proof/bundles/*/systems/`, `proof/interim/`) is
  text, and it was changed by the same replacement: where a recorded output quotes the footer, the
  page image it came from still shows the old name.
- **The seeds of the registered draws and samples.** Five strings are hashed into a seeded
  draw or sample and are inputs, not labels: the text of the draw's seed in `selection/draw.py`
  and `selection/draw.json` (and where it is quoted), the seed of the scorer-readiness sample in
  `scorer-readiness/draw.py`, and in the invented-content tools the seeds of the audit sample,
  its order and the figure sample. Changing a letter of one would draw other sections or other
  items. They are the strings in `PROTECTED` in the script. Outside `proof/` the same rule left two
  keys of the web service alone: the strings that derive the upload fingerprint (changing them
  would change every stored fingerprint).
- **A sealed archive.** `data/dev-v2/check.sealed.tar.gz` is sealed by its hash and is not
  opened; whatever name its documents carry is unchanged.
- **Behaviour, not names.** No prompt, threshold, scorer or model setting changed. The recorded
  answers are keyed by requests that include the page images, so they could not be replayed on a
  commit that prints other footers either; the replay is therefore run at the tag, as before.
