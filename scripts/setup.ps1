# MoneyPilot one-time setup: venv + deps + Desktop shortcuts.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   -Autostart  also starts the widget at login (off by default)
param([switch]$Autostart)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\.venv")) {
    py -3.11 -m venv "$root\.venv"
}
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"

$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$desktop\MoneyPilot.lnk")
$lnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$lnk.Arguments = "-m app"
$lnk.WorkingDirectory = $root
$icon = "$root\app\ui\assets\icon.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

# --- MoneyPilot Widget shortcut (always-on floating gauge) ---
$wlnk = $ws.CreateShortcut("$desktop\MoneyPilot Widget.lnk")
$wlnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$wlnk.Arguments = "-m app.widget"
$wlnk.WorkingDirectory = $root
if (Test-Path $icon) { $wlnk.IconLocation = $icon }
$wlnk.Save()

# Opt-in: start the widget at login (Startup folder). Off unless -Autostart.
if ($Autostart) {
    $startup = [Environment]::GetFolderPath("Startup")
    $slnk = $ws.CreateShortcut("$startup\MoneyPilot Widget.lnk")
    $slnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
    $slnk.Arguments = "-m app.widget"
    $slnk.WorkingDirectory = $root
    if (Test-Path $icon) { $slnk.IconLocation = $icon }
    $slnk.Save()
    Write-Host "Widget will start at login." -ForegroundColor Green
}

Write-Host "Done. Shortcuts 'MoneyPilot' + 'MoneyPilot Widget' created." -ForegroundColor Green
