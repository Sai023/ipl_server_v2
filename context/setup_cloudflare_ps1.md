# setup_cloudflare.ps1 — The One-Time Tunnel Installer

## What it does (business view)

`setup_cloudflare.ps1` is the **one-shot installer** that pulls down
`cloudflared.exe` so the operator can run

```powershell
python server.py --tunnel cloudflare
```

and get a free public HTTPS URL (`https://<random>.trycloudflare.com`)
to share with their league friends. **No Cloudflare account, no
sign-up, no expiry.**

The script is **idempotent** — running it twice does nothing the
second time. It auto-detects ARM64 vs amd64 Windows, downloads the
right binary from the official Cloudflare GitHub releases page, and
saves it to the project root.

This is the only file in the project that **modifies the user's
environment** (adds an executable to the project folder). Everything
else is in-process Python or static HTML/JS.

## Where it sits in the flow

```
Operator (first-time setup, runs once)
   │
   ▼
.\setup_cloudflare.ps1
   ├── 1. Already on PATH?           → exit 0, ready to go.
   ├── 2. Already in project folder? → exit 0, ready to go.
   ├── 3. Detect $env:PROCESSOR_ARCHITECTURE
   │      ARM64 → cloudflared-windows-arm64.exe
   │      else  → cloudflared-windows-amd64.exe
   ├── 4. Invoke-WebRequest to download the binary into the project root
   ├── 5. Run `cloudflared --version` to verify
   └── 6. Print "ready" message
                  │
                  ▼
   Operator runs: python server.py --tunnel cloudflare
                  │
                  ▼
   server.py:try_cloudflare()
       ├── shutil.which("cloudflared")       ← global PATH check
       └── fallback: BASE_DIR / "cloudflared.exe"   ← what this script populates
```

## Inputs / Outputs

- **Inputs:**
  - `$env:PROCESSOR_ARCHITECTURE` — read at runtime.
  - Internet connection (to GitHub releases).
- **Outputs:**
  - `cloudflared.exe` placed in the project root
    (`$PSScriptRoot / cloudflared.exe`).
  - Coloured console messages.
  - Exit code 0 (success or already-installed) or 1 (download failed).

## Key business rules it enforces

### 1. Idempotent — never overwrites
- Step 1 checks `Get-Command cloudflared` — if it's on the system
  PATH, the script exits immediately without modifying anything.
- Step 2 checks `Test-Path $localExe` — if `cloudflared.exe` is
  already in the project folder, exits early.
- Only step 3+ executes on a fresh install.

### 2. Architecture auto-detection
Read directly from `$env:PROCESSOR_ARCHITECTURE`:
- `ARM64` → `cloudflared-windows-arm64.exe`
- anything else → `cloudflared-windows-amd64.exe`

Older Windows machines never report `ARM64`; new Surface devices do.

### 3. Source URL is the **latest** release
`https://github.com/cloudflare/cloudflared/releases/latest/download/<asset>`
— `latest` is a GitHub redirect, so each fresh run pulls the newest
release without the script needing to know version numbers.

### 4. Failure mode is helpful
If the download fails (firewall, GitHub down, etc.), the script
prints the manual fallback:

> "Please download manually: <url>. Save it as 'cloudflared.exe' in
> the project folder, then run: python server.py --tunnel cloudflare"

Not a bare stack trace.

### 5. Verification is **best-effort**
Step 5 runs `& $localExe --version`. If the call fails (rare —
usually only if the binary is corrupt), the script prints
`(Could not read version — file may still be OK)` and continues.
The verification is not load-bearing.

### 6. No admin rights required, but recommended
The script's docstring tells the operator to run as Admin. In
practice, the script needs Admin only if it ever wrote to a
system-protected location — it doesn't. Writes go to the project
folder, which is the user's. Admin is recommended for the broader
Cloudflare setup workflow (e.g. firewall rules) but not required by
the script itself.

## Called by / Calls into

- **Called by:** an operator running it manually in PowerShell.
- **Calls into:**
  - GitHub: `https://github.com/cloudflare/cloudflared/releases/latest/...`.
  - PowerShell cmdlets: `Get-Command`, `Test-Path`,
    `Invoke-WebRequest`, `Join-Path`.

## Supports which user capabilities

Not a user capability — this is **deployment plumbing**. Indirectly
supports every capability when the league is accessed from outside
the operator's LAN:

- **§9.1 Refresh from any device** — requires a public URL.
- **§10.2 Auto-rollover** — works locally without a tunnel, but if
  family/friends rely on the league being reachable, the tunnel is
  what makes it usable.

## Dead Code Audit

The script is 99 lines and has no dead code:

- All 6 numbered steps are reachable.
- Both error paths (PATH skip, project-folder skip) exit cleanly.
- The architecture detection covers both real cases.
- The `try/catch` around `Invoke-WebRequest` is exercised whenever
  the network fails.

**No dead code.**

## Open Questions

1. **No checksum verification.** The script downloads the binary and
   runs it. A compromised mirror (or man-in-the-middle on an HTTPS
   downgrade) could substitute a malicious `cloudflared.exe`. Worth
   either pinning a known-good SHA256 or pulling the official
   checksums file from the same release.
2. **The `Get-Command cloudflared` path is for the Cloudflare CLI,
   which `cloudflared.exe` is.** If the operator installs the
   `cloudflared` MSI (which puts it on PATH), the script's early-exit
   works correctly. But — `server.py:try_cloudflare()` *prefers*
   `shutil.which("cloudflared")` over the local copy, so the two
   stay aligned.
3. **No 32-bit support.** If anyone is still running 32-bit Windows
   (vanishingly rare on modern hardware), the script downloads the
   amd64 binary, which won't run. The `try/catch` around
   `--version` catches it; the manual-fallback message is fine, but
   doesn't tell the user *why*.
4. **The script doesn't add cloudflared to PATH.** It only puts the
   binary in the project folder. That's fine for `server.py --tunnel
   cloudflare` (which falls back to the local file), but an operator
   running `cloudflared tunnel ...` directly from another folder
   would get "not found". Worth mentioning in the README.
5. **No uninstall path.** Worth documenting `Remove-Item
   .\cloudflared.exe` if the operator ever wants to revert.
6. **Distribution philosophy.** Today the script *and* a vendored
   `cloudflared.exe` both live in the repo (see
   [cloudflared_exe.md](cloudflared_exe.md)). The script's purpose is
   "download cloudflared.exe if it's not already here" — which is
   redundant if the binary is committed. Worth picking one: either
   stop committing the binary and rely on the script, or commit the
   binary and only keep the script as a recovery tool.
