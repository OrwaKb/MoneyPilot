#requires -Version 5
# ASCII only on purpose: Windows PowerShell 5.1 reads a BOM-less .ps1 as the
# system ANSI codepage, so any non-ASCII char (e.g. an em-dash) would be
# mis-decoded and break parsing. Keep this file plain ASCII.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$port = 8000

if (-not (Test-Path $py)) {
  Write-Host "venv python not found at $py" -ForegroundColor Red
  Write-Host "Run setup first (see README)." -ForegroundColor Red
  exit 1
}

# Resolve cloudflared. The winget package does not reliably add it to PATH (and
# a session opened before install has a stale PATH), so fall back to its known
# install locations before giving up.
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
  $candidates = @(
    (Join-Path $env:ProgramFiles 'cloudflared\cloudflared.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'cloudflared\cloudflared.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\cloudflared.exe')
  )
  $cf = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $cf) {
  $pkg = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
  if (Test-Path $pkg) {
    $hit = Get-ChildItem $pkg -Recurse -Filter cloudflared.exe -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if ($hit) { $cf = $hit.FullName }
  }
}
if (-not $cf) {
  Write-Host "cloudflared not found. Install it once with:" -ForegroundColor Yellow
  Write-Host "  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
  Write-Host "If you just installed it, open a NEW terminal so PATH refreshes, then re-run." -ForegroundColor Yellow
  exit 1
}

Write-Host "Starting MoneyPilot Web on http://127.0.0.1:$port ..."
Write-Host "Using cloudflared: $cf"
$uvArgs = @("-m", "uvicorn", "web.server:get_app", "--factory",
            "--host", "127.0.0.1", "--port", "$port")
$uv = Start-Process -PassThru -NoNewWindow $py -ArgumentList $uvArgs
try {
  Write-Host "Opening Cloudflare tunnel. Share the https://<random>.trycloudflare.com URL it prints below with your friend."
  & $cf tunnel --url "http://127.0.0.1:$port"
} finally {
  if ($uv -and -not $uv.HasExited) { Stop-Process -Id $uv.Id -Force }
  Write-Host "Server stopped."
}
