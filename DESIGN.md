---
name: ReadableSTEM
description: Accessible web pages from STEM course PDFs; a plain, technical, reader-first interface that shows the real output.
colors:
  ink: "#16181d"
  ink-muted: "#474c55"
  surface: "#f5f6f8"
  surface-2: "#eef1f5"
  background: "#ffffff"
  accent: "#1c46ad"
  accent-hover: "#16388a"
  accent-tint: "#e8eefb"
  border-control: "#767b85"
  border-subtle: "#d5d9e0"
  focus-outline: "#0b3d91"
  focus-halo: "#ffd33d"
  ok: "#155a36"
  warn: "#7a4100"
  err: "#9b1c1c"
  dark-background: "#0f1115"
  dark-surface: "#171a20"
  dark-ink: "#e9ebef"
  dark-ink-muted: "#b4bac4"
  dark-accent: "#8ab4ff"
typography:
  display:
    fontFamily: "Source Serif 4, Georgia, serif"
    fontSize: "clamp(2.25rem, 1.7rem + 2.4vw, 3.5rem)"
    fontWeight: 600
    lineHeight: 1.1
  headline:
    fontFamily: "Source Serif 4, Georgia, serif"
    fontSize: "1.75rem"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.6
  code:
    fontFamily: "Source Code Pro, ui-monospace, monospace"
    fontSize: "0.9em"
    fontWeight: 400
    lineHeight: 1.4
  math:
    fontFamily: "Latin Modern Math, STIX Two Math, Cambria Math, math"
    fontSize: "1.6rem"
    fontWeight: 400
    lineHeight: 1.3
  hero-math:
    fontFamily: "LM Math Hero, Latin Modern Math, STIX Two Math, math"
    fontSize: "1.5em"
    fontWeight: 400
    lineHeight: 1.3
  label:
    fontFamily: "Source Sans 3, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 700
    lineHeight: 1.4
rounded:
  control: "4px"
  frame: "8px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.background}"
    rounded: "{rounded.control}"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.background}"
    textColor: "{colors.accent}"
    rounded: "{rounded.control}"
    height: "44px"
  card:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.frame}"
    padding: "24px"
---

# Design System: ReadableSTEM

## Overview

A reader-first, technical interface for instructors, accessibility staff and procurement reviewers. It is plain on purpose: one blue accent, humanist text, a serif for headings where people read, generous line length control, and the real converted output shown beside its PDF. References (approved 2026-11-03): Observable Framework docs (prose column, serif display, dark mode from tokens), Stripe Docs (restraint, 4px grid, 4 and 8px radii) and arXiv HTML (the converted page as a paper). GOV.UK is not a visual reference; its focus ring, error summary and forced-colours care are the accessibility floor.

Two modes. Read and Persuade surfaces (landing, compare, help, guides, offices, privacy, terms, limits) use Source Serif 4 600 for h1 and h2 and 18px prose in a 68ch column. Operate surfaces (upload, documents, job page, review split and list, Symbols, account) use Source Sans 3 700 headings, 16px text and scan-first density. The converted student HTML is a separate artefact and is not styled by this system.

## Colors

One accent (`accent`, 8.30:1 on white, 7.68:1 on `surface`). Neutrals are cool slate. Status colours (`ok`, `warn`, `err`) are semantic only, always written out in words, and sit on their tints at 7:1 or more. All text is at least 7:1 in light and dark. `border-control` (4.25:1) is the border for inputs and checkboxes (3:1 non-text rule); `border-subtle` is decorative only. Dark mode follows `prefers-color-scheme` with its own tokens (accent `#8ab4ff`); there is no toggle. Pictures of documents stay on white "paper" (`--paper`) in dark mode and use the light tokens inside.

Links in text are always underlined. Yellow is reserved for focus.

## Typography

Source Sans 3 (400, 700) for text everywhere; Source Serif 4 600 for display, h1 and h2 on Read/Persuade surfaces; Source Code Pro 400 for LaTeX and code; Latin Modern Math only for equations. Self-hosted woff2, latin plus a greek range loaded on demand, about 59 KB on a typical page. Scale: 56 (fluid 36-56) / 44 (fluid 32-44) / 28 / 22 / 18 / 16 / 14. 14px is the floor. Prose line height 1.6; UI 1.4. No tracking except -0.01em at display size.

## Layout

4px grid (`--space-1` to `--space-9`: 4, 8, 12, 16, 24, 32, 48, 64, 96). Page container 75rem; review screen up to 90rem; prose column 68ch; hero h1 20 to 22ch. Section rhythm 64px desktop, 48px mobile. Targets are 44px for primary actions, never under 24px. Below 52rem two-pane layouts stack, PDF first; below 40rem the navigation folds into a Menu disclosure. 200% zoom and 320px width must not scroll sideways.

## Elevation & Depth

Flat. Depth comes from 1px borders and surface tints, not shadows. The only shadow vocabulary is the focus halo and a hairline under the page image in review.

## Shapes

4px on controls, tags, inputs and code; 8px on framed containers (cards, panes, notices); 0 on full-width bands, tables and page images. No pills. Numbered marks on the review page image are discs because they mark a point on a page, not because buttons are round.

## Components

Header (brand, nav, Sign in as a secondary button, Menu disclosure), column footer (Product, Trust, Guides, Source), buttons (primary filled, secondary outlined, danger, small), tags (status in words), notices, form fields with hint and error, card, stat tile (number, label, set and date, a "how measured" link), two-pane before/after, comparison table, FAQ in native details. Buttons live in `controls.css`, shared with the listening player and the showcase. A decision's row of choices sticks to the bottom of the window on wide screens; the listening transport sticks to the top. The focus ring is a 3px dark outline with a 5px yellow halo (yellow outline with a dark gap in dark mode, `Highlight` outline in forced colours) and is not to be changed. All are shown on `/style`.

Motion: none by default; 120ms colour transitions only under `prefers-reduced-motion: no-preference`.

## Do's and Don'ts

- Do show the real PDF and the real converted page, with the numbers' sample sizes.
- Do keep any text at 7:1 or better, 14px or larger.
- Do add a forced-colours rule for anything that relies on a background or a shadow.
- Don't add a second accent, gradients, glass, eyebrows, section numbers or identical icon-card grids.
- Don't put a coloured stripe on one side of a card or notice; use a full border or a top rule.
- Don't use a pill shape, or a radius other than 4 and 8.
- Don't style the converted student HTML from this stylesheet.
