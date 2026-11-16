# Markwright — visual identity kit

**Brand premise:** evidence, not verdict. The identity is for an open-source operations/control plane for LLM text watermarking—not for a new watermark algorithm or an AI-written-text oracle.

## Recommended identity

**TRACE** is the primary identity. Its geometric M reads as a watermark signature, while the small square captures the idea of one configuration-bound attestation. It stays legible as a 16-pixel favicon and can be reproduced with just one color. Two alternate directions are also included: **CUSTODY** (a bounded, connected key lineage) and **SIGNAL** (the measured output of a watermark detector).

## Logo rules

- Minimum icon size: 16 × 16 px (32 px preferred). Keep 15% clear space on all sides.
- Prefer the gradient symbol on Deep Navy (#08152B); use the dark-ink/light alternate for white backgrounds.
- Use the horizontal wordmark in top navigation, the square icon for favicons, and the large-banner lockup for hero and OpenGraph.
- Do not add checkmarks, a traffic-light red/green status, or a generic AI fingerprint to the mark; the mark communicates provenance, not absolute truth.

## Theming and color

| Color | Hex | Role |
|---|---|---|
| Deep Navy | `#08152B` | Website backgrounds |
| Surface | `#0D2240` | Raised panels |
| Signal Blue | `#2563EB` | Primary interactions |
| Cobalt | `#3B82F6` | Links and emphasis |
| Cyan | `#65D6F8` | Brand highlight |
| Ice | `#A5E8FF` | Secondary highlight |
| Frost | `#EAF5FF` | On-dark typography |

The `04_themes/` directory has ready-to-use CSS variables and a machine-readable JSON palette. Themes: **Midnight** (default operator UI), **Frost** (docs/reports), **Ocean** (editorial campaigns).

## Product integrity rules

1. The product is an infrastructure and provenance tool; do not market it as definitive AI-text detection.
2. Evidence levels use blue tone/intensity, never a red-green success/failure verdict.
3. "No evidence" and "insufficient evidence" have the same neutral treatment.
4. Statistical caveats, p-value confidence interval and false-positive targets must remain visible in product interfaces.
5. Avoid implying that a signed watermark configuration proves authorship.

## Included files

- `01_logos/` — dark/light wordmarks, stacked lockups, transparent symbols, SVG + PNG.
- `02_favicons/` — ICO, vector favicon, 16/32/48/64/128/180/192/256/512 PNG, web manifest.
- `03_banners/` — GitHub social preview, OpenGraph, README banner, LinkedIn, light/dark website heroes.
- `04_themes/` — CSS tokens, JSON tokens, integration notes.
- `05_concepts/` — all three logo directions, dark/light/flat.
- `06_mockups/` — example operator-dashboard visual in light and dark modes (conceptual, not an actual screenshot).
- `00_preview/` — presentation board and logo-comparison sheet.
- `index.html` — local interactive brand preview. Open in your browser.

**Usage:** Start with `01_logos/wordmark-horizontal-dark.svg`, `02_favicons/favicon.ico`, and `03_banners/github-social.png`. The source SVGs can be edited in any vector editor.
