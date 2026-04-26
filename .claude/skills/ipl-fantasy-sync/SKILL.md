---
name: ipl-fantasy-sync
description: "Senior System Architect skill for IPL Fantasy 2026 (v2.0-stable). Layered architecture: config.py → logic/ → db_manager.py (DAO) → tasks.py / scraper.py → server.py → routes.py. Zero logic duplication. One-file-at-a-time pushes."
---

# IPL Fantasy 2026 — Quick Reference  (`Sai023/ipl_server_v2` · `main`)

> **Full canonical skill:** `SKILL.md` (root) — 28 KB, all sections including Lessons Learned, dependency graph, audit traces.

---

## Architecture (v2.0-stable)

```
config.py  ── single source: DB_PATH, DEADLINE_HOUR, APP_VERSION, VERSION_MAP
    └── logic/scoring_engine.py  v1.1.0  calc_pts, apply_multiplier, debug_calc_pts
    └── logic/rollover_engine.py v1.0.0  last_monday_deadline, already_rolled, pick_active_team
    └── logic/fuzzy_match.py    v1.0.0  _norm, _build_player_index, _fuzzy_match, _fuzzy_fielder
        └── db_manager.py  v5.9   pure DAO (SELECT/INSERT/UPDATE only, no IPL rules)
            └── tasks.py   v1.0.0  start_bg_scrape() — daemon thread
            └── scraper.py v10.10  run_full_scrape(db) export
            └── init_db.py v1.0.0  run_all_sync(db)
                └── routes.py  v1.0.0  Blueprint — all 24 @app.route handlers
                    └── server.py v13.0  Flask init, middleware, tunnel, registers blueprint
```

**Import hierarchy rule:** Never import db_manager/server/tasks/scraper FROM logic/. Never import server FROM routes (use Blueprint injection). config.py imports stdlib only.

---

## File Versions

| File | Ver | Role |
|------|-----|------|
| `config.py` | 1.0.0 | Constants + VERSION_MAP |
| `logic/scoring_engine.py` | 1.1.0 | `calc_pts`, `debug_calc_pts`, `CAP_MULT=2.0`, `VC_MULT=1.5` |
| `logic/rollover_engine.py` | 1.0.0 | Monday 14:00 UTC deadline logic |
| `logic/fuzzy_match.py` | 1.0.0 | Player name resolution |
| `db_manager.py` | 5.9 | Pure DAO |
| `routes.py` | 1.0.0 | All API route handlers (Blueprint) |
| `server.py` | 13.0 | Flask init + middleware + blueprint registration |
| `tasks.py` | 1.0.0 | Daemon thread orchestration |
| `scraper.py` | 10.10 | Cricbuzz ingestion |
| `ipl_glue.js` | 7.5 | Version handshake on page load |

---

## Key Operational Procedures

**Verify version first:**
```bash
curl http://localhost:5000/api/version
```

**Post-restart:**
```powershell
git pull && python server.py --tunnel cloudflare
python scraper.py   # re-scrapes all completed matches
```

**Moe & Sai audit:**
```bash
curl http://localhost:5000/api/audit-scores/Sai
curl http://localhost:5000/api/audit-scores/Moe
```

**Scoring trace:**
```python
from logic.scoring_engine import debug_calc_pts
t = debug_calc_pts(score_dict, player_id="k04", cap_id="k04", vc_id="s05")
print(t["steps"], t["base_pts"], t["multiplier"], t["final_pts"])
```

**Add new logic rule:** Add to `logic/`, bump engine version in `config.py`, update VERSION_MAP, push config first then engine then consumer.

---

## Critical Constants

| Constant | Value | Notes |
|----------|-------|-------|
| `DEADLINE_HOUR` | 14 | **14:00 UTC = 16:00 SAST** |
| `CAP_MULT` | 2.0 | In `logic/scoring_engine.py` |
| `VC_MULT` | 1.5 | In `logic/scoring_engine.py` |
| `BUDGET_TOTAL` | 100.0 CR | Server/Routes local constant |
| `XI_SIZE` | 11 | Server/Routes local constant |
| `MAX_WEEKS` | 8 | Server/Routes local constant |

---

## Scoring Rules (summary)

`played +4` | `runs +1 each` | `fours +1` | `sixes +2` | `30+ +4` | `50+ +8` | `100+ +16` | `duck -2`
SR (>=10 balls): `>125 +6` | `>=110 +4` | `>=100 +2` | `<70 -2` | `<60 -4`
`wickets *25` | `lbw/bowled +8` | `maidens +12` | `2wkt +4` | `3wkt +4` | `4wkt +8` | `5wkt +8`
Eco (>=2 overs): `<5 +6` | `<6 +4` | `<7 +2` | `>12 -6` | `>11 -4` | `>10 -2`
`catch +8` | `3+ catches +4` | `stumping +12` | `direct RO +12` | `assist +6`

---

## W1–W4 History Seed (current: `2026.v8.w3w4-defined`)

```
Sai W1: k04 k19 s04 s05 s07 r01 r03 r11 m04 m07 m12  cap=k04 vc=s05
Sai W2: d22 p10 c12 c02 g03 rr14 rr11 l11 c09 p03 s04  cap=c09 vc=rr11
Moe W1: k04 m04 m07 m17 r02 r03 r12 s01 s04 k07 r16   cap=r03 vc=s04
Moe W2: m03 r05 k09 r16 p07 c11 rr04 s05 m11 s04 l01  cap=l01 vc=s04
W3/W4: same XI as W2 for each user (own variables — never alias)
```

**To add W5:** define `_SAI_W5_TEAM` + `_MOE_W5_TEAM` (own literals), add to `_HISTORY_SEED`, bump `_SEED_VERSION` in `init_db.py`.
