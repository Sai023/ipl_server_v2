# Bakes the app's paper (the page colour plus the two grain layers from Static/paper.css) into ONE seamless tile for the Android app,
# so the native loading screen has exactly the same texture as the web page that appears after it.
#   powershell -ExecutionPolicy Bypass -File tools\make_android_paper_tile.ps1      ->  Static\brand\android\paper_tex.jpg  (520 x 520)
# Needs Google Chrome or Microsoft Edge (headless). The grain tiles are 260 px and 520 px and both repeat seamlessly, so 520 px repeats cleanly.
# Android: copy it to app/src/main/res/drawable-nodpi/paper_tex.jpg and tile it (res/drawable/paper_bg.xml, tileMode repeat).
$repo  = Split-Path -Parent $PSScriptRoot
$css   = Get-Content (Join-Path $repo "Static\paper.css") -Raw
$theme = Get-Content (Join-Path $repo "Static\theme.css") -Raw
$tex   = [regex]::Match($css, ':root\s*\{\s*--paper-tex:\s*(?<v>.*?);\s*\}', 'Singleline').Groups['v'].Value
$paper = [regex]::Match($theme, '--paper:\s*(?<v>#[0-9A-Fa-f]{6})').Groups['v'].Value
if (-not $tex -or -not $paper) { throw "could not read --paper-tex from paper.css or --paper from theme.css" }
$browser = @("C:\Program Files\Google\Chrome\Application\chrome.exe",
             "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             "C:\Program Files\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Chrome or Edge not found" }
$tmp  = Join-Path $env:TEMP ("bcl_tile_" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$html = Join-Path $tmp "tile.html"; $png = Join-Path $tmp "tile.png"
Set-Content -Path $html -Encoding UTF8 -Value ("<!doctype html><meta charset=utf-8><style>html,body{margin:0;overflow:hidden}html{background-color:$paper;background-image:$tex}</style>")
# render a little larger than the tile (headless windows have a minimum size) and crop the top-left 520 x 520 afterwards
& $browser --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 --window-size=600,600 --virtual-time-budget=3000 --screenshot="$png" ([System.Uri]$html).AbsoluteUri 2>$null | Out-Null
if (-not (Test-Path $png)) { throw "the browser did not write a screenshot" }
Add-Type -AssemblyName System.Drawing
$src  = [System.Drawing.Image]::FromFile($png)
$tile = New-Object System.Drawing.Bitmap 520, 520
$g = [System.Drawing.Graphics]::FromImage($tile); $g.DrawImage($src, (New-Object System.Drawing.Rectangle 0,0,520,520), (New-Object System.Drawing.Rectangle 0,0,520,520), [System.Drawing.GraphicsUnit]::Pixel); $g.Dispose()
$enc = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" }
$ps  = New-Object System.Drawing.Imaging.EncoderParameters 1
$ps.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]90)
$out = Join-Path $repo "Static\brand\android\paper_tex.jpg"
$tile.Save($out, $enc, $ps); $tile.Dispose(); $src.Dispose()
Remove-Item -Recurse -Force $tmp
"{0}  {1} bytes  (page colour {2})" -f $out, (Get-Item $out).Length, $paper
