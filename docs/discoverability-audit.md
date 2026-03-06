# Discoverability Audit

This document reviews KnightMind from the perspective of discoverability and first-time user comprehension.

## Assumptions

- Canonical public web URL is `https://knightmind.dev`.
- Main repo URL is `https://github.com/laulopezreal/KnightMind`.

If your production URLs differ, update `apps/web/index.html`, `apps/web/public/sitemap.xml`, and this document.

Canonical URL source: `.env.example` production domain guidance (`https://knightmind.dev`).

## 1) README improvements

### Implemented

- Added a clear value proposition and feature summary near the top of the root README.
- Added a concise quickstart with expected local URLs.
- Added dedicated sections for onboarding flow, deployment links, and contribution/discoverability guidance.
- Added a short "Findability" section with target search phrases and where they are represented.

### Next recommended

- Add one architecture image (system overview) and one product screenshot to the README.
- Add badges (build status, last release, license) once CI and release pipeline are stable.

## 2) Documentation structure

### Implemented

- Added `docs/README.md` as a docs index with role-based navigation.
- Added `docs/onboarding.md` for first-time setup and first successful workflow.

### Next recommended

- Add dedicated pages for:
  - `docs/deployment.md`
  - `docs/troubleshooting.md`
  - `docs/faq.md`
- Link all docs pages from root README and from `docs/README.md`.

## 3) Website landing pages

### Implemented

- Added metadata support for social cards and search snippets in `apps/web/index.html`.
- Added machine-readable JSON-LD schema (`SoftwareApplication`) in `apps/web/index.html`.

### Next recommended

- Add an explicit public marketing route/page (separate from logged-in app flow).
- Include a static "How it works" section with product benefits and screenshots.

## 4) SEO improvements

### Implemented

- Added `robots.txt` and `sitemap.xml` in `apps/web/public/`.
- Added canonical URL, meta description, keywords, OpenGraph, and Twitter card tags.

### Next recommended

- Generate sitemap automatically at build time when public routes expand.
- Add per-route metadata once server-side rendering or prerendering is introduced.

## 5) Content structure and GitHub discoverability

### Implemented

- README now includes:
  - Audience/problem statement
  - Feature-oriented section headings
  - "How to contribute" path to reduce newcomer confusion
  - Search-intent terms integrated naturally
- Added a docs index and onboarding guide so users can self-serve quickly.

### Next recommended

- Add GitHub topics in repository settings (`chess`, `fastapi`, `react`, `stockfish`, `spaced-repetition`, `analytics`).
- Add issue templates (`bug`, `feature`, `docs`) and a PR template emphasizing user impact.
- Add `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` for trust/discoverability and ecosystem clarity.
