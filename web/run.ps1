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
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Host "cloudflared not found. Install it once with:" -ForegroundColor Yellow
  Write-Host "  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
  exit 1
}

Write-Host "Starting MoneyPilot Web on http://127.0.0.1:$port ..."
$uvArgs = @("-m", "uvicorn", "web.server:get_app", "--factory",
            "--host", "127.0.0.1", "--port", "$port")
$uv = Start-Process -PassThru -NoNewWindow $py -ArgumentList $uvArgs
try {
  Write-Host "Opening Cloudflare tunnel. Share the https://<random>.trycloudflare.com URL it prints below with your friend."
  cloudflared tunnel --url "http://127.0.0.1:$port"
} finally {
  if ($uv -and -not $uv.HasExited) { Stop-Process -Id $uv.Id -Force }
  Write-Host "Server stopped."
}
