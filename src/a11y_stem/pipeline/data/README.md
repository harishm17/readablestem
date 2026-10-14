# Data files of the pipeline

## words-en.txt.gz

A list of 63,849 lower-case English words, one per line, sorted, gzip-compressed (168 KB).
`pipeline/hyphens.py` uses it to decide whether a hyphen at the end of a line was put there
by line breaking ("veloc-" and "ity") or belongs to a compound ("well-" and "known"). It is
read only to answer "is this a word"; no text from a document is ever compared against
anything outside the machine.

| Field | Value |
|---|---|
| Source | the Debian/Ubuntu `wamerican` package, version 2020.12.07-4build1 (`/usr/share/dict/american-english`), built from SCOWL (Spell Checker Oriented Word Lists) by Kevin Atkinson, http://wordlist.sourceforge.net/ |
| Licence | the SCOWL permission notice (a permissive licence: use, copy, modify, distribute and sell the lists "for any purpose ... without fee", provided the copyright notice and the permission notice appear in all copies); the text is below and in `THIRD_PARTY_NOTICES.md`. The parts SCOWL takes from the Moby word lists are public domain |
| Filter | only entries made of two or more letters a-z, all lower case: no proper nouns (they start with a capital), no possessives, no single letters, no abbreviations with punctuation |
| SHA-256 of the `.gz` file | `1e2e548ab2be7d641a24470ec121034f6d9b03f36df8a0b66fbe2ecaddbfbb8e` (`tests/test_layout_hyphens.py` fails if it stops matching) |

Rebuild (the file is byte for byte the same on any machine with the same package):

```
python3 -c "import re,gzip,io; ws=sorted({w.strip() for w in open('/usr/share/dict/american-english') if re.fullmatch(r'[a-z]{2,}',w.strip())}); b=io.BytesIO(); f=gzip.GzipFile(fileobj=b,mode='wb',mtime=0,compresslevel=9); f.write(('\n'.join(ws)+'\n').encode()); f.close(); open('words-en.txt.gz','wb').write(b.getvalue())"
```

### Copyright and permission notice (extracted from the SCOWL README, as the package ships it)

    The collective work is Copyright 2000-2011 by Kevin Atkinson as well
    as any of the copyrights mentioned below:

      Copyright 2000-2011 by Kevin Atkinson

      Permission to use, copy, modify, distribute and sell these word
      lists, the associated scripts, the output created from the scripts,
      and its documentation for any purpose is hereby granted without fee,
      provided that the above copyright notice appears in all copies and
      that both that copyright notice and this permission notice appear in
      supporting documentation. Kevin Atkinson makes no representations
      about the suitability of this array for any purpose. It is provided
      "as is" without express or implied warranty.

The word list is general English. A technical word it lacks ("eigen-" and "vector") is
joined as one word (see `hyphens.py`), the way hyphenation breaks are repaired without a
list.
