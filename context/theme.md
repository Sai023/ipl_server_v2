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

## Choosing a control (the kit in `paper.css` + `ui.js`)

Pick the control by the job, not by habit. Everything below is accessible by keyboard, has a visible focus ring and a 44 px target on phones.

| the job | the control | how |
|---|---|---|
| one choice from 2 to 5 short options (role, sort, filter) | **segmented control**, a radio group; arrow keys move the choice | `Ui.seg({id,label,value,options:[{v,l,n,cls}],onSelect:"fn"})`, `Ui.segSet(el,v)` |
| one choice from many options (team, week) | **dropdown** (`<select>`, one custom arrow, 44 px) | plain `<select>` |
| a value on a scale (maximum price) | **range slider** with a live value and a shortcut ("Fits my budget") | `<input class="range">`; `Ui.rangeFill` paints the track |
| one role per person (regular / captain / vice-captain) | **radio rows in a bottom sheet** with a plain-language description | `Ui.sheet({title,subtitle,items:[{radio:true,label,desc,badge,checked,onClick}]})` |
| a short secret (4-digit passcode) | **boxes drawn over one real input** (paste, autofill and the numeric keypad keep working), show/hide, shake on a wrong code | `Ui.pin({id,label})`, `Ui.pinReset(input,true)` |
| moving through weeks | **stepper**: previous, dropdown, next | `.stepper` + `.stepper-btn` + `select.stepper-sel` |
| a reversible but easy-to-misfire action (remove or swap a player) | do it, then offer **Undo** in the toast | `Ui.toast(msg,{action:{label:"Undo",onClick}})` |
| a labelled input with help or an error | **field**: label, control, one line of hint (`.hint.ok / .warn / .err`) | `.field` |
| what is left to do before saving | **one status line** (never a tooltip: phones have no hover) | `.nw-hint.todo / .bad / .good` |

Filters that narrow a list update it **in place** (no page rebuild), so typing, slider drags and focus are never interrupted, and active filters show as removable chips with a live result count.

Touch targets: buttons are 44 px on a phone (40 px for the compact kind), icon buttons 44 px, list-row buttons 40 px. Use the in-page fit sweep idea (overflow, clipped text, targets under 40 px) after adding a screen.

Gotcha: never `rotate()` or `skew()` a box whose height is unbounded (a card with a 74-row list, the player picker). Its far corners swing sideways by about height x angle, so a 0.16 degree tilt on a 12,000 px card moved the outline 17 px and made the page scroll sideways. Tilt only small, fixed-size decorations (tape, stickers); the card outline gets its hand-drawn look from uneven corner radii and insets instead. When a fit check looks clean, still compare `documentElement.scrollWidth` with `clientWidth` on the longest screen once its data has loaded.
