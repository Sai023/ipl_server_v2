# Static/mc_hub.js — The Match Centre Renderer

## What it does (business view)

`mc_hub.js` owns the **Match Centre tab** end-to-end — the default
landing view of the SPA. Two pieces:

1. **The hub** — a list of every match grouped by week, each card
   showing fixture / status / **user's points from that match** and
   a season-summary stat bar on top (Total / Matches / Avg / Best).
   Clicking a completed match opens the box-score modal.
2. **The Box Score modal** — a bottom-sheet popup showing the user's
   historical XI for that match, with per-player base/final pts,
   Captain ×2 / VC ×1.5 annotations, the top scorer highlighted with
   a 2px gold border, and a `MATCH TOTAL` footer.

The file is the **third script loaded** in `index.html` (after the
inline script and `ipl_glue.js`). It overrides
`window._buildMatchCentreTab` and exposes two more globals,
`_openMatchModal` / `_closeMatchModal`, that the hub cards reference
in their `onclick` handlers.

## Where it sits in the flow

```
mc_hub.js  (loaded last)
   ├── _injectTabStyles()  ← makes the tab bar horizontally scrollable
   │
   ├── window._buildMatchCentreTab = function(){
   │       if cached return _renderHub(_mcData)
   │       else fetch /api/match-centre + show spinner
   │   }
   │
   └── window._openMatchModal = function(matchId){
           fetch /api/match-details/<id>?user=<name>
           render via _buildBoxScore()
       }
```

`mc_hub.js`'s cache (`_mcData`) is invalidated on every
`ipl:state-updated` event so a fresh scrape immediately refreshes the
hub.

## Inputs / Outputs

- **Inputs:**
  - `GET /api/match-centre?user=<name>` — the hub payload.
  - `GET /api/match-details/<id>?user=<name>` — the modal payload.
  - `window._username`, `window._activeTab`, `window._state` (for the
    re-render hook).
- **Outputs:**
  - HTML strings returned from `_buildMatchCentreTab` (gets inserted
    by `render()` in `index.html`).
  - Modal DOM (created and removed directly via `document.body`).
  - Custom event hooks: listens for `ipl:state-updated` to invalidate
    its cache.

## Key business rules it enforces

### 1. Hub render contract — six pieces per card
Each match card shows:
- **Match number** (`M1`, `M2`, …).
- **Title** (`SRH vs RCB, 1st Match` style).
- **Team tags** — coloured pills using `_avClass(team)` for each side.
- **Meta line** — `date_label · venue` (when present).
- **Result line** (when present, post-match).
- **User points** — the big number on the right, with status pill
  ("Completed" / "Upcoming").

Upcoming matches show `—` for points and are dimmed (`mc-upcoming`
class) — they're non-clickable.

### 2. Box Score — independent integrity check
The modal always computes the **match total client-side** as
`sum(p.final_pts)` and compares to the server-reported `user_pts`. If
they disagree, a ⚠ icon appears next to the total with the server's
value in the tooltip. This is the integrity guard the SKILL.md
"Match Centre Receipts" section refers to.

### 3. Top scorer is computed client-side
`_buildBoxScore` scans the players array for `max(final_pts)` and
draws a 2px gold left-border on that row. Also shown in the
"Top Scorer" stat box at the top of the modal. Falls back to
`d.top_scorer` only if the client-side scan finds zero pts (e.g. a
no-result match).

