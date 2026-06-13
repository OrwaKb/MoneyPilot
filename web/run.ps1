#requires -Version 5
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$port = 8000

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Host "cloudflared not found. Install it once with:" -ForegroundColor Yellow
  Write-Host "  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
  exit 1
}

Write-Host "Starting MoneyPilot Web on http://127.0.0.1:$port ..."
$uv = Start-Process -PassThru -NoNewWindow $py -ArgumentList `
  "-m","uvicorn","web.server:get_app","--factory","--host","127.0.0.1","--port","$port"
try {
  Write-Host "Opening Cloudflare tunnel — share the https://<random>.trycloudflare.com URL it prints below."
  cloudflared tunnel --url "http://127.0.0.1:$port"
} finally {
  if ($uv -and -not $uv.HasExited) { Stop-Process -Id $uv.Id -Force }
  Write-Host "Server stopped."
}
