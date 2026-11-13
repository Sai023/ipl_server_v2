# Brand assets — BCL Fantasy logo

**The mark:** a red ball striking three gold stumps while a bail flies off, over a teal crease line, on a deep-navy
tile. Palette = the app's own navy / gold / teal plus cricket-ball red. One definition generates every file, so
they cannot drift apart: `tools/gen_brand.js` (run from the repo root: `node tools/gen_brand.js`).

| File (`Static/brand/`) | Use |
|---|---|
| `icon.svg` | rounded tile — app bar, loading + login screens, browser-tab favicon, web-app icon ("any") |
| `icon-square.svg` | full-bleed square — Apple touch icon + Android maskable icon source |
| `mark.svg` | the mark alone on a transparent background — empty-state illustrations, dark surfaces |
| `logo-lockup.svg` | tile + geometric "BCL" + "FANTASY" — documents, sharing, README |
| `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`, `favicon-32.png` | rasterised by `tools/make_brand_pngs.ps1` (headless Chrome/Edge) |
| `android/ic_launcher_foreground.xml`, `android/ic_launcher_background.xml` | drop-in vector drawables for the Android WebView app (`ipl_fantasy_app/app/src/main/res/drawable/`) — NOT yet copied there |

Wired in: `templates/index.html` (favicon + apple-touch-icon links, loading screen, app bar, login hero),
`routes.py` (`/manifest.json` icons incl. maskable, `/offline` page), `Static/ui.js` (empty-state illustration).

**Wordmark in the app** is live text, not an image: `BCL` (900 weight, white) + `FANTASY` (700, gold, tracked caps) —
classes `.bn-bcl` / `.bn-fan` in `Static/screens.css`. The monoline "BCL" in `logo-lockup.svg` is drawn as paths so the
file does not depend on installed fonts (only the small "FANTASY" line uses a system font stack).

**Regenerating:** edit the colours/geometry in `tools/gen_brand.js`, run it, then run the PNG script, then commit.
To update the Android app icon: copy `Static/brand/android/*.xml` over the two drawables of the same name and rebuild the APK.
