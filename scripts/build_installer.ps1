# Build the MoneyPilot installer (dist\MoneyPilot-Setup.exe) from the already-built
# PyInstaller bundle. Run scripts\build_exe.ps1 first.
#   powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\dist\MoneyPilot\MoneyPilot.exe")) {
    throw "Build the app first: scripts\build_exe.ps1 (need dist\MoneyPilot\MoneyPilot.exe)"
}

# version from app/version.py so the installer matches the build
$m = Select-String -Path "$root\app\version.py" -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $m) { throw "could not read __version__ from app\version.py" }
$version = $m.Matches[0].Groups[1].Value
Write-Host "Version: $version" -ForegroundColor Cyan

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "ISCC.exe not found. Install Inno Setup:  winget install --id JRSoftware.InnoSetup"
}

Write-Host "Compiling installer with $iscc ..." -ForegroundColor Cyan
& $iscc "/DMyAppVersion=$version" "$root\packaging\MoneyPilot.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

$out = "$root\dist\MoneyPilot-Setup.exe"
if (-not (Test-Path $out)) { throw "Installer not produced at $out" }
$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "Done. $out  ($mb MB)" -ForegroundColor Green
