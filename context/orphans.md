# Orphan Files

> Files in the project tree that no other file references and no
> route serves. Tracked here so cleanup can delete them with
> confidence. Both are tracked as **O1** and **O2** in the
> [dead_code_register.md](dead_code_register.md).

---

## O1 — Root `ipl_glue.js` (project root, NOT `Static/`)

- **Path:** `ipl_server_v2/ipl_glue.js`
- **Size:** 875 lines.
- **Version in header:** `v7.7`.
- **Compared to the live copy:** [Static/ipl_glue.js](../Static/ipl_glue.js) is **v7.8** at 775 lines and is what
  `index.html:822` loads (`<script src="/static/ipl_glue.js?v=76">`).

### Why it's an orphan
- `routes.py` serves `/static/<path>` from `STATIC_DIR =
  base.BASE_DIR / "static"` ([base.py:41](../base.py:41)). The
  project root is **not** served. Files placed in the root cannot be
  reached by any URL.
- `index.html` references only `/static/ipl_glue.js`, never
  `/ipl_glue.js`. No other file imports or `eval`s this content.

### What the v7.7 file contains that v7.8 doesn't
- **`_patchXiGrid()`** — a wrapper around `_buildXiGrid` that stamped
  `data-pid` attributes onto cards and injected `season_pts` badges
  at render time. This was the Phase 8 fix referenced in
  [README.md:218](../README.md:218).
- The v7.8 approach in `Static/ipl_glue.js` is different: badges are
  rendered **inline** by `_buildNwSquad` in `index.html` (line 343,
  the `<div class="nw-player-pts">` cell). The `_patchXiGrid`
  workaround is no longer needed in v7.8.
- The README's troubleshooting table still describes the v7.7 fix —
  another doc drift to clean up.

### Recommendation
**Delete.** Confirmed:

- Not served by Flask.
- Not imported by anything.
- Not referenced by README except as a historical patch description
  whose mechanism is superseded.

Tracked as **O1** in the register; status: `open`.

---

## O2 — `A _ Sticky budget _ table picker.html`

- **Path:** `ipl_server_v2/A _ Sticky budget _ table picker.html`
- **Size:** ~12 KB.

### Why it's an orphan
- The filename pattern (spaces, underscores, prefix `A _`) suggests a
  copy-paste from a prototype or design tool — possibly a
  ChatGPT/Claude artifact saved to disk.
- `index.html` is the only HTML the Flask server serves (via the
  `/` route, the 404 handler, and the static folder). This file is
  in neither location.
- No `import`, `<script src>`, or `<link href>` in any file
  references it.
- The content is a self-contained HTML+CSS+JS prototype for what
  looks like a **sticky-budget player picker** — likely an early
  experiment that was superseded by the live picker in
  `index.html`'s `_buildPicker`.

### Recommendation
**Delete.** The file is functionally a discarded prototype.

Tracked as **O2** in the register; status: `open`.

---

## Open Questions

1. **How did these get into the repo?** Worth a brief look at the git
   history — if someone repeatedly drops drafts into the project
   root, a `.gitignore` rule (`/A *.html`, etc.) could prevent the
   next one.
2. **Are there other orphans?** A full sweep of `*.html`, `*.js`, and
   `*.py` in the project root vs `Static/` and `templates/` was done
   for Phase 0; both files above are the only matches. Worth
   re-running this sweep at the end of the cleanup pass to confirm
   nothing else slipped in.
