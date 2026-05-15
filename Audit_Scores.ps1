<#
.SYNOPSIS
    IPL Fantasy 2026—Score Audit & Validation Tool
.DESCRIPTION
    Calls /api/audit-scores/{user} to fetch a full calculation trace for Sai and Moe.
#>
param(
    [int]$Port = 5000,
    [string[]]$Users = @("Sai", "Moe"),
    [switch]$Clean,
    [switch]$DeleteJson
)

$Base = "http://localhost:$Port"
$ErrorActionPreference = "Stop"

function Write-Header([string]$text) {
    Write-Host ""
    Write-Host ("-" * 68) -ForegroundColor DarkGray
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ("-" * 68) -ForegroundColor DarkGray
}

function Format-Raw($r) {
    "runs=$($r.runs) balls=$($r.balls) 4s=$($r.fours) 6s=$($r.sixes) " +
    "wkts=$($r.wickets) overs=$($r.overs) rc=$($r.runs_conceded) " +
    "cts=$($r.catches) st=$($r.stumpings) lbw=$($r.lbw_bowled)"
}

$allGood = $true
$allMismatches = @()

Write-Header "IPL Fantasy 2026 — Score Audit (server: $Base)"

foreach ($user in $Users) {
    Write-Header "$user — Full Calculation Trace"
    try {
        $audit = Invoke-RestMethod "$Base/api/audit-scores/$user" -TimeoutSec 30
    } catch {
        Write-Host "  ERROR fetching audit for $user : $_" -ForegroundColor Red
        Write-Host "  Is the server running? Start with: python server.py" -ForegroundColor Yellow
        continue
    }

    $userGood = $true
    foreach ($wk in $audit.weeks) {
        $stored = $wk.stored_week_pts
        $computed = $wk.computed_week_pts
        $ok = ($stored -eq $computed)

        if (-not $ok) { $userGood = $false; $allGood = $false }

        $flag = if ($ok) { "[OK]" } else { "[MISMATCH stored=$stored computed=$computed]" }
        $color = if ($ok) { "Green" } else { "Red" }
        
        $matchTitles = ($wk.matches_in_week | ForEach-Object { $_.title }) -join ", "

        Write-Host ""
        Write-Host ("  Week $($wk.week_no) $flag") -ForegroundColor $color
        Write-Host ("  Matches: " + $(if ($matchTitles) { $matchTitles } else { "(none scraped)" })) -ForegroundColor DarkGray

        foreach ($p in $wk.players) {
            if ($p.matches.Count -eq 0) { continue }
            $role = if ($p.is_cap) { "[C] " } elseif ($p.is_vc) { "[VC]" } else { "   " }
            
            foreach ($m in $p.matches) {
                $raw = Format-Raw $m.raw
                $pts = "base=$($m.base_pts) x$($m.multiplier) = $($m.final_pts) pts"
                Write-Host "    $role $($p.name.PadRight(24)) | $pts" -ForegroundColor Yellow
                Write-Host "    $($m.match_title.PadRight(36)) | $raw" -ForegroundColor White
                
                if ($m.base_pts -gt 200) {
                    Write-Host "    *** WARNING: base_pts=$($m.base_pts) for ONE match is unusually high. ***" -ForegroundColor Magenta
                    $allMismatches += "$user W$($wk.week_no) $($p.name): $($m.base_pts) pts"
                }
            }
        }
    }

    $totalFlag = if ($audit.total_stored -eq $audit.total_computed) { "[OK]" } else { "[MISMATCH]" }
    $totalColor = if ($audit.total_stored -eq $audit.total_computed) { "Green" } else { "Red" }

    Write-Host ""
    Write-Host ("  $user TOTAL: stored=$($audit.total_stored) computed=$($audit.total_computed) $totalFlag") -ForegroundColor $totalColor

    if (-not $userGood) {
        $allMismatches += "$user total mismatch"
    }
} # <--- THIS WAS MISSING: Closes the 'foreach ($user in $Users)' loop

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Header "Summary"
if ($allGood -and $allMismatches.Count -eq 0) {
    Write-Host "  All stored week_pts match computed values." -ForegroundColor Green
    Write-Host "  No suspiciously high single-match scores detected." -ForegroundColor Green
    Write-Host "  Scores look correct!" -ForegroundColor Green
} else {
    Write-Host "  Issues detected:" -ForegroundColor Red
    foreach ($issue in $allMismatches) {
        Write-Host "    - $issue" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Recommended fix:" -ForegroundColor Yellow
    Write-Host "  1. Run: .\Audit_Scores.ps1 -Clean -DeleteJson" -ForegroundColor Yellow
    Write-Host "  2. Then: python scraper.py" -ForegroundColor Yellow
    Write-Host "  3. Then restart server.py" -ForegroundColor Yellow
}

# ── Clean (optional) ─────────────────────────────────────────────────────────
if ($Clean) {
    Write-Header "Cleaning Score Tables"
    $djFlag = if ($DeleteJson) { "?delete_json=1" } else { "" }
    try {
        $result = Invoke-RestMethod -Method Post "$Base/api/clean-scores$djFlag" -TimeoutSec 30
        Write-Host "  Cleared tables: $($result.cleared -join ', ')" -ForegroundColor Green
        if ($result.json_files_deleted -gt 0) {
            Write-Host "  JSON cache files deleted: $($result.json_files_deleted)" -ForegroundColor Green
        }
        Write-Host ""
        Write-Host "  Next steps:" -ForegroundColor Cyan
        foreach ($step in $result.next_steps) {
            Write-Host "    * $step"
        }
    } catch {
        Write-Host "  ERROR: $_" -ForegroundColor Red
    }
}
