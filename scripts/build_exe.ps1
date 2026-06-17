# Build the downloadable MoneyPilot.exe (one-folder) and zip it for friends.
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# Output: dist\MoneyPilot\MoneyPilot.exe  and  dist\MoneyPilot-windows.zip
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\.venv\Scripts\python.exe"

Write-Host "Ensuring PyInstaller is installed..." -ForegroundColor Cyan
& $py -m pip install --quiet "pyinstaller>=6.0"

Write-Host "Cleaning previous build..." -ForegroundColor Cyan
Remove-Item "$root\build", "$root\dist" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Building (this collects the ~233MB Claude runtime; be patient)..." -ForegroundColor Cyan
Push-Location $root
& $py -m PyInstaller --noconfirm --clean MoneyPilot.spec
Pop-Location

$appDir = "$root\dist\MoneyPilot"
if (-not (Test-Path "$appDir\MoneyPilot.exe")) {
    throw "Build failed: MoneyPilot.exe not found in $appDir"
}

# Bundle the friend Read-me into the folder, then zip the whole thing.
$readme = "$root\dist\MoneyPilot\READ-ME-FIRST.txt"
if (Test-Path "$root\packaging\READ-ME-FIRST.txt") {
    Copy-Item "$root\packaging\READ-ME-FIRST.txt" $readme -Force
}

$zip = "$root\dist\MoneyPilot-windows.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Write-Host "Zipping -> $zip" -ForegroundColor Cyan
Compress-Archive -Path $appDir -DestinationPath $zip

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "Done. $zip  ($mb MB)" -ForegroundColor Green
Write-Host "Smoke test: & '$appDir\MoneyPilot.exe' --selftest" -ForegroundColor Green
