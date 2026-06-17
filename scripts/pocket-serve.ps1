# Expose the desktop app's Pocket sync listener to YOUR Tailscale network over
# HTTPS, so the phone capture app can reach it. One-time-ish: run it after
# Tailscale is installed + HTTPS certs are enabled in the Tailscale admin.
#
#   powershell -ExecutionPolicy Bypass -File scripts\pocket-serve.ps1
#
# This is NOT an always-on server: it just forwards your tailnet HTTPS to
# 127.0.0.1:8788, which only answers while the MoneyPilot desktop app is open.
$ErrorActionPreference = "Stop"
$port = 8788

$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts) {
    foreach ($c in @("$env:ProgramFiles\Tailscale\tailscale.exe",
                     "${env:ProgramFiles(x86)}\Tailscale\tailscale.exe")) {
        if (Test-Path $c) { $ts = $c; break }
    }
}
if (-not $ts) {
    Write-Host "Tailscale not found. Install it on this PC and your phone" -ForegroundColor Yellow
    Write-Host "(https://tailscale.com/download), sign into the same account on both," -ForegroundColor Yellow
    Write-Host "and enable HTTPS certificates in the Tailscale admin console. Then rerun." -ForegroundColor Yellow
    exit 1
}

Write-Host "Forwarding tailnet HTTPS -> 127.0.0.1:$port ..." -ForegroundColor Cyan
& $ts serve --bg --https=443 "http://127.0.0.1:$port"
Write-Host ""
& $ts serve status
Write-Host ""
Write-Host "Done. Open the MoneyPilot desktop app, go to Settings -> PHONE, and" -ForegroundColor Green
Write-Host "open the pairing link on your phone. (To stop: tailscale serve --https=443 off)" -ForegroundColor Green
