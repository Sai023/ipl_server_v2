<#
.SYNOPSIS
  Installs (or refreshes) the scheduled SA20 scoring runner on THIS PC. It never talks to the database.

.DESCRIPTION
  1. freezes a copy of the code (git archive of -Ref, default origin/main) into <RunnerDir>\app
  2. copies runner.py next to it and writes the machine-local config.json (the connection FILE path, never its content)
  3. registers two per-user Windows tasks (no admin rights, no stored password, they run while you are logged on):
       "SA20 full run"      09:00 and 13:15 every day until a week after the final: finds new Cricbuzz links + a scoring pass
       "SA20 scoring pass"  every 30 minutes from 14:00 to 23:00 on every day of the season: scores finished matches
  Re-run it any time to refresh the code copy or the schedule. -Uninstall removes the tasks (logs stay).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\scheduled_runner\install_tasks.ps1 -DsnFile "<file holding the connection string>"
  ... -SkipTasks    # install the files only, to test runner.py before anything is scheduled
  ... -Uninstall    # remove the two tasks
#>
[CmdletBinding()]
param(
    [string]$DsnFile,
    [string]$Ref = 'origin/main',
    [string]$RunnerDir = (Join-Path $env:USERPROFILE 'BCLFantasyRunner'),
    [string]$Competition = 'sa20_2027',
    [string]$SeriesId = '12791',
    [string]$SeriesSlug = 'sa20-2027',
    [datetime]$SeasonFirstDay = '2027-01-17',
    [datetime]$SeasonLastDay = '2027-02-21',
    [string]$Python,
    [switch]$SkipTasks,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$TaskPath = '\BCLFantasy\'
$TaskFull = 'SA20 full run'
$TaskScore = 'SA20 scoring pass'

if ($Uninstall) {
    foreach ($n in $TaskFull, $TaskScore) {
        Unregister-ScheduledTask -TaskName $n -TaskPath $TaskPath -Confirm:$false -ErrorAction SilentlyContinue
        "removed task '$n' (if it existed)"
    }
    return
}

foreach ($bad in $env:LOCALAPPDATA, $env:APPDATA) {
    if ($bad -and $RunnerDir.StartsWith($bad, [StringComparison]::OrdinalIgnoreCase)) {
        # A packaged host app (the Claude desktop app is one) gets its writes under AppData redirected to a private copy that the
        # Task Scheduler cannot see: the tasks would fail with 0x8007010B "directory name is invalid". Learned the hard way.
        throw "refusing -RunnerDir under AppData ($RunnerDir): pick a plain local folder, e.g. <your profile>\BCLFantasyRunner"
    }
}
foreach ($bad in $env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial) {
    if ($bad -and $RunnerDir.StartsWith($bad, [StringComparison]::OrdinalIgnoreCase)) {
        # Documents/Desktop are often inside OneDrive: it would sync the logs and code copy and can lock or dehydrate files under an unattended job.
        throw "refusing -RunnerDir inside OneDrive ($RunnerDir): pick a plain local folder, e.g. <your profile>\BCLFantasyRunner"
    }
}
if (-not $DsnFile) { throw 'give -DsnFile <the file that holds the production connection string>' }
if (-not (Test-Path -LiteralPath $DsnFile)) { throw 'the connection file was not found' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sha = (git -C $repo rev-parse --short $Ref).Trim()
if ($LASTEXITCODE -or -not $sha) { throw "cannot resolve $Ref in $repo" }
if (-not $Python) { $Python = Join-Path (Split-Path (Get-Command python).Source) 'pythonw.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw "pythonw.exe not found at $Python (pass -Python)" }

# 1. frozen copy of the code (the live code, not whatever branch this checkout is on)
New-Item -ItemType Directory -Force $RunnerDir, (Join-Path $RunnerDir 'logs'), (Join-Path $RunnerDir 'state') | Out-Null
$zip = Join-Path $env:TEMP "bcl-runner-$sha.zip"
git -C $repo archive --format=zip --output=$zip $Ref
if ($LASTEXITCODE) { throw 'git archive failed' }
$new = Join-Path $RunnerDir 'app.new'
if (Test-Path -LiteralPath $new) { Remove-Item -Recurse -Force -LiteralPath $new }
Expand-Archive -LiteralPath $zip -DestinationPath $new
Remove-Item -LiteralPath $zip
"$sha $(Get-Date -Format 'yyyy-MM-dd HH:mm') (git archive of $Ref)" | Set-Content -LiteralPath (Join-Path $new 'VERSION.txt') -Encoding ASCII
$app = Join-Path $RunnerDir 'app'
$prev = Join-Path $RunnerDir 'app.prev'
if (Test-Path -LiteralPath $prev) { Remove-Item -Recurse -Force -LiteralPath $prev }
if (Test-Path -LiteralPath $app) { Rename-Item -LiteralPath $app -NewName 'app.prev' }
Rename-Item -LiteralPath $new -NewName 'app'

# 2. runner + machine-local config (a PATH to the connection file, never its content)
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'runner.py') -Destination (Join-Path $RunnerDir 'runner.py') -Force
$cfg = [ordered]@{
    python       = $Python
    dsn_file     = (Resolve-Path -LiteralPath $DsnFile).Path
    competition  = $Competition
    series_id    = $SeriesId
    series_slug  = $SeriesSlug
    time_limit_s = 600
}
[System.IO.File]::WriteAllText((Join-Path $RunnerDir 'config.json'), ($cfg | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
"files installed in $RunnerDir (code $sha)"

if ($SkipTasks) { 'tasks NOT registered (-SkipTasks)'; return }

# 3. the two tasks
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$runner = Join-Path $RunnerDir 'runner.py'
function Esc([string]$s) { [Security.SecurityElement]::Escape($s) }
function Daily([datetime]$Start, [datetime]$End, [string]$Repetition = '') {
    "    <CalendarTrigger>$Repetition<StartBoundary>$($Start.ToString('s'))</StartBoundary><EndBoundary>$($End.ToString('s'))</EndBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
}
function New-TaskXml([string]$Description, [string[]]$Triggers, [string]$Arguments) {
    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$(Esc $Description)</Description></RegistrationInfo>
  <Triggers>
$($Triggers -join "`n")
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>$(Esc $user)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$(Esc $Python)</Command>
      <Arguments>$(Esc ('"' + $runner + '" ' + $Arguments))</Arguments>
      <WorkingDirectory>$(Esc $RunnerDir)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

$until = $SeasonLastDay.Date.AddDays(8)
$fullTriggers = @(
    (Daily ([datetime]::Today.AddHours(9)) $until),
    (Daily ([datetime]::Today.AddHours(13).AddMinutes(15)) $until)
)
$rep = '<Repetition><Interval>PT30M</Interval><Duration>PT9H</Duration><StopAtDurationEnd>true</StopAtDurationEnd></Repetition>'
$scoreTriggers = @(Daily ($SeasonFirstDay.Date.AddHours(14)) ($SeasonLastDay.Date.AddDays(2)) $rep)

$xmlFull = New-TaskXml 'BCL Fantasy: finds new Cricbuzz links for the SA20 matches and scores finished ones (writes to the production database). One run at a time; safe to repeat.' $fullTriggers '--mode full'
$xmlScore = New-TaskXml 'BCL Fantasy: scores finished SA20 matches every 30 minutes on match days (writes to the production database). One run at a time; safe to repeat.' $scoreTriggers '--mode score'
[xml]$null = $xmlFull; [xml]$null = $xmlScore        # well-formed?
Register-ScheduledTask -Xml $xmlFull -TaskName $TaskFull -TaskPath $TaskPath -Force | Out-Null
Register-ScheduledTask -Xml $xmlScore -TaskName $TaskScore -TaskPath $TaskPath -Force | Out-Null
foreach ($n in $TaskFull, $TaskScore) {
    $t = Get-ScheduledTask -TaskName $n -TaskPath $TaskPath
    $i = $t | Get-ScheduledTaskInfo
    '{0,-20} {1,-8} next run {2}' -f $n, $t.State, $i.NextRunTime
}
