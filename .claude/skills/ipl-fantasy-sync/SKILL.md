---
name: ipl-fantasy-sync
description: "IPL 2026 Fantasy League — self-hosted Flask app with SQLite, Cricbuzz scraper, daily APScheduler discovery, and a public Cloudflare tunnel. The authoritative documentation lives in the context/ folder, not here. Always read context/ before making changes."
---

# IPL Fantasy 2026 — Project Skill

The architecture, dead-code state, and design decisions for this project are
documented in **`context/`** at the repository root. Read those first — they
are kept in sync with the code, this file is intentionally thin.

## Start here

1. **[context/README.md](../../../context/README.md)** — index, glossary,
   how to read the rest.
2. **[context/user_capabilities.md](../../../context/user_capabilities.md)** —
   what the user can actually do in the system, line-anchored.
3. **[context/docs_audit.md](../../../context/docs_audit.md)** — every
   drift between the in-repo docs (README/old SKILL.md) and reality.
4. **[context/dead_code_register.md](../../../context/dead_code_register.md)** —
   the active workbook for cleanup.

## Per-file context

Each source file has its own `context/<file>.md` document — same template
across all of them (business purpose, flow, inputs/outputs, business rules,
dependencies, dead-code audit, open questions).

## Why this file is short

A previous version of this skill ran to ~600 lines across two SKILL.md
files and was severely out of date — it advertised dead endpoints, claimed
"9 tabs" when there are 6, listed wrong version numbers, and described a
deprecated badge-injection patch. Anyone editing the project from that
skill would have built on false foundations. The fix was to delete the
stale content and point at the canonical docs instead.