### 4. "No breakdown yet" graceful state
If the match has a `user_pts > 0` from `user_match_points` (the
seeded history blob) but no `player_match_points` rows yet (scraper
hasn't run), `_buildBoxScore` shows the match-total but renders each
player row with `—` and a small italic note: *"Per-player breakdown
available after the scraper runs for this match."*

### 5. Captain/VC multiplier annotations
For each row, if `base_pts > 0`:
- Captain → `{base} × 2` annotation in gold.
- Vice-Captain → `{base} × 1.5` annotation in teal.
- Neither → no annotation, just the final number.

If `base_pts == 0` but the player was the captain/VC, a tiny `×2` /
`×1.5` annotation is still shown next to the `—` to signal "they had
the multiplier, they just scored nothing".

### 6. Responsive tabs
`_injectTabStyles()` ([lines 46-75](../Static/mc_hub.js:46)) injects
CSS that makes the `.tabs` row horizontally scrollable on narrow
screens — `overflow-x: auto`, `flex-wrap: nowrap`, hidden scrollbars,
iOS momentum scrolling, and a special teal border on the Match Centre
tab so users can find it after scrolling.

### 7. Cache invalidation
`_mcData = null` on every `ipl:state-updated` event ([line 30](../Static/mc_hub.js:30)).
That means a scraper run that updates state guarantees the next visit
to the Match Centre tab re-fetches fresh data, no manual refresh
required.

### 8. Idempotent style injection
`_injectTabStyles()` checks for `#ipl-tab-styles` before adding the
`<style>` element. Safe to call multiple times.

## Called by / Calls into

- **Called by:**
  - `index.html`'s `render()` function — calls
    `_buildMatchCentreTab()` when `_activeTab === "match-centre"`
    ([index.html:759](../templates/index.html:759)).
  - Hub cards' inline `onclick` calls `_openMatchModal('<match_id>')`.
- **Calls into:**
  - `IplApi.getMatchCentre(name)` → `/api/match-centre?user=<n>`.
  - `IplApi.getMatchDetails(id, name)` → `/api/match-details/<id>?user=<n>`.
  - Several inline-script globals as fallbacks: `esc`, `escAttr`,
    `_avClass`, `_initials`. The `_esc` / `_escAttr` / `_avCls` /
    `_ini` helpers at the top of the file ([lines 84-102](../Static/mc_hub.js:84))
    are safe-fallback wrappers in case the inline script renames them.

## Supports which user capabilities

From [user_capabilities.md](user_capabilities.md):

- **§3.1 Match Centre hub** — `_buildMatchCentreTab` + `_renderHub`.
- **§3.2 Box Score modal** — `_openMatchModal` + `_buildBoxScore`.
- **§3 "Default tab"** — first load lands here because
  `index.html:21` sets `_activeTab="match-centre"`.

## Dead Code Audit

| Item | Lines | Verdict |
|------|-------|---------|
| `_ROLE_COLOURS`, `_rolePill` | 105–120 | **Live.** Used in box score rows. |
| `_statBox`, `_renderHub`, `_buildBoxScore` | 123–322 | **Live.** Renderers. |
| `_openMatchModal`, `_closeMatchModal` | 325–366 | **Live.** Hub cards reference them in `onclick`. |
| `_buildMatchCentreTab` override | 369–397 | **Live.** Final override of the global from index.html. |
| `_injectTabStyles` | 46–76 | **Live.** Fires at DOMContentLoaded. |
| Stale "9 tabs" comment in `_injectTabStyles` docstring | 7, 40–44 | **STALE.** The CSS comment says *"all 9 tabs remain accessible without wrapping or clipping"* but there are only **6** tabs. Tracked as **S5** (the SKILL.md version of the same drift). Worth syncing this comment too. |
| Safe-fallback helpers `_esc, _escAttr, _avCls, _ini` | 84–102 | **Live.** Defensive shims — if `index.html` ever renames a helper, these provide a fallback. Negligible cost; keep. |

**No dead code in `mc_hub.js`.** The file is the youngest in the
project (Phase 9.5) and was built knowing the dead-tab pattern.

## Open Questions

1. **The "9 tabs" comment** ([line 7](../Static/mc_hub.js:7) and
   [line 40-44](../Static/mc_hub.js:40)) is stale. One-line fix.
2. **The modal is always a bottom-sheet.** Looks great on mobile,
   wastes space on desktop. Worth considering a centred modal at
   `>768px` viewport via a media query — the CSS injection function
   is the right place to add it.
3. **`_renderHub` shows `user_match_pts === 0` for completed matches
   with a "zero" colour class** ([line 191](../Static/mc_hub.js:191)).
   Looks correct for a player who simply scored 0 — but identical to
   a player whose week wasn't scraped yet. The "—" treatment used
   in the modal would communicate "no data" more clearly.
4. **`_buildBoxScore` is 120 lines of HTML-string concatenation.**
   Hard to read. Worth refactoring into smaller `_buildPlayerRow`,
   `_buildScoreBox`, `_buildTotalFooter` helpers — the renderers
   currently live inside one large function.
5. **The "integrity check" tooltip only shows on mismatch** and uses
   a `⚠` glyph with the server value. Operators have no way to know
   *which* player row produced the divergence. Worth adding a
   per-row mismatch indicator (e.g. when `pmp_map[pid]` is missing
   but the player is in the XI).
6. **No loading shimmer in the hub.** The current state is a single
   "⏳ Loading Match Centre…" line; for a hub with 74 cards, a
   skeleton grid would feel snappier. Cosmetic only.
