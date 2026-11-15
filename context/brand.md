# Brand assets — BCL Fantasy logo

> **Current look = the paper "scorebook" set** (`mark-paper.svg`, `icon-paper.svg`, `logo-sketch.png`, `wordmark-bcl.png`; see `context/theme.md`;
> generators `tools/gen_paper_mark.js` + `tools/make_brand_pngs.ps1`). The navy tile described in the next few paragraphs is the previous brand; its files
> are still in `Static/brand/` but the web app no longer uses them. The Android section at the bottom is current.

**The mark (previous, navy brand):** a red ball striking three gold stumps while a bail flies off, over a teal crease line, on a deep-navy
tile. Palette = the app's own navy / gold / teal plus cricket-ball red. One definition generates every file, so
they cannot drift apart: `tools/gen_brand.js` (run from the repo root: `node tools/gen_brand.js`).

| File (`Static/brand/`) | Use |
|---|---|
| `icon.svg` | rounded tile — app bar, loading + login screens, browser-tab favicon, web-app icon ("any") |
| `icon-square.svg` | full-bleed square — Apple touch icon + Android maskable icon source |
| `mark.svg` | the mark alone on a transparent background — empty-state illustrations, dark surfaces |
| `logo-lockup.svg` | tile + geometric "BCL" + "FANTASY" — documents, sharing, README |
| `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`, `favicon-32.png` | rasterised by `tools/make_brand_pngs.ps1` (headless Chrome/Edge) |

Wired in: `templates/index.html` (favicon + apple-touch-icon links, loading screen, app bar, login hero),
`routes.py` (`/manifest.json` icons incl. maskable, `/offline` page), `Static/ui.js` (empty-state illustration).

**Wordmark in the app** is live text, not an image: `BCL` (900 weight, white) + `FANTASY` (700, gold, tracked caps) —
classes `.bn-bcl` / `.bn-fan` in `Static/screens.css`. The monoline "BCL" in `logo-lockup.svg` is drawn as paths so the
file does not depend on installed fonts (only the small "FANTASY" line uses a system font stack).

**Regenerating (previous brand):** edit the colours/geometry in `tools/gen_brand.js`, run it, then run the PNG script, then commit.

## Android app (a WebView wrapper in `C:\Users\Sahil\Downloads\ipl_fantasy_app`, outside this repo)

**Current look: v1.3 (2026-11-15), the paper "scorebook" design.** The native parts are generated from, or matched to, the web theme:

| What | Where (this repo) | How it is made |
|---|---|---|
| launcher icon layers, themed (monochrome) icon, the loading splash's pencil line | `Static/brand/android/ic_launcher_foreground.xml`, `ic_launcher_background.xml`, `ic_launcher_monochrome.xml`, `pencil_line.xml` | `node tools/gen_android_paper_icon.js` redraws the web pencil mark (`tools/gen_paper_mark.js`) as Android vectors: one scale + shift is baked into every coordinate, so no group transforms are needed |
| paper grain tile | `Static/brand/android/paper_tex.jpg` (520 px, seamless) | `powershell -ExecutionPolicy Bypass -File tools\make_android_paper_tile.ps1` bakes the `--paper-tex` layers from `paper.css` over the `--paper` colour from `theme.css`, so it cannot drift from the page |
| logo on the loading and error screens | `Static/brand/logo-sketch.png` | copied as `res/drawable-nodpi/logo_sketch.png` |
| colours, themes, layouts, `MainActivity.java` | the Android project | hand-written; `values/colors.xml` uses the same values as `theme.css` |

To refresh the app after changing the art: run the two generators, copy `Static/brand/android/*.xml` into `app/src/main/res/drawable/` and `paper_tex.jpg` into
`res/drawable-nodpi/`, bump `versionCode` + `versionName` in `app/build.gradle.kts`, then `gradlew.bat assembleRelease --no-daemon` (signed with
`keystore/fantasy-release.jks`; keep it: the signer certificate SHA-256 is `47c85fa4...eb169c`, and phones only accept updates signed by the same key).

* **Launcher icon.** An adaptive icon is a 108dp canvas that the launcher crops to a circle, squircle or rounded square. The generator scales the drawing until
  its smallest enclosing circle is 32dp (safe circle 33dp, launcher mask 36dp) and refuses to write if anything drawn would pass the mask. The background layer is cream
  paper with ruled lines, a red margin and a pencil crease; cropping its edges is harmless. Preview under phone masks: `node tools/preview_android_icon.js`.
  The themed icon is one colour: stumps and bail solid, a solid ball with its two seams cut out, and the stump behind the ball cut away (a clip made of the whole canvas
  plus the ball drawn the other way round, so the ball's area has winding 0).
* **Loading and error screens** copy the web loader (`#app-loading`): the same paper tile, the sketch logo at 250dp, tracked FANTASY, a pencil line that draws itself
  (a `ClipDrawable` level animation in `MainActivity.startPencil`), and one status line; the error screen has a butter Retry button with a pencil outline.
* **Themes** are chained by API level because a style in a newer `values-vNN` folder REPLACES the same-named style instead of adding to it: base (light Material parent so
  native pop-ups such as the lists behind a `<select>` are paper-coloured, not grey), v27 adds dark icons on the navigation bar, v29 turns force-dark off, v31 paints the
  system splash paper with the icon's foreground layer.
* **Status and navigation bars follow the page.** `MainActivity.followThemeColor` reads `<meta name="theme-color">` after each page load (paper `#F3EBD8` on the current design,
  navy `#07111F` on the previous one) and colours the bars and their icons to match, so the same APK looks right with either design and the app does not have to be
  installed in a particular order relative to a web deploy. Until the page reports a colour the bars are paper (the theme).
* **Gotchas.** XML comments cannot contain a double hyphen (do not paste CSS token names such as `--paper` into them). Gradle needs about 2 GB of free RAM: stop the emulator
  before building. A freshly booted emulator is overloaded for a couple of minutes (System UI ANRs): wait until it is idle before using it. Windows build: start
  `gradlew.bat` by absolute path (`Start-Process`); `cmd /c gradlew.bat` fails.

**Shipped:** v1.3 (versionCode 4), `C:\Users\Sahil\Downloads\BCLFantasy-1.3.apk`, 261,764 bytes, SHA-256 `4c595c2ddfeb1b9eba3c5267b24f53ef0eade44f9b0be41ad547f50c259c7c7f`.
Verified on an API 34 virtual phone: Android 12+ startup splash, loading screen, offline screen, launcher icon (circle mask), the paper app with matching bars, a native
`<select>` pop-up, phone dark mode (the app stays paper), the in-place update from 1.2, and the old navy site (bars turn navy). Not verified: the themed icon on a real
device (previewed only) and any real phone. Previous releases: v1.2 (2026-11-14, navy tile icon and navy splash) and v1.1.
