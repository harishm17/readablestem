# Dependencies

Written by `scripts/dependency_record.py --write`; do not edit it by hand. Versions
come from `uv.lock` and the `Dockerfile`, a Python package's license from the
package's own metadata, and the rest from `data/dependency_facts.toml`. The test
suite fails when this file is out of date, when a locked package has no row, when
a license is neither on the allowed list nor accepted below, when a package that
reads a stranger's bytes names no limits or no hostile-input tests, and when the
`Dockerfile` installs something with neither a version nor a stated rule.

Allowed licenses: 0BSD, Apache-2.0, BSD, BSD-2-Clause, BSD-3-Clause, CC0-1.0, ISC, MIT, MIT-0, MIT-CMU, OFL-1.1, PSF-2.0, Unlicense, Zlib. A program that is only ever run as a child process
and never linked may carry another license.

## What reads a stranger's bytes

| Dependency | Reads | Runs in | Hostile-input tests |
|---|---|---|---|
| `anthropic` | a model's reply | the worker, under the spend guard; a reply is parsed by our own validators before it is used | `tests/test_steps_validate.py` |
| `defusedxml` | XML made from an upload (LaTeXML's output) and MathML a model returned | the worker and the conversion child; entity expansion and external entities are refused by the library | `tests/test_steps_validate.py`, `tests/test_source_escapes.py` |
| `fastapi` | every request | the web process | `tests/test_audit_fuzz.py` |
| `latex2mathml` | LaTeX a model returned | the worker, after the LaTeX allowlist and its limit of 2,000 characters | `tests/test_steps_validate.py` |
| `onnxruntime` | page pictures of an upload, as arrays; the model files are ours, checked by SHA-256 | the conversion child, under its memory and time limits | `tests/test_audit_uploads.py` |
| `openai` | a model's reply | the worker, under the spend guard; a reply is parsed by our own validators before it is used | `tests/test_steps_validate.py` |
| `pillow` | pictures inside an upload | the conversion child; the pixel count of a picture is bounded before it is decoded | `tests/test_audit_uploads.py` |
| `py3langid` | text of an upload | the worker; it reads plain text only | `tests/test_text_language.py` |
| `pymupdf` | every uploaded PDF | the conversion child: a separate process with memory, time and file-size limits, so a file that crashes, hangs or balloons the parser costs that child and nothing else | `tests/test_audit_uploads.py`, `tests/workflows/test_wf49_hostile_input.py` |
| `pymupdf-layout` | pages of an uploaded PDF | the conversion child, behind the probe that guards the table step | `tests/test_audit_uploads.py`, `tests/test_tables_fuzz.py` |
| `python-multipart` | the body of every upload request | the web process, behind the size limits that hold before anything is parsed | `tests/test_audit_uploads.py`, `tests/test_audit_fuzz.py` |
| `latexml` (Debian package) | every uploaded LaTeX project | bubblewrap with no network, a read-only system and prlimit limits (core/sandbox.py); ImageMagick's policy allows PNG, JPEG and GIF only | `tests/test_source_escapes.py`, `tests/test_sandbox.py`, `tests/test_archive_intake.py`, `tests/workflows/test_wf49_hostile_input.py` |
| `libreoffice-impress-nogui` (Debian package) | every uploaded PowerPoint file | a child process with memory, time and output limits and its own empty profile | `tests/test_office_convert.py` |
| `libreoffice-writer-nogui` (Debian package) | every uploaded Word file | a child process with memory, time and output limits and its own empty profile | `tests/test_office_convert.py` |
| `pandoc` (Debian package) | HTML this app wrote | a child process with a time limit | `tests/test_export_docx.py` |

## Licenses outside the allowed list

| Package | License | Why it is accepted |
|---|---|---|
| `certifi` | MPL-2.0 | a file of root certificates, used unmodified; MPL-2.0 asks only that changes to its own files be published |
| `pgserver` | not stated | its metadata states no license; a development tool only (an embedded PostgreSQL for local tests), never in the image and never distributed with this project |
| `psycopg` | LGPL-3.0-only | used unmodified as a library installed from the index; LGPL-3.0 lets anyone replace it with another build (THIRD_PARTY_NOTICES.md) |
| `psycopg-binary` | LGPL-3.0-only | the prebuilt psycopg, under the same terms and used the same way |
| `pymupdf` | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | ADR 0003 (docs/decisions/0003-pdf-library-agpl.md): used under AGPL-3.0; the hosted app offers its complete source through the link in every page footer |
| `pymupdf-layout` | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | ADR 0003 and its amendment of 2026-10-09: the same terms as PyMuPDF, for table proposals only |

## Python packages (`uv.lock`)

95 packages. "Where" is the first of: the image, the image's tracing
extra, the development tools, the browser checks.

| Package | Version | License, as the package states it | Where | Why it is here |
|---|---|---|---|---|
| `alembic` | 1.20.0 | MIT | image | database migrations (migrations/) |
| `annotated-doc` | 0.0.5 | MIT | image | needed by fastapi |
| `annotated-types` | 0.8.0 | MIT | image | needed by pydantic |
| `anthropic` | 1.15.0 | MIT | image | the client for the Claude models (models/anthropic_client.py) |
| `anyio` | 4.15.1 | MIT | image | needed by anthropic, httpx, httpx2 and 3 more |
| `authlib` | 1.9.1 | BSD-3-Clause | image | sign-in with an identity provider (web/auth.py) |
| `backoff` | 2.2.1 | MIT | image (tracing) | needed by langfuse |
| `certifi` | 2026.10.23 | MPL-2.0 | image | needed by httpcore, httpx, requests |
| `cffi` | 2.1.1 | MIT-0 | image | needed by cryptography |
| `charset-normalizer` | 3.5.2 | MIT | image (tracing) | needed by requests |
| `click` | 8.5.0 | BSD-3-Clause | image | needed by uvicorn |
| `colorama` | 0.4.6 | BSD License | development | needed by pytest |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | image | needed by authlib, joserfc |
| `defusedxml` | 0.7.1 | PSFL | image | every XML parse of the app: MathML, LaTeXML's output, export checks |
| `docstring-parser` | 0.18.0 | MIT | image | needed by anthropic |
| `execnet` | 2.1.2 | MIT | development | needed by pytest-xdist |
| `fastapi` | 0.143.3 | MIT | image | the web app's routes |
| `fasteners` | 0.20 | Apache-2.0 | development | needed by pgserver |
| `flatbuffers` | 25.12.19 | Apache 2.0 | image | needed by onnxruntime |
| `googleapis-common-protos` | 1.75.5 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-otlp-proto-http |
| `greenlet` | 3.5.6 | MIT AND PSF-2.0 | browser checks | needed by playwright |
| `h11` | 0.16.0 | MIT | image | needed by httpcore, httpcore2, uvicorn |
| `httpcore` | 1.0.9 | BSD-3-Clause | image | needed by httpx |
| `httpcore2` | 2.13.1 | BSD-3-Clause | image | needed by httpx2 |
| `httptools` | 0.8.0 | MIT | image | needed by uvicorn |
| `httpx` | 0.28.1 | BSD-3-Clause | image | outgoing HTTP: model transports, the table model download at build, corpus fetchers of the eval tools |
| `httpx2` | 2.13.1 | BSD-3-Clause | image | needed by anthropic, openai |
| `httpx2-jsfetch` | 1.0 | BSD-3-Clause | image | needed by httpx2 |
| `idna` | 3.20 | BSD-3-Clause | image | needed by anyio, httpx, httpx2 and 1 more |
| `iniconfig` | 2.3.0 | MIT | development | needed by pytest |
| `itsdangerous` | 2.2.0 | BSD License | image | signed session cookies and tokens (web/app.py) |
| `jinja2` | 3.1.6 | BSD License | image | the site's templates; autoescape is on |
| `jiter` | 0.17.0 | MIT | image | needed by anthropic, openai |
| `joserfc` | 1.7.7 | BSD-3-Clause | image | needed by authlib |
| `langfuse` | 4.17.0 | MIT | image (tracing) | optional tracing, off by default; inputs are masked when it is on |
| `latex2mathml` | 3.81.1 | MIT | image | LaTeX to MathML for equations (pipeline/validate.py) |
| `lxml` | 6.1.3 | BSD-3-Clause | development | needed by python-docx |
| `mako` | 1.4.3 | MIT | image | needed by alembic |
| `markupsafe` | 3.0.4 | BSD-3-Clause | image | escaping of text placed into HTML |
| `networkx` | 3.7 | BSD-3-Clause | image | needed by pymupdf-layout |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | image | image arrays for the table models; required by py3langid |
| `onnxruntime` | 1.31.0 | MIT License | image | runs the layout models, SLANet-plus and the optional local math reader on the CPU |
| `openai` | 3.35.1 | Apache-2.0 | image | the client for OpenAI models (models/openai_client.py) |
| `opentelemetry-api` | 1.45.1 | Apache-2.0 | image | needed by anthropic, fastapi, langfuse and 4 more |
| `opentelemetry-exporter-http-transport` | 0.66b1 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-otlp-proto-http |
| `opentelemetry-exporter-otlp-common` | 0.66b1 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-otlp-proto-http |
| `opentelemetry-exporter-otlp-proto-common` | 1.45.1 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-otlp-proto-http |
| `opentelemetry-exporter-otlp-proto-http` | 1.45.1 | Apache-2.0 | image (tracing) | needed by langfuse |
| `opentelemetry-proto` | 1.45.1 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-otlp-proto-common, opentelemetry-exporter-otlp-proto-http |
| `opentelemetry-sdk` | 1.45.1 | Apache-2.0 | image (tracing) | needed by langfuse, opentelemetry-exporter-otlp-common, opentelemetry-exporter-otlp-proto-http |
| `opentelemetry-semantic-conventions` | 0.66b1 | Apache-2.0 | image (tracing) | needed by opentelemetry-sdk |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | image | needed by langfuse, onnxruntime, pytest |
| `pgserver` | 0.1.4 | not stated | development | an embedded PostgreSQL for local tests |
| `pillow` | 13.0.0 | MIT-CMU | image | page and figure pictures: decode, crop, encode |
| `platformdirs` | 4.12.4 | MIT | development | needed by pgserver |
| `playwright` | 1.64.0 | Apache-2.0 | browser checks | headless Chromium for the axe scans, the browser tests and equation rendering in the evals |
| `pluggy` | 1.6.0 | MIT | development | needed by pytest |
| `protobuf` | 7.36.2 | 3-Clause BSD License | image | needed by googleapis-common-protos, onnxruntime, opentelemetry-proto |
| `psutil` | 7.2.2 | BSD-3-Clause | development | needed by pgserver |
| `psycopg` | 3.3.6 | LGPL-3.0-only | image | the PostgreSQL driver of the hosted app |
| `psycopg-binary` | 3.3.6 | LGPL-3.0-only | image | needed by psycopg |
| `py3langid` | 0.4.0 | BSD-3-Clause | image | the language of a document and of passages in another language (pipeline/language.py) |
| `pycparser` | 3.0 | BSD-3-Clause | image | needed by cffi |
| `pydantic` | 2.14.1 | MIT | image | the data model of results and of model replies |
| `pydantic-core` | 2.50.1 | MIT | image | needed by pydantic |
| `pydantic-settings` | 2.16.0 | MIT | image | settings from the environment |
| `pyee` | 13.0.1 | MIT | browser checks | needed by playwright |
| `pygments` | 2.21.0 | BSD-2-Clause | development | needed by pytest |
| `pymupdf` | 1.28.2 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | image | reads PDFs: text, pictures, structure; renders pages |
| `pymupdf-layout` | 1.28.2 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License | image | table proposals only (pipeline/tables.py); ruled out for reading order and as a witness |
| `pytest` | 9.1.1 | MIT | development | the test runner |
| `pytest-timeout` | 2.4.0 | MIT | development | a time limit for each test |
| `pytest-xdist` | 3.8.0 | MIT | development | runs tests in parallel |
| `python-docx` | 1.2.0 | MIT | development | the tests open the Word export with it; never in the image |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | image | needed by pydantic-settings, uvicorn |
| `python-multipart` | 0.0.32 | Apache-2.0 | image | parses upload forms for the web app |
| `pyyaml` | 6.0.3 | MIT | image | needed by pymupdf-layout, uvicorn |
| `requests` | 2.34.2 | Apache-2.0 | image (tracing) | needed by opentelemetry-exporter-http-transport, opentelemetry-exporter-otlp-proto-http |
| `ruff` | 0.17.1 | MIT | development | the linter and formatter |
| `sniffio` | 1.3.1 | MIT OR Apache-2.0 | image | needed by anthropic, openai |
| `sqlalchemy` | 2.1.5 | MIT | image | the database layer |
| `starlette` | 1.7.0 | BSD-3-Clause | image | needed by fastapi |
| `structlog` | 26.1.0 | MIT OR Apache-2.0 | image | structured logs with content-free fields |
| `truststore` | 0.10.4 | MIT | image | needed by httpcore2, httpx2 |
| `ty` | 0.0.89 | MIT License | development | the type checker |
| `typing-extensions` | 4.16.0 | PSF-2.0 | image | needed by alembic, anthropic, anyio and 17 more |
| `typing-inspection` | 0.4.4 | MIT | image | needed by fastapi, pydantic, pydantic-settings |
| `tzdata` | 2026.5 | Apache-2.0 | image | needed by psycopg |
| `urllib3` | 2.8.0 | MIT | image (tracing) | needed by requests |
| `uvicorn` | 0.54.0 | BSD-3-Clause | image | the web server |
| `uvloop` | 0.23.0 | MIT License | image | needed by uvicorn |
| `vulture` | 2.16 | MIT License | development | finds functions and classes nothing calls (scripts/dead_code.py) |
| `watchfiles` | 1.3.0 | MIT | image | needed by uvicorn |
| `websockets` | 17.2 | BSD-3-Clause | image | needed by uvicorn |
| `wrapt` | 2.5.0 | BSD-2-Clause | image (tracing) | needed by langfuse |

## What the `Dockerfile` installs

| Dependency | Kind | Version | License | Installed | Why it is here |
|---|---|---|---|---|---|
| `bubblewrap` | Debian package | not fixed: Debian's security updates for trixie: this is the sandbox itself, and a fixed version would hold a security fix back; the build prints what was installed | LGPL-2.0-or-later; only ever run as a child process | INSTALL_LATEXML=true | the sandbox LaTeXML and TeX run in |
| `fonts-crosextra-caladea` | Debian package | not fixed: Debian trixie; a font file, with no code that runs | Apache-2.0 | INSTALL_LIBREOFFICE=true | a font with the metrics of Cambria |
| `fonts-crosextra-carlito` | Debian package | not fixed: Debian trixie; a font file, with no code that runs | OFL-1.1 | INSTALL_LIBREOFFICE=true | a font with the metrics of Calibri, so Word files keep their line breaks |
| `fonts-liberation2` | Debian package | not fixed: Debian trixie; a font file, with no code that runs | OFL-1.1 | INSTALL_LIBREOFFICE=true | fonts with the metrics of Arial, Times New Roman and Courier New |
| `latexml` | Debian package | 0.8.8-2 | public domain (a work of NIST); TeX Live's base is LPPL-1.3c and other free licenses; ImageMagick's own license; only ever run as a child process | INSTALL_LATEXML=true | converts uploaded LaTeX and Beamer source to XML and HTML (source/latexml.py); pulls in TeX Live's base, ImageMagick and Perl |
| `libreoffice-impress-nogui` | Debian package | not fixed: Debian's security updates for trixie, as for Writer | MPL-2.0 and others; only ever run as a child process | INSTALL_LIBREOFFICE=true | PowerPoint uploads to a tagged PDF, the same way |
| `libreoffice-writer-nogui` | Debian package | not fixed: Debian's security updates for trixie: LibreOffice reads strangers' files and gets security fixes often, so a fixed version would be out of date within weeks | MPL-2.0 and others; only ever run as a child process | INSTALL_LIBREOFFICE=true | Word uploads to a tagged PDF, headless, in a limited child (web/office_convert.py) |
| `pandoc` | Debian package | 3.1.11.1+ds-2 | GPL-2.0-or-later; only ever run as a child process | always | writes the Word download from the finished HTML (pipeline/export_docx.py) |
| `tini` | Debian package | not fixed: Debian's security updates for trixie; the build prints its version | MIT | always | pid 1 of the container: forwards signals and reaps what a killed sandbox leaves behind |
| `util-linux` | Debian package | not fixed: Debian's security updates for trixie; part of the base system | GPL-2.0-or-later and others; only ever run as a child process | INSTALL_LATEXML=true | prlimit, which sets the limits of a sandboxed child |
| `onnx` | Python package (build stage) | 1.23.2 | Apache-2.0 | INSTALL_MATH_READER=true | checks the optional local math reader's model files at build (math-reader stage); not in the runtime image |
| `onnxruntime` | Python package (build stage) | 1.31.0 | MIT | INSTALL_MATH_READER=true | the same stage; the version the lock pins |
| `ghcr.io/astral-sh/uv` | base image | 0.13.4 @ sha256:9f130421e930 | Apache-2.0 OR MIT | always | the installer of the build stage; not in the runtime image |
| `python` | base image | 3.12-slim-trixie @ sha256:a6e34c598f24 | PSF-2.0 (CPython); Debian's packages under their own free licenses | always | the base image of every stage: Debian trixie (slim) with CPython 3.12 |
