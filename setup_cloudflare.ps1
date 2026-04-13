<#
.SYNOPSIS
    One-time setup script — downloads cloudflared.exe for a free, persistent
    Cloudflare Tunnel so your IPL Fantasy friends can access the site anywhere.

.USAGE
    Right-click PowerShell → Run as Administrator, then:
        .\setup_cloudflare.ps1

    After it finishes, start the server with:
        python server.py --tunnel cloudflare

    You'll get a stable HTTPS URL like:
        https://some-name.trycloudflare.com
    Share that link with your friends!
#>

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  IPL Fantasy 2026 — Cloudflare Tunnel Setup" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Check if cloudflared is already on PATH ──────────────────────────────
$existing = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "OK  cloudflared already on PATH: $($existing.Source)" -ForegroundColor Green
    Write-Host ""
    Write-Host "You're ready! Start the server with:" -ForegroundColor White
    Write-Host "    python server.py --tunnel cloudflare" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# ── 2. Check if cloudflared.exe is already in the project folder ─────────────
$localExe = Join-Path $PSScriptRoot "cloudflared.exe"
if (Test-Path $localExe) {
    Write-Host "OK  cloudflared.exe already exists in project folder." -ForegroundColor Green
    Write-Host ""
    Write-Host "You're ready! Start the server with:" -ForegroundColor White
    Write-Host "    python server.py --tunnel cloudflare" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# ── 3. Detect architecture ───────────────────────────────────────────────────
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -eq "ARM64") {
    $assetName = "cloudflared-windows-arm64.exe"
} else {
    $assetName = "cloudflared-windows-amd64.exe"
}

$downloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/$assetName"
Write-Host "Detected arch: $arch" -ForegroundColor Gray
Write-Host "Downloading:   $downloadUrl" -ForegroundColor Gray
Write-Host ""

# ── 4. Download ──────────────────────────────────────────────────────────────
try {
    Write-Host "Downloading cloudflared.exe ... " -NoNewline
    Invoke-WebRequest -Uri $downloadUrl -OutFile $localExe -UseBasicParsing
    Write-Host "Done!" -ForegroundColor Green
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    Write-Host ""
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please download manually:" -ForegroundColor Yellow
    Write-Host "  $downloadUrl" -ForegroundColor Cyan
    Write-Host "Save it as 'cloudflared.exe' in the project folder, then run:" -ForegroundColor Yellow
    Write-Host "  python server.py --tunnel cloudflare" -ForegroundColor Yellow
    exit 1
}

# ── 5. Verify ────────────────────────────────────────────────────────────────
try {
    $ver = & $localExe --version 2>&1
    Write-Host "Version:  $ver" -ForegroundColor Gray
} catch {
    Write-Host "(Could not read version — file may still be OK)" -ForegroundColor Yellow
}

# ── 6. Done ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Start the server with:" -ForegroundColor White
Write-Host "    python server.py --tunnel cloudflare" -ForegroundColor Yellow
Write-Host ""
Write-Host "Your friends will get a free persistent HTTPS URL like:" -ForegroundColor White
Write-Host "    https://something-random.trycloudflare.com" -ForegroundColor Green
Write-Host ""
Write-Host "No account, no sign-up, no time limit!" -ForegroundColor Gray
Write-Host ""
