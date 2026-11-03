# ADR 0008: LaTeX source uploads, converted by LaTeXML in a sandbox

| Field | Value |
|---|---|
| Status | **Accepted 2026-11-03** as "build it, sandboxed": the switch is `FEATURE_LATEX_SOURCE`, off by default, and the sandbox probe must pass on the deployed image before it is turned on |
| Date | 2026-11-03 |
| Decider | Harish |
| Revisits | the rule that MathML is always rebuilt by this tool and never pasted in (`web/mathml.py`, docs/output.md), for one source of MathML |

## Context

An instructor who writes their materials in LaTeX has the exact equations already. Compiling to a
PDF and reading the equations back from the picture of it (the PDF path) throws that away and
then has to guess it again. LaTeXML (NIST) reads the source and writes the document as XML with
Presentation MathML, the same macros expanded the way the author's own TeX would.

The cost is that a `.tex` file is a program. TeX can read any file the process can read, write
files, start programs (`\write18`), loop for ever and use all memory, and `\catcode` lets a file
build any command from characters, so no scan of the text proves a file harmless. The web service
holds the model API keys and the database address in its environment, in the same container.

## Decision

1. **Accept LaTeX source** (a `.tex` file, or a zip of a project) when `FEATURE_LATEX_SOURCE` is
   on, and convert it with LaTeXML into the document model a PDF becomes, so review, the
   record, the file and every export are the ones a PDF job has.
2. **Run it sandboxed, and only sandboxed.** LaTeXML runs under bubblewrap (`core/sandbox.py`): a
   minimal read-only root with no application and no `/home`, the project read-only, a writable
   output folder of its own per step, no network, a new pid namespace, an empty environment,
   `prlimit` caps on memory, CPU time and file size, a wall-clock limit and an output-bytes
   limit. If the sandbox cannot start there is no unsandboxed run: the job fails with "upload
   the PDF instead". A lint (`source/lint.py`) and the zip rules (`web/archive_intake.py`) refuse
   the plain forms earlier, as defence in depth, and are never the guarantee.
3. **Prove it on the deployed image.** `readablestem ops sandbox-probe` checks the sandbox on the
   machine it runs on (docs/operations.md). If it does not print PASS on Railway, the switch
   stays off; the alternative is a separate service with no secrets, which this ADR does not
   build.
4. **Accept LaTeXML's sanitised MathML.** For a source job only, an equation's MathML is
   LaTeXML's own, passed through the existing allowlist sanitiser (`web/mathml.py`), and its LaTeX
   is the source's own (`alttext`). It is an exception to "MathML is rebuilt, never pasted"
   because it is compiled from the author's source, not guessed from a picture. An equation
   LaTeXML could not read (`ltx_ERROR`, an unknown macro) is a required decision showing the TeX,
   with this tool's own LaTeX conversion as the proposal when that works.
5. **Figures use the ordinary figure step** with the project's own picture (PNG, JPEG, GIF, or
   the first page of a PDF). A figure LaTeXML could not draw (TikZ in the shipped image) is a
   required decision with no picture. EPS and SVG are refused in this release.
6. **Quota:** one page per 3,000 characters of TeX (comments removed) or per figure, whichever
   is larger, counted at upload as an estimate.
7. **Beamer** is refused with "upload the compiled PDF" until its own spike passes.

## Consequences

- The image grows by about 270 MB (`INSTALL_LATEXML`, docs/deploy-railway.md).
- LaTeXML 0.8.8 in Debian trixie has no `mhchem` binding and, without TeX Live's pictures
  packages, no TikZ: `\ce` and TikZ pictures become decisions, not conversions. Custom `.sty`
  files are not read (`--includestyles` is off): a document that depends on one may produce
  unknown-macro decisions.
- A source job has no pages to show beside its decisions, so its review is the list view
  (`/jobs/{id}/review/list`); the split view redirects there.
- Every refusal and failure is a content-free code with a plain message that says what to do.
