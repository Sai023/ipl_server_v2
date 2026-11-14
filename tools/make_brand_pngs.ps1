# Rasterise the brand icon (Static/brand/icon-paper.svg) into the PNG icons the web manifest / iPhone / favicon need.
# Run from anywhere:   powershell -ExecutionPolicy Bypass -File tools\make_brand_pngs.ps1
# Needs Google Chrome or Microsoft Edge (headless). Regenerate the SVGs first with:  node tools/gen_paper_mark.js
#   round = corner radius in px for icons that should have rounded corners ("any" purpose, favicon). Maskable and iPhone icons are
#   full-bleed squares: the phone applies its own mask / rounding.
$repo  = Split-Path -Parent $PSScriptRoot
$brand = Join-Path $repo "Static\brand"
$browser = @("C:\Program Files\Google\Chrome\Application\chrome.exe",
             "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             "C:\Program Files\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Chrome or Edge not found" }
$tmp = Join-Path $env:TEMP ("bcl_png_" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $tmp | Out-Null
$jobs = @(
  @{ svg = "icon-paper.svg"; out = "icon-512.png";           size = 512; round = 112 },   # web app manifest, "any"
  @{ svg = "icon-paper.svg"; out = "icon-192.png";           size = 192; round = 42  },   # web app manifest, "any"
  @{ svg = "icon-paper.svg"; out = "favicon-32.png";         size = 32;  round = 7   },   # browser tab
  @{ svg = "icon-paper.svg"; out = "icon-maskable-512.png";  size = 512; round = 0   },   # Android adaptive / maskable
  @{ svg = "icon-paper.svg"; out = "apple-touch-icon.png";   size = 180; round = 0   }    # iPhone home screen (iOS rounds it itself)
)
foreach ($j in $jobs) {
  $html = Join-Path $tmp ("p_" + $j.out + ".html")
  $svgUri = ([System.Uri](Join-Path $brand $j.svg)).AbsoluteUri
  Set-Content -Path $html -Encoding UTF8 -Value ("<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:transparent;overflow:hidden}img{display:block;width:" + $j.size + "px;height:" + $j.size + "px;border-radius:" + $j.round + "px}</style><img src='" + $svgUri + "'>")
  $out = Join-Path $brand $j.out
  & $browser --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files --default-background-color=00000000 --force-device-scale-factor=1 --window-size="$($j.size),$($j.size)" --virtual-time-budget=3000 --screenshot="$out" ([System.Uri]$html).AbsoluteUri 2>$null | Out-Null
  "{0,-24} {1,4}px  {2,7} bytes" -f $j.out, $j.size, $(if (Test-Path $out) { (Get-Item $out).Length } else { -1 })
}
Remove-Item -Recurse -Force $tmp
