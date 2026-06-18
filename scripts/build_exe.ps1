# Build the downloadable MoneyPilot.exe (one-folder) and zip it for friends.
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# Output: dist\MoneyPilot\MoneyPilot.exe  and  dist\MoneyPilot-windows.zip
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = "$root\.venv\Scripts\python.exe"

# Gate 1 (fail fast, before the slow build): never ship a red suite. MoneyPilot
# self-updates a friend on an unsigned build, so a regression here reaches them
# automatically. $LASTEXITCODE is checked explicitly — $ErrorActionPreference
# does not trip on a native exe's non-zero exit.
Write-Host "Gate: running the test suite (pytest)..." -ForegroundColor Cyan
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) {
    throw "Aborting build: test suite failed (exit $LASTEXITCODE). Fix the tests before shipping."
}

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

# Gate 2 (before zipping/installer): prove the FROZEN bundle actually resolves
# its UI, icon, writable data dir, and Claude runtime. A green pytest can still
# pair with a broken bundle (missing collected data); --selftest is what catches
# that, so a broken zip can't be published to a friend.
Write-Host "Gate: headless --selftest on the built exe..." -ForegroundColor Cyan
& "$appDir\MoneyPilot.exe" --selftest
if ($LASTEXITCODE -ne 0) {
    throw "Aborting build: MoneyPilot.exe --selftest failed (exit $LASTEXITCODE). The bundle is broken; not shipping."
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
