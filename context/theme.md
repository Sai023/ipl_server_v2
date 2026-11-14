# Theme: "the scorebook"

The app looks like a page from a cricket scorebook: cream paper with a fine grain, graphite pencil outlines, pastel crayon
colour, handwriting for titles and big numbers. It replaces the earlier dark navy look.

## Files (load order matters)

| file | role |
|---|---|
| `Static/theme.css` | **Tokens only**: palette, type, shape, depth. The single place to change how the app looks. |
| `Static/style.css`, `ui.css`, `screens.css` | Structure and layout (the original rules). Every colour in them is a token, never a literal. |
| `Static/paper.css` | The design layer: paper texture, hand-drawn double outlines, handwriting, tape and sticky notes, scorebook tables, the field plan, brand lockup, loader. Loaded last. |
| `Static/brand/*` | `logo-sketch.png` (transparent sketch logo), `wordmark-bcl.png`, `mark-paper.svg` (compact mark), `icon-paper.svg` (paper tile) and the PNG icons made from it. |

Fonts come from Google Fonts (`Caveat` for handwriting, `Nunito` for text), with system fallbacks (`--font`, `--font-hand`).

## Colour tokens

* Surfaces: `--paper` (page), `--sheet` (cards), `--sheet-2` (raised: dialogs, toasts, menus), `--sheet-3` (inset / hover), `--field` (inputs).
* Ink: `--text`, `--text-strong`, `--muted`, `--dim`, `--on-fill` (text on a pastel fill).
* Neutral overlays: `--wash` is the base; `--tint-1..3` (backgrounds), `--hair` (ruled-paper blue dividers), `--line`, `--line-strong`, `--well`, `--hi`.
* Seven crayon families, each with four tokens: `--X-fill` (solid pastel), `--X-ink` (text and icons), `--X-wash` (base for translucent tints), `--X-line` (outlines).
  Families: `gold` (butter), `teal` (mint), `danger` (coral), `success` (sage), `info` (sky), `violet` (lavender), `orange` (peach).
* Translucent tints are written `color-mix(in srgb, var(--X-wash) NN%, transparent)`, so retuning a wash retunes every tint.
* Pencil: `--pencil`, `--border`, `--border-strong`; hand-drawn corner radii `--r-hand`, `--r-hand-sm`.

Rule of thumb when writing CSS: text uses `--X-ink`, a solid badge or button uses `--X-fill` with `--on-fill` text, a soft background uses a
`color-mix` of `--X-wash`, a border uses `--X-line`. Never write a hex or `rgba()` colour in a component.

## Keeping it readable

`node tools/theme_contrast.js` reads `theme.css` and checks every ink colour against the surfaces and tints it sits on (WCAG AA, 4.5:1). Run it after
any palette change. It exits 1 on a failure.

## Role and team colours

Role badges everywhere: WK mint, BAT butter, AR lavender, BOWL coral. Team stamps (`.av-*`) take each franchise's colour from `--tcr` (an `r,g,b` triplet)
as a 30% crayon fill with a darker ink of the same hue.

## Brand assets

* `node tools/gen_paper_mark.js` regenerates `mark-paper.svg` and `icon-paper.svg` (seeded, deterministic); `tools/make_brand_pngs.ps1` rasterises the PNG icons.
* The sketch logo and the brush "BCL" wordmark are produced outside the repo (a raster illustration edited with scripts); only the exported PNGs live here.
